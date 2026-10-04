"""Command lints: Lean commands in candidate files that can change what a statement means or run code at build
time. They are warnings recorded in the receipt and shown on the card, never a verdict: Comparator compares the
elaborated statement and replays the proof in the kernel whatever these commands did. Defence in depth, and a
signal for the reviewer (catalog rows S2, E1 to E3, P6, P7).

The scan works on text with comments, strings and character literals masked (`leanmod`), so a keyword inside a
comment or a string is not reported and a quote cannot hide code from it. It does not elaborate anything.
"""

from __future__ import annotations

import re
from typing import Iterable

from . import leanmod

_ID = r"[\w.'!?⁰-₟]"            # identifier characters, subscripts included


def _kw(word: str) -> str:
    return rf"(?<![\w.'!?#]){word}(?!{_ID})"


# (kind, pattern, why): the order is the order of the report on the same line.
RULES: tuple[tuple[str, re.Pattern[str], str], ...] = tuple((kind, re.compile(pattern), why) for kind, pattern, why in (
    ("notation", _kw(r"notation\d?"), "notation can make the same text mean something else (S2)"),
    ("infix", _kw(r"(?:infixl|infixr|infix|prefix|postfix)"), "an operator can be redefined (S2)"),
    ("macro_rules", _kw("macro_rules"), "macro rules can rewrite what a statement elaborates to (S2)"),
    ("macro", _kw("macro"), "a macro can rewrite what a statement elaborates to (S2)"),
    ("syntax", _kw("syntax"), "new syntax can shadow existing notation (S2)"),
    ("elab_rules", _kw("elab_rules"), "a custom elaborator runs code and can change meaning (S2, E2)"),
    ("elab", _kw("elab"), "a custom elaborator runs code and can change meaning (S2, E2)"),
    ("skipKernelTC", r"(?<![\w.'!?])set_option\s+debug\.skipKernelTC(?!" + _ID + ")",
     "turns off kernel type checking for what follows (E3)"),
    ("#exit", _kw("#exit"), "everything after it is never elaborated (P7)"),
    ("unsafe", _kw("unsafe"), "unsafe code is not checked by the kernel (P6)"),
    ("implemented_by", _kw("implemented_by"), "compiled code may differ from the kernel term (P6)"),
    ("extern", _kw("extern"), "compiled code may differ from the kernel term (P6)"),
    ("run_cmd", _kw("run_cmd"), "runs code at build time (E2)"),
    ("run_elab", _kw("run_elab"), "runs code at build time (E2)"),
    ("run_meta", _kw("run_meta"), "runs code at build time (E2)"),
    ("#eval", _kw("#eval"), "runs code at build time; what it prints is not evidence (E1, E2)"),
    ("initialize", _kw(r"(?:builtin_)?initialize"), "runs code whenever the module is imported (E2)"),
))
_INSTANCE = re.compile(_kw("instance"))
_DERIVING = re.compile(r"(?<![\w.'!?])deriving\s+$")
_CLASS = re.compile(_kw(r"class") + r"\s+(?:inductive\s+)?(?:\([^)]*\)\s*)?([^\s:({\[⦃]+)")
_OPEN = {"(": ")", "[": "]", "{": "}", "⦃": "⦄", "⟨": "⟩"}


def declared_classes(masked_texts: Iterable[str]) -> set[str]:
    """Last name components of the classes the scanned files declare."""
    return {m.group(1).rsplit(".", 1)[-1] for text in masked_texts for m in _CLASS.finditer(text)}


def _instance_class(masked: str, start: int) -> str | None:
    """The class of the instance declared at `start` (just after `instance`): the head of the type after the
    first colon outside brackets, or None when no such colon comes before `:=`, `where` or the next command."""
    depth: list[str] = []
    i, n = start, len(masked)
    while i < n:
        ch = masked[i]
        if ch in _OPEN:
            depth.append(_OPEN[ch])
        elif depth and ch == depth[-1]:
            depth.pop()
        elif not depth and masked.startswith(":=", i):
            return None
        elif not depth and ch == ":":
            head = re.match(r"\s*@?([^\s()\[\]{}⦃⦄⟨⟩,:]+)", masked[i + 1:])
            return head.group(1) if head else None
        elif not depth and ch == "\n" and i + 1 < n and masked[i + 1] not in " \t\n":
            return None                       # a new command starts in column 0
        i += 1
    return None


def lint(text: str, where: str, local_classes: set[str] = frozenset()) -> list[dict[str, object]]:
    """Findings in one Lean file: {file, line, kind, why, text}. `local_classes` are classes the scanned files
    declare; an instance of any other class may shadow an instance from Lean core or a library."""
    masked = leanmod.mask_comments_and_strings(text)
    lines = text.splitlines()
    found: list[tuple[int, int, str, str]] = []
    try:
        if leanmod.parse_header(text).prelude:
            found.append((0, 0, "prelude", "`prelude` drops Lean's standard library: core names can be redefined (E4)"))
    except leanmod.LeanModError:
        pass                                   # header problems are reported by the check itself
    for kind, pattern, why in RULES:
        for m in pattern.finditer(masked):
            found.append((m.start(), 1, kind, why))
    for m in _INSTANCE.finditer(masked):
        before = masked[max(0, m.start() - 40):m.start()]
        if _DERIVING.search(before):
            continue
        bracket = masked.rfind("[", 0, m.start())
        if bracket >= 0 and masked.rfind("]", bracket, m.start()) < 0 and "\n" not in masked[bracket:m.start()]:
            found.append((m.start(), 2, "instance", "an instance attribute can change how a statement elaborates (S2)"))
            continue
        cls = _instance_class(masked, m.end())
        if cls is None or cls.rsplit(".", 1)[-1] not in local_classes:
            what = f"an instance of {cls}" if cls else "an instance"
            found.append((m.start(), 2, "instance", f"{what}, a class declared outside the scanned files: it may "
                                                    "shadow an instance from Lean or a library (S2)"))
    out = []
    for offset, _, kind, why in sorted(found):
        line = text.count("\n", 0, offset) + 1
        out.append({"file": where, "line": line, "kind": kind, "why": why, "text": lines[line - 1].strip()[:160]
                    if line - 1 < len(lines) else ""})
    return out


def lint_files(files: dict[str, str]) -> list[dict[str, object]]:
    """Lint several files (path -> text); classes declared in any of them count as local for all of them."""
    masked = {}
    for path, text in files.items():
        try:
            masked[path] = leanmod.mask_comments_and_strings(text)
        except leanmod.LeanModError:
            masked[path] = None
    local = declared_classes(m for m in masked.values() if m is not None)
    findings: list[dict[str, object]] = []
    for path, text in sorted(files.items()):
        if masked[path] is None:
            findings.append({"file": path, "line": 0, "kind": "unreadable", "text": "",
                             "why": "comments or strings could not be delimited; the file was not scanned"})
            continue
        findings += lint(text, path, local)
    return findings
