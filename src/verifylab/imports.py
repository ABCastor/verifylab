"""Cross-repo Lean theorem imports and offline snapshot resolver (N1, N2, N5).

An item's `[[imports]]` table names an upstream VerifyLab item, full item blob,
Lean module, selected declarations, full receipt id and full fidelity-review id.
Repository URL, subdirectory and commit pin (`rev`) come solely from the
consumer repository's resolved `lake-manifest.json` entry.

Resolution is strictly offline: it reads git objects at the pinned commit of a
locally available package repository (machine build cache, `[lean] packages`,
`[lean] cache`, `.lake/packages`, or caller-supplied offline stores), never
fetches over the network, and never adopts the source repository's
`vl.trustedRef` or checked-out HEAD. Source status and fidelity are derived over
the whole admitted snapshot at that pinned commit so no cherry-picked pass hides
a retraction, correction, newer failure, vacuity or stale/disputed review.
"""

from __future__ import annotations

import json
import os
import posixpath
import re
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator

from . import gitref, leanmod, machine, packages, status
from .config import CONFIG_PATH, Config, ConfigError, LeanConfig, parse_config
from .records import (
    IMPORT_KEYS,
    Item,
    RecordError,
    canonical_json,
    parse_item,
    receipt_problems,
    review_problems,
    sha256_hex,
)
from .repo import (
    EVIDENCE,
    ITEMS,
    REVIEWS,
    TARGETS,
    Repo,
    StoredRecord,
    TrustedRepo,
    _parse_items,
    input_file,
)

# Guard names that tests may disable (`guards` set) to prove each planted case slips through only when disabled.
GUARDS = frozenset({
    "manifest_pin",
    "provider_uniqueness",
    "snapshot_status",
    "blob_match",
    "checked_theorems",
    "source_fidelity",
    "unused_import",
    "dependency_receipt",
})

SOURCE_QUALIFICATION_NOTE = (
    "source theorem/review lineage resolved offline; cross-repo compiled artifact qualification pending (N3)"
)
INDEX_DISCLAIMER = "locator cache only; never proof, fidelity, or freshness evidence"

def _enabled(guards: frozenset[str], name: str) -> bool:
    unknown = set(guards) - GUARDS
    if unknown:
        raise ValueError(f"unknown import guard(s): {sorted(unknown)}")
    return name not in guards


class ImportResolutionError(Exception):
    """Raised when a cross-repo import snapshot cannot be opened."""


def normalize_subdir(raw: Any) -> str | None:
    """Normalize a manifest package `subDir` as a safe repo-relative POSIX path, or None."""
    if raw is None or raw == "":
        return None
    if not isinstance(raw, str) or "\0" in raw or "\\" in raw:
        raise ValueError(f"invalid manifest subDir: {raw!r}")
    norm = posixpath.normpath(raw.strip("/"))
    if norm in ("", "."):
        return None
    if norm.startswith("/") or norm == ".." or norm.startswith("../"):
        raise ValueError(f"unsafe manifest subDir '{raw}'")
    return norm


@dataclass(frozen=True)
class ManifestPackage:
    name: str
    kind: str
    url: str
    rev: str
    sub_dir: str | None
    inherited: bool


@dataclass(frozen=True)
class ManifestInfo:
    packages_dir: str
    packages: dict[str, ManifestPackage]
    problems: tuple[str, ...] = ()


def resolve_manifest_packages(manifest_bytes: bytes | None, where: str = packages.MANIFEST) -> ManifestInfo:
    """Parse `lake-manifest.json` into keyed package entries."""
    if manifest_bytes is None:
        return ManifestInfo(".lake/packages", {}, (f"{where}: missing",))
    try:
        data = json.loads(manifest_bytes)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        return ManifestInfo(".lake/packages", {}, (f"{where}: {exc}",))
    if not isinstance(data, dict) or not isinstance(data.get("packages", []), list):
        return ManifestInfo(".lake/packages", {}, (f"{where}: 'packages' must be a list",))
    packages_dir = data.get("packagesDir") or ".lake/packages"
    if not isinstance(packages_dir, str) or not packages_dir.strip():
        packages_dir = ".lake/packages"
    found: dict[str, ManifestPackage] = {}
    problems: list[str] = []
    for idx, entry in enumerate(data.get("packages", [])):
        if not isinstance(entry, dict):
            problems.append(f"{where}: packages[{idx}] must be an object")
            continue
        name = entry.get("name")
        if not isinstance(name, str) or not name:
            problems.append(f"{where}: packages[{idx}] has no name")
            continue
        if name in found:
            problems.append(f"{where}: duplicate package entry '{name}'")
            continue
        try:
            sub_dir = normalize_subdir(entry.get("subDir"))
        except ValueError as exc:
            problems.append(f"{where}: package '{name}': {exc}")
            continue
        found[name] = ManifestPackage(
            name=name,
            kind=str(entry.get("type") or ""),
            url=str(entry.get("url") or ""),
            rev=str(entry.get("rev") or ""),
            sub_dir=sub_dir,
            inherited=bool(entry.get("inherited")),
        )
    return ManifestInfo(packages_dir, found, tuple(problems))


def manifest_path(repo: Repo) -> str:
    project = posixpath.normpath(repo.config.lean.project.strip() or ".")
    return posixpath.normpath(posixpath.join(project, packages.MANIFEST))


