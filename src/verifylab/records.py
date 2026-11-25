"""Record formats: research items (Markdown + TOML front matter), receipts and reviews (JSON).

Status is never stored in a record. It is derived from receipts and reviews (see status.py).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import tomllib
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

from .config import PROTECTED_EXTERNAL_KERNELS, RULES_INPUT

ITEM_KINDS = ("question", "result", "conjecture", "source", "intuition", "explanation")
CLAIM_TYPES = ("formal", "computation", "numeric", "empirical", "literature", "prose")
SOURCE_ACCESS = ("full-text-read", "abstract-only", "citation-only", "secondary")
REVIEW_KINDS = ("fidelity", "compare", "correction", "retraction", "understanding", "correspondence", "appraisal")
# Reviews that bind the meaning they read (the target, its definitions, the statement): a change of any of them
# makes the review stale.
MEANING_KINDS = ("fidelity", "correspondence")
# How the formal statement relates to the paper's (ADR 0013): the same, a stronger one, a weaker one, another.
CORRESPONDENCE_OUTCOMES = ("same", "stronger", "weaker", "different")
STATEMENT_CHARS = 200
# Where a statement comes from (ADR 0014): declared by the item's author, never checked and never read by status.
PROVENANCE_KINDS = ("upstream-reviewed", "lab-authored", "model-authored", "own")
SOURCE_REQUIRED = ("upstream-reviewed", "lab-authored")     # kinds that must say where the statement is from
PROVENANCE_KEYS = ("kind", "source")
ORIGIN_KEYS = ("agent", "model", "context", "tokens", "from", "inspired_by")
# Items created after this date should say how they came to be (`[origin]`) and, with a target, where the statement
# comes from (`[provenance]`): `vl validate` warns when they do not. Older items are never asked.
ORIGIN_ASKED_AFTER = "2026-10-11"
# Statement-probe findings a fidelity review answers for: `trivial` (the target is meant to be closed by automation
# alone), `foundations` (it read what the statement says about Choice and the set theory it works in).
ACKNOWLEDGEABLE = ("trivial", "foundations")
VERDICTS = ("pass", "fail", "error", "unsupported")
ASSURANCE = ("protected", "exploratory")

# What a check of an item answers: the fields that define the question (target, theorem list, witnesses, evaluator)
# and those that bind the answer to it (proof terms, imports, solution module; candidate, entry, files). A receipt
# records their digest, and goes stale when the item's current values differ. Prose, limits and relations do not.
QUESTION_FIELDS = {"lean": ("target", "theorems", "witnesses", "proofs", "imports", "solution"),
                   "python": ("evaluator", "candidate", "entry", "files")}

RECEIPT_SCHEMA = "vl.receipt/1"
REVIEW_SCHEMA = "vl.review/1"

ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,63}$")
AUTHOR_RE = re.compile(r"^(human|agent):[A-Za-z0-9._@+-]{1,64}$")
FRONT = "+++"

_LIST_FIELDS = ("assumptions", "limits", "uses", "cites", "supersedes", "answers", "refutes", "tags")
_ID_LIST_FIELDS = ("uses", "cites", "supersedes", "answers", "refutes")
_ITEM_KEYS = {
    "id", "kind", "title", "author", "recorded_by", "created", "statement", "claim",
    "ref", "access", "lean", "python", "provenance", "origin", "imports", *_LIST_FIELDS,
}
IMPORT_KEYS = frozenset({"package", "item", "item_revision", "module", "theorems", "receipt", "fidelity_review"})
_FORBIDDEN_IMPORT_KEYS = frozenset({"url", "repo", "repository", "commit", "rev", "subDir", "subdir", "branch", "tag"})
_PACKAGE_RE = re.compile(r"^[A-Za-z0-9_-][A-Za-z0-9._-]*$")
_HEX_SHA_RE = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_REQUIRED_BY_KIND = {
    "question": ("statement",),
    "result": ("statement", "claim"),
    "conjecture": ("statement",),
    "source": ("ref", "access"),
    "intuition": ("statement",),
    "explanation": (),
}


class RecordError(ValueError):
    """An invalid record or input, with its location and recovery when available."""

    def __init__(self, message: str, *, has_recovery: bool = False):
        super().__init__(message)
        self.has_recovery = has_recovery


def canonical_json(data: Any) -> str:
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def pretty_json(data: Any) -> str:
    """The text of a record or export file: indented, keys sorted, a final newline."""
    return json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def toml_str(text: str) -> str:
    return json.dumps(text, ensure_ascii=False).replace("\x7f", "\\u007f")


def toml_list(values) -> str:
    return "[" + ", ".join(toml_str(v) for v in values) + "]"


def timestamp(raw: Any) -> float | None:
    """POSIX seconds of a recorded ISO 8601 time (without a zone, UTC), None when it is not one."""
    try:
        moment = datetime.fromisoformat(raw)
    except (TypeError, ValueError):
        return None
    return (moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)).timestamp()


def wall_seconds(receipt: dict[str, Any]) -> float | None:
    """How long a receipt's run took (`finished_at` - `started_at`); None when either time is not one or the run ends
    before it starts."""
    start, end = timestamp(receipt.get("started_at")), timestamp(receipt.get("finished_at"))
    return end - start if start is not None and end is not None and end >= start else None


def bad_statement(kind: str, text: str) -> bool:
    """Whether `text` is not a correspondence review's `statement`: one non-empty line of at most STATEMENT_CHARS."""
    return kind != "correspondence" or not text.strip() or len(text) > STATEMENT_CHARS or "\n" in text


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def git_blob_sha(data: bytes) -> str:
    """Same value as `git hash-object`: the item revision used in `id@rev` references."""
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


