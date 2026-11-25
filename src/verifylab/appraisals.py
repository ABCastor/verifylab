"""Optional attributed appraisals. Their basis never participates in proof freshness."""
from __future__ import annotations

import json
import math
import re
from pathlib import Path, PurePosixPath
from typing import Any

from .records import RecordError, canonical_json, sha256_hex

SCHEMA = "vl.appraisal.v1"
BASIS_SCHEMA = "vl.appraisal-basis.v1"
SUBJECTS = ("statement", "proof", "connection", "assumptions", "application", "result")
_DEFAULTS = {
    "novelty": {"question": "How novel is this subject relative to the supplied sources?",
                "labels": ["low", "medium", "high"], "ordered": True},
    "usefulness": {"question": "How useful is this subject for the named goal?",
                   "labels": ["low", "medium", "high"], "ordered": True},
}
_INPUT_KEYS = {"schema", "subject", "ratings", "input_context", "goal", "context_files", "provenance", "sources",
               "review_references", "raw_output"}
_STORED_KEYS = (_INPUT_KEYS - {"input_context", "goal", "context_files"}) | {"basis", "basis_digest"}


def _text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _finite(value: Any) -> bool:
    pending = [value]
    while pending:
        current = pending.pop()
        if isinstance(current, float):
            if not math.isfinite(current):
                return False
        elif isinstance(current, dict):
            if not all(isinstance(key, str) for key in current):
                return False
            pending.extend(current)
            pending.extend(current.values())
        elif isinstance(current, list):
            pending.extend(current)
        elif isinstance(current, str):
            try:
                current.encode("utf-8")
            except UnicodeEncodeError:
                return False
        elif current is not None and not isinstance(current, (int, bool)):
            return False
    return True


def _path(value: Any) -> bool:
    return (_text(value) and value == str(PurePosixPath(value)) and not PurePosixPath(value).is_absolute()
            and ".." not in PurePosixPath(value).parts and "\\" not in value and "\0" not in value and value != ".")


def _criteria(raw: Any) -> dict:
    if not isinstance(raw, dict) or not raw:
        raise RecordError("appraisal.criteria must be a non-empty table")
    criteria = {}
    for key, criterion in raw.items():
        if not isinstance(key, str) or not re.fullmatch(r"[a-z][a-z0-9_-]*", key):
            raise RecordError("appraisal criterion IDs must be lowercase identifiers")
        if not isinstance(criterion, dict) or set(criterion) - {"question", "labels", "ordered", "description", "version", "evidence"}:
            raise RecordError(f"appraisal criterion {key}: unsupported fields")
        labels = criterion.get("labels")
        if (not _text(criterion.get("question")) or not isinstance(labels, list) or not labels
                or not all(_text(label) for label in labels) or len(set(labels)) != len(labels)
                or any(label in ("unknown", "abstain") for label in labels)):
            raise RecordError(f"appraisal criterion {key}: needs a question and distinct labels (unknown/abstain are reserved)")
        if "ordered" in criterion and type(criterion["ordered"]) is not bool:
            raise RecordError(f"appraisal criterion {key}: ordered must be boolean")
        if any(not _text(criterion[field]) for field in ("description", "version", "evidence") if field in criterion):
            raise RecordError(f"appraisal criterion {key}: description, version and evidence must be non-empty text")
        criteria[key] = {**criterion, "ordered": criterion.get("ordered", True)}
    return criteria


def manifest(repo) -> dict:
    """The exact effective rubric, including label ordering and descriptions, read from trusted config."""
    raw = repo.config.raw.get("appraisal", {})
    if not isinstance(raw, dict) or set(raw) - {"version", "criteria"}:
        raise RecordError("[appraisal] supports only version and criteria")
    version = raw.get("version", 1)
    if type(version) is not int or version < 1:
        raise RecordError("appraisal.version must be a positive integer")
    return {"version": version, "criteria": _criteria(raw.get("criteria", _DEFAULTS))}


def _manifest_problems(value: Any) -> list[str]:
    try:
        if not isinstance(value, dict) or set(value) != {"version", "criteria"}:
            raise RecordError("manifest needs version and criteria")
        if type(value["version"]) is not int or value["version"] < 1:
            raise RecordError("manifest version must be a positive integer")
        _criteria(value["criteria"])
    except RecordError as exc:
        return [str(exc)]
    return []