def read_manifest(repo: Repo, commit: str | None = None) -> bytes | None:
    rel = manifest_path(repo)
    if commit is not None:
        return gitref.show(repo.root, commit, rel)
    file = repo.root / rel
    return file.read_bytes() if file.is_file() else None


def replay_package_stores(repo: Repo) -> tuple[Path, ...]:
    """Existing offline package store directories for `repo` so `vl replay` can read pinned git objects offline."""
    dirs: list[Path] = []
    machine_cache = machine.load().cache_for(gitref.main_worktree(repo.root))
    if machine_cache:
        dirs.append(Path(machine_cache) / "packages")
    lean_raw = repo.config.raw.get("lean", {}) or {}
    for raw_dir, suffix in ((lean_raw.get("packages"), ()), (lean_raw.get("cache"), ("packages",))):
        if isinstance(raw_dir, str) and raw_dir.strip():
            p = Path(raw_dir).expanduser()
            p = p if p.is_absolute() else repo.root / p
            for part in suffix:
                p = p / part
            dirs.append(p)
    project = posixpath.normpath(repo.config.lean.project.strip() or ".")
    raw_manifest = read_manifest(repo, commit=repo.trusted_commit)
    pkg_dir = resolve_manifest_packages(raw_manifest).packages_dir if raw_manifest else ".lake/packages"
    dirs.append(repo.root / project / pkg_dir)
    out: list[Path] = []
    for d in dirs:
        r = d.resolve() if d.exists() else d
        if r.is_dir() and r not in out:
            out.append(r)
    return tuple(out)


def _env_store_dirs() -> list[Path]:
    raw = os.environ.get("VL_PACKAGE_STORES", "")
    return [Path(p).expanduser() for p in raw.split(os.pathsep) if p.strip()]


def package_repo_candidates(repo: Repo, package_name: str, manifest_packages_dir: str = ".lake/packages",
                            store_dirs: Iterable[Path] = ()) -> list[Path]:
    """Candidate offline directories for `package_name`, checked in priority order without network access."""
    bases: list[Path] = [Path(d).expanduser() for d in store_dirs] + _env_store_dirs()
    machine_cache = machine.load().cache_for(gitref.main_worktree(repo.root))
    if machine_cache:
        bases.append(Path(machine_cache) / "packages")
    lean_raw = repo.config.raw.get("lean", {}) or {}
    for raw_dir, suffix in ((lean_raw.get("packages"), ()), (lean_raw.get("cache"), ("packages",))):
        if isinstance(raw_dir, str) and raw_dir.strip():
            path = Path(raw_dir).expanduser()
            path = path if path.is_absolute() else repo.root / path
            for part in suffix:
                path = path / part
            bases.append(path)
    project = posixpath.normpath(repo.config.lean.project.strip() or ".")
    bases.append(repo.root / project / (manifest_packages_dir or ".lake/packages"))

    out: list[Path] = []
    seen: set[Path] = set()
    for base in bases:
        for cand in (base / package_name, base if base.name == package_name else None):
            if cand is None:
                continue
            resolved = cand.resolve() if cand.exists() else cand
            if resolved.is_dir() and resolved not in seen:
                seen.add(resolved)
                out.append(resolved)
    return out


def _is_git_repo(path: Path) -> bool:
    try:
        gitref.git(path, "rev-parse", "--git-dir")
        return True
    except gitref.GitError:
        return False


def locate_package_git(repo: Repo, pkg: ManifestPackage, manifest_packages_dir: str = ".lake/packages",
                       store_dirs: Iterable[Path] = (), enforce_pin: bool = True) -> tuple[Path | None, str | None, str | None]:
    """Locate an offline git checkout/store containing `pkg.rev`.

    Returns `(git_root, resolved_commit, error)`. Never performs network fetches or substitutes `HEAD`
    for a missing commit when `enforce_pin` is True.
    """
    if enforce_pin:
        if pkg.kind != "git":
            return None, None, f"package '{pkg.name}' has non-git manifest type '{pkg.kind}'; git pin required"
        if not pkg.url.strip():
            return None, None, f"package '{pkg.name}' has empty repository url in {packages.MANIFEST}"
        if not gitref.is_full_sha(pkg.rev):
            return None, None, f"package '{pkg.name}' rev '{pkg.rev}' in {packages.MANIFEST} is not a full git commit sha"

    candidates = package_repo_candidates(repo, pkg.name, manifest_packages_dir, store_dirs)
    saw_repo_without_commit: list[Path] = []
    for cand in candidates:
        if not _is_git_repo(cand):
            continue
        if enforce_pin:
            try:
                resolved = gitref.rev_parse(cand, f"{pkg.rev}^{{commit}}")
            except gitref.GitError:
                resolved = None
            if resolved == pkg.rev:
                return cand, resolved, None
            saw_repo_without_commit.append(cand)
        else:
            commit = None
            if gitref.is_full_sha(pkg.rev) and gitref.ref_exists(cand, pkg.rev):
                commit = pkg.rev
            else:
                try:
                    commit = gitref.head(cand)
                except gitref.GitError:
                    commit = None
            if commit:
                return cand, commit, None

    if saw_repo_without_commit:
        locs = ", ".join(str(p) for p in saw_repo_without_commit)
        return None, None, (
            f"package '{pkg.name}' offline store ({locs}) does not contain pinned commit {pkg.rev}; "
            "offline resolver never fetches or falls back to HEAD"
        )
    return None, None, (
        f"package '{pkg.name}' repository not found offline in build cache or package stores "
        "(expected local clone at pinned commit; never fetches)"
    )