@dataclass(frozen=True)
class Item:
    id: str
    kind: str
    title: str
    author: str
    created: str
    body: str
    revision: str
    path: str
    statement: str | None = None
    claim: str | None = None
    ref: str | None = None
    access: str | None = None
    recorded_by: str | None = None
    assumptions: tuple[str, ...] = ()
    limits: tuple[str, ...] = ()
    uses: tuple[str, ...] = ()
    cites: tuple[str, ...] = ()
    supersedes: tuple[str, ...] = ()
    answers: tuple[str, ...] = ()
    refutes: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
    lean: dict[str, Any] = field(default_factory=dict)
    python: dict[str, Any] = field(default_factory=dict)
    provenance: dict[str, Any] = field(default_factory=dict)
    origin: dict[str, Any] = field(default_factory=dict)
    imports: tuple[dict[str, Any], ...] = ()


def split_front_matter(text: str, where: str) -> tuple[str, str]:
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != FRONT:
        raise RecordError(f"{where}: an item must start with a '+++' TOML front matter line")
    for end in range(1, len(lines)):
        if lines[end].strip() == FRONT:
            return "".join(lines[1:end]), "".join(lines[end + 1:])
    raise RecordError(f"{where}: front matter is not closed by a '+++' line")


def _str_field(meta: dict[str, Any], key: str, where: str) -> str | None:
    value = meta.get(key)
    if value is None:
        return None
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if not isinstance(value, str) or not value.strip():
        raise RecordError(f"{where}: '{key}' must be a non-empty string")
    return value


def _list_field(meta: dict[str, Any], key: str, where: str) -> tuple[str, ...]:
    value = meta.get(key, [])
    if not isinstance(value, list) or not all(isinstance(v, str) and v.strip() for v in value):
        raise RecordError(f"{where}: '{key}' must be a list of non-empty strings")
    return tuple(value)


def _line(value: Any) -> bool:
    """A non-blank, single-line, printable string: the card and the catalog show these words, and a newline or an
    escape sequence in them could pass for another line of the card."""
    return isinstance(value, str) and bool(value.strip()) and value.isprintable()


def _text_in(table: dict[str, Any], key: str, where: str, name: str) -> None:
    if key in table and not _line(table[key]):
        raise RecordError(f"{where}: {name}.{key} must be a non-empty single line of printable text")


def _provenance_table(meta: dict[str, Any], where: str) -> dict[str, Any]:
    """`[provenance]`: `kind` (PROVENANCE_KINDS) and `source`, required for the kinds in SOURCE_REQUIRED."""
    table = meta.get("provenance", {})
    if not isinstance(table, dict):
        raise RecordError(f"{where}: 'provenance' must be a table")
    unknown = sorted(set(table) - set(PROVENANCE_KEYS))
    if unknown:
        raise RecordError(f"{where}: unknown provenance field(s) {unknown}; valid fields: {sorted(PROVENANCE_KEYS)}")
    if "provenance" in meta and table.get("kind") not in PROVENANCE_KINDS:
        raise RecordError(f"{where}: provenance.kind must be one of {PROVENANCE_KINDS}")
    _text_in(table, "source", where, "provenance")
    if table and table["kind"] in SOURCE_REQUIRED and "source" not in table:
        raise RecordError(f"{where}: a {table['kind']} statement needs provenance.source (a URL, commit or path)")
    return dict(table)


