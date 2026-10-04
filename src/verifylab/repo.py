"""The research repository: items, receipts and reviews, read from the worktree or from the trusted ref."""

from __future__ import annotations

import json
import posixpath
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Iterable, Iterator

from . import gitref
from .config import (CHECK_INPUT, CONFIG_INPUTS, CONFIG_PATH, Config, ConfigError, config_digest, load_config,
                     load_worktree_config, parse_config)
from .records import (Item, RecordError, canonical_json, parse_item, question_digest, receipt_problems,
                      review_problems, sha256_hex)

ITEMS = "items"
TARGETS = "targets"
EVIDENCE = "evidence"
REVIEWS = "reviews"
EVALUATORS = "evaluators"
CACHE_DIR = ".vl-cache"              # machine-local cache: never versioned
EXPLORE_DIR = f"{CACHE_DIR}/explore"   # exploratory receipts: never versioned, never admitted, never protected


def input_file(rel_path: str) -> str:
    """The file a recorded input is read from: `research/vl.toml#verdict-rules` (and the older `#verdict` and
    `#check`) is a part of `research/vl.toml`."""
    return CONFIG_PATH if rel_path in CONFIG_INPUTS else rel_path


def _parse_items(files: Iterable[tuple[str, bytes]]) -> tuple[dict[str, Item], list[str]]:
    items: dict[str, Item] = {}
    problems: list[str] = []
    for rel, data in files:
        try:
            item = parse_item(data, rel)
        except RecordError as exc:
            problems.append(str(exc))
            continue
        if item.id in items:
            problems.append(f"{rel}: duplicate id '{item.id}'")
            continue
        items[item.id] = item
    return items, problems


@dataclass(frozen=True)
class StoredRecord:
    """A receipt or review with where it was found and whether it is committed on the trusted ref."""

    path: str
    data: dict[str, Any]
    problems: tuple[str, ...]
    admitted: bool