def _package_module_candidates(pkg_root: Path, rev: str, sub_dir: str | None, module: str) -> list[str]:
    """Repo-relative file paths where `module` could live in `(pkg_root, rev, sub_dir)`."""
    base = sub_dir or "."
    lakefile = posixpath.normpath(posixpath.join(base, "lakefile.toml"))
    raw = gitref.show(pkg_root, rev, lakefile)
    paths: list[str] = []
    has_libs = False
    if raw is not None:
        try:
            libs = leanmod.lean_libraries(raw)
        except leanmod.LeanModError:
            libs = []
        has_libs = bool(libs)
        for lib in libs:
            mod_rel = leanmod.module_to_path(module)
            if lib.is_local(module):
                paths.append(posixpath.normpath(posixpath.join(base, lib.src_dir, mod_rel)))
    if not paths and (not has_libs or gitref.show(pkg_root, rev, posixpath.normpath(posixpath.join(base, "lakefile.lean"))) is not None):
        paths.append(posixpath.normpath(posixpath.join(base, leanmod.module_to_path(module))))
    return list(dict.fromkeys(paths))


def read_package_module(pkg_root: Path, rev: str, sub_dir: str | None, module: str) -> tuple[str | None, bytes | None]:
    """Return `(repo_relative_path, bytes)` for `module` at `(pkg_root, rev)`, or `(None, None)`."""
    for rel in _package_module_candidates(pkg_root, rev, sub_dir, module):
        data = gitref.show(pkg_root, rev, rel)
        if data is not None:
            return rel, data
    return None, None


class SnapshotRepo(TrustedRepo):
    """Read-only VerifyLab view strictly pinned to one source git commit `rev`.

    Never reads the package repository's disk worktree, git `vl.trustedRef`, or exploratory store.
    All derived statuses and meanings come solely from the admitted snapshot at `rev`.
    """

    def __init__(self, config: Config, config_rel: str = CONFIG_PATH):
        super().__init__(config)
        self._config_rel = config_rel
        self._prefix = posixpath.dirname(posixpath.dirname(config_rel))

    @classmethod
    def open_at(cls, pkg_root: Path, rev: str, sub_dir: str | None = None) -> "SnapshotRepo":
        candidates = [posixpath.join(sub_dir, CONFIG_PATH), CONFIG_PATH] if sub_dir else [CONFIG_PATH]
        raw: bytes | None = None
        cfg_rel = CONFIG_PATH
        for cand in candidates:
            raw = gitref.show(pkg_root, rev, cand)
            if raw is not None:
                cfg_rel = cand
                break
        if raw is None:
            raise ImportResolutionError(f"source package has no {CONFIG_PATH} at commit {rev[:12]}")
        try:
            parsed = parse_config(pkg_root, raw.decode("utf-8"))
        except (ConfigError, UnicodeDecodeError) as exc:
            raise ImportResolutionError(f"source package {cfg_rel} at {rev[:12]} is invalid: {exc}") from exc
        pinned = replace(
            parsed,
            trusted_ref=rev,
            trusted_ref_source="lake-manifest",
            trusted_commit=rev,
        )
        return cls(pinned, config_rel=cfg_rel)

    def _git_path(self, rel_path: str) -> str:
        if not self._prefix:
            return rel_path
        return posixpath.normpath(posixpath.join(self._prefix, rel_path))

    def read_trusted(self, rel_path: str) -> bytes | None:
        return gitref.show(self.root, self.trusted_commit, self._git_path(rel_path)) if self.trusted_commit else None

    def trusted_paths(self, folder: str) -> list[str]:
        if not self.trusted_commit:
            return []
        paths = gitref.ls_files(self.root, self.trusted_commit, self._git_path(folder))
        if not self._prefix:
            return paths
        pfx = f"{self._prefix}/"
        return [p[len(pfx):] if p.startswith(pfx) else p for p in paths]

    def worktree_config(self) -> Config:
        return self.config

    def item_files(self) -> list[Path]:
        return []

    def load_items(self) -> tuple[dict[str, Item], list[str]]:
        return self.trusted_items()

    def lean_config_at(self, commit: str) -> LeanConfig:
        if commit == self.trusted_commit:
            return self.config.lean
        data = gitref.show(self.root, commit, self._config_rel)
        if data is None and self._prefix:
            data = gitref.show(self.root, commit, CONFIG_PATH)
        if data is None:
            raise ConfigError(f"{self._config_rel} is not in commit {commit[:12]}")
        try:
            return parse_config(self.root, data.decode("utf-8")).lean
        except UnicodeDecodeError as exc:
            raise ConfigError(f"{self._config_rel} in commit {commit[:12]} is not UTF-8") from exc

    def _records(self, folder: str, item_id: str, check, explore: bool = False) -> Iterator[StoredRecord]:
        if explore:
            return
        rel_dir = self.rel(folder, item_id)
        for path in self.trusted_paths(rel_dir):
            if posixpath.dirname(path) == rel_dir and path.endswith(".json"):
                raw = self.read_trusted(path)
                yield self._record(path, raw if raw is not None else b"", item_id, check, False, admitted=True)

    def _roots(self, ref: str | None) -> tuple[str, ...] | None:
        if not ref or not gitref.ref_exists(self.root, ref):
            return None
        data = gitref.show(self.root, ref, self._config_rel)
        if data is None and self._prefix:
            data = gitref.show(self.root, ref, CONFIG_PATH)
        try:
            return tuple(sorted(set(parse_config(self.root, data.decode("utf-8")).lean.roots))) if data else None
        except (ConfigError, UnicodeDecodeError):
            return None

    def record_folders(self, folder: str) -> list[str]:
        prefix = self.rel(folder)
        names = {
            p[len(prefix) + 1:].split("/", 1)[0]
            for p in self.trusted_paths(prefix)
            if "/" in p[len(prefix) + 1:]
        }
        return sorted(names)