def _origin_table(meta: dict[str, Any], where: str) -> dict[str, Any]:
    """`[origin]`: who made the item and from what. `agent` is required once the table exists; `tokens` is a count;
    `from` lists the items it came from (ids, checked like relations), `inspired_by` anything else (free text)."""
    table = meta.get("origin", {})
    if not isinstance(table, dict):
        raise RecordError(f"{where}: 'origin' must be a table")
    unknown = sorted(set(table) - set(ORIGIN_KEYS))
    if unknown:
        raise RecordError(f"{where}: unknown origin field(s) {unknown}; valid fields: {sorted(ORIGIN_KEYS)}")
    if "origin" in meta and "agent" not in table:
        raise RecordError(f"{where}: origin needs 'agent' ('human:<name>' or 'agent:<name>')")
    if "agent" in table and not (_line(table["agent"]) and AUTHOR_RE.match(table["agent"])):
        raise RecordError(f"{where}: origin.agent must look like 'human:<name>' or 'agent:<name>'")
    for key in ("model", "context"):
        _text_in(table, key, where, "origin")
    if "tokens" in table and (type(table["tokens"]) is not int or table["tokens"] < 0):
        raise RecordError(f"{where}: origin.tokens must be a non-negative integer")
    for key in ("from", "inspired_by"):
        if key in table and (not isinstance(table[key], list) or not all(_line(v) for v in table[key])):
            raise RecordError(f"{where}: origin.{key} must be a list of non-empty single lines of printable text")
    bad = [v for v in table.get("from", []) if not ID_RE.match(v.split("@", 1)[0])]
    if bad:
        raise RecordError(f"{where}: origin.from holds invalid ids {bad} (put other pointers in inspired_by)")
    return dict(table)


def _imports_table(meta: dict[str, Any], where: str) -> tuple[dict[str, Any], ...]:
    """Validate and normalize `[[imports]]` entries without adding editable repo/subdir/commit fields."""
    if "imports" not in meta:
        return ()
    from . import leanmod

    raw = meta["imports"]
    if not isinstance(raw, list):
        raise RecordError(f"{where}: 'imports' must be a list of tables ([[imports]])")
    seen: set[tuple[str, str, str]] = set()
    normalized: list[dict[str, Any]] = []
    for idx, entry in enumerate(raw):
        loc = f"{where}: imports[{idx}]"
        if not isinstance(entry, dict):
            raise RecordError(f"{loc} must be a table")
        forbidden = sorted(_FORBIDDEN_IMPORT_KEYS & set(entry))
        if forbidden:
            raise RecordError(
                f"{loc} cannot set {', '.join(forbidden)} (repository, subdirectory and commit come from the resolved lake-manifest.json entry)"
            )
        unknown = sorted(set(entry) - IMPORT_KEYS)
        if unknown:
            raise RecordError(f"{loc} has unknown field(s) {unknown}; valid fields: {sorted(IMPORT_KEYS)}")
        missing = sorted(IMPORT_KEYS - set(entry))
        if missing:
            raise RecordError(f"{loc} missing required field(s) {missing}")
        pkg = entry["package"]
        if not _line(pkg) or not _PACKAGE_RE.fullmatch(pkg):
            raise RecordError(f"{loc}.package must be a valid Lake package name")
        src_item = entry["item"]
        if not _line(src_item) or not ID_RE.match(src_item):
            raise RecordError(f"{loc}.item '{src_item}' must match {ID_RE.pattern}")
        item_rev = entry["item_revision"]
        if not isinstance(item_rev, str) or not _HEX_SHA_RE.fullmatch(item_rev):
            raise RecordError(f"{loc}.item_revision must be a full 40- or 64-hex Git blob sha")
        module = entry["module"]
        if not _line(module) or not leanmod.is_valid_module_name(module) or leanmod.is_reserved(module):
            raise RecordError(f"{loc}.module '{module}' must be a valid non-reserved Lean module name")
        theorems = entry["theorems"]
        if (not isinstance(theorems, list) or not theorems
                or not all(_line(t) and leanmod.is_valid_decl_name(t) for t in theorems)):
            raise RecordError(f"{loc}.theorems must be a non-empty list of valid Lean declaration names")
        if len(set(theorems)) != len(theorems):
            raise RecordError(f"{loc}.theorems must not contain duplicate declarations")
        receipt = entry["receipt"]
        if not isinstance(receipt, str) or not _SHA256_RE.fullmatch(receipt):
            raise RecordError(f"{loc}.receipt must be a full 64-hex receipt_id")
        fid_review = entry["fidelity_review"]
        if not isinstance(fid_review, str) or not _SHA256_RE.fullmatch(fid_review):
            raise RecordError(f"{loc}.fidelity_review must be a full 64-hex review_id")
        key = (pkg, src_item, module)
        if key in seen:
            raise RecordError(f"{loc} duplicates import ({pkg}, {src_item}, {module})")
        seen.add(key)
        normalized.append({
            "package": pkg,
            "item": src_item,
            "item_revision": item_rev,
            "module": module,
            "theorems": list(theorems),
            "receipt": receipt,
            "fidelity_review": fid_review,
        })
    return tuple(normalized)


