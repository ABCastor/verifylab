"""Project configuration: `research/vl.toml` at the root of the research repository.

The trusted ref is not in that file: a candidate branch could commit a pointer at itself. It is the repository's
own git config `vl.trustedRef` (written by `vl init`; `main` when unset), and only an explicit `--trusted-ref`
overrides it. The configuration itself is read from the trusted ref, never from the worktree: a lane that edits
`research/vl.toml` changes nothing until the edit is integrated (`vl validate` reports the difference).
"""

from __future__ import annotations

import hashlib
import json
import math
import posixpath
import tomllib
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from . import gitref, machine

CONFIG_NAME = "vl.toml"
DEFAULT_RESEARCH_DIR = "research"
CONFIG_PATH = f"{DEFAULT_RESEARCH_DIR}/{CONFIG_NAME}"   # always here, whatever research_dir says
# Receipt inputs: only the part of the configuration a verdict depends on. A check records RULES_INPUT: permitted
# axioms, external kernels, the Lean project and the roots that tell project modules (read from the trusted ref) from
# dependencies; never a machine-local path. A receipt that recorded an older input keeps that input's basis:
# VERDICT_INPUT is the same plus the `packages` and `cache` paths, CHECK_INPUT that without the roots (its roots are
# compared through its trusted commit).
RULES_INPUT = f"{CONFIG_PATH}#verdict-rules"
VERDICT_INPUT = f"{CONFIG_PATH}#verdict"
CHECK_INPUT = f"{CONFIG_PATH}#check"
CONFIG_INPUTS = (RULES_INPUT, VERDICT_INPUT, CHECK_INPUT)
TRUSTED_REF_KEY = "vl.trustedRef"     # repository-local git config, never a committed file
DEFAULT_TRUSTED_REF = "main"
DEFAULT_AXIOMS = ("propext", "Quot.sound", "Classical.choice")
# Statement probes: heartbeats per attempt (thousands, as Lean's maxHeartbeats; deterministic) and seconds for
# the whole probe run. The floors keep a configuration from switching the probes off by starving them.
PROBE_HEARTBEATS, PROBE_HEARTBEATS_MIN = 5000, 1000
PROBE_TIMEOUT, PROBE_TIMEOUT_MIN = 300.0, 10.0


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class LeanConfig:
    project: str = "."
    roots: tuple[str, ...] = ()
    permitted_axioms: tuple[str, ...] = DEFAULT_AXIOMS
    external_kernels: bool = True
    # A built `.lake` used read-only by lanes and checks (its `packages/` holds Mathlib).
    # Lets a research checkout work without its own multi-GB build cache.
    cache: str | None = None
    packages: str | None = None


@dataclass(frozen=True)
class Config:
    root: Path
    name: str
    trusted_ref: str = DEFAULT_TRUSTED_REF
    research_dir: str = DEFAULT_RESEARCH_DIR
    lean: LeanConfig = field(default_factory=LeanConfig)
    tools: dict[str, str] = field(default_factory=dict)
    lanes_dir: str = "../.vl-lanes"
    max_parallel: int = 3
    memory_max: str = "16G"
    probe_heartbeats: int = PROBE_HEARTBEATS
    probe_timeout: float = PROBE_TIMEOUT
    raw: dict[str, Any] = field(default_factory=dict)
    legacy_trusted_ref: str | None = None          # [project] trusted_ref: deprecated and ignored
    legacy_memory_total: str | None = None         # [check] memory_total: deprecated and ignored (machine.toml)
    trusted_ref_source: str = "default"            # "git config vl.trustedRef", "--trusted-ref" or "default"
    trusted_commit: str | None = None              # what the trusted ref resolved to, once per command; None: nothing
    source: str = "worktree"                       # where this file was read: "trusted ref" or "worktree"
    warnings: tuple[tuple[str, str], ...] = ()     # (where, message)

    @property
    def research_path(self) -> Path:
        return self.root / self.research_dir

    def rel(self, *parts: str) -> str:
        return "/".join([self.research_dir, *parts])


def find_root(start: Path) -> Path:
    for candidate in [start, *start.parents]:
        if (candidate / DEFAULT_RESEARCH_DIR / CONFIG_NAME).is_file():
            return candidate
    raise ConfigError(f"no {DEFAULT_RESEARCH_DIR}/{CONFIG_NAME} above {start}; run `vl init` first")