@dataclass(frozen=True)
class ResolvedImport:
    package: str
    item: str
    item_revision: str
    module: str
    theorems: tuple[str, ...]
    receipt: str
    fidelity_review: str
    manifest_url: str
    manifest_sub_dir: str | None
    manifest_rev: str
    source_status: str
    source_status_notes: tuple[str, ...]
    source_fidelity: dict[str, Any]
    source_meaning_digest: str
    source_statement: str | None
    source_limits: tuple[str, ...]
    source_assumptions: tuple[str, ...]
    source_corrections: tuple[dict[str, Any], ...]
    source_module_path: str
    source_item_title: str

    def receipt_entry(self) -> dict[str, Any]:
        """Deterministic record written into `receipt.inputs.imports` and included in `inputs.digest`."""
        return {
            "package": self.package,
            "item": self.item,
            "item_revision": self.item_revision,
            "module": self.module,
            "theorems": list(self.theorems),
            "receipt": self.receipt,
            "fidelity_review": self.fidelity_review,
            "manifest": {
                "url": self.manifest_url,
                "subDir": self.manifest_sub_dir,
                "rev": self.manifest_rev,
            },
            "source_meaning_digest": self.source_meaning_digest,
        }

    def card_entry(self) -> dict[str, Any]:
        """Rich metadata for `vl show` and `vl export catalog`, separating source and consumer state."""
        return {
            **self.receipt_entry(),
            "source_title": self.source_item_title,
            "source_module_path": self.source_module_path,
            "source_status": self.source_status,
            "source_status_notes": list(self.source_status_notes),
            "source_fidelity": dict(self.source_fidelity),
            "source_statement": self.source_statement,
            "source_limits": list(self.source_limits),
            "source_assumptions": list(self.source_assumptions),
            "source_corrections": list(self.source_corrections),
            "qualification": SOURCE_QUALIFICATION_NOTE,
        }


def _check_provider_uniqueness(
    consumer_repo: Repo,
    manifest: ManifestInfo,
    target_pkg: ManifestPackage,
    pkg_root: Path,
    pkg_rev: str,
    module: str,
    store_dirs: Iterable[Path],
    enforce_pin: bool,
) -> tuple[str | None, bytes | None, str | None]:
    """Enforce unambiguous module ownership and return `(module_path, module_bytes, error)`."""
    if leanmod.in_roots(module, consumer_repo.config.lean.roots):
        return None, None, (
            f"ambiguous module provider: '{module}' matches consumer [lean].roots "
            f"{list(consumer_repo.config.lean.roots)}"
        )
    mod_path, mod_bytes = read_package_module(pkg_root, pkg_rev, target_pkg.sub_dir, module)
    if mod_path is None or mod_bytes is None:
        return None, None, (
            f"module '{module}' was not found in package '{target_pkg.name}' at pinned commit {pkg_rev[:12]}"
        )
    other_providers: list[str] = []
    for name, other in sorted(manifest.packages.items()):
        if name == target_pkg.name:
            continue
        other_root, other_rev, err = locate_package_git(
            consumer_repo, other, manifest.packages_dir, store_dirs, enforce_pin=enforce_pin
        )
        if err is not None or other_root is None or other_rev is None:
            continue
        o_path, _ = read_package_module(other_root, other_rev, other.sub_dir, module)
        if o_path is not None:
            other_providers.append(name)
    if other_providers:
        providers = ", ".join([target_pkg.name, *other_providers])
        return None, None, f"ambiguous module provider: '{module}' is provided by multiple packages ({providers})"
    return mod_path, mod_bytes, None