def parse_item(data: bytes, path: str) -> Item:
    where = path
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise RecordError(f"{where}: not UTF-8") from exc
    front, body = split_front_matter(text, where)
    try:
        meta = tomllib.loads(front)
    except tomllib.TOMLDecodeError as exc:
        raise RecordError(f"{where}: front matter is not valid TOML: {exc}") from exc

    unknown = sorted(set(meta) - _ITEM_KEYS)
    if unknown:
        raise RecordError(f"{where}: unknown field(s) {unknown}; valid fields: {sorted(_ITEM_KEYS)}")
    for key in ("id", "kind", "title", "author", "created"):
        if key not in meta:
            raise RecordError(f"{where}: missing required field '{key}'")

    item_id = _str_field(meta, "id", where)
    if not ID_RE.match(item_id):
        raise RecordError(f"{where}: id '{item_id}' must match {ID_RE.pattern}")
    if Path(path).stem != item_id:
        raise RecordError(f"{where}: file name must be '{item_id}.md'")
    kind = _str_field(meta, "kind", where)
    if kind not in ITEM_KINDS:
        raise RecordError(f"{where}: kind '{kind}' not in {ITEM_KINDS}")
    for key in _REQUIRED_BY_KIND[kind]:
        if key not in meta:
            raise RecordError(f"{where}: a {kind} needs '{key}'")
    author = _str_field(meta, "author", where)
    if not AUTHOR_RE.match(author):
        raise RecordError(f"{where}: author must look like 'human:<name>' or 'agent:<name>'")
    recorded_by = _str_field(meta, "recorded_by", where)
    if recorded_by is not None and not AUTHOR_RE.match(recorded_by):
        raise RecordError(f"{where}: recorded_by must look like 'human:<name>' or 'agent:<name>'")
    claim = _str_field(meta, "claim", where)
    if claim is not None and claim not in CLAIM_TYPES:
        raise RecordError(f"{where}: claim '{claim}' not in {CLAIM_TYPES}")
    access = _str_field(meta, "access", where)
    if access is not None and access not in SOURCE_ACCESS:
        raise RecordError(f"{where}: access '{access}' not in {SOURCE_ACCESS}")
    if kind == "explanation" and not body.strip():
        raise RecordError(f"{where}: an explanation needs a body")
    for key in ("lean", "python"):
        if key in meta and not isinstance(meta[key], dict):
            raise RecordError(f"{where}: '{key}' must be a table")

    lists = {key: _list_field(meta, key, where) for key in _LIST_FIELDS}
    for key in _ID_LIST_FIELDS:
        bad = [v for v in lists[key] if not ID_RE.match(v.split("@", 1)[0])]
        if bad:
            raise RecordError(f"{where}: '{key}' holds invalid ids {bad}")

    return Item(
        id=item_id,
        kind=kind,
        title=_str_field(meta, "title", where),
        author=author,
        created=_str_field(meta, "created", where),
        body=body,
        revision=git_blob_sha(data),
        path=path,
        statement=_str_field(meta, "statement", where),
        claim=claim,
        ref=_str_field(meta, "ref", where),
        access=access,
        recorded_by=recorded_by,
        lean=dict(meta.get("lean", {})),
        python=dict(meta.get("python", {})),
        provenance=_provenance_table(meta, where),
        origin=_origin_table(meta, where),
        imports=_imports_table(meta, where),
        **lists,
    )


def question_fields(item: "Item") -> dict[str, Any]:
    fields: dict[str, Any] = {
        table: {key: getattr(item, table)[key] for key in keys if key in getattr(item, table)}
        for table, keys in QUESTION_FIELDS.items()
    }
    if item.imports:
        fields["imports"] = [dict(entry) for entry in item.imports]
    return fields