class Repo:
    def __init__(self, config: Config):
        self.config = config          # read from the trusted ref (see config.load_config)
        self.root = config.root
        self.trusted_commit = config.trusted_commit   # the trusted ref, resolved once for this command
        self._trusted_items: tuple[dict[str, Item], list[str]] | None = None

    @classmethod
    def open(cls, start: Path | None = None, trusted_ref: str | None = None) -> "Repo":
        return cls(load_config(start or Path.cwd(), trusted_ref))

    def worktree_config(self) -> Config:
        """The worktree's own research/vl.toml: only exploratory checks read it, and they never count."""
        return load_worktree_config(self.root, self.config)

    def rel(self, *parts: str) -> str:
        return self.config.rel(*parts)

    def path(self, *parts: str) -> Path:
        return self.root / self.rel(*parts)

    # The trusted commit -------------------------------------------------------

    def read_trusted(self, rel_path: str) -> bytes | None:
        """A file as committed on the trusted ref: None when it is not there (or no trusted ref resolves); a read
        that fails raises GitError, never None."""
        return gitref.show(self.root, self.trusted_commit, rel_path) if self.trusted_commit else None

    def trusted_paths(self, folder: str) -> list[str]:
        return gitref.ls_files(self.root, self.trusted_commit, folder) if self.trusted_commit else []

    # Items -----------------------------------------------------------------

    def item_files(self) -> list[Path]:
        folder = self.path(ITEMS)
        return sorted(folder.glob("*.md")) if folder.is_dir() else []

    def load_items(self) -> tuple[dict[str, Item], list[str]]:
        """All items in the worktree plus the problems found while parsing them."""
        return _parse_items((str(file.relative_to(self.root)), file.read_bytes()) for file in self.item_files())

    def trusted_items(self) -> tuple[dict[str, Item], list[str]]:
        """All items as committed on the trusted ref (their relations, kind and claim decide derived status), plus
        the problems found while parsing them."""
        if self._trusted_items is None:
            folder = self.rel(ITEMS)
            files = [p for p in self.trusted_paths(folder) if posixpath.dirname(p) == folder and p.endswith(".md")]
            self._trusted_items = _parse_items((p, self.read_trusted(p) or b"") for p in files)
        return self._trusted_items

    def load_item(self, item_id: str) -> Item:
        file = self.path(ITEMS, f"{item_id}.md")
        if not file.is_file():
            raise RecordError(f"no item '{item_id}' ({self.rel(ITEMS, item_id + '.md')})")
        return parse_item(file.read_bytes(), str(file.relative_to(self.root)))

    def trusted_item(self, item_id: str, ref: str | None = None) -> Item | None:
        """The item as committed on `ref` (default: the trusted commit)."""
        rel = self.rel(ITEMS, f"{item_id}.md")
        data = gitref.show(self.root, ref, rel) if ref else self.read_trusted(rel)
        return parse_item(data, rel) if data is not None else None

    # Receipts and reviews ----------------------------------------------------

    def _records(self, folder: str, item_id: str, check, explore: bool = False) -> Iterator[StoredRecord]:
        """Records filed under `folder/<item_id>/`, or the exploratory receipts of the item when `explore`.

        Admitted records are enumerated from the trusted commit's tree and read from it: deleting, renaming or
        never having checked out a record in this worktree cannot hide it. A worktree record whose path and bytes
        are not on the trusted ref is a proposal (not admitted); one that differs from the admitted record at the
        same path is rejected, since records are immutable. The exploratory store is never admitted, whatever is
        committed there: a receipt in it that claims more than `exploratory` is rejected."""
        rel_dir = f"{EXPLORE_DIR}/{item_id}" if explore else self.rel(folder, item_id)
        admitted: dict[str, bytes] = {}
        if not explore:
            for path in self.trusted_paths(rel_dir):
                if posixpath.dirname(path) == rel_dir and path.endswith(".json"):
                    data = self.read_trusted(path)
                    admitted[path] = data if data is not None else b""
        for rel, raw in admitted.items():
            yield self._record(rel, raw, item_id, check, explore, admitted=True)
        base = self.root / rel_dir
        if not base.is_dir():
            return
        for file in sorted(base.glob("*.json")):
            rel = str(file.relative_to(self.root))
            if not file.is_file() or file.is_symlink():
                yield StoredRecord(rel, {}, ("not a regular file",), False)
                continue
            raw = file.read_bytes()
            if rel in admitted:
                if raw != admitted[rel]:
                    yield StoredRecord(rel, {}, ("differs from the admitted record at this path on the trusted ref; "
                                                 "records are immutable",), False)
                continue
            yield self._record(rel, raw, item_id, check, explore, admitted=False)

    @staticmethod
    def _record(rel: str, raw: bytes, item_id: str, check, explore: bool, admitted: bool) -> StoredRecord:
        try:
            data = json.loads(raw)
        except UnicodeDecodeError:
            return StoredRecord(rel, {}, ("not UTF-8",), admitted)
        except (ValueError, RecursionError) as exc:
            why = "nested too deeply" if isinstance(exc, RecursionError) else "not valid JSON"
            return StoredRecord(rel, {}, (why,), admitted)
        try:
            problems = list(check(data))
        except RecursionError:
            problems = ["nested too deeply"]
        if isinstance(data, dict) and data.get("item") != item_id:
            problems.append(f"filed under '{item_id}' but names item '{data.get('item')}'")
        if explore and isinstance(data, dict) and data.get("assurance") != "exploratory":
            problems.append(f"assurance '{data.get('assurance')}' in the exploratory store {EXPLORE_DIR}/: "
                            "receipts there are never protected")
        return StoredRecord(rel, data if isinstance(data, dict) else {}, tuple(problems), admitted)

    def receipts(self, item_id: str) -> list[StoredRecord]:
        return [*self._records(EVIDENCE, item_id, receipt_problems),
                *self._records(EVIDENCE, item_id, receipt_problems, explore=True)]

    def explore_path(self, item_id: str, name: str) -> Path:
        return self.root / EXPLORE_DIR / item_id / name

    def reviews(self, item_id: str) -> list[StoredRecord]:
        records = []
        for record in self._records(REVIEWS, item_id, review_problems):
            problems = self.review_revision_problems(record) if not record.problems else []
            records.append(replace(record, problems=record.problems + tuple(problems)))
        return records

    def review_revision_problems(self, record: StoredRecord) -> list[str]:
        """Reject a fidelity record that attributes bound item text to a different item revision.

        Earlier vl versions could record an old requested revision but bind the current trusted meaning.
        A missing blob cannot establish a mismatch; target-only legacy reviews retain their existing policy.
        """
        review = record.data
        if review.get("kind") != "fidelity" or not isinstance(review.get("meaning"), dict):
            return []
        _, raw = gitref.blob(self.root, review["item_revision"])
        if raw is None:
            return []
        path = self.rel(ITEMS, review["item"] + ".md")
        try:
            item = parse_item(raw, path)
        except RecordError as exc:
            return [f"fidelity item_revision is not a revision of the reviewed item: {exc}"]
        basis = review["meaning"]
        expected = {"statement_sha256": sha256_hex((item.statement or "").encode())}
        for name in ("limits", "assumptions"):
            expected[f"{name}_sha256"] = sha256_hex(canonical_json(list(getattr(item, name))).encode())
        if item.lean:
            expected.update(theorems=[str(t) for t in item.lean.get("theorems") or []],
                            witnesses=[str(w) for w in item.lean.get("witnesses") or []])
        mismatches = [key for key, value in expected.items() if key in basis and basis[key] != value]
        target = item.lean.get("target") or item.python.get("evaluator")
        if target != review.get("target_path"):
            mismatches.append("target_path")
        return (["fidelity meaning disagrees with item_revision: " + ", ".join(mismatches)
                 + "; read the admitted meaning and record a new review"] if mismatches else [])

    def record_folders(self, folder: str) -> list[str]:
        """Names of the item folders under `folder` (evidence or reviews), in the worktree or on the trusted ref."""
        base = self.path(folder)
        names = {d.name for d in base.iterdir() if d.is_dir()} if base.is_dir() else set()
        prefix = self.rel(folder)
        names |= {p[len(prefix) + 1:].split("/", 1)[0] for p in self.trusted_paths(prefix) if "/" in p[len(prefix) + 1:]}
        return sorted(names)

    # Inputs ------------------------------------------------------------------

    def worktree_digest(self, rel_path: str) -> str | None:
        """Digest of a recorded input as it is in the worktree now (see `digest`), or None when it is not there."""
        file = self.root / input_file(rel_path)
        return self.digest(rel_path, file.read_bytes()) if file.is_file() else None

    def stale_inputs(self, receipt: dict[str, Any], item: Item, admitted: bool = False) -> list[str]:
        """Inputs that changed since the check: candidate files against the worktree (and, for an `admitted`
        receipt, against the trusted ref too, so that a file only this checkout has, ignored or uncommitted, cannot
        keep a result verified that a fresh clone would show stale), trusted files (targets, definitions,
        evaluators) against the trusted ref as it is now, and the question fields against `item` (the worktree's)
        and, for an admitted receipt, against the item as committed on the trusted ref."""
        inputs = receipt.get("inputs", {})
        stale = []
        for p, digest in inputs.get("files", {}).items():
            if self.worktree_digest(p) != digest:
                stale.append(p)
            elif admitted and self.trusted_digest(p) != digest:
                stale.append(f"{p} (as checked, it is not on the trusted ref: a fresh clone would not have it)")
        for p, digest in inputs.get("trusted_files", {}).items():
            if self.trusted_digest(p) != digest:
                stale.append(f"{p} (trusted ref)")
            elif p == CHECK_INPUT and not self.same_roots(inputs.get("trusted_commit")):
                stale.append(f"{p} (trusted ref: [lean] roots differ from those of the checked commit)")
        question = self.question_stale(receipt, item)
        if question:
            stale.append(question)
        elif admitted:
            # An admitted receipt answers for the item as committed on the trusted ref too: a proof term or solution
            # only this worktree has must not verify what a fresh clone would show answered otherwise.
            trusted = self.trusted_items()[0].get(item.id)
            if trusted is None:
                stale.append(f"{item.path}#question (the item is not on the trusted ref, or does not parse there)")
            elif self.question_stale(receipt, trusted):
                stale.append(f"{item.path}#question (as checked, it is not the item on the trusted ref: a fresh "
                             "clone would ask or answer otherwise)")
        return sorted(stale)

    def question_stale(self, receipt: dict[str, Any], item: Item) -> str | None:
        """Why the receipt no longer answers `item`'s question fields (the question and the answer's binding: proof
        terms, imports, solution; candidate, entry, files), or None. A receipt written before `question_digest`
        existed is bound through the item blob its `item_revision` names."""
        recorded = receipt.get("question_digest")
        where = f"{item.path}#question"
        if not isinstance(recorded, str):
            revision = receipt.get("item_revision")
            full, data = gitref.blob(self.root, revision) if isinstance(revision, str) and revision else (None, None)
            if data is None:
                return (f"{where} (the receipt names no question digest and its item revision "
                        f"{str(revision)[:12]} is not in the git object store)")
            try:
                recorded = question_digest(parse_item(data, item.path))
            except RecordError:
                return f"{where} (item revision {str(revision)[:12]} of the receipt does not parse)"
        return where if recorded != question_digest(item) else None

    def _roots(self, ref: str | None) -> tuple[str, ...] | None:
        """The [lean] roots of `ref`'s research/vl.toml; None when `ref` is not in this repository or has none."""
        if not ref or not gitref.ref_exists(self.root, ref):
            return None
        data = gitref.show(self.root, ref, CONFIG_PATH)
        try:
            return tuple(sorted(set(parse_config(self.root, data.decode("utf-8")).lean.roots))) if data else None
        except (ConfigError, UnicodeDecodeError):
            return None

    def same_roots(self, commit) -> bool:
        """Receipts binding `research/vl.toml#check`, a digest without the roots, answer only for the [lean] roots
        of the commit they checked: compare those with the trusted ref's."""
        if not isinstance(commit, str) or not commit or commit.startswith("-"):
            return False
        then = self._roots(commit)
        return then is not None and then == self._roots(self.trusted_commit)

    def trusted_digest(self, rel_path: str) -> str | None:
        """Digest of a recorded input as committed on the trusted ref, or None when it is not there."""
        content = self.read_trusted(input_file(rel_path))
        return self.digest(rel_path, content) if content is not None else None

    def input_digest(self, rel_path: str, ref: str) -> str | None:
        """Digest of a recorded input as committed on `ref`, or None when it is not there."""
        content = gitref.show(self.root, ref, input_file(rel_path))
        return self.digest(rel_path, content) if content is not None else None

    def digest(self, rel_path: str, content: bytes) -> str | None:
        """sha256 of a file's content; for a configuration input (config.CONFIG_INPUTS), the digest of the part of
        `research/vl.toml` that input names; `research/vl.toml` itself is a whole file like any other."""
        if rel_path not in CONFIG_INPUTS:
            return sha256_hex(content)
        try:
            return config_digest(rel_path, parse_config(self.root, content.decode("utf-8")))
        except (ConfigError, UnicodeDecodeError):
            return None
