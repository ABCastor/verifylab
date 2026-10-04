"""Shared helpers for the Lean checker tests: the planted-defect fixture, tool discovery, peak memory.

Where the tools come from, in this order: the COMPARATOR_* variables (COMPARATOR_BIN, COMPARATOR_LANDRUN,
COMPARATOR_LEAN4EXPORT, COMPARATOR_NANODA), then this account's own vl set-up: the [tools] of its machine.toml, then
its tools store for the fixture's toolchain (`vl init --tools`). A built Mathlib for the smoke test: VL_TEST_MATHLIB (a
Lake project whose `.lake/packages` holds a built Mathlib), else a project named under [caches] in that machine.toml.
The account's set-up is read once, at import, before any test points XDG_CONFIG_HOME and XDG_DATA_HOME at folders
of its own; nothing here names a path of one particular machine.
"""

from __future__ import annotations

import glob
import os
import shutil
import subprocess
import threading
import time
import tomllib
from dataclasses import dataclass
from pathlib import Path

from verifylab import gitref, jail, machine
from verifylab.adapters.base import CheckOutcome, CheckRequest
from verifylab.adapters.lean_comparator import TOOLS, LeanComparatorAdapter, toolchain_dir
from verifylab.repo import Repo

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "lean-planted"
CASES = FIXTURE / "cases"
TOOLCHAIN = (FIXTURE / "lean-toolchain").read_text(encoding="utf-8").strip()


def _account_machine() -> machine.Machine | None:
    try:
        return machine.load()
    except machine.MachineError:
        return None


ACCOUNT = _account_machine()
ACCOUNT_STORE = machine.store_root() / machine.toolchain_key(TOOLCHAIN)


def _discover() -> dict[str, Path]:
    """name -> executable for each tool found (see the module docstring for the order)."""
    found = {}
    for name, (var, _) in TOOLS.items():
        for raw in (os.environ.get(var), ACCOUNT.tools.get(name) if ACCOUNT else None, str(ACCOUNT_STORE / name)):
            if raw and Path(raw).is_file() and os.access(raw, os.X_OK):
                found[name] = Path(raw).resolve()
                break
    return found


TOOL_PATHS = _discover()


def _mathlib() -> tuple[Path | None, Path | None]:
    """(packages folder, lake-manifest.json) of a project with a built Mathlib, or (None, None)."""
    projects = [Path(os.environ["VL_TEST_MATHLIB"])] if os.environ.get("VL_TEST_MATHLIB") else []
    caches = {Path(project): Path(lake) for project, lake in (ACCOUNT.caches.items() if ACCOUNT else ())}
    candidates = [(p / ".lake" / "packages", p / "lake-manifest.json") for p in projects]
    candidates += [(lake / "packages", project / "lake-manifest.json") for project, lake in caches.items()]
    for packages, manifest in candidates:
        if (packages / "mathlib").is_dir() and manifest.is_file():
            return packages, manifest
    return None, None


MATHLIB_PACKAGES, MATHLIB_MANIFEST = _mathlib()


def tool_env() -> dict[str, str]:
    """The tools found, as the COMPARATOR_* variables exploratory checks and the Comparator runner read."""
    return {TOOLS[name][0]: str(path) for name, path in TOOL_PATHS.items()}


def lean_unavailable() -> str | None:
    """Why the Lean tests cannot run here, or None."""
    # nanoda too: the fixture keeps [lean] external_kernels on, and a protected check refuses to run without it.
    missing = [name for name in ("comparator", "landrun", "lean4export", "nanoda") if name not in TOOL_PATHS]
    if missing:
        return (f"Comparator tools missing: {missing} (set COMPARATOR_BIN, COMPARATOR_LANDRUN, COMPARATOR_LEAN4EXPORT, "
                "COMPARATOR_NANODA, or `vl init --tools`)")
    if not (toolchain_dir(TOOLCHAIN) / "bin" / "lake").is_file():
        return f"toolchain {TOOLCHAIN} not installed"
    if not jail.available():
        return "bwrap missing"
    return None


