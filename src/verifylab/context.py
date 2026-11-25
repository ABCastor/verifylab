"""Optional project context and live note/review retrieval. These views decide no proof status."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path, PurePosixPath
from typing import Any

from .config import Config, ConfigError
from .records import RecordError
from .repo import ITEMS, REVIEWS, Repo

ROLES = ("problem", "map", "ideas")
SKIP_DIRS = {".git", ".lake", "node_modules", "__pycache__", "build", "evidence", "reviews", "targets", "items"}
MAX_NOTE_BYTES = 2 * 1024 * 1024


def _relative(value: Any, key: str) -> str:
    if (not isinstance(value, str) or not value.strip() or "\\" in value or "\0" in value
            or PurePosixPath(value).is_absolute() or ".." in PurePosixPath(value).parts
            or any(part.startswith(".") for part in PurePosixPath(value).parts)
            or str(PurePosixPath(value)) == "."):
        raise ConfigError(f"[context] {key} must be a non-hidden repository-relative path without '..'")
    return str(PurePosixPath(value))


def settings(config: Config) -> dict[str, Any]:
    """Paths from the ordinary trusted config; no new receipt/config digest basis."""
    raw = config.raw.get("context", {})
    if not isinstance(raw, dict):
        raise ConfigError("[context] must be a table")
    unknown = sorted(set(raw) - {*ROLES, "notes"})
    if unknown:
        raise ConfigError(f"[context] unknown keys: {', '.join(unknown)}")
    result = {role: _relative(raw.get(role, config.rel(f"{role}.md")), role) for role in ROLES}
    if any(not result[role].endswith(".md") for role in ROLES):
        raise ConfigError("[context] problem, map and ideas must name Markdown (.md) files")
    if len({result[role] for role in ROLES}) != len(ROLES):
        raise ConfigError("[context] problem, map and ideas must name distinct files")
    notes = raw.get("notes", [config.research_dir])
    if not isinstance(notes, list):
        raise ConfigError("[context] notes must be a list of repository-relative files or folders")
    result["notes"] = [_relative(path, "notes") for path in notes]
    return result


def contained(root: Path, rel: str) -> Path:
    path = root / rel
    if not path.resolve().is_relative_to(root.resolve()):
        raise ConfigError(f"context path escapes the repository: {rel}")
    return path


def note_files(repo: Repo) -> tuple[list[Path], list[str]]:
    config = settings(repo.config)
    files: set[Path] = set()
    warnings = []
    item_dir = repo.path(ITEMS).resolve()
    for rel in dict.fromkeys([*(config[role] for role in ROLES), *config["notes"]]):
        try:
            path = contained(repo.root, rel)
        except ConfigError as exc:
            warnings.append(str(exc))
            continue
        if path.is_file():
            candidates = [path]
        elif path.is_dir():
            candidates = []
            for folder, dirs, names in os.walk(path, followlinks=False,
                    onerror=lambda exc: warnings.append(f"note folder not searched: {exc}")):
                dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not d.startswith(".")
                                 and not (Path(folder) / d).is_symlink())
                candidates.extend(Path(folder) / name for name in names if name.endswith(".md"))
        else:
            continue
        for candidate in candidates:
            rel_file = candidate.relative_to(repo.root).as_posix()
            try:
                safe = contained(repo.root, rel_file)
                if safe.is_symlink() or safe.resolve().is_relative_to(item_dir) or safe.suffix != ".md":
                    continue
                if safe.stat().st_size > MAX_NOTE_BYTES:
                    warnings.append(f"note larger than {MAX_NOTE_BYTES} bytes, not searched: {rel_file}")
                    continue
                files.add(safe)
            except (ConfigError, OSError) as exc:
                warnings.append(f"{rel_file}: {exc}")
    return sorted(files), warnings


def matcher(text: str, regex: bool = False):
    if regex:
        pattern = re.compile(text, re.IGNORECASE)
        return lambda value: pattern.search(value) is not None
    tokens = text.casefold().split()
    return lambda value: all(token in value.casefold() for token in tokens)


def search_notes(repo: Repo, matches) -> tuple[list[dict[str, Any]], list[str], int]:
    files, warnings = note_files(repo)
    rows = []
    searched = 0
    for file in files:
        rel = file.relative_to(repo.root).as_posix()
        try:
            lines = file.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeError) as exc:
            warnings.append(f"{rel}: not searched: {exc}")
            continue
        searched += 1
        for line, text in enumerate(lines, 1):
            if matches(text):
                rows.append({"path": rel, "line": line, "locator": f"{rel}:{line}", "text": text[:500]})
    return rows, warnings, searched


def search_reviews(repo: Repo, matches) -> tuple[list[dict[str, Any]], list[str], int]:
    """Use admitted records plus proposals through Repo, never unchecked JSON as a scientific judgment."""
    rows, warnings, searched = [], [], 0
    try:
        contained(repo.root, repo.rel(REVIEWS))
    except ConfigError as exc:
        return [], [str(exc)], 0
    for item_id in repo.record_folders(REVIEWS):
        # Repo also reads proposals from disk. Reject unsafe folders before it can follow them.
        try:
            contained(repo.root, repo.rel(REVIEWS, item_id))
        except ConfigError as exc:
            warnings.append(str(exc))
            continue
        for record in repo.reviews(item_id):
            if record.problems:
                warnings.append(f"{record.path}: invalid review, not searched; run vl validate")
                continue
            searched += 1
            data = record.data
            # Search structured appraisals too: criterion/options and exact raw producer output remain retrievable.
            haystack = json.dumps(data, ensure_ascii=False, sort_keys=True)
            if not matches(haystack):
                continue
            row = {"path": record.path, "line": 1, "locator": f"{record.path}:1",
                   "item": data["item"], "review_id": data["review_id"], "kind": data["kind"],
                   "author": data["author"], "admitted": record.admitted,
                   "text": data.get("text", ""), "verdict": data.get("verdict")}
            if "assessment" in data:
                from .appraisals import appraisal_view
                try:
                    contained(repo.root, repo.rel(ITEMS, f"{item_id}.md"))
                    item = repo.load_item(item_id)
                except (RecordError, ConfigError):
                    item = repo.trusted_item(item_id)
                row.update(appraisal_view(repo, item, record) if item else {
                    "assessment": data["assessment"],
                    "applicability": {"state": "unknown", "reasons": ["subject item unavailable"]}})
            rows.append(row)
    return rows, warnings, searched
