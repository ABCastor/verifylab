"""Lean 4 module utilities: header parsing, module <-> path mapping, import closure, import rewriting,
and locating `theorem NAME ... := sorry` declarations in a target file.

Everything here fails closed: text we cannot parse with confidence raises `LeanModError` instead of
being guessed at. The header grammar follows Lean 4.34 (`Lean/Parser/Module/Syntax.lean`):

    header := ["module"] ["prelude"] import*
    import := ["public"] ["meta"] "import" ["all"] ident

Comments (`--` to end of line, nested `/- ... -/`) are whitespace. A doc comment (`/--`, `/-!`) is a
token, so it ends the header, exactly as in Lean.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable, Iterable, Mapping, Sequence

RESERVED_PREFIXES = ("VLTrusted", "VLChallenge", "VLSolution")
_KEYWORDS = {"module", "prelude", "public", "meta", "import", "all"}


class LeanModError(ValueError):
    """Lean text or a module name could not be handled with confidence."""


# Names ---------------------------------------------------------------------------------------------

def _is_id_start(ch: str) -> bool:
    return ch == "_" or ch.isalpha()


def _is_id_rest(ch: str) -> bool:
    return ch == "_" or ch in "'!?" or ch.isalnum()


def is_valid_component(part: str) -> bool:
    return bool(part) and _is_id_start(part[0]) and all(_is_id_rest(c) for c in part[1:])


def is_valid_module_name(name: str) -> bool:
    """Plain dotted Lean names only. Escaped names («...») are rejected on purpose."""
    return isinstance(name, str) and bool(name) and all(is_valid_component(p) for p in name.split("."))


is_valid_decl_name = is_valid_module_name


def module_to_path(module: str) -> str:
    """`Fixture.Defs` -> `Fixture/Defs.lean` (relative to the Lean project's source root)."""
    if not is_valid_module_name(module):
        raise LeanModError(f"not a plain Lean module name: {module!r}")
    return "/".join(module.split(".")) + ".lean"


def path_to_module(path: str) -> str:
    if not path.endswith(".lean") or path.startswith("/"):
        raise LeanModError(f"not a relative .lean path: {path!r}")
    module = ".".join(path[: -len(".lean")].split("/"))
    if not is_valid_module_name(module):
        raise LeanModError(f"path does not map to a plain module name: {path!r}")
    return module


def in_roots(module: str, roots: Iterable[str]) -> bool:
    return any(module == root or module.startswith(root + ".") for root in roots)


def is_reserved(module: str) -> bool:
    return in_roots(module, RESERVED_PREFIXES)


# Comments and strings ------------------------------------------------------------------------------

def _skip_block_comment(text: str, i: int) -> int:
    """`text[i:]` starts with `/-`; return the index just past the matching `-/` (comments nest)."""
    depth, j, n = 0, i, len(text)
    while j < n:
        if text.startswith("/-", j):
            depth += 1
            j += 2
        elif text.startswith("-/", j):
            depth -= 1
            j += 2
            if depth == 0:
                return j
        else:
            j += 1
    raise LeanModError("unterminated block comment")


_CHAR_LITERAL = re.compile(r"'(?:\\(?:x[0-9a-fA-F]{2}|u[0-9a-fA-F]{4}|[^\n])|[^\\'\n])'")
_RAW_STRING = re.compile(r'r(#*)"')


def mask_comments_and_strings(text: str) -> str:
    """Same length as `text`; every comment (doc comments included), string literal (raw strings included)
    and character literal is replaced by spaces, newlines kept, so offsets and columns still match the original.
    The `{…}` parts of an interpolated string (`s!"…{x}…"`) are code and stay visible."""
    out = list(text)
    n = len(text)

    def blank(a: int, b: int) -> None:
        for k in range(a, b):
            if out[k] != "\n":
                out[k] = " "

    def string(i: int) -> int:
        """`text[i]` opens a string literal; blank it (keeping interpolated code) and return the index after it."""
        interpolated = i > 1 and text[i - 1] == "!" and _is_id_rest(text[i - 2])     # s!"…", m!"…", f!"…"
        j = i + 1
        start = i
        while j < n and text[j] != '"':
            if text[j] == "\\":
                j += 2
            elif interpolated and text[j] == "{":
                blank(start, j + 1)
                j = code(j + 1, closing="}")
                start = j - 1           # the closing brace is blanked with the rest of the literal
            else:
                j += 1
        if j >= n:
            raise LeanModError("unterminated string literal")
        blank(start, j + 1)
        return j + 1

    def code(i: int, closing: str | None = None) -> int:
        """Scan code from `i`; with `closing`, stop after the matching brace (nested braces counted)."""
        depth = 0
        while i < n:
            ch = text[i]
            if text.startswith("--", i):
                j = text.find("\n", i)
                j = n if j < 0 else j
                blank(i, j)
                i = j
            elif text.startswith("/-", i):
                j = _skip_block_comment(text, i)
                blank(i, j)
                i = j
            elif ch == '"':
                i = string(i)
            elif ch == "r" and (i == 0 or not _is_id_rest(text[i - 1])) and (m := _RAW_STRING.match(text, i)):
                end = text.find('"' + m.group(1), m.end())
                if end < 0:
                    raise LeanModError("unterminated raw string literal")
                end += 1 + len(m.group(1))
                blank(i, end)
                i = end
            elif ch == "'" and (i == 0 or not _is_id_rest(text[i - 1])) and (m := _CHAR_LITERAL.match(text, i)):
                blank(i, m.end())
                i = m.end()
            elif closing and ch == "{":
                depth += 1
                i += 1
            elif closing and ch == closing:
                if depth == 0:
                    return i + 1
                depth -= 1
                i += 1
            else:
                i += 1
        if closing:
            raise LeanModError("unterminated interpolation in a string literal")
        return i

    code(0)
    return "".join(out)


# Header --------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Import:
    module: str
    start: int          # offset of the module identifier in the text
    end: int
    public: bool = False
    meta: bool = False
    all: bool = False


@dataclass(frozen=True)
class Header:
    module_keyword: bool
    prelude: bool
    imports: tuple[Import, ...]
    end: int            # offset just past the last header token (0 when the header is empty)

    @property
    def modules(self) -> list[str]:
        return [imp.module for imp in self.imports]


def _skip_ws(text: str, i: int) -> int:
    n = len(text)
    while i < n:
        ch = text[i]
        if ch in " \t\r\n":
            i += 1
        elif text.startswith("--", i):
            j = text.find("\n", i)
            i = n if j < 0 else j
        elif text.startswith("/-", i) and not text.startswith("/--", i) and not text.startswith("/-!", i):
            i = _skip_block_comment(text, i)
        else:
            break
    return i


def _read_ident(text: str, i: int) -> tuple[str, int]:
    """A dotted identifier starting at `i`; returns ('', i) when there is none."""
    n, j = len(text), i
    if j < n and text[j] == "«":
        raise LeanModError(f"escaped identifiers («...») are not supported (offset {i})")
    if j >= n or not _is_id_start(text[j]):
        return "", i
    while True:
        while j < n and _is_id_rest(text[j]):
            j += 1
        if j < n and text[j] == ".":
            if j + 1 < n and text[j + 1] == "«":
                raise LeanModError(f"escaped identifiers («...») are not supported (offset {j})")
            if j + 1 < n and _is_id_start(text[j + 1]):
                j += 1
                continue
            raise LeanModError(f"identifier with a trailing dot at offset {i}")
        break
    return text[i:j], j


def _word_at(text: str, i: int) -> tuple[str, int]:
    """The identifier at `i` (after whitespace/comments), and the offset after it."""
    i = _skip_ws(text, i)
    word, j = _read_ident(text, i)
    return word, j


_BODY_IMPORT = re.compile(r"(?m)^[ \t]*(?:public[ \t]+)?(?:meta[ \t]+)?import[ \t\n]")


def parse_header(text: str) -> Header:
    """Parse the import header. Raises LeanModError on anything not certainly understood, including an
    `import` line after the header (Lean rejects it; we refuse to guess what was meant)."""
    # A byte-order mark is skipped, never removed: every offset in the Header is one in `text` as given, which is
    # what the rewriting functions apply them to.
    i = end = 1 if text[:1] == "﻿" else 0
    module_kw = prelude = False
    word, j = _word_at(text, i)
    if word == "module":
        module_kw, i, end = True, j, j
        word, j = _word_at(text, i)
    if word == "prelude":
        prelude, i, end = True, j, j
    imports: list[Import] = []
    while True:
        word, j = _word_at(text, i)
        public = meta = False
        k = j
        if word == "public":
            public = True
            word, k = _word_at(text, k)
        if word == "meta":
            meta = True
            word, k = _word_at(text, k)
        if word != "import":
            break                       # `public section`, `meta def`, a command: the header is over
        start = _skip_ws(text, k)
        name, after = _read_ident(text, start)
        imp_all = False
        if name == "all":
            imp_all = True
            start = _skip_ws(text, after)
            name, after = _read_ident(text, start)
        if not name:
            raise LeanModError(f"`import` without a module name at offset {start}")
        if name in _KEYWORDS:
            raise LeanModError(f"`import {name}` is not a module import (offset {start})")
        if not is_valid_module_name(name):
            raise LeanModError(f"unsupported module name {name!r}")
        imports.append(Import(name, start, after, public, meta, imp_all))
        i = end = after
    body = mask_comments_and_strings(text[end:])
    stray = _BODY_IMPORT.search(body)
    if stray:
        line = text[: end + stray.start()].count("\n") + 1
        raise LeanModError(f"`import` after the header (line {line}); Lean only accepts imports at the top")
    return Header(module_kw, prelude, tuple(imports), end)


def imports_of(text: str) -> list[str]:
    return parse_header(text).modules


def rewrite_imports(text: str, rename: Callable[[str], str | None]) -> str:
    """Replace each imported module name for which `rename` returns a new name; keep everything else
    (keywords, comments, layout) byte for byte."""
    header = parse_header(text)
    out = text
    for imp in reversed(header.imports):
        new = rename(imp.module)
        if new is not None and new != imp.module:
            if not is_valid_module_name(new):
                raise LeanModError(f"invalid replacement module name {new!r}")
            out = out[: imp.start] + new + out[imp.end:]
    return out


def prefix_imports(text: str, prefix: str, roots: Sequence[str]) -> str:
    """`import Fixture.Defs` -> `import VLTrusted.Fixture.Defs` for every import under `roots`."""
    return rewrite_imports(text, lambda m: f"{prefix}.{m}" if in_roots(m, roots) else None)


def insert_imports(text: str, modules: Sequence[str]) -> str:
    """Append `import M` lines at the end of the header for modules not already imported."""
    header = parse_header(text)
    have = set(header.modules)
    extra = []
    for module in modules:
        if not is_valid_module_name(module):
            raise LeanModError(f"invalid module name {module!r}")
        if module not in have:
            have.add(module)
            extra.append(module)
    if not extra:
        return text
    lines = "".join(f"import {m}\n" for m in extra)
    if header.end == 0:
        return lines + text
    return text[: header.end] + "\n" + lines.rstrip("\n") + text[header.end:]


# Closure -------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Closure:
    modules: dict[str, bytes]       # in-project module -> source, in discovery order
    external: frozenset[str]        # imports outside the project roots (Init, Std, Mathlib, ...)
    parents: dict[str, str]         # module -> a module (or "<start>") that imports it


def decode(data: bytes, where: str) -> str:
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise LeanModError(f"{where}: not UTF-8") from exc


def closure(start: Iterable[str], roots: Sequence[str], read: Callable[[str], bytes | None]) -> Closure:
    """Transitive in-project imports reachable from `start` (modules), reading sources with
    `read(module_path)`. A missing in-project module raises LeanModError naming it and its importer."""
    modules: dict[str, bytes] = {}
    external: set[str] = set()
    parents: dict[str, str] = {}
    queue: list[tuple[str, str]] = []
    for module in start:
        if not is_valid_module_name(module):
            raise LeanModError(f"invalid module name {module!r}")
        if in_roots(module, roots):
            queue.append((module, "<start>"))
        else:
            external.add(module)
    while queue:
        module, parent = queue.pop(0)
        if module in modules:
            continue
        path = module_to_path(module)
        data = read(path)
        if data is None:
            via = "" if parent == "<start>" else f" (imported by {parent})"
            raise LeanModError(f"module {module} not found at {path}{via}")
        modules[module] = data
        parents[module] = parent
        for imported in imports_of(decode(data, path)):
            if in_roots(imported, roots):
                if imported not in modules:
                    queue.append((imported, module))
            else:
                external.add(imported)
    return Closure(modules, frozenset(external), parents)


# Target theorems -----------------------------------------------------------------------------------

@dataclass(frozen=True)
class TheoremDecl:
    name: str                       # fully qualified (namespaces applied, `_root_.` stripped)
    private: bool
    line: int
    body: tuple[int, int] | None    # span of `sorry` / `by sorry` when the body is exactly that


_DECL = re.compile(
    r"^(?:@\[[^\]]*\]\s*)*"
    r"(?P<mods>(?:(?:private|protected|public|noncomputable|unsafe|partial|nonrec)\s+)*)"
    r"(?P<kw>theorem|lemma)\s+(?P<name>[^\s:(\[{.]+(?:\.[^\s:(\[{.]+)*)(?:\.\{[^}]*\})?"
)
_SORRY_BODY = re.compile(r":=\s*(?P<body>(?:by\s+)?sorry)\s*\Z")


def _chunks(masked: str, offset: int) -> list[tuple[int, int]]:
    """Split into commands: a command starts at a non-blank character in column 0."""
    starts = [m.start() for m in re.finditer(r"(?m)^\S", masked) if m.start() >= offset]
    bounds = []
    for k, s in enumerate(starts):
        e = starts[k + 1] if k + 1 < len(starts) else len(masked)
        bounds.append((s, e))
    return bounds


def find_theorems(text: str) -> dict[str, list[TheoremDecl]]:
    """Every `theorem`/`lemma` declared at column 0, keyed by its fully qualified name.

    Conventions this relies on (and that a target file must follow): each command starts in column 0
    and continuation lines are indented; namespaces are opened with `namespace X` and closed with
    `end X`. Anything else is reported as a parse problem by the caller, never guessed."""
    header = parse_header(text)
    masked = mask_comments_and_strings(text)
    scopes: list[tuple[str, list[str]]] = []          # (kind, name components)
    found: dict[str, list[TheoremDecl]] = {}
    for s, e in _chunks(masked, header.end):
        chunk = masked[s:e]
        words = chunk.split()
        head = words[0] if words else ""
        if head == "namespace" and len(words) > 1:
            name = words[1]
            if not is_valid_decl_name(name):
                raise LeanModError(f"unsupported namespace name {name!r}")
            scopes.append(("namespace", name.split(".")))
            continue
        if head == "section" or (head in ("noncomputable", "public") and words[1:2] == ["section"]):
            idx = words.index("section")
            name = words[idx + 1] if len(words) > idx + 1 and is_valid_decl_name(words[idx + 1]) else ""
            scopes.append(("section", name.split(".") if name else []))
            continue
        if head == "mutual":
            scopes.append(("mutual", []))
            continue
        if head == "end":
            name = words[1] if len(words) > 1 and is_valid_decl_name(words[1]) else ""
            if not scopes:
                raise LeanModError(f"`end {name}` without an open namespace or section".replace("  ", " "))
            kind, parts = scopes.pop()
            if name and name.split(".") != parts:
                raise LeanModError(f"`end {name}` closes {kind} {'.'.join(parts) or '(anonymous)'}")
            continue
        match = _DECL.match(chunk)
        if not match:
            continue
        local = match.group("name")
        if not is_valid_decl_name(local):
            continue                    # e.g. «escaped»: never matches a listed plain name, so fails closed
        if local.startswith("_root_."):
            full = local[len("_root_."):]
        else:
            ns = [p for kind, parts in scopes if kind == "namespace" for p in parts]
            full = ".".join([*ns, local])
        body = None
        sorry = _SORRY_BODY.search(chunk.rstrip())
        if sorry:
            body = (s + sorry.start("body"), s + sorry.end("body"))
        decl = TheoremDecl(full, "private" in match.group("mods").split(), text[:s].count("\n") + 1, body)
        found.setdefault(full, []).append(decl)
    if scopes:
        kind, parts = scopes[-1]
        raise LeanModError(f"{kind} {'.'.join(parts) or '(anonymous)'} is never closed")
    return found


def target_problems(text: str, theorems: Sequence[str]) -> list[str]:
    """Why `theorems` cannot be checked against this target text (empty list = all fine)."""
    try:
        found = find_theorems(text)
    except LeanModError as exc:
        return [f"cannot parse the target: {exc}"]
    problems = []
    for name in theorems:
        decls = found.get(name, [])
        if not decls:
            problems.append(f"theorem '{name}' is not declared in the target")
        elif len(decls) > 1:
            problems.append(f"theorem '{name}' is declared {len(decls)} times in the target")
        elif decls[0].private:
            problems.append(f"theorem '{name}' is private (module-mangled name); private targets are unsupported")
        elif decls[0].body is None:
            problems.append(f"theorem '{name}' (line {decls[0].line}) must have body `sorry` in the target")
    return problems


def fill_sorries(text: str, proofs: Mapping[str, str]) -> str:
    """Replace the `sorry` body of each named theorem by `(proof)`."""
    found = find_theorems(text)
    spans = []
    for name, proof in proofs.items():
        decls = found.get(name, [])
        if len(decls) != 1 or decls[0].body is None:
            raise LeanModError(f"theorem '{name}' has no unique `sorry` body to fill")
        spans.append((decls[0].body, proof))
    out = text
    for (a, b), proof in sorted(spans, key=lambda x: x[0][0], reverse=True):
        out = out[:a] + "(" + proof.strip() + "\n  )" + out[b:]
    return out