@dataclass(frozen=True)
class Case:
    name: str
    description: str
    item: str
    stage: str
    expect: str
    reasons: tuple[str, ...]
    lean_section: str | None
    overlay: Path | None
    trusted_overlay: Path | None = None        # files committed on the trusted ref whatever the stage
    trivial: tuple[str, ...] = ()              # target theorems the triviality probe must flag
    vacuous: tuple[str, ...] = ()              # target theorems the vacuity probe must flag
    clean: tuple[str, ...] = ()                # target theorems neither probe may flag
    lint: str | None = None                    # a command-lint kind the check must report
    guard: str | None = None                   # disabling this guard must make the case go unflagged


def case_names(prefix: str = "") -> list[str]:
    return sorted(p.name for p in CASES.iterdir() if p.is_dir() and p.name.startswith(prefix))


def load_case(name: str) -> Case:
    folder = CASES / name
    text = (folder / "case.toml").read_text(encoding="utf-8")
    meta = tomllib.loads(text)
    lean_section = None
    if "[lean]" in text.splitlines():
        lines = text.splitlines(keepends=True)
        start = next(k for k, line in enumerate(lines) if line.strip() == "[lean]")
        lean_section = "".join(lines[start:]).rstrip() + "\n"
    overlay, trusted = folder / "overlay", folder / "trusted"
    return Case(name, meta["description"], meta["item"], meta.get("stage", "candidate"), meta["expect"],
                tuple(meta.get("reasons", [])), lean_section, overlay if overlay.is_dir() else None,
                trusted if trusted.is_dir() else None, tuple(meta.get("trivial", [])), tuple(meta.get("vacuous", [])),
                tuple(meta.get("clean", [])), meta.get("lint"), meta.get("guard"))


def git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, text=True).stdout


def set_lean_section(root: Path, item_id: str, lean_section: str) -> None:
    path = root / "research" / "items" / f"{item_id}.md"
    text = path.read_text(encoding="utf-8")
    head, sep, rest = text.partition("\n[lean]\n")
    assert sep, f"{path} has no [lean] table to replace"
    _, close, body = rest.partition("\n+++\n")
    path.write_text(head + "\n" + lean_section + "+++\n" + body, encoding="utf-8")


def apply_case(root: Path, case: Case) -> None:
    if case.overlay is not None:
        shutil.copytree(case.overlay, root, dirs_exist_ok=True)
    if case.lean_section is not None:
        set_lean_section(root, case.item, case.lean_section)


def make_repo(tmp_path: Path, case: Case | None = None) -> Path:
    """The fixture committed on branch `trusted`, then the case applied and committed on `candidate`."""
    root = tmp_path / "repo"
    shutil.copytree(FIXTURE, root, ignore=shutil.ignore_patterns("cases", ".lake"))
    if case is not None and case.trusted_overlay is not None:
        shutil.copytree(case.trusted_overlay, root, dirs_exist_ok=True)
    if case is not None and case.stage == "trusted":
        apply_case(root, case)
    git(root, "init", "-q", "-b", "trusted")
    git(root, "config", "vl.trustedRef", "trusted")
    git(root, "config", "user.name", "Test")
    git(root, "config", "user.email", "test@example.com")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "trusted fixture")
    git(root, "checkout", "-q", "-b", "candidate")
    if case is not None and case.stage == "candidate":
        apply_case(root, case)
        git(root, "add", "-A")
        git(root, "commit", "-q", "--allow-empty", "-m", f"candidate {case.name}")
    return root


_SCRATCH_COUNTER = [0]


def request(root: Path, item_id: str, *, assurance: str = "protected", guards=(),
            timeout: float = 900, memory_max: str | None = "12G") -> CheckRequest:
    repo = Repo.open(root)
    _SCRATCH_COUNTER[0] += 1
    scratch = root.parent / f"scratch-{_SCRATCH_COUNTER[0]}"
    return CheckRequest(repo=repo, item=repo.load_item(item_id), assurance=assurance,
                        trusted_commit=gitref.rev_parse(root, repo.config.trusted_ref), scratch=scratch,
                        timeout=timeout, memory_max=memory_max, guards=frozenset(guards))