def _strs(value: Any, key: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise ConfigError(f"{key} must be a list of strings")
    return tuple(value)


def _table(raw: dict[str, Any], name: str) -> dict[str, Any]:
    value = raw.get(name, {})
    if not isinstance(value, dict):
        raise ConfigError(f"[{name}] must be a table")
    return value


def _text(table: dict[str, Any], key: str, default: str | None, where: str) -> str | None:
    """A string value. Where the default is None (an optional path), an empty string means unset."""
    value = table.get(key, default)
    if value is not None and not isinstance(value, str):
        raise ConfigError(f"{where} {key} must be a string")
    return None if default is None and not value else value


def _flag(table: dict[str, Any], key: str, default: bool, where: str) -> bool:
    value = table.get(key, default)
    if not isinstance(value, bool):
        raise ConfigError(f"{where} {key} must be true or false")
    return value


def _number(table: dict[str, Any], key: str, default: float, minimum: float, kind: type, where: str = "[check]") -> Any:
    value = table.get(key, default)
    if (isinstance(value, bool) or not isinstance(value, (int, float)) or (kind is int and not isinstance(value, int))
            or not math.isfinite(value)):
        raise ConfigError(f"{where} {key} must be {'an integer' if kind is int else 'a finite number'}")
    if value < minimum:
        raise ConfigError(f"{where} {key} must be at least {minimum:g}")
    return kind(value)


def parse_config(root: Path, text: str, trusted_ref: str = DEFAULT_TRUSTED_REF) -> Config:
    try:
        raw = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{CONFIG_NAME}: {exc}") from exc
    project = _table(raw, "project")
    legacy = project.get("trusted_ref")
    if legacy is not None and not isinstance(legacy, str):
        raise ConfigError("[project] trusted_ref (deprecated) must be a string")
    lean_raw = _table(raw, "lean")
    lean = LeanConfig(
        project=_text(lean_raw, "project", ".", "[lean]"),
        roots=_strs(lean_raw.get("roots", []), "[lean] roots"),
        permitted_axioms=_strs(lean_raw.get("permitted_axioms", list(DEFAULT_AXIOMS)), "[lean] permitted_axioms"),
        external_kernels=_flag(lean_raw, "external_kernels", True, "[lean]"),
        cache=_text(lean_raw, "cache", None, "[lean]"),
        packages=_text(lean_raw, "packages", None, "[lean]"),
    )
    tools = _table(raw, "tools")
    if not all(isinstance(v, str) for v in tools.values()):
        raise ConfigError("[tools] values must be paths")
    lanes = _table(raw, "lanes")
    check = _table(raw, "check")
    return Config(
        root=root,
        name=_text(project, "name", root.name, "[project]"),
        trusted_ref=trusted_ref,
        research_dir=_text(project, "research_dir", DEFAULT_RESEARCH_DIR, "[project]"),
        lean=lean,
        tools=dict(tools),
        lanes_dir=_text(lanes, "dir", "../.vl-lanes", "[lanes]"),
        max_parallel=_number(lanes, "max_parallel", 3, 1, int, "[lanes]"),
        memory_max=_text(check, "memory_max", "16G", "[check]"),
        probe_heartbeats=_number(check, "probe_heartbeats", PROBE_HEARTBEATS, PROBE_HEARTBEATS_MIN, int),
        probe_timeout=_number(check, "probe_timeout", PROBE_TIMEOUT, PROBE_TIMEOUT_MIN, float),
        raw=raw,
        legacy_trusted_ref=legacy,
        legacy_memory_total=_text(check, "memory_total", None, "[check]"),
    )


def _rules(config: Config) -> dict[str, Any]:
    lean = config.lean
    return {"permitted_axioms": sorted(set(lean.permitted_axioms)), "external_kernels": lean.external_kernels,
            "project": posixpath.normpath(lean.project.strip() or "."), "roots": sorted(set(lean.roots))}


def _digest(basis: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(basis, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def rules_digest(config: Config) -> str:
    """Digest of what decides a Lean verdict (RULES_INPUT): permitted axioms, external kernels, the Lean project and
    the roots. Paths of this machine (`packages`, `cache`) are left out: what Lake builds against is bound by
    `lake-manifest.json` and `lean-toolchain`, each a trusted input of its own; so are resource limits, tool paths and
    lanes, so editing them does not stale receipts (the modules a check reads are recorded one by one)."""
    return _digest({"lean": _rules(config)})


def verdict_digest(config: Config) -> str:
    """VERDICT_INPUT, as receipts that recorded it computed it: the rules plus the `packages` and `cache` paths."""
    lean = config.lean
    return _digest({"lean": {**_rules(config), "packages": lean.packages, "cache": lean.cache}})


def check_digest(config: Config) -> str:
    """CHECK_INPUT, as receipts that recorded it computed it: VERDICT_INPUT without the roots."""
    basis = {**_rules(config), "packages": config.lean.packages, "cache": config.lean.cache}
    del basis["roots"]
    return _digest({"lean": basis})


def config_digest(input_name: str, config: Config) -> str:
    return {RULES_INPUT: rules_digest, VERDICT_INPUT: verdict_digest, CHECK_INPUT: check_digest}[input_name](config)


def resolve_trusted_ref(root: Path, explicit: str | None = None) -> tuple[str, str]:
    """(ref, where it came from): an explicit --trusted-ref, else the repository's own `git config vl.trustedRef`
    (its .git/config only: no environment, global or per-worktree value), else `main`."""
    configured = gitref.local_config(root, TRUSTED_REF_KEY)
    ref, source = ((explicit, "--trusted-ref") if explicit else (configured, f"git config {TRUSTED_REF_KEY}")
                   if configured else (DEFAULT_TRUSTED_REF, "default"))
    if ref.startswith("-"):
        raise ConfigError(f"trusted ref '{ref}' (from {source}) starts with '-': refused, git would read it as an option")
    return ref, source


def load_config(start: Path, trusted_ref: str | None = None) -> Config:
    """The configuration as committed on the trusted ref; the worktree's copy only while the trusted ref has none."""
    root = find_root(start.resolve())
    ref, ref_source = resolve_trusted_ref(root, trusted_ref)
    warnings: list[tuple[str, str]] = []
    if ref_source == "default":
        warnings.append((f"git config {TRUSTED_REF_KEY}",
                         f"{TRUSTED_REF_KEY} is not set; using '{DEFAULT_TRUSTED_REF}'. Set the integrator's branch with "
                         f"`git config {TRUSTED_REF_KEY} <branch>` (vl init does)"))
    worktree = (root / CONFIG_PATH).read_bytes()
    # The trusted ref is resolved once: every trusted read of this command is of that one commit. A ref that does
    # not resolve admits nothing (vl validate reports it).
    commit = gitref.rev_parse(root, ref) if gitref.ref_exists(root, ref) else None
    trusted = gitref.show(root, commit, CONFIG_PATH) if commit else None
    config = None
    if trusted is None:
        warnings.append((CONFIG_PATH, f"{CONFIG_PATH} is not on the trusted ref '{ref}': the worktree's copy is used "
                                      "until it is committed there"))
    else:
        try:
            config, source = parse_config(root, trusted.decode("utf-8"), trusted_ref=ref), "trusted ref"
        except (ConfigError, UnicodeDecodeError) as exc:
            warnings.append((CONFIG_PATH, f"{CONFIG_PATH} on the trusted ref '{ref}' does not parse ({exc}): the "
                                          "worktree's copy is used, and receipts bound to the trusted one are stale"))
        if config is not None and worktree != trusted:
            legacy = _legacy_trusted_ref(root, worktree)
            warnings.append((CONFIG_PATH, f"the worktree's {CONFIG_PATH} differs from the one on the trusted ref '{ref}'; "
                                          "vl uses the trusted one for every check, status and lane until the change is "
                                          "integrated there"
                                          + (f" (its [project] trusted_ref = '{legacy}' is ignored)" if legacy else "")))
    if config is None:
        try:
            config, source = parse_config(root, worktree.decode("utf-8"), trusted_ref=ref), "worktree"
        except UnicodeDecodeError as exc:
            raise ConfigError(f"{CONFIG_PATH}: not UTF-8") from exc
    if config.legacy_trusted_ref is not None:
        hint = (f"; to keep trusting '{config.legacy_trusted_ref}', run `git config {TRUSTED_REF_KEY} "
                f"{config.legacy_trusted_ref}`" if config.legacy_trusted_ref != ref else "")
        warnings.append((CONFIG_PATH, f"[project] trusted_ref = '{config.legacy_trusted_ref}' is deprecated and ignored: "
                                      f"a file a branch can commit never chooses the trusted ref, which is '{ref}' "
                                      f"(from {ref_source}){hint}"))
    if config.legacy_memory_total is not None:
        warnings.append((CONFIG_PATH, f"[check] memory_total = '{config.legacy_memory_total}' is deprecated and "
                                      "ignored: the cap of all vl runs together belongs to the machine, so that two "
                                      f"projects never set different caps; set it in {machine.config_path()} [check]"))
    return replace(config, trusted_ref_source=ref_source, trusted_commit=commit, source=source,
                   warnings=tuple(warnings))


def _legacy_trusted_ref(root: Path, data: bytes) -> str | None:
    try:
        return parse_config(root, data.decode("utf-8")).legacy_trusted_ref
    except (ConfigError, UnicodeDecodeError):
        return None


def load_worktree_config(root: Path, trusted: Config) -> Config:
    """The worktree's own configuration, for exploratory checks only (they never count)."""
    try:
        return parse_config(root, (root / CONFIG_PATH).read_text(encoding="utf-8"), trusted_ref=trusted.trusted_ref)
    except (OSError, UnicodeDecodeError, ConfigError):
        return trusted