def _payload_problems(value: dict, rubric: dict) -> list[str]:
    problems = []
    if value.get("schema") != SCHEMA:
        problems.append(f"assessment.schema must be {SCHEMA}")
    if value.get("subject") not in SUBJECTS:
        problems.append(f"assessment.subject must be one of {SUBJECTS}")
    ratings = value.get("ratings")
    if not isinstance(ratings, dict) or not ratings:
        return problems + ["assessment.ratings must be a non-empty object"]
    for key, rating in ratings.items():
        criterion = rubric.get("criteria", {}).get(key)
        if criterion is None:
            problems.append(f"unknown appraisal criterion {key}")
            continue
        if not isinstance(rating, dict) or set(rating) - {"value", "score", "abstention", "distribution", "confidence"}:
            problems.append(f"rating {key}: unsupported shape or fields")
            continue
        if sum(field in rating for field in ("value", "score", "abstention")) != 1:
            problems.append(f"rating {key}: exactly one value, score or abstention is required")
        valid_score = ("score" in rating and type(rating["score"]) in (int, float)
                       and (not isinstance(rating["score"], float) or math.isfinite(rating["score"]))
                       and 0 <= rating["score"] <= len(criterion["labels"]) - 1)
        if "score" in rating:
            if not criterion.get("ordered", True):
                problems.append(f"rating {key}: score requires an ordered criterion")
            if not valid_score:
                problems.append(f"rating {key}: score must be a finite ordinal position from 0 to {len(criterion['labels']) - 1}")
        if "value" in rating and rating["value"] not in criterion["labels"]:
            problems.append(f"rating {key}: value must be a configured label")
        if "abstention" in rating and rating["abstention"] not in ("unknown", "abstain"):
            problems.append(f"rating {key}: abstention must be unknown or abstain")
        if "confidence" in rating and (type(rating["confidence"]) not in (int, float)
                                       or (isinstance(rating["confidence"], float) and not math.isfinite(rating["confidence"]))):
            problems.append(f"rating {key}: confidence must be a finite native scalar")
        if "distribution" in rating:
            dist = rating["distribution"]
            if (not isinstance(dist, dict) or set(dist) != set(criterion["labels"])
                    or any(type(p) not in (int, float) or (isinstance(p, float) and not math.isfinite(p)) or not 0 <= p <= 1 for p in dist.values())
                    or not math.isclose(sum(dist.values()), 1, abs_tol=1e-6)):
                problems.append(f"rating {key}: distribution must give every configured label a finite probability summing to 1")
            elif valid_score:
                expected = sum(index * dist[label] for index, label in enumerate(criterion["labels"]))
                if not math.isclose(rating["score"], expected, rel_tol=1e-6, abs_tol=1e-6):
                    problems.append(f"rating {key}: score must match the distribution's expected ordinal position")
    provenance = value.get("provenance", {})
    if (not isinstance(provenance, dict) or set(provenance) - {"model", "provider", "run", "role", "context_summary"}
            or not all(_text(v) for v in provenance.values())):
        problems.append("assessment.provenance supports model/provider/run/role/context_summary as non-empty text")
    sources = value.get("sources", [])
    if (not isinstance(sources, list) or any(not isinstance(s, dict) or "locator" not in s
            or set(s) - {"locator", "access", "note"} or not all(_text(v) for v in s.values()) for s in sources)):
        problems.append("assessment.sources must hold locator and optional access/note text")
    refs = value.get("review_references", [])
    if not isinstance(refs, list) or any(not isinstance(r, str) or not re.fullmatch(r"[0-9a-f]{64}", r) for r in refs):
        problems.append("assessment.review_references must be full review IDs")
    if not _finite(value):
        problems.append("assessment holds nonfinite or unsupported JSON values")
    return problems


def assessment_problems(value: Any, item: str | None = None, revision: str | None = None) -> list[str]:
    """Validate persisted data independently of today's manifest, so historical rubrics remain readable."""
    if not isinstance(value, dict):
        return ["assessment must be an object"]
    problems = ["assessment has unsupported fields"] if set(value) - _STORED_KEYS else []
    basis = value.get("basis")
    if not isinstance(basis, dict) or set(basis) != {"schema", "item", "item_revision", "manifest", "goal", "input_context", "files"}:
        return problems + ["assessment.basis has missing or unsupported fields"]
    if basis["schema"] != BASIS_SCHEMA:
        problems.append(f"assessment.basis.schema must be {BASIS_SCHEMA}")
    if not _text(basis["item"]) or not isinstance(basis["item_revision"], str) or not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", basis["item_revision"]):
        problems.append("assessment basis needs item and full item revision")
    if item is not None and basis["item"] != item or revision is not None and basis["item_revision"] != revision:
        problems.append("assessment basis disagrees with reviewed item/revision")
    if not _text(basis["input_context"]) or not isinstance(basis["goal"], str):
        problems.append("assessment basis needs actual input_context text and a goal string")
    if isinstance(value.get("ratings"), dict) and "usefulness" in value["ratings"] and not _text(basis["goal"]):
        problems.append("a usefulness rating needs a named goal")
    files = basis["files"]
    if (not isinstance(files, dict) or any(not _path(p) or not isinstance(d, str) or not re.fullmatch(r"[0-9a-f]{64}", d)
                                           for p, d in files.items())):
        problems.append("assessment basis files must map normalized repository paths to sha256")
    manifest_errors = _manifest_problems(basis["manifest"])
    problems += manifest_errors
    if not manifest_errors:
        problems += _payload_problems(value, basis["manifest"])
    try:
        if value.get("basis_digest") != "sha256:" + sha256_hex(canonical_json(basis).encode()):
            problems.append("assessment.basis_digest does not match basis")
    except (UnicodeEncodeError, ValueError):
        problems.append("assessment basis cannot be hashed")
    return problems