def _resolve_one(
    consumer_repo: Repo,
    imp: dict[str, Any],
    manifest: ManifestInfo,
    store_dirs: Iterable[Path],
    guards: frozenset[str],
) -> tuple[ResolvedImport | None, list[str]]:
    errors: list[str] = []
    pkg_name = imp["package"]
    pkg = manifest.packages.get(pkg_name)
    if pkg is None:
        return None, [f"imports package '{pkg_name}' is not listed in consumer {packages.MANIFEST}"]

    enforce_pin = _enabled(guards, "manifest_pin")
    pkg_root, pkg_rev, loc_err = locate_package_git(
        consumer_repo, pkg, manifest.packages_dir, store_dirs, enforce_pin=enforce_pin
    )
    if loc_err or pkg_root is None or pkg_rev is None:
        return None, [loc_err or f"cannot locate package '{pkg_name}' offline"]

    module = imp["module"]
    if _enabled(guards, "provider_uniqueness"):
        mod_path, mod_bytes, prov_err = _check_provider_uniqueness(
            consumer_repo, manifest, pkg, pkg_root, pkg_rev, module, store_dirs, enforce_pin
        )
        if prov_err or mod_path is None or mod_bytes is None:
            return None, [prov_err or f"missing module '{module}'"]
    else:
        mod_path, mod_bytes = read_package_module(pkg_root, pkg_rev, pkg.sub_dir, module)
        mod_path = mod_path or leanmod.module_to_path(module)

    try:
        snap = SnapshotRepo.open_at(pkg_root, pkg_rev, pkg.sub_dir)
    except ImportResolutionError as exc:
        return None, [f"package '{pkg_name}': {exc}"]

    src_items, item_problems = snap.trusted_items()
    src_item = src_items.get(imp["item"])
    if src_item is None:
        detail = "; ".join(p for p in item_problems if imp["item"] in p)
        msg = f"source item '{imp['item']}' not found at {pkg_name}@{pkg_rev[:12]}"
        if detail:
            msg += f" ({detail})"
        return None, [msg]

    if _enabled(guards, "blob_match") and src_item.revision != imp["item_revision"]:
        errors.append(
            f"source item '{imp['item']}' blob mismatch at {pkg_name}@{pkg_rev[:12]}: "
            f"import pins {imp['item_revision'][:12]}, snapshot holds {src_item.revision[:12]}"
        )

    src_receipts = snap.receipts(imp["item"])
    src_reviews = snap.reviews(imp["item"])
    r_rec = next((r for r in src_receipts if not r.problems and r.data.get("receipt_id") == imp["receipt"]), None)
    if r_rec is None:
        bad = next((r for r in src_receipts if r.data.get("receipt_id") == imp["receipt"]), None)
        why = f"malformed ({'; '.join(bad.problems)})" if bad else f"not admitted in {pkg_name}@{pkg_rev[:12]}"
        errors.append(f"source receipt {imp['receipt'][:16]} for '{imp['item']}' is invalid or missing ({why})")
    else:
        if r_rec.data.get("assurance") != "protected" or r_rec.data.get("verdict") != "pass":
            errors.append(
                f"source receipt {imp['receipt'][:16]} is not a protected pass "
                f"({r_rec.data.get('assurance')} {r_rec.data.get('verdict')})"
            )
        if _enabled(guards, "snapshot_status"):
            stale = snap.stale_inputs(r_rec.data, src_item, admitted=True)
            if stale:
                errors.append(
                    f"pinned source receipt {imp['receipt'][:16]} is stale at {pkg_name}@{pkg_rev[:12]}: "
                    + ", ".join(stale)
                )

    src_status = status.derive(snap, src_item, src_items)
    if _enabled(guards, "snapshot_status") and src_status.label != "verified":
        detail = f" ({'; '.join(src_status.notes)})" if src_status.notes else ""
        errors.append(
            f"source item '{imp['item']}' snapshot status at {pkg_name}@{pkg_rev[:12]} is '{src_status.label}', "
            f"not 'verified'{detail}"
        )

    src_meaning = status.meaning(snap, src_item)
    src_meaning_digest = status.meaning_digest(src_meaning) if src_meaning is not None else ""
    src_fidelity = status.fidelity(snap, src_item)
    f_rec = next((r for r in src_reviews if not r.problems and r.data.get("review_id") == imp["fidelity_review"]), None)
    if _enabled(guards, "source_fidelity"):
        if f_rec is None:
            errors.append(
                f"source fidelity review {imp['fidelity_review'][:16]} for '{imp['item']}' "
                f"is not admitted in {pkg_name}@{pkg_rev[:12]}"
            )
        else:
            if f_rec.data.get("kind") != "fidelity" or f_rec.data.get("verdict") != "faithful":
                errors.append(
                    f"source review {imp['fidelity_review'][:16]} is {f_rec.data.get('kind')} "
                    f"verdict={f_rec.data.get('verdict')}, not a faithful fidelity review"
                )
            bound, _ = status.bound_meaning(snap, f_rec)
            bound_digest = status.meaning_digest(bound) if isinstance(bound, dict) else f_rec.data.get("meaning_digest")
            if bound_digest != src_meaning_digest:
                errors.append(
                    f"source fidelity review {imp['fidelity_review'][:16]} does not match source meaning at "
                    f"{pkg_name}@{pkg_rev[:12]}"
                )
        if _enabled(guards, "snapshot_status"):
            if src_fidelity is None or src_fidelity.label != "faithful":
                label = src_fidelity.label if src_fidelity is not None else "none"
                errors.append(
                    f"source item '{imp['item']}' whole-snapshot fidelity at {pkg_name}@{pkg_rev[:12]} "
                    f"is '{label}', not 'faithful'"
                )

    if _enabled(guards, "checked_theorems"):
        src_theorems = list((src_item.lean or {}).get("theorems") or [])
        missing_in_item = [t for t in imp["theorems"] if t not in src_theorems]
        if missing_in_item:
            errors.append(
                f"imported declaration(s) {missing_in_item} not in source '{imp['item']}' [lean].theorems"
            )
        if r_rec is not None:
            checked_theorems = list((r_rec.data.get("checked") or {}).get("theorems") or [])
            missing_in_receipt = [t for t in imp["theorems"] if t not in checked_theorems]
            if missing_in_receipt:
                errors.append(
                    f"imported declaration(s) {missing_in_receipt} not in receipt {imp['receipt'][:16]} checked.theorems"
                )
            cand_mods = (r_rec.data.get("checked") or {}).get("candidate_modules")
            if isinstance(cand_mods, list) and module not in cand_mods:
                errors.append(
                    f"imported module '{module}' is not in receipt {imp['receipt'][:16]} checked.candidate_modules"
                )
        src_solution = (src_item.lean or {}).get("solution")
        src_proofs = (src_item.lean or {}).get("proofs")
        src_imports = list((src_item.lean or {}).get("imports") or [])
        if src_proofs and src_solution != module:
            errors.append(
                f"source item '{imp['item']}' uses wrapper [lean].proofs mapping rather than direct module "
                "[lean].solution; wrapper bridge is not yet supported"
            )
        elif isinstance(src_solution, str) and src_solution != module:
            errors.append(
                f"imported module '{module}' does not match source '{imp['item']}' [lean].solution '{src_solution}'"
            )
        elif not src_solution and module not in src_imports:
            errors.append(
                f"imported module '{module}' is not declared as [lean].solution or [lean].imports on source '{imp['item']}'"
            )
        if mod_bytes is not None:
            from .index import scan_lean
            declared = {name for name, _, _, _ in scan_lean(mod_bytes.decode("utf-8", errors="replace"))}
            not_declared = [t for t in imp["theorems"] if t not in declared]
            if not_declared:
                errors.append(
                    f"imported declaration(s) {not_declared} not declared in {pkg_name}:{mod_path}@{pkg_rev[:12]}"
                )

    if errors:
        return None, errors

    corrections = tuple(
        {
            "kind": r.data["kind"],
            "review_id": r.data["review_id"],
            "author": r.data["author"],
            "created": r.data["created"],
            "text": r.data["text"],
        }
        for r in src_reviews
        if not r.problems and r.data.get("kind") in ("correction", "retraction")
    )
    fid_summary = {
        "label": src_fidelity.label if src_fidelity else "unknown",
        "review": f_rec.path if f_rec else None,
        "review_id": imp["fidelity_review"],
        "by": src_fidelity.by if src_fidelity else None,
        "created": src_fidelity.created if src_fidelity else None,
        "self_review": src_fidelity.self_review if src_fidelity else None,
    }
    return ResolvedImport(
        package=pkg.name,
        item=imp["item"],
        item_revision=imp["item_revision"],
        module=module,
        theorems=tuple(imp["theorems"]),
        receipt=imp["receipt"],
        fidelity_review=imp["fidelity_review"],
        manifest_url=pkg.url,
        manifest_sub_dir=pkg.sub_dir,
        manifest_rev=pkg_rev if enforce_pin else pkg.rev or pkg_rev,
        source_status=src_status.label,
        source_status_notes=src_status.notes,
        source_fidelity=fid_summary,
        source_meaning_digest=src_meaning_digest,
        source_statement=src_item.statement,
        source_limits=src_item.limits,
        source_assumptions=src_item.assumptions,
        source_corrections=corrections,
        source_module_path=mod_path or leanmod.module_to_path(module),
        source_item_title=src_item.title,
    ), []


