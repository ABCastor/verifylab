"""What `vl import comparator` reads next to the challenges: the catalogue of the source repository
(`formalization.yaml`, mathlib-initiative schema v0.4) and its folder of family scope notes (`docs/NNN.md`).

The standard library has no YAML reader, so `parse_yaml` reads the subset the catalogue uses and refuses anything
else with the line number, never guessing: block mappings and lists by indentation (a list may sit at its key's
indentation), `- key: value` items, plain, "double-quoted" and 'single-quoted' scalars, flow lists of scalars
(`["a", "b"]`), `>`/`|` block scalars (all lines at one indentation; their blank and comment lines are dropped),
`#` comments. Plain scalars are strings except `true`, `false`, `null` and `~`; numbers stay strings. Anchors,
aliases, tags, flow mappings, multi-line plain or quoted scalars, tabs and document markers are errors. A JSON file
is read as the YAML it is. The catalogue does not link a challenge to its paper (v0.4 lists papers and main results
apart): the scope notes do, in links.

`parse_yaml(text, typed=True)` reads front matter that carries numbers, as people and YAML dumpers write it
(`vl attempts`); it shares the scalars above and has its own block reader (`_Front`). A plain scalar written as a
decimal number (`-800`, `0.35`, `1.0e-05`; no leading zero, a signed exponent only after a fraction, as dumpers
write it, so that the abbreviated commit `1234e56` stays text) is an int or a float. Flow collections may be mappings
and may nest (`{value: [-800, -200], axioms: [propext]}`), each closing on its line. Plain and quoted scalars may
run over several lines (folded as YAML folds them; a double-quoted one with YAML's escapes), a block scalar
(`|`, `>`, with `-`, `+` or an indentation digit) keeps its blank and `#` lines, and a value may start on the line
after its key. Anchors, aliases, tags, document markers, flow collections over several lines, tabs in indentation
and keys that are not plain names are still errors with their line.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, NamedTuple

VERSION = "v0.4"
MAX_BYTES = 8 * 1024 * 1024


class CatalogueError(ValueError):
    pass


class _Line(NamedTuple):
    number: int
    indent: int
    text: str


_KEY = re.compile(r"([A-Za-z_][A-Za-z0-9_.-]*):(?: +(.*))?")
_BLOCK_SCALAR = re.compile(r"[>|][+-]?")


def _strip_comment(text: str) -> str:
    quote, i = None, 0
    while i < len(text):
        ch = text[i]
        if quote == '"' and ch == "\\":
            i += 1
        elif quote:
            quote = None if ch == quote else quote
        elif ch in "\"'" and (i == 0 or text[i - 1] in " \t[,"):
            quote = ch
        elif ch == "#" and (i == 0 or text[i - 1] in " \t"):
            return text[:i].rstrip()
        i += 1
    return text.rstrip()


def _scalar(text: str, number: int) -> Any:
    text = _strip_comment(text).strip()
    where = f"line {number}"
    if not text or text in ("null", "~"):
        return None
    if text in ("true", "false"):
        return text == "true"
    if text[0] == '"':
        try:
            value = json.loads(text)
        except ValueError:
            raise CatalogueError(f"{where}: unsupported double-quoted scalar {text[:60]!r}") from None
        return value
    if text[0] == "'":
        if len(text) < 2 or text[-1] != "'" or re.search(r"(?<!')'(?!')", text[1:-1].replace("''", "")):
            raise CatalogueError(f"{where}: unsupported single-quoted scalar {text[:60]!r}")
        return text[1:-1].replace("''", "'")
    if text[0] == "[":
        return _flow_list(text, number)
    if text[0] in "{&*!|>@`%" or text == "-" or text.startswith("- ") or re.search(r": |:$", text):
        raise CatalogueError(f"{where}: unsupported YAML ({text[:60]!r})")
    return text


def _flow_list(text: str, number: int) -> list[Any]:
    if text[-1] != "]":
        raise CatalogueError(f"line {number}: a flow list must close on its line")
    parts, current, quote, i, body = [], "", None, 0, text[1:-1]
    while i < len(body):
        ch = body[i]
        if quote:
            if ch == "\\" and quote == '"':
                current += body[i:i + 2]
                i += 2
                continue
            quote = None if ch == quote else quote
        elif ch in "\"'" and not current.strip():
            quote = ch
        elif ch in "[{":
            raise CatalogueError(f"line {number}: nested flow collections are not supported")
        elif ch == ",":
            parts.append(current)
            current, i = "", i + 1
            continue
        current += ch
        i += 1
    if quote:
        raise CatalogueError(f"line {number}: unclosed quote in a flow list")
    parts.append(current)
    if not parts[-1].strip():
        parts.pop()                  # an empty list, or a trailing comma
    return [_scalar(part, number) for part in parts]


_NUMBER = re.compile(r"-?(?:0|[1-9][0-9]*)(\.[0-9]+(?:[eE][+-][0-9]+)?)?")
_FLOW_KEY = re.compile(r"([A-Za-z_][A-Za-z0-9_.-]*):(?: +|(?=[,}]))")


def _typed_scalar(text: str, number: int) -> Any:
    text = _strip_comment(text).strip()
    if text[:1] in ("[", "{"):
        value, end = _flow(text, 0, number)
        if text[end:].strip():
            raise CatalogueError(f"line {number}: unexpected text after the closing {text[end - 1]!r}")
        return value
    match = _NUMBER.fullmatch(text)
    if match:
        return float(text) if match.group(1) else int(text)
    if text[:1] == '"':                                           # on one line, with the escapes YAML defines
        if not re.fullmatch(r'"(?:[^"\\]|\\.)*"', text):
            raise CatalogueError(f"line {number}: unsupported double-quoted scalar {text[:60]!r}")
        return _unescape(text[1:-1], number)
    return _scalar(text, number)


def _flow(text: str, i: int, number: int) -> tuple[Any, int]:
    """The flow list or mapping that opens at text[i], and the index after its closing bracket."""
    close = "]" if text[i] == "[" else "}"
    result: Any = [] if close == "]" else {}
    i += 1
    while True:
        while i < len(text) and text[i] == " ":
            i += 1
        if i < len(text) and text[i] == close:                     # empty, or after a trailing comma
            return result, i + 1
        if i >= len(text):
            raise CatalogueError(f"line {number}: a flow collection must close on its line with {close!r}")
        key = None
        if close == "}":
            match = _FLOW_KEY.match(text, i)
            if not match:
                raise CatalogueError(f"line {number}: expected 'key: value' inside {{...}}, not {text[i:i + 40]!r}")
            key, i = match.group(1), match.end()
            if key in result:
                raise CatalogueError(f"line {number}: duplicate key '{key}'")
        if i < len(text) and text[i] in "[{":
            value, i = _flow(text, i, number)
        else:
            start, quote = i, None
            while i < len(text) and (quote or text[i] not in ",]}"):
                if quote == '"' and text[i] == "\\":
                    i += 1
                elif quote:
                    quote = None if text[i] == quote else quote
                elif text[i] in "\"'" and not text[start:i].strip():
                    quote = text[i]
                i += 1
            if quote:
                raise CatalogueError(f"line {number}: a flow collection must close on its line with {close!r}")
            if not text[start:i].strip():
                raise CatalogueError(f"line {number}: an empty entry in a flow collection (write null)")
            value = _typed_scalar(text[start:i], number)
        while i < len(text) and text[i] == " ":
            i += 1
        if i >= len(text) or text[i] not in "," + close:
            raise CatalogueError(f"line {number}: a flow collection must close on its line with {close!r}")
        if key is None:
            result.append(value)
        else:
            result[key] = value
        if text[i] == close:
            return result, i + 1
        i += 1


def _is_item(line: _Line) -> bool:
    return line.text == "-" or line.text.startswith("- ")


def _block(lines: list[_Line], i: int, indent: int) -> tuple[Any, int]:
    if lines[i].indent != indent:
        raise CatalogueError(f"line {lines[i].number}: unexpected indentation")
    return _list(lines, i, indent) if _is_item(lines[i]) else _mapping(lines, i, indent)


def _nested(lines: list[_Line], i: int, indent: int, key: bool = True) -> tuple[Any, int]:
    """The value after a `key:` (or, with `key` false, a bare `-`) with nothing else on its line. Only a key may
    be followed by a list at its own indentation; after a bare `-` that line is the next item."""
    if i < len(lines) and (lines[i].indent > indent or (key and lines[i].indent == indent and _is_item(lines[i]))):
        return _block(lines, i, lines[i].indent)
    return None, i


def _mapping(lines: list[_Line], i: int, indent: int) -> tuple[dict[str, Any], int]:
    result: dict[str, Any] = {}
    while i < len(lines) and lines[i].indent == indent and not _is_item(lines[i]):
        line = lines[i]
        match = _KEY.fullmatch(line.text)
        if not match:
            raise CatalogueError(f"line {line.number}: expected 'key: value', not {line.text[:60]!r}")
        key, rest = match.group(1), _strip_comment(match.group(2) or "").strip()
        if key in result:
            raise CatalogueError(f"line {line.number}: duplicate key '{key}'")
        i += 1
        if not rest:
            result[key], i = _nested(lines, i, indent)
        elif rest[0] in ">|":
            if not _BLOCK_SCALAR.fullmatch(rest):
                raise CatalogueError(f"line {line.number}: unsupported block scalar header {rest!r}")
            start = i
            while i < len(lines) and lines[i].indent > indent:
                i += 1
            if len({x.indent for x in lines[start:i]}) > 1:
                raise CatalogueError(f"line {line.number}: a block scalar whose lines are indented differently "
                                     "is not supported")
            result[key] = (" " if rest[0] == ">" else "\n").join(x.text for x in lines[start:i])
        else:
            result[key] = _scalar(rest, line.number)
    if i < len(lines) and lines[i].indent > indent:
        raise CatalogueError(f"line {lines[i].number}: unexpected indentation (a multi-line scalar?)")
    return result, i


def _list(lines: list[_Line], i: int, indent: int) -> tuple[list[Any], int]:
    items: list[Any] = []
    while i < len(lines) and lines[i].indent == indent and _is_item(lines[i]):
        line = lines[i]
        body = line.text[1:].lstrip(" ")
        offset = len(line.text) - len(body)
        if not body:
            value, i = _nested(lines, i + 1, indent, key=False)
        elif _KEY.fullmatch(_strip_comment(body)):
            lines[i] = _Line(line.number, indent + offset, body)      # the item's mapping starts after the dash
            value, i = _mapping(lines, i, indent + offset)
        else:
            value, i = _scalar(body, line.number), i + 1
        items.append(value)
    return items, i


def parse_yaml(text: str, typed: bool = False) -> Any:
    if typed:
        try:
            return _Front(text).document()
        except RecursionError:
            raise CatalogueError("lists or mappings nested too deeply to read") from None
    if text.lstrip().startswith("{"):
        try:
            return json.loads(text)
        except ValueError as exc:
            raise CatalogueError(f"not valid JSON or YAML: {exc}") from None
    lines = []
    for number, raw in enumerate(text.splitlines(), 1):
        body = raw.lstrip(" \t")
        if "\t" in raw[:len(raw) - len(body)]:
            raise CatalogueError(f"line {number}: tabs are not supported")
        if not body.strip() or body.startswith("#"):
            continue
        if raw.startswith(("---", "...", "%")):
            raise CatalogueError(f"line {number}: document markers and directives are not supported")
        lines.append(_Line(number, len(raw) - len(body), body.rstrip()))
    if not lines:
        return None
    value, end = _block(lines, 0, lines[0].indent)
    if end != len(lines):
        raise CatalogueError(f"line {lines[end].number}: unexpected content")
    return value


# The typed reader -----------------------------------------------------------------------------------------------------

_KEY_HEAD = re.compile(r"([A-Za-z_][A-Za-z0-9_.-]*):(?: +|$)")
_BLOCK_HEADER = re.compile(r"([>|])(?:([+-])([1-9])?|([1-9])([+-])?)?")
_COMMENT = re.compile(r"(?:^|[ \t])#")
_ESCAPES = {"0": "\0", "a": "\a", "b": "\b", "t": "\t", "\t": "\t", "n": "\n", "v": "\v", "f": "\f", "r": "\r",
            "e": "\x1b", " ": " ", '"': '"', "/": "/", "\\": "\\", "N": "\x85", "_": "\xa0", "L": "\u2028",
            "P": "\u2029"}
_HEX = {"x": 2, "u": 4, "U": 8}


def _dash(text: str) -> bool:
    return text == "-" or text.startswith("- ")


def _unescape(text: str, number: int) -> str:
    """The content of one line of a double-quoted scalar, with the escapes YAML defines."""
    out, i = [], 0
    while i < len(text):
        ch = text[i]
        if ch != "\\":
            out.append(ch)
            i += 1
            continue
        code = text[i + 1:i + 2]
        digits = text[i + 2:i + 2 + _HEX.get(code, 0)]
        if code in _ESCAPES:
            out.append(_ESCAPES[code])
        elif code in _HEX and len(digits) == _HEX[code] and re.fullmatch(r"[0-9A-Fa-f]+", digits) \
                and int(digits, 16) < 0x110000:
            out.append(chr(int(digits, 16)))
        else:
            raise CatalogueError(f"line {number}: unsupported escape {text[i:i + 2]!r} in a double-quoted scalar")
        i += 2 + len(digits)
    try:                                                          # "\ud83d\ude00", as JSON writes one character
        return "".join(out).encode("utf-16", "surrogatepass").decode("utf-16")
    except UnicodeError:
        raise CatalogueError(f"line {number}: half of a surrogate pair in a double-quoted scalar") from None


class _Front:
    """The block structure of `parse_yaml(text, typed=True)`. It walks the raw lines, because a scalar that runs
    over several lines owns its blank lines, and a block scalar its `#` lines too. `indent` is everywhere the
    column of the parent: of the key for a mapping value, of the dash for a list item."""

    def __init__(self, text: str):
        self.raw = text.splitlines()
        self.i = 0

    def document(self) -> Any:
        if not self._more():
            return None
        value = self._block(self._indent(self.i))
        if self._more():
            raise CatalogueError(f"line {self.i + 1}: unexpected content")
        return value

    def _indent(self, i: int) -> int:
        return len(self.raw[i]) - len(self.raw[i].lstrip(" "))

    def _more(self) -> bool:
        """Move to the next line that is not blank or a comment, and say whether there is one."""
        while self.i < len(self.raw):
            raw = self.raw[self.i]
            body = raw.strip()
            if body and not body.startswith("#"):
                if raw.lstrip(" ").startswith("\t"):
                    raise CatalogueError(f"line {self.i + 1}: tabs are not supported")
                if raw.startswith(("---", "...", "%")):
                    raise CatalogueError(f"line {self.i + 1}: document markers and directives are not supported")
                return True
            self.i += 1
        return False

    def _deeper(self, indent: int) -> None:
        if self._more() and self._indent(self.i) > indent:
            raise CatalogueError(f"line {self.i + 1}: unexpected indentation")

    def _block(self, indent: int) -> Any:
        return self._list(indent) if _dash(self.raw[self.i].strip()) else self._mapping(indent)

    def _mapping(self, indent: int) -> dict[str, Any]:
        result: dict[str, Any] = {}
        while self._more() and self._indent(self.i) == indent and not _dash(self.raw[self.i].strip()):
            text, number = self.raw[self.i].strip(), self.i + 1
            match = _KEY_HEAD.match(text)
            if not match:
                raise CatalogueError(f"line {number}: expected 'key: value', not {text[:60]!r}")
            if match.group(1) in result:
                raise CatalogueError(f"line {number}: duplicate key '{match.group(1)}'")
            self.i += 1
            result[match.group(1)] = self._value(text[match.end():], indent, number, key=True)
        self._deeper(indent)
        return result

    def _list(self, indent: int) -> list[Any]:
        items: list[Any] = []
        while self._more() and self._indent(self.i) == indent and _dash(self.raw[self.i].strip()):
            text, number = self.raw[self.i].strip(), self.i + 1
            body = text[1:].lstrip(" ")
            if _dash(body) or _KEY_HEAD.match(body):       # `- key: value` and `- - x`: a block after the dash
                column = indent + len(text) - len(body)
                self.raw[self.i] = " " * column + body
                items.append(self._block(column))
            else:
                self.i += 1
                items.append(self._value(body, indent, number, key=False))
        self._deeper(indent)
        return items

    def _value(self, text: str, indent: int, number: int, key: bool) -> Any:
        """What follows `key:` or `- ` on line `number`; the reader stands on the next line. Only a key may be
        followed by a list at its own indentation; after a bare `-` that line is the next item."""
        if not text or text.startswith("#"):
            if not self._more() or self._indent(self.i) < indent:
                return None
            line = self.raw[self.i].strip()
            if self._indent(self.i) == indent:
                return self._list(indent) if key and _dash(line) else None
            if _dash(line) or _KEY_HEAD.match(line):
                return self._block(self._indent(self.i))
            self.i += 1                                       # a scalar that starts on the line after its key
            return self._value(line, indent, self.i, key)
        if text[0] in "&*":
            raise CatalogueError(f"line {number}: anchors and aliases are not supported")
        if text[0] == "!":
            raise CatalogueError(f"line {number}: tags are not supported")
        if text[0] in "|>":
            return self._block_scalar(text, indent, number)
        if text[0] in "\"'":
            return self._quoted(text, indent, number)
        if text[0] in "[{":
            return _typed_scalar(text, number)
        return self._plain(text, indent, number)

    def _plain(self, text: str, indent: int, number: int) -> Any:
        """A plain scalar: one line is typed (a number, true, null); with continuation lines, indented more than the
        parent, it is text folded as YAML folds it (a line break is a space, a blank line a line break)."""
        cut = _COMMENT.search(text)
        pieces, ended, blank, j = [text[:cut.start()].rstrip() if cut else text], bool(cut), 0, self.i
        while not ended and j < len(self.raw):
            line = self.raw[j].strip()
            if not line:
                blank, j = blank + 1, j + 1
                continue
            if self._indent(j) <= indent or line.startswith("#"):
                break
            cut = _COMMENT.search(line)
            piece, ended = (line[:cut.start()].rstrip(), True) if cut else (line, False)
            if re.search(r": |:$", piece):
                raise CatalogueError(f"line {j + 1}: 'key: value' inside a text that runs over several lines "
                                     "(check the indentation, or put the text in quotes)")
            pieces.append("\n" * blank + piece if blank else " " + piece)
            blank, j = 0, j + 1
            self.i = j
        first = _typed_scalar(pieces[0], number)              # also refuses what a plain scalar may not be
        return first if len(pieces) == 1 else "".join(pieces)

    def _quoted(self, text: str, indent: int, number: int) -> str:
        """A 'single-quoted' or "double-quoted" scalar, which may run over several lines indented more than the
        parent: a line break is a space, a blank line a line break, and in double quotes a backslash at the end of
        a line joins the next one to it."""
        quote, pos, lines, at = text[0], 1, [], number
        while True:
            end = pos
            while end < len(text) and (text[end] != quote or quote == "'" and text[end + 1:end + 2] == "'"):
                end += 2 if text[end] == ("'" if quote == "'" else "\\") else 1
            lines.append((text[pos:end], at))
            if end < len(text):
                break
            if self.i >= len(self.raw) or (self.raw[self.i].strip() and self._indent(self.i) <= indent):
                raise CatalogueError(f"line {number}: this quoted scalar is not closed (its next lines are indented "
                                     "more than its key)")
            text, pos, at = self.raw[self.i], 0, self.i + 1
            self.i += 1
        tail = text[end + 1:].strip()
        if tail and not tail.startswith("#"):
            raise CatalogueError(f"line {at}: unexpected text after the closing quote: {tail[:40]!r}")
        out, blank, joined = [], 0, False
        for index, (line, at) in enumerate(lines):
            last, escaped = index == len(lines) - 1, False
            line = line.lstrip(" \t") if index else line
            if not last:
                line = line.rstrip(" \t")
                escaped = quote == '"' and (len(line) - len(line.rstrip("\\"))) % 2 == 1
                line = line[:-1] if escaped else line
            if index and not line and not last and not escaped:
                blank += 1
                continue
            if index:
                out.append("\n" * blank if blank or joined else " ")
            out.append(_unescape(line, at) if quote == '"' else line.replace("''", "'"))
            blank, joined = 0, escaped
        return "".join(out)

    def _block_scalar(self, header: str, indent: int, number: int) -> str:
        """`|` keeps the line breaks and `>` folds them; `-` drops the final line break and `+` keeps every trailing
        one; a digit gives the indentation of the text when its first line starts with spaces."""
        cut = _COMMENT.search(header)
        match = _BLOCK_HEADER.fullmatch(header[:cut.start()].rstrip() if cut else header)
        if not match:
            raise CatalogueError(f"line {number}: unsupported block scalar header {header!r}")
        chomp, digit = match.group(2) or match.group(5) or "", match.group(3) or match.group(4)
        column = indent + int(digit) if digit else None
        lines: list[str] = []
        while self.i < len(self.raw):
            raw = self.raw[self.i]
            if raw.strip():
                column = self._indent(self.i) if column is None else column
                if self._indent(self.i) < column or column <= indent:
                    break
            lines.append(raw[column:] if raw.strip() else "")
            self.i += 1
        trailing = 0
        while lines and not lines[-1]:
            lines.pop()
            trailing += 1
        if match.group(1) == "|":
            body = "\n".join(lines)
        else:
            parts, blank, spaced = [], 0, None                # spaced: the line before starts with a space
            for line in lines:
                if not line:
                    blank += 1
                    continue
                wide = line[0] in " \t"
                if spaced is None:
                    parts.append("\n" * blank)
                elif spaced or wide:
                    parts.append("\n" * (blank + 1))
                else:
                    parts.append("\n" * blank if blank else " ")
                parts.append(line)
                blank, spaced = 0, wide
            body = "".join(parts)
        if chomp == "+":
            return body + "\n" * (trailing + bool(lines))
        return body + ("\n" if lines and chomp != "-" else "")


# The catalogue ------------------------------------------------------------------------------------------------

PAPER_DIR = re.compile(r"(?:^|/)preprints/([^/]+)/[^/]+$")


@dataclass(frozen=True)
class Catalogue:
    review: str | None                                  # `review: status:`, for the whole catalogue
    papers: dict[str, str]                              # preprint folder -> title
    results: dict[str, tuple[tuple[str, str | None], ...]]    # challenge name -> (declaration, file) of main results


def read_catalogue(path: Path) -> Catalogue:
    """`formalization.yaml` (schema v0.4), or the JSON conversion of it. Any other version is refused."""
    try:
        if path.stat().st_size > MAX_BYTES:
            raise CatalogueError(f"{path}: larger than {MAX_BYTES} bytes")
        raw = parse_yaml(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError) as exc:
        raise CatalogueError(f"{path}: {exc}") from None
    except CatalogueError as exc:
        raise CatalogueError(f"{path}: {exc}") from None
    if not isinstance(raw, dict) or raw.get("version") != VERSION:
        raise CatalogueError(f"{path}: not a catalogue of version {VERSION} "
                             f"(version: {raw.get('version') if isinstance(raw, dict) else None!r})")
    papers = {}
    for entry in raw.get("sources") or []:
        match = PAPER_DIR.search(str(entry.get("id", ""))) if isinstance(entry, dict) else None
        if match and isinstance(entry.get("title"), str) and entry["title"].strip():
            papers.setdefault(match.group(1), entry["title"].strip())
    results: dict[str, list[tuple[str, str | None]]] = {}
    status = raw.get("status") if isinstance(raw.get("status"), dict) else {}
    for entry in status.get("main_results") or []:
        config = entry.get("comparator_config") if isinstance(entry, dict) else None
        if isinstance(config, str) and config.endswith(".json"):
            name = PurePosixPath(config).stem
            results.setdefault(name, []).append((str(entry.get("declaration") or ""), entry.get("file")))
    review = raw.get("review", {}).get("status") if isinstance(raw.get("review"), dict) else None
    return Catalogue(review if isinstance(review, str) else None, papers,
                     {name: tuple(rows) for name, rows in results.items()})


# The family scope notes ---------------------------------------------------------------------------------------

_LINK = re.compile(r"\[([^\]\n]*)\]\(((?:[^()\s]|\([^()\s]*\))+)\)")      # a target may hold `(...)`: CAT(0)
_CHALLENGE = re.compile(r"(?:\.\./)*ComparatorChallenges/([A-Za-z0-9_]+)\.lean")


@dataclass(frozen=True)
class Paper:
    folder: str        # the preprint's folder name, its identity
    title: str         # the link text of the first note that cites it
    path: str          # `preprints/<folder>/<file>.pdf`, in the source repository


@dataclass(frozen=True)
class Notes:
    papers: dict[str, Paper]                     # folder -> paper
    cited_by: dict[str, tuple[str, ...]]         # challenge name -> folders of the papers its notes cite
    families: dict[str, tuple[str, ...]]         # challenge name -> the notes (file stems) that list it


def read_notes(folder: Path) -> Notes:
    """Each `*.md` of `folder` that links both a paper (`.../preprints/<folder>/<file>.pdf`) and a challenge
    (`.../ComparatorChallenges/NAME.lean`) says that those challenges formalize that family's papers."""
    papers: dict[str, Paper] = {}
    cited: dict[str, dict[str, None]] = {}
    families: dict[str, list[str]] = {}
    for file in sorted(folder.glob("*.md")):
        try:
            if file.stat().st_size > MAX_BYTES:
                raise CatalogueError(f"{file}: larger than {MAX_BYTES} bytes")
            text = file.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise CatalogueError(f"{file}: {exc}") from None
        found, names = [], []
        for title, target in _LINK.findall(text):
            paper = PAPER_DIR.search(target)
            challenge = _CHALLENGE.fullmatch(target)
            if paper and target.endswith(".pdf"):
                found.append(paper.group(1))
                papers.setdefault(paper.group(1), Paper(paper.group(1), title.strip() or paper.group(1),
                                                        paper.group(0).lstrip("/")))
            elif challenge:
                names.append(challenge.group(1))
        for name in dict.fromkeys(names):
            families.setdefault(name, []).append(file.stem)
            cited.setdefault(name, {}).update(dict.fromkeys(found))
    return Notes(papers, {name: tuple(folders) for name, folders in cited.items()},
                 {name: tuple(stems) for name, stems in families.items()})
