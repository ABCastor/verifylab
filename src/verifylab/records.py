"""Record formats: research items (Markdown + TOML front matter), receipts and reviews (JSON).

Status is never stored in a record. It is derived from receipts and reviews (see status.py).
"""

from __future__ import annotations

import hashlib
import json
import re
import tomllib
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path, PurePosixPath
from typing import Any

ITEM_KINDS = ("question", "result", "conjecture", "source", "intuition", "explanation")
CLAIM_TYPES = ("formal", "computation", "numeric", "empirical", "literature", "prose")
SOURCE_ACCESS = ("full-text-read", "abstract-only", "citation-only", "secondary")
REVIEW_KINDS = ("fidelity", "compare", "correction", "retraction", "understanding")
# Statement-probe findings a fidelity review can accept as intended (the target is closed by automation alone).
ACKNOWLEDGEABLE = ("trivial",)
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
    "ref", "access", "lean", "python", *_LIST_FIELDS,
}
_REQUIRED_BY_KIND = {
    "question": ("statement",),
    "result": ("statement", "claim"),
    "conjecture": ("statement",),
    "source": ("ref", "access"),
    "intuition": ("statement",),
    "explanation": (),
}


class RecordError(ValueError):
    """A record is malformed. The message names the file and the field."""


def canonical_json(data: Any) -> str:
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


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
        **lists,
    )


def question_fields(item: "Item") -> dict[str, dict[str, Any]]:
    return {table: {key: getattr(item, table)[key] for key in keys if key in getattr(item, table)}
            for table, keys in QUESTION_FIELDS.items()}


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
    "inputs": {"files": _MapOf(str), "trusted_files": _MapOf(str), "trusted_commit": str, "trusted_ref": str},
    "checked": {"kernels": _ListOf(str), "permitted_axioms": _ListOf(str), "probes": _MapOf(dict),
                "probe_run": {"battery": _ListOf(str), "problems": _ListOf(str), "skipped": str}, "lints": list},
    "extra": {"phases": _MapOf(_NUMBER), "seconds": _NUMBER, "memory_peak_bytes": _NUMBER,
              "modules": _ListOf({"module": str, "ms": _NUMBER})},
}
_REVIEW_SHAPE = {"verdict": str, "target_sha256": str, "target_path": str, "compare_with": str, "meaning_digest": str,
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
    basis = {"files": inputs["files"], "trusted_files": trusted, "target": target}
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
    if not review["text"].strip():
        problems.append("a review needs a reason in 'text'")
    problems += _shape_problems(review, _REVIEW_SHAPE, "")
    if "acknowledges" in review:
        acknowledges = review["acknowledges"]
        if review["kind"] != "fidelity":
            problems.append("only a fidelity review can acknowledge probe findings")
        elif (not isinstance(acknowledges, list) or not acknowledges
              or not all(isinstance(a, str) and a in ACKNOWLEDGEABLE for a in acknowledges)):
            problems.append(f"'acknowledges' must be a non-empty list of {ACKNOWLEDGEABLE}")
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


def write_new_json(path: Path, data: dict[str, Any]) -> None:
    """Records are immutable: refuse to overwrite an existing file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as handle:
        handle.write(json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n")


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