def resolve_imports(
    consumer_repo: Repo,
    item: Item,
    commit: str | None = None,
    store_dirs: Iterable[Path] = (),
    guards: frozenset[str] = frozenset(),
) -> tuple[list[ResolvedImport], list[str]]:
    """Resolve every `[[imports]]` entry on `item` offline against `consumer_repo`'s manifest at `commit` (None = worktree)."""
    if not item.imports:
        return [], []
    raw_manifest = read_manifest(consumer_repo, commit=commit)
    manifest = resolve_manifest_packages(raw_manifest, where=manifest_path(consumer_repo))
    if manifest.problems:
        return [], list(manifest.problems)
    resolved: list[ResolvedImport] = []
    errors: list[str] = []
    for imp in item.imports:
        res, errs = _resolve_one(consumer_repo, imp, manifest, store_dirs, guards)
        if res is not None:
            resolved.append(res)
        errors.extend(errs)
    return resolved, errors


def _consumer_lean_modules(repo: Repo, item: Item, read_fn: Callable[[str], bytes | None]) -> set[str]:
    """External (and candidate) module names imported by `item`'s Lean target, solution and in-project closure."""
    if not item.lean:
        return set()
    roots = repo.config.lean.roots
    project = posixpath.normpath(repo.config.lean.project.strip() or ".")
    prel = (lambda p: p) if project == "." else (lambda p: f"{project}/{p}")

    imported: set[str] = set()
    seeds: list[str] = []
    for key in ("imports",):
        raw = item.lean.get(key)
        if isinstance(raw, list):
            for mod in raw:
                if isinstance(mod, str) and leanmod.is_valid_module_name(mod):
                    imported.add(mod)
                    seeds.append(mod)
    sol = item.lean.get("solution")
    if isinstance(sol, str) and leanmod.is_valid_module_name(sol):
        imported.add(sol)
        seeds.append(sol)
    target = item.lean.get("target")
    if isinstance(target, str) and target.strip():
        raw_target = read_fn(target)
        if raw_target is not None:
            try:
                hdr = leanmod.parse_header(leanmod.decode(raw_target, target))
                imported.update(hdr.modules)
                seeds.extend(hdr.modules)
            except leanmod.LeanModError:
                pass
    try:
        cl = leanmod.closure([m for m in seeds if leanmod.in_roots(m, roots)], roots, lambda p: read_fn(prel(p)))
        for data in cl.modules.values():
            hdr = leanmod.parse_header(leanmod.decode(data, "closure"))
            imported.update(hdr.modules)
    except leanmod.LeanModError:
        pass
    return imported