def question_digest(item: "Item") -> str:
    """`sha256:` of the canonical JSON of the item's question fields (QUESTION_FIELDS)."""
    basis = json.dumps(question_fields(item), sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
    return "sha256:" + sha256_hex(basis.encode("utf-8"))


def receipt_id(receipt: dict[str, Any]) -> str:
    body = {k: v for k, v in receipt.items() if k != "receipt_id"}
    return sha256_hex(canonical_json(body).encode("utf-8"))


def review_id(review: dict[str, Any]) -> str:
    body = {k: v for k, v in review.items() if k != "review_id"}
    return sha256_hex(canonical_json(body).encode("utf-8"))


class _ListOf:
    def __init__(self, kind):
        self.kind = kind


class _MapOf:
    def __init__(self, kind):
        self.kind = kind


_NUMBER = (int, float)
# The nested parts of a receipt that status, show and validate read, with the type each must have when present.
# A sealed receipt that differs is rejected as malformed, never trusted to the code that reads it.
_RECEIPT_SHAPE = {
    "reasons": _ListOf(str),
    "command": _ListOf(str),
    "question_digest": str,
    "target": dict,
    "inputs": {"files": _MapOf(str), "trusted_files": _MapOf(str), "trusted_commit": str, "trusted_ref": str,
               "imports": _ListOf(dict)},
    "checked": {"kernels": _ListOf(str), "permitted_axioms": _ListOf(str), "probes": _MapOf(dict),
                "probe_run": {"battery": _ListOf(str), "problems": _ListOf(str), "skipped": str}, "lints": list},
    "environment": {"host": {"sandbox_class": str, "kernel": str, "arch": str, "cpu_model": str, "cores": int,
                             "ram_bytes": int},
                    "environment_class": {"digest": str, "adapter": str, "assurance": str, "sandbox_class": str,
                                          "arch": str, "kernel": str, "memory_cap": str, "toolchain": str,
                                          "lean_version": str, "tools": _MapOf(str), "packages_identity": str,
                                          "python_version": str, "bwrap": str},
                    "build_cache": {"restored": _MapOf(str), "stored_keys": _MapOf(str)},
                    "solution_build": {"key": str},
                    "third_party_packages": _ListOf(str)},
    "extra": {"phases": _MapOf(_NUMBER), "seconds": _NUMBER, "memory_peak_bytes": _NUMBER, "cpu_seconds": _NUMBER,
              "queue_wait_seconds": _NUMBER, "modules": _ListOf({"module": str, "ms": _NUMBER})},
}
_REVIEW_SHAPE = {"verdict": str, "statement": str, "target_sha256": str, "target_path": str, "compare_with": str, "meaning_digest": str,
                 "trusted_ref": str, "trusted_commit": str,
                 "meaning": {"semantic_environment": _MapOf(str), "semantic_environment_error": str,
                             "files": _MapOf(str), "theorems": _ListOf(str), "witnesses": _ListOf(str),
                             "statement_sha256": str, "limits_sha256": str, "assumptions_sha256": str,
                             "closure_error": str}}


def _kind_name(kind) -> str:
    if isinstance(kind, dict):
        return "an object"
    if isinstance(kind, (_ListOf, _MapOf)):
        return f"{'a list' if isinstance(kind, _ListOf) else 'a map'} of {_kind_name(kind.kind)}"
    names = [k.__name__ for k in (kind if isinstance(kind, tuple) else (kind,))]
    return "number" if names == ["int", "float"] else " or ".join(names)


def _matches(value, kind) -> bool:
    if isinstance(kind, dict):      # an element of a list or map: every key the readers use must be there
        return isinstance(value, dict) and all(k in value for k in kind) and not _shape_problems(value, kind, "")
    if isinstance(kind, _ListOf):
        return isinstance(value, list) and all(_matches(v, kind.kind) for v in value)
    if isinstance(kind, _MapOf):
        return isinstance(value, dict) and all(_matches(v, kind.kind) for v in value.values())
    return isinstance(value, kind) and not isinstance(value, bool)


def _shape_problems(record: dict, shape: dict, prefix: str) -> list[str]:
    problems = []
    for key, kind in shape.items():
        if key not in record:
            continue
        value = record[key]
        if isinstance(kind, dict) and isinstance(value, dict):
            problems += _shape_problems(value, kind, f"{prefix}{key}.")
        elif not _matches(value, kind):
            problems.append(f"'{prefix}{key}' must be {_kind_name(kind)}")
    return problems


_RECEIPT_REQUIRED = {
    "schema": str, "receipt_id": str, "item": str, "item_revision": str, "adapter": str,
    "assurance": str, "verdict": str, "reasons": list, "inputs": dict, "environment": dict,
    "checked": dict, "command": list, "started_at": str, "finished_at": str, "tool_version": str,
}


def historical_kernel_profile(receipt: dict[str, Any]) -> bool:
    """Whether a protected Lean receipt has a pre-minimum historical profile, identified strictly by its recorded
    structure and config basis (never by a timestamp): lacks `#verdict-rules`, `environment.packages.identity`,
    `checked.definitions_mode`, and `checked.probe_run`."""
    inputs = receipt.get("inputs") if isinstance(receipt.get("inputs"), dict) else {}
    trusted = inputs.get("trusted_files") if isinstance(inputs.get("trusted_files"), dict) else {}
    env = receipt.get("environment") if isinstance(receipt.get("environment"), dict) else {}
    packages = env.get("packages") if isinstance(env.get("packages"), dict) else {}
    checked = receipt.get("checked") if isinstance(receipt.get("checked"), dict) else {}
    return (RULES_INPUT not in trusted and "identity" not in packages
            and "definitions_mode" not in checked and "probe_run" not in checked)


def _protected_pass_problems(receipt: dict) -> list[str]:
    """A protected pass must record the provenance and coverage its adapter actually produces."""
    adapter = receipt["adapter"]
    if adapter not in ("lean-comparator", "python-eval"):
        return [f"protected pass has unknown adapter '{adapter}'"]
    target = receipt.get("target")
    if not isinstance(target, dict) or not target:
        return ["protected pass needs a non-empty target"]
    inputs, checked, environment = receipt["inputs"], receipt["checked"], receipt["environment"]
    problems = []
    trusted = inputs.get("trusted_files")
    if not isinstance(trusted, dict) or not trusted:
        return ["protected pass needs non-empty inputs.trusted_files"]
    for name, files in (("files", inputs["files"]), ("trusted_files", trusted)):
        for path, digest in files.items():
            if (path in ("", ".") or path != str(PurePosixPath(path)) or PurePosixPath(path).is_absolute()
                    or ".." in PurePosixPath(path).parts or "\\" in path or "\0" in path):
                problems.append(f"inputs.{name} path '{path}' must be normalized and repository-relative")
            if not re.fullmatch(r"[0-9a-f]{64}", digest):
                problems.append(f"inputs.{name} digest for '{path}' must be a hex sha256")
    commit = inputs.get("trusted_commit")
    if not isinstance(commit, str) or not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", commit):
        problems.append("protected pass needs inputs.trusted_commit as a full Git commit id")
    if target.get("source") != "trusted-commit" or target.get("commit") != commit:
        problems.append("protected pass target must come from inputs.trusted_commit")
    path = target.get("path" if adapter == "lean-comparator" else "evaluator")
    if not isinstance(path, str) or path not in trusted or target.get("sha256") != trusted.get(path):
        problems.append("target identity and sha256 must match its inputs.trusted_files entry")
    basis: dict[str, Any] = {"files": inputs["files"], "trusted_files": trusted, "target": target}
    if "imports" in inputs:
        imp_list = inputs["imports"]
        if not isinstance(imp_list, list) or not imp_list:
            problems.append("inputs.imports must be a non-empty list when present")
        else:
            for idx, imp in enumerate(imp_list):
                manifest = imp.get("manifest") if isinstance(imp, dict) else None
                if (not isinstance(imp, dict)
                        or not isinstance(imp.get("package"), str) or not _PACKAGE_RE.fullmatch(imp["package"])
                        or not isinstance(imp.get("item"), str) or not ID_RE.match(imp["item"])
                        or not isinstance(imp.get("item_revision"), str) or not _HEX_SHA_RE.fullmatch(imp["item_revision"])
                        or not isinstance(imp.get("module"), str) or not imp["module"]
                        or not isinstance(imp.get("theorems"), list) or not imp["theorems"]
                        or not all(isinstance(t, str) and t.strip() for t in imp["theorems"])
                        or not isinstance(imp.get("receipt"), str) or not _SHA256_RE.fullmatch(imp["receipt"])
                        or not isinstance(imp.get("fidelity_review"), str) or not _SHA256_RE.fullmatch(imp["fidelity_review"])
                        or not isinstance(manifest, dict)
                        or not isinstance(manifest.get("url"), str) or not manifest["url"].strip()
                        or not isinstance(manifest.get("rev"), str) or not _HEX_SHA_RE.fullmatch(manifest["rev"])
                        or (manifest.get("subDir") is not None and not isinstance(manifest.get("subDir"), str))
                        or not isinstance(imp.get("source_meaning_digest"), str)
                        or not imp["source_meaning_digest"].startswith("sha256:")):
                    problems.append(f"inputs.imports[{idx}] is malformed")
            basis["imports"] = imp_list
    if inputs["digest"] != "sha256:" + sha256_hex(canonical_json(basis).encode("utf-8")):
        problems.append("inputs.digest does not match files, trusted_files and target")
    if adapter == "lean-comparator":
        theorems = target.get("theorems")
        if (not isinstance(theorems, list) or not theorems
                or not all(isinstance(t, str) and t.strip() for t in theorems)
                or len(set(theorems)) != len(theorems) or checked.get("theorems") != theorems):
            problems.append("protected Lean pass needs checked.theorems matching non-empty target.theorems")
        kernels = checked.get("kernels")
        if not isinstance(kernels, list) or "lean" not in kernels:
            problems.append("protected Lean pass needs checked.kernels including lean")
        elif (PROTECTED_EXTERNAL_KERNELS and not historical_kernel_profile(receipt)
              and not any(k in kernels for k in PROTECTED_EXTERNAL_KERNELS)):
            problems.append("protected Lean pass requires an external kernel (nanoda)")
        if not isinstance(checked.get("permitted_axioms"), list):
            problems.append("protected Lean pass needs checked.permitted_axioms")
        for key in ("toolchain", "lean_version"):
            if not isinstance(environment.get(key), str) or not environment[key].strip():
                problems.append(f"protected Lean pass needs environment.{key}")
        tools = environment.get("tools")
        for tool in ("comparator", "lean4export", "landrun", *(["nanoda"] if isinstance(kernels, list)
                                                               and "nanoda" in kernels else [])):
            identity = tools.get(tool) if isinstance(tools, dict) else None
            if (not isinstance(identity, dict) or not isinstance(identity.get("path"), str)
                    or not identity["path"] or not isinstance(identity.get("sha256"), str)
                    or not re.fullmatch(r"[0-9a-f]{64}", identity["sha256"])):
                problems.append(f"protected Lean pass needs environment.tools.{tool} path and sha256")
    else:
        cases = checked.get("cases")
        if (type(cases) is not int or cases <= 0 or type(checked.get("passed")) is not int
                or checked["passed"] != cases or type(checked.get("failed")) is not int or checked["failed"] != 0
                or type(checked.get("judge_errors")) is not int or checked["judge_errors"] != 0):
            problems.append("protected Python pass needs positive checked.cases, all passed, no failures or judge errors")
        digest = target.get("cases_sha256")
        if (not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest)
                or checked.get("cases_sha256") != digest):
            problems.append("checked.cases_sha256 must match target.cases_sha256")
        candidate, entry = target.get("candidate"), target.get("entry")
        if not isinstance(candidate, str) or candidate not in inputs["files"]:
            problems.append("target.candidate must name an inputs.files entry")
        if not isinstance(entry, str) or not entry.isidentifier():
            problems.append("target.entry must be a Python identifier")
        for key in ("interpreter", "python_version"):
            if not isinstance(environment.get(key), str) or not environment[key].strip():
                problems.append(f"protected Python pass needs environment.{key}")
    if not isinstance(environment.get("isolation"), dict) or not environment["isolation"]:
        problems.append("protected pass needs environment.isolation")
    return problems


