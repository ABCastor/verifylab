"""SQLite search cache at `<root>/.vl-cache/index.sqlite`: items and Lean declaration names.

The index is a derived cache, never truth: it is safe to delete, and `vl show` / `vl validate` read
records directly. A signature of its inputs (item files, Lean files under `[lean] roots`, the roots
themselves) is stored; a missing, stale or corrupt index is rebuilt and the caller is told why.
FTS5 is used when SQLite provides it, plain LIKE otherwise.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from . import leanmod
from .records import RecordError, parse_item
from .repo import CACHE_DIR, Repo

SCHEMA_VERSION = 1
DB_NAME = "index.sqlite"
SKIP_DIRS = {".lake", "lake-packages", ".git", "build", "node_modules", "__pycache__"}

_DECL_RE = re.compile(
    r"^\s*(?:@\[[^\]]*\]\s*)*"
    r"(?:(?:private|protected|noncomputable|partial|unsafe|nonrec|scoped|local)\s+)*"
    r"(class\s+inductive|theorem|lemma|def|abbrev|structure|inductive|class|instance)\s+"
    r"([^\s:({\[⦃]+)"
)
_NAMESPACE_RE = re.compile(r"^\s*namespace\s+(\S+)")
_SECTION_RE = re.compile(r"^\s*(?:noncomputable\s+)?section\b")
_MUTUAL_RE = re.compile(r"^\s*mutual\s*$")
_END_RE = re.compile(r"^\s*end(?:\s+(\S+))?\s*$")


def fts5_available() -> bool:
    try:
        con = sqlite3.connect(":memory:")
        try:
            con.execute("CREATE VIRTUAL TABLE probe USING fts5(x)")
        finally:
            con.close()
        return True
    except sqlite3.Error:
        return False


# Lean declarations ------------------------------------------------------------------------------


def _strip_comments(line: str, depth: int) -> tuple[str, int]:
    """Remove `/- … -/` (nested) and `--` comments from one line, carrying block depth across lines."""
    out, i = [], 0
    while i < len(line):
        two = line[i:i + 2]
        if depth:
            if two == "/-":
                depth, i = depth + 1, i + 2
            elif two == "-/":
                depth, i = depth - 1, i + 2
            else:
                i += 1
            continue
        if two == "/-":
            depth, i = 1, i + 2
        elif two == "--":
            break
        else:
            out.append(line[i])
            i += 1
    return "".join(out), depth


def scan_lean(text: str) -> list[tuple[str, str, int, str]]:
    """(qualified name, keyword, line number, source line) for each declaration, tracking namespaces.

    Cheap and approximate: anonymous instances and names in «guillemets» are skipped.
    """
    decls = []
    scopes: list[str | None] = []  # namespace name, or None for a section / mutual block
    depth = 0
    for number, raw in enumerate(text.splitlines(), start=1):
        line, depth = _strip_comments(raw, depth)
        if not line.strip():
            continue
        if m := _NAMESPACE_RE.match(line):
            scopes.append(m.group(1))
        elif _SECTION_RE.match(line) or _MUTUAL_RE.match(line):
            scopes.append(None)
        elif _END_RE.match(line):
            if scopes:
                scopes.pop()
        elif m := _DECL_RE.match(line):
            keyword, name = " ".join(m.group(1).split()), m.group(2)
            if name.startswith("_root_."):
                name = name[len("_root_."):]
            else:
                prefix = ".".join(s for s in scopes if s)
                name = f"{prefix}.{name}" if prefix else name
            decls.append((name, keyword, number, raw.strip()[:200]))
    return decls


def lean_files(repo: Repo) -> list[Path]:
    """The .lean files of the modules under the `[lean] roots` of the Lean project: a root is a module name, so `Foo.Bar`
    is the file `Foo/Bar.lean` and everything under the folder `Foo/Bar/`. Never a file outside the repository,
    whatever `project` or a root says (`..`, an absolute path, a symbolic link)."""
    base = repo.root / repo.config.lean.project
    inside = repo.root.resolve()
    files: set[Path] = set()
    for root in repo.config.lean.roots:
        if not leanmod.is_valid_module_name(root):
            continue
        rel = leanmod.module_to_path(root)
        module_file = (base / rel).resolve()
        if module_file.is_relative_to(inside) and module_file.is_file():
            files.add(module_file)
        start = (base / rel[:-len(".lean")]).resolve()
        if not start.is_relative_to(inside) or not start.is_dir():
            continue
        for folder, dirs, names in os.walk(start):
            dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not d.startswith("."))
            files.update(path for n in names if n.endswith(".lean")
                         and (path := Path(folder) / n).resolve().is_relative_to(inside))
    return sorted(files)


def _rel(repo: Repo, path: Path) -> str:
    try:
        return str(path.relative_to(repo.root))
    except ValueError:
        return str(path)


# Index --------------------------------------------------------------------------------------------


@dataclass
class IndexInfo:
    path: str
    rebuilt: bool
    reason: str | None          # why it was rebuilt: missing, stale, corrupt, schema
    fts: bool
    items: int
    unparsed_items: int
    lean_files: int
    decls: int


def signature(repo: Repo) -> str:
    """Hash of everything the index is built from. Item files are hashed by content (they are small and
    read anyway); Lean files by size and mtime."""
    entries = []
    for file in repo.item_files():
        data = file.read_bytes()
        entries.append(["item", _rel(repo, file), len(data), hashlib.sha1(data).hexdigest()])
    for file in lean_files(repo):
        st = file.stat()
        entries.append(["lean", _rel(repo, file), st.st_size, st.st_mtime_ns])
    basis = {
        "schema": SCHEMA_VERSION,
        "research_dir": repo.config.research_dir,
        "lean_project": repo.config.lean.project,
        "roots": list(repo.config.lean.roots),
        "files": entries,
    }
    return hashlib.sha256(json.dumps(basis, sort_keys=True).encode()).hexdigest()


def _build(repo: Repo, target: Path, sig: str, fts: bool) -> None:
    tmp = target.with_name(f"{target.name}.tmp-{os.getpid()}")
    tmp.unlink(missing_ok=True)
    try:
        _fill(repo, tmp, sig, fts)
    except BaseException:
        tmp.unlink(missing_ok=True)       # a half-built index never stays behind
        raise
    os.replace(tmp, target)


def _fill(repo: Repo, tmp: Path, sig: str, fts: bool) -> None:
    con = sqlite3.connect(tmp)
    try:
        con.executescript("""
            CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE items (id TEXT PRIMARY KEY, kind TEXT, title TEXT, path TEXT, revision TEXT,
                                statement TEXT, limits TEXT, assumptions TEXT, body TEXT, haystack TEXT);
            CREATE TABLE decls (name TEXT, kind TEXT, path TEXT, line INTEGER, source TEXT,
                                name_lower TEXT, source_lower TEXT);
        """)
        if fts:
            con.execute("CREATE VIRTUAL TABLE items_fts USING fts5(id, title, statement, limits, assumptions, body)")
        unparsed = 0
        for file in repo.item_files():
            rel = _rel(repo, file)
            try:
                item = parse_item(file.read_bytes(), rel)
            except RecordError:
                unparsed += 1
                continue
            row = (item.id, item.title, item.statement or "", "\n".join(item.limits),
                   "\n".join(item.assumptions), item.body)
            haystack = "\n".join(row).casefold()
            con.execute("INSERT OR IGNORE INTO items VALUES (?,?,?,?,?,?,?,?,?,?)",
                        (item.id, item.kind, item.title, rel, item.revision, *row[2:], haystack))
            if fts:
                con.execute("INSERT INTO items_fts VALUES (?,?,?,?,?,?)", row)
        files = lean_files(repo)
        for file in files:
            rel = _rel(repo, file)
            text = file.read_text(encoding="utf-8", errors="replace")
            con.executemany("INSERT INTO decls VALUES (?,?,?,?,?,?,?)",
                            [(n, k, rel, ln, src, n.casefold(), src.casefold()) for n, k, ln, src in scan_lean(text)])
        con.execute("CREATE INDEX decls_name ON decls(name_lower)")
        con.executemany("INSERT INTO meta VALUES (?,?)", [
            ("schema", str(SCHEMA_VERSION)), ("signature", sig), ("fts", "1" if fts else "0"),
            ("unparsed_items", str(unparsed)), ("lean_files", str(len(files))),
        ])
        con.commit()
    finally:
        con.close()


def _escape(token: str) -> str:
    return token.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _like(token: str) -> str:
    return f"%{_escape(token)}%"


class Index:
    def __init__(self, con: sqlite3.Connection, info: IndexInfo):
        self.con = con
        self.info = info

    def close(self) -> None:
        self.con.close()

    def search_items(self, text: str, kind: str | None = None) -> list[str]:
        """Ids of matching items, best first. Every query token must match (prefix match with FTS5)."""
        words = re.findall(r"\w+", text.casefold())
        if self.info.fts and words:
            query = " AND ".join(f'"{w}"*' for w in words)
            rows = self.con.execute(
                "SELECT f.id FROM items_fts f JOIN items i ON i.id = f.id WHERE items_fts MATCH ? "
                "AND (? IS NULL OR i.kind = ?) ORDER BY bm25(items_fts, 8.0, 5.0, 2.0, 1.0, 1.0, 0.5), f.id",
                (query, kind, kind)).fetchall()
            return [r[0] for r in rows]
        tokens = text.casefold().split() or [text.casefold()]
        where = " AND ".join("haystack LIKE ? ESCAPE '\\'" for _ in tokens)
        rows = self.con.execute(
            f"SELECT id, title FROM items WHERE {where} AND (? IS NULL OR kind = ?)",
            (*[_like(t) for t in tokens], kind, kind)).fetchall()

        def rank(row: tuple[str, str]) -> tuple[int, str]:
            head = f"{row[0]} {row[1]}".casefold()
            return (-sum(t in head for t in tokens), row[0])

        return [r[0] for r in sorted(rows, key=rank)]

    def search_decls(self, text: str, limit: int) -> tuple[list[tuple[str, str, str, int]], int]:
        """(name, keyword, path, line) best first, and the total number of matches.

        A declaration matches when every whitespace-separated token occurs in its name or source line;
        exact names, then name suffixes, then name matches rank before source-line matches."""
        query = text.casefold().strip()
        tokens = query.split() or [query]
        hay = "(name_lower || ' ' || source_lower)"
        where = " AND ".join(f"{hay} LIKE ? ESCAPE '\\'" for _ in tokens)
        in_name = " AND ".join("name_lower LIKE ? ESCAPE '\\'" for _ in tokens)
        params = [_like(t) for t in tokens]
        total = self.con.execute(f"SELECT count(*) FROM decls WHERE {where}", params).fetchone()[0]
        rows = self.con.execute(
            f"SELECT name, kind, path, line FROM decls WHERE {where} ORDER BY "
            f"CASE WHEN name_lower = ? THEN 0 WHEN name_lower LIKE ? ESCAPE '\\' THEN 1 "
            f"WHEN {in_name} THEN 2 ELSE 3 END, length(name), name, path, line LIMIT ?",
            (*params, query, f"%.{_escape(query)}", *params, limit)).fetchall()
        return [tuple(r) for r in rows], total


def _check(con: sqlite3.Connection, sig: str) -> str | None:
    """None when the index is usable; otherwise the reason to rebuild."""
    meta = dict(con.execute("SELECT key, value FROM meta").fetchall())
    if meta.get("schema") != str(SCHEMA_VERSION):
        return "schema"
    if meta.get("fts") == "1" and not fts5_available():
        return "fts5 unavailable"
    if meta.get("signature") != sig:
        return "stale"
    con.execute("SELECT count(*) FROM items").fetchone()
    con.execute("SELECT count(*) FROM decls").fetchone()
    return None


def _connect_ro(target: Path) -> sqlite3.Connection:
    return sqlite3.connect(target.resolve().as_uri() + "?mode=ro", uri=True)


def open_index(repo: Repo, *, fts: bool | None = None, force: str | None = None) -> Index:
    """Open the index, rebuilding it first when it is missing, stale or corrupt (or when `force` gives
    a reason, e.g. corruption found while querying)."""
    folder = repo.root / CACHE_DIR
    folder.mkdir(exist_ok=True)
    target = folder / DB_NAME
    sig = signature(repo)
    want_fts = fts5_available() if fts is None else fts
    reason = force or (None if target.exists() else "missing")
    if reason is None:
        try:
            con = _connect_ro(target)
            try:
                reason = _check(con, sig)
                if reason is None and fts is not None:
                    has_fts = con.execute("SELECT value FROM meta WHERE key='fts'").fetchone()[0] == "1"
                    reason = None if has_fts == fts else "search mode changed"
            finally:
                con.close()
        except sqlite3.DatabaseError as exc:
            reason = f"corrupt: {exc}"
    if reason is not None:
        _build(repo, target, sig, want_fts)
    con = _connect_ro(target)
    meta = dict(con.execute("SELECT key, value FROM meta").fetchall())
    info = IndexInfo(
        path=_rel(repo, target),
        rebuilt=reason is not None,
        reason=reason,
        fts=meta.get("fts") == "1",
        items=con.execute("SELECT count(*) FROM items").fetchone()[0],
        unparsed_items=int(meta.get("unparsed_items", "0")),
        lean_files=int(meta.get("lean_files", "0")),
        decls=con.execute("SELECT count(*) FROM decls").fetchone()[0],
    )
    return Index(con, info)