def check_consumer_lean_usage(
    repo: Repo,
    item: Item,
    commit: str | None = None,
    store_dirs: Iterable[Path] = (),
    guards: frozenset[str] = frozenset(),
) -> list[str]:
    """Detect unused `[[imports]]` entries and custom-package Lean imports lacking `[[imports]]` lineage."""
    problems: list[str] = []

    def read_fn(rel_path: str) -> bytes | None:
        if commit is not None:
            return gitref.show(repo.root, commit, rel_path)
        file = repo.root / rel_path
        return file.read_bytes() if file.is_file() else None

    used_modules = _consumer_lean_modules(repo, item, read_fn)
    declared_modules = {imp["module"] for imp in item.imports}

    if _enabled(guards, "unused_import") and item.imports:
        if not item.lean or not (item.lean.get("target") or item.lean.get("solution")):
            problems.append("unused import: item declares [[imports]] without a [lean] target or solution")
        else:
            for imp in item.imports:
                if imp["module"] not in used_modules:
                    problems.append(
                        f"unused import: [[imports]] declares module '{imp['module']}', "
                        "which is not imported by this item's Lean target, [lean].solution or [lean].imports"
                    )

    if _enabled(guards, "dependency_receipt"):
        roots = repo.config.lean.roots
        external = sorted(m for m in used_modules if not leanmod.in_roots(m, roots) and not leanmod.is_reserved(m))
        if external:
            manifest = resolve_manifest_packages(read_manifest(repo, commit=commit), where=manifest_path(repo))
            # Check non-official packages in manifest to see which external modules they provide.
            custom_pkgs = [
                pkg for pkg in manifest.packages.values()
                if not packages.trusted_package({"name": pkg.name, "type": pkg.kind, "url": pkg.url}, None)
            ]
            for mod in external:
                if mod in declared_modules:
                    continue
                providers: list[str] = []
                for pkg in custom_pkgs:
                    pkg_root, pkg_rev, err = locate_package_git(
                        repo, pkg, manifest.packages_dir, store_dirs, enforce_pin=False
                    )
                    if err is None and pkg_root is not None and pkg_rev is not None:
                        p, _ = read_package_module(pkg_root, pkg_rev, pkg.sub_dir, mod)
                        if p is not None:
                            providers.append(pkg.name)
                if providers:
                    problems.append(
                        f"dependency without receipt: Lean code imports module '{mod}' from package(s) "
                        f"{', '.join(providers)} without a matching [[imports]] entry"
                    )
    return problems


def unpropagated_constraints(item: Item, resolved: list[ResolvedImport]) -> list[str]:
    """Source limits or assumptions absent from `item.limits` / `item.assumptions` (notice of possible weakening)."""
    if not resolved:
        return []
    consumer_text = "\n".join([item.statement or "", *item.limits, *item.assumptions]).casefold()
    notes: list[str] = []
    for res in resolved:
        for kind, vals in (("limit", res.source_limits), ("assumption", res.source_assumptions)):
            for val in vals:
                if val.casefold() not in consumer_text:
                    notes.append(
                        f"imported {res.package}/{res.item} states {kind} not carried in consumer limits/assumptions: {val}"
                    )
    return notes


def stale_imports(
    repo: Repo,
    receipt: dict[str, Any],
    item: Item,
    admitted: bool = False,
) -> list[str]:
    """Freshness check for receipts that record `inputs.imports`.

    Upstream branches moving A -> B do NOT stale a receipt whose consumer `lake-manifest.json`
    and `item.imports` keep pin A. Changing the consumer manifest pin (`url`, `subDir`, `rev`)
    or `item.imports` DOES stale the receipt.
    """
    recorded = receipt.get("inputs", {}).get("imports")
    if not recorded:
        return []
    stale: list[str] = []
    commit = repo.trusted_commit if admitted else None
    raw_manifest = read_manifest(repo, commit=commit)
    manifest = resolve_manifest_packages(raw_manifest, where=manifest_path(repo))
    by_key = {(e.get("package"), e.get("item"), e.get("module")): e for e in recorded if isinstance(e, dict)}
    for imp in item.imports:
        key = (imp["package"], imp["item"], imp["module"])
        rec_entry = by_key.get(key)
        if rec_entry is None:
            stale.append(f"imports[{imp['package']}/{imp['item']}] (added or changed)")
            continue
        pkg = manifest.packages.get(imp["package"])
        rec_manifest = rec_entry.get("manifest") or {}
        if pkg is None:
            stale.append(f"{manifest_path(repo)} (missing package '{imp['package']}')")
        elif (pkg.url, pkg.sub_dir, pkg.rev) != (
            rec_manifest.get("url"), rec_manifest.get("subDir"), rec_manifest.get("rev")
        ):
            stale.append(
                f"{manifest_path(repo)}#{imp['package']} "
                f"(pinned commit/url changed: {str(rec_manifest.get('rev'))[:12]} -> {pkg.rev[:12]})"
            )
    return stale