def load_assessment(path: str, repo, item) -> dict:
    """Read only the caller's supplied payload; do not generate context or explanations."""
    def pairs(values):
        result = {}
        for key, value in values:
            if key in result:
                raise ValueError(f"duplicate JSON key {key}")
            result[key] = value
        return result
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"), object_pairs_hook=pairs,
                             parse_constant=lambda s: (_ for _ in ()).throw(ValueError(f"nonfinite {s}")))
    except (OSError, ValueError, RecursionError) as exc:
        raise RecordError(f"cannot read assessment: {exc}") from exc
    if not isinstance(payload, dict) or set(payload) - _INPUT_KEYS:
        raise RecordError("assessment JSON must be an object with supported input fields")
    rubric = manifest(repo)
    problems = _payload_problems(payload, rubric)
    if not _text(payload.get("input_context")):
        problems.append("assessment needs input_context describing or locating the actual supplied context")
    if "goal" in payload and not _text(payload["goal"]):
        problems.append("assessment goal must be non-empty text when supplied")
    paths = payload.get("context_files", [])
    if not isinstance(paths, list) or not all(_path(p) for p in paths) or len(set(paths)) != len(paths):
        problems.append("context_files must be distinct normalized repository-relative paths")
    if problems:
        raise RecordError("; ".join(problems))
    files = {}
    for rel in paths:
        file = repo.root / rel
        if not file.resolve().is_relative_to(repo.root.resolve()) or not file.is_file():
            raise RecordError(f"context file is unavailable or outside repository: {rel}")
        files[rel] = sha256_hex(file.read_bytes())
    basis = {"schema": BASIS_SCHEMA, "item": item.id, "item_revision": item.revision, "manifest": rubric,
             "goal": payload.get("goal", ""), "input_context": payload["input_context"], "files": files}
    assessment = {k: v for k, v in payload.items() if k in _STORED_KEYS}
    assessment.update(basis=basis, basis_digest="sha256:" + sha256_hex(canonical_json(basis).encode()))
    problems = assessment_problems(assessment, item.id, item.revision)
    if problems:
        raise RecordError("; ".join(problems))
    return assessment


def appraisal_view(repo, item, record) -> dict:
    """A stored contribution plus applicability to this view; no aggregation or world-knowledge claim."""
    data = record.data
    assessment = data.get("assessment", {})
    reasons = list(record.problems)
    state = "unknown" if reasons else "current"
    if not isinstance(assessment, dict) or "basis" not in assessment:
        return {"assessment": assessment, "applicability": {"state": "unknown", "reasons": reasons + ["missing basis"]}}
    if reasons:
        return {"assessment": assessment, "applicability": {"state": "unknown", "reasons": reasons}}
    basis = assessment["basis"]
    if data.get("item_revision") != item.revision:
        reasons.append("subject revision changed")
    try:
        if basis["manifest"] != manifest(repo):
            reasons.append("appraisal manifest changed")
    except RecordError as exc:
        state = "unknown"
        reasons.append(str(exc))
    for path, digest in basis["files"].items():
        # TrustedRepo exports must never inspect local filesystem state.
        if record.admitted:
            current = repo.read_trusted(path)
        else:
            file = repo.root / path
            try:
                if not file.resolve().is_relative_to(repo.root.resolve()):
                    raise OSError("context path escapes the repository")
                current = file.read_bytes()
            except OSError:
                state = "unknown"
                current = None
        if current is None or sha256_hex(current) != digest:
            reasons.append(f"context file changed or unavailable: {path}")
    if reasons and state != "unknown":
        state = "stale"
    return {"assessment": assessment, "applicability": {"state": state, "reasons": reasons,
            "limit": "Only recorded local basis is compared; later literature and external context are not detected."}}