def receipt_problems(receipt: Any) -> list[str]:
    """Structural problems of a receipt; an empty list means well-formed and self-consistent."""
    if not isinstance(receipt, dict):
        return ["receipt is not a JSON object"]
    problems = []
    for key, kind in _RECEIPT_REQUIRED.items():
        if key not in receipt:
            problems.append(f"missing '{key}'")
        elif not isinstance(receipt[key], kind):
            problems.append(f"'{key}' must be {kind.__name__}")
    if problems:
        return problems
    if receipt["schema"] != RECEIPT_SCHEMA:
        problems.append(f"schema '{receipt['schema']}' is not {RECEIPT_SCHEMA}")
    if receipt["verdict"] not in VERDICTS:
        problems.append(f"verdict '{receipt['verdict']}' not in {VERDICTS}")
    if receipt["assurance"] not in ASSURANCE:
        problems.append(f"assurance '{receipt['assurance']}' not in {ASSURANCE}")
    if not receipt["inputs"].get("files"):
        problems.append("inputs.files must be a non-empty map of path to sha256")
    if not isinstance(receipt["inputs"].get("digest"), str):
        problems.append("inputs.digest must be a string")
    problems += _shape_problems(receipt, _RECEIPT_SHAPE, "")
    try:
        if not problems and receipt["assurance"] == "protected" and receipt["verdict"] == "pass":
            problems += _protected_pass_problems(receipt)
        if receipt["receipt_id"] != receipt_id(receipt):
            problems.append("receipt_id does not match its content (edited or forged)")
    except UnicodeEncodeError:
        problems.append(_NOT_UNICODE)
    return problems