def case_root(tmp_path: Path, name: str, assurance: str = "protected", guards=()) -> Path:
    return tmp_path / f"{name}-{assurance}-{'-'.join(sorted(guards)) or 'all-guards'}" / "repo"


def run_case(tmp_path: Path, name: str, *, assurance: str = "protected", guards=()) -> CheckOutcome:
    case = load_case(name)
    root = make_repo(case_root(tmp_path, name, assurance, guards).parent, case)
    return LeanComparatorAdapter().check(request(root, case.item, assurance=assurance, guards=guards))


class SharedCases:
    """Each planted case run once per session for each (assurance, disabled guards): tests that only read the
    outcome of the same run share it. A test that changes the environment of the run (a monkeypatch, a variable,
    its own machine.toml) calls run_case instead."""

    def __init__(self, tmp_path_factory):
        self.factory = tmp_path_factory
        self.runs: dict[tuple[str, str, tuple[str, ...]], tuple[CheckOutcome, Path]] = {}

    def run(self, name: str, *, assurance: str = "protected", guards=()) -> tuple[CheckOutcome, Path]:
        """(outcome, the repository it checked)."""
        key = (name, assurance, tuple(sorted(guards)))
        if key not in self.runs:
            base = self.factory.mktemp(f"shared-{name}")
            self.runs[key] = (run_case(base, name, assurance=assurance, guards=guards),
                              case_root(base, name, assurance, guards))
        return self.runs[key]

    def __call__(self, name: str, *, assurance: str = "protected", guards=()) -> CheckOutcome:
        return self.run(name, assurance=assurance, guards=guards)[0]


def explain(outcome: CheckOutcome) -> str:
    return f"verdict={outcome.verdict} reasons={outcome.reasons}\n--- log tail ---\n{outcome.log[-3000:]}"


def assert_reasons(outcome: CheckOutcome, expected) -> None:
    joined = " ".join(outcome.reasons).lower()
    for fragment in expected:
        assert fragment.lower() in joined, f"reason {fragment!r} missing\n{explain(outcome)}"


def newer_than(folder: Path, stamp: Path) -> list[str]:
    """Paths under `folder` modified after `stamp` (`find -newer`)."""
    out = subprocess.run(["find", str(folder), "-newer", str(stamp)], capture_output=True, text=True, check=True)
    return out.stdout.splitlines()


def write_stamp(tmp_path: Path) -> Path:
    """A stamp file, after proving on a planted write that `newer_than` can see a change at all."""
    stamp = tmp_path / "stamp"
    stamp.touch()
    time.sleep(1.1)
    canary = tmp_path / "canary"
    canary.mkdir()
    (canary / "written.olean").write_text("x")
    assert str(canary / "written.olean") in newer_than(canary, stamp), "find -newer cannot see a planted write"
    return stamp


class ScopePeakMemory:
    """Peak cgroup memory of the systemd user scopes that run a process whose command line contains
    `marker`. Polls memory.peak while the check runs (a scope disappears when it exits)."""

    def __init__(self, marker: str, interval: float = 0.2):
        self.marker = marker
        self.interval = interval
        self.peak = 0
        self.scopes: set[str] = set()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._poll, daemon=True)

    def _poll(self) -> None:
        base = f"/sys/fs/cgroup/user.slice/user-{os.getuid()}.slice/user@{os.getuid()}.service"
        while not self._stop.is_set():
            for scope in glob.glob(f"{base}/vl.slice/run-*.scope") + glob.glob(f"{base}/app.slice/run-*.scope"):
                try:
                    pids = Path(scope, "cgroup.procs").read_text().split()
                    if any(self.marker in Path(f"/proc/{pid}/cmdline").read_bytes().decode(errors="replace")
                           for pid in pids):
                        self.scopes.add(scope)
                    if scope in self.scopes:
                        self.peak = max(self.peak, int(Path(scope, "memory.peak").read_text()))
                except (OSError, ValueError):
                    continue
            time.sleep(self.interval)

    def __enter__(self) -> "ScopePeakMemory":
        self._thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self._stop.set()
        self._thread.join()