def discover_local_package_locators(
    repo: Repo,
    store_dirs: Iterable[Path] = (),
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Collect read-only index locators from resolved offline manifest packages (N5).

    Returns `(source_items, source_decls)`. Strictly locator metadata only — never proof or fidelity evidence.
    """
    raw_manifest = read_manifest(repo, commit=None) or read_manifest(repo, commit=repo.trusted_commit)
    if raw_manifest is None:
        return [], []
    manifest = resolve_manifest_packages(raw_manifest, where=manifest_path(repo))
    item_rows: list[dict[str, Any]] = []
    decl_rows: list[dict[str, Any]] = []
    for pkg in sorted(manifest.packages.values(), key=lambda p: p.name):
        if pkg.name in packages.DEFAULT_TRUSTED:
            continue
        pkg_root, pkg_rev, err = locate_package_git(
            repo, pkg, manifest.packages_dir, store_dirs, enforce_pin=True
        )
        if err is not None or pkg_root is None or pkg_rev is None:
            continue
        try:
            snap = SnapshotRepo.open_at(pkg_root, pkg_rev, pkg.sub_dir)
        except ImportResolutionError:
            continue
        items, _ = snap.trusted_items()
        for src_item in sorted(items.values(), key=lambda i: i.id):
            receipt_locs = sorted(
                r.path for r in snap.receipts(src_item.id)
                if not r.problems and r.data.get("assurance") == "protected" and r.data.get("verdict") == "pass"
            )
            review_locs = sorted(
                r.path for r in snap.reviews(src_item.id)
                if not r.problems and r.data.get("kind") == "fidelity" and r.data.get("verdict") == "faithful"
            )
            item_rows.append({
                "source": f"package:{pkg.name}@{pkg_rev[:12]}",
                "package": pkg.name,
                "rev": pkg_rev,
                "id": src_item.id,
                "kind": src_item.kind,
                "title": src_item.title,
                "path": f"{pkg.name}:{src_item.path}",
                "revision": src_item.revision,
                "statement": src_item.statement or "",
                "limits": "\n".join(src_item.limits),
                "assumptions": "\n".join(src_item.assumptions),
                "evidence_locator": receipt_locs[-1] if receipt_locs else "",
                "review_locator": review_locs[-1] if review_locs else "",
            })
        if pkg.sub_dir:
            pkg_files = gitref.ls_files(pkg_root, pkg_rev, pkg.sub_dir)
        else:
            raw_tree = gitref.git(pkg_root, "ls-tree", "-r", "--name-only", "-z", pkg_rev).stdout
            pkg_files = [os.fsdecode(p) for p in raw_tree.split(b"\0") if p]
        for path in pkg_files:
            if not path.endswith(".lean"):
                continue
            raw = gitref.show(pkg_root, pkg_rev, path)
            if raw is None:
                continue
            from .index import scan_lean
            text = raw.decode("utf-8", errors="replace")
            for name, kind, line, src in scan_lean(text):
                decl_rows.append({
                    "name": name,
                    "kind": kind,
                    "path": f"{pkg.name}@{pkg_rev[:12]}:{path}",
                    "line": line,
                    "source": src,
                })
    return item_rows, decl_rows


def inspect_item_imports(
    repo: Repo,
    item: Item,
    commit: str | None = None,
    store_dirs: Iterable[Path] = (),
) -> tuple[list[dict[str, Any]], list[str], list[str]]:
    """Resolve `item.imports` for display in `vl show` / `vl export catalog` without raising on missing offline stores."""
    if not item.imports:
        return [], [], []
    raw_manifest = read_manifest(repo, commit=commit)
    manifest = resolve_manifest_packages(raw_manifest, where=manifest_path(repo))
    resolved: list[ResolvedImport] = []
    entries: list[dict[str, Any]] = []
    errors: list[str] = list(manifest.problems)
    if manifest.problems:
        for imp in item.imports:
            entries.append({
                **imp,
                "resolved": False,
                "error": "; ".join(manifest.problems),
                "qualification": SOURCE_QUALIFICATION_NOTE,
            })
        return entries, errors, []
    for imp in item.imports:
        res, errs = _resolve_one(repo, imp, manifest, store_dirs, frozenset())
        if res is not None:
            resolved.append(res)
            entries.append({**res.card_entry(), "resolved": True})
        else:
            errors.extend(errs)
            pkg = manifest.packages.get(imp["package"])
            entries.append({
                **imp,
                "resolved": False,
                "manifest": ({"url": pkg.url, "subDir": pkg.sub_dir, "rev": pkg.rev} if pkg else None),
                "error": "; ".join(errs),
                "qualification": SOURCE_QUALIFICATION_NOTE,
            })
    warnings = unpropagated_constraints(item, resolved)
    return entries, errors, warnings