_NOT_UNICODE = "holds text that is not valid Unicode (an escaped lone surrogate), so it cannot be hashed"

_REVIEW_REQUIRED = {
    "schema": str, "review_id": str, "item": str, "item_revision": str, "kind": str,
    "author": str, "text": str, "created": str,
}


def review_problems(review: Any) -> list[str]:
    if not isinstance(review, dict):
        return ["review is not a JSON object"]
    problems = []
    for key, kind in _REVIEW_REQUIRED.items():
        if key not in review:
            problems.append(f"missing '{key}'")
        elif not isinstance(review[key], kind):
            problems.append(f"'{key}' must be {kind.__name__}")
    if problems:
        return problems
    if review["schema"] != REVIEW_SCHEMA:
        problems.append(f"schema '{review['schema']}' is not {REVIEW_SCHEMA}")
    if review["kind"] not in REVIEW_KINDS:
        problems.append(f"kind '{review['kind']}' not in {REVIEW_KINDS}")
    if not AUTHOR_RE.match(review["author"]):
        problems.append("author must look like 'human:<name>' or 'agent:<name>'")
    if not review["text"].strip() and review["kind"] != "appraisal":
        problems.append("a review needs a reason in 'text'")
    if review["kind"] == "appraisal":
        from .appraisals import assessment_problems
        problems += assessment_problems(review.get("assessment"), review["item"], review["item_revision"])
        if set(review) - (set(_REVIEW_REQUIRED) | {"assessment"}):
            problems.append("an appraisal review has unsupported fields")
    elif "assessment" in review:
        problems.append("assessment only applies to an appraisal review")
    if "score" in review:
        problems.append("score is supported only inside appraisal assessment.ratings")
    problems += _shape_problems(review, _REVIEW_SHAPE, "")
    if "acknowledges" in review:
        acknowledges = review["acknowledges"]
        if review["kind"] != "fidelity":
            problems.append("only a fidelity review can acknowledge probe findings")
        elif (not isinstance(acknowledges, list) or not acknowledges
              or not all(isinstance(a, str) and a in ACKNOWLEDGEABLE for a in acknowledges)):
            problems.append(f"'acknowledges' must be a non-empty list of {ACKNOWLEDGEABLE}")
    if isinstance(review.get("statement"), str) and bad_statement(review["kind"], review["statement"]):
        problems.append(f"'statement' is for a correspondence review: one non-empty line of at most "
                        f"{STATEMENT_CHARS} characters")
    if review["kind"] == "correspondence":
        if review.get("verdict") not in CORRESPONDENCE_OUTCOMES:
            problems.append(f"a correspondence review needs a 'verdict' in {CORRESPONDENCE_OUTCOMES}")
        if "meaning" not in review:
            problems.append("a correspondence review binds the meaning it read: 'meaning' is missing")
    try:
        if ("meaning" in review) != ("meaning_digest" in review) or ("meaning" in review and isinstance(
                review["meaning"], dict) and review.get("meaning_digest") != "sha256:" + sha256_hex(
                canonical_json(review["meaning"]).encode("utf-8"))):
            problems.append("'meaning_digest' must be the digest of 'meaning'")
        if review["review_id"] != review_id(review):
            problems.append("review_id does not match its content (edited or forged)")
    except UnicodeEncodeError:
        problems.append(_NOT_UNICODE)
    return problems


def write_new_bytes(path: Path, data: bytes) -> None:
    """Publish complete bytes at a new immutable destination on the same filesystem."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".vl-record-", dir=path.parent) as directory:
        staged = Path(directory) / "record"
        staged.write_bytes(data)
        os.link(staged, path)


def write_new_json(path: Path, data: dict[str, Any]) -> None:
    write_new_bytes(path, pretty_json(data).encode("utf-8"))


def seal_receipt(fields: dict[str, Any]) -> dict[str, Any]:
    receipt = {"schema": RECEIPT_SCHEMA, **fields}
    receipt.pop("receipt_id", None)
    receipt["receipt_id"] = receipt_id(receipt)
    return receipt


def seal_review(fields: dict[str, Any]) -> dict[str, Any]:
    review = {"schema": REVIEW_SCHEMA, **fields}
    review.pop("review_id", None)
    review["review_id"] = review_id(review)
    return review
