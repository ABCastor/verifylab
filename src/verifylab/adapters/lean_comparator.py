"""Lean checker: Comparator on a fresh check project whose trusted side cannot be touched by the candidate.

Comparator (leanprover/comparator) builds a Challenge module and a Solution module, exports both, and
checks that (1) every listed theorem has the same statement in both, (2) every declaration the
statements use is identical in both environments, (3) the solution's proofs use only permitted axioms,
(4) the kernel (and optionally nanoda) accepts the solution. It assumes the Challenge's whole import
closure and the lakefile are trusted, and that nothing adversarial was compiled before it ran.

In a research repository the candidate controls its worktree, including the definitions a target
statement mentions. So the check project is built like this:

* Protected mode. The target file and every in-project module in its import closure (imports under
  `[lean] roots`) are read from the trusted commit with `git show`, never from the worktree. The target
  becomes module `VLChallenge`. Imports outside the roots (Init, Std, Mathlib, ...) are taken as built.
* Shared mode, the default: when the worktree's copy of every module in that closure is byte-identical to the
  trusted one, challenge and solution import those modules under their own names.
* Renamed mode, when the candidate changed a module of the closure: the trusted copies are written under a
  renamed prefix (module `Fixture.Defs` becomes `VLTrusted.Fixture.Defs`, file `VLTrusted/Fixture/Defs.lean`)
  and every in-project `import` inside them is rewritten the same way. Declaration names stay identical on
  both sides (namespaces are not renamed) while their sources differ, so Comparator's declaration comparison
  rejects a candidate that altered a definition used by a statement. Declarations whose names embed the module
  name (`private` declarations, `_auxLemma`s) get different names on the two sides and FAIL CLOSED (a mismatch,
  never a false pass).
* The candidate side is the worktree's in-project modules, copied unchanged, plus `VLSolution`: either
  generated (the target text with each listed theorem's `sorry` replaced by its proof term, target
  imports kept as candidate modules, plus the item's `imports`) or a one-line module importing the
  item's `solution` module.
* The project files that shape the build (`lean-toolchain`, `lake-manifest.json`, the lakefile's
  `leanOptions`) and `research/vl.toml` (roots, permitted axioms) come from the trusted commit in protected
  mode; the lakefile itself is generated. The receipt binds to the configuration only through
  `research/vl.toml#verdict-rules`, the digest of what decides a verdict (`config.rules_digest`): never a path of
  this machine, such as where the Lake packages or the build cache are.
* The programs that judge (Comparator, lean4export, landrun, nanoda, the Lean toolchain) come, in protected mode,
  from the machine configuration (`verifylab.machine`): machine.toml, then the tools store of the toolchain, then,
  deprecated, the trusted `[tools]` paths. Never from `COMPARATOR_*`, `ELAN_HOME` or `PATH`, never from a relative
  path, and only when the `REVISIONS` file next to the tool pins its sha256 (`vl init --tools` copies and pins).
  Exploratory checks also take unpinned tools, and say so in their outcome.
* Exploratory mode does all of the above from the worktree, in the same jail: weak by construction. A candidate
  that weakens its own copy of the target passes there, which is why exploratory receipts never count as verified.

Isolation: Comparator runs via `lake env comparator config.json` inside `verifylab.jail.Jail` (bwrap,
no network, scrubbed environment). Protected checks use an outer systemd service enforcing Comparator's
`RestrictAddressFamilies=~AF_UNIX`, verified before each exec, with the same memory and task caps.
Comparator's own landrun (Landlock)
sandbox runs nested inside bwrap; before every check a probe proves Landlock really denies a write that
bwrap allows, because landrun's `--best-effort` would otherwise degrade to no sandbox silently.
Its output is read while it runs (line-buffered with `stdbuf -oL`; `env`, `stdbuf`, `sh` and `git` are started by
absolute path from the machine's launchers, never from PATH), so the receipt records where the time
went: Comparator's phases, Lake's per-module build times and job counts, the memory unit's peak.

Statement probes (`lean_probes`): before Comparator, the challenge is built with the same `lake build` under
the same landrun sandbox Comparator uses, and its build products are copied to a separate probe workspace;
Comparator then finds the challenge built. After a pass, the probes run on that copy, never on the check
project, whose `.lake` the candidate's build may have rewritten. They are recorded in `checked.probes`.
Command lints (`leanlint`) over the Lean files the candidate changed or added are recorded in `checked.lints`.

Lake packages (Mathlib) are reused from an existing packages directory mounted READ-ONLY at its own
path; the generated lakefile and manifest point `packagesDir` at it. A build that tries to write into it
(a rebuild of Mathlib, a re-clone) fails with a read-only file-system error instead of modifying it, and
before Comparator runs, Lake's own `lake build --no-build` on the dependency modules the trusted side
imports (exit 3: a rebuild would be needed) stops the check with "shared build cache is incomplete".
The packages directory itself is trusted as found: point machine.toml `[caches]` at a build candidates cannot
write when that matters.
"""

from __future__ import annotations

import json
import os
import posixpath
import re
import secrets
import shutil
import subprocess
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable, Sequence

from .. import candidate_exec, gitref, jail, leanlint, leanmod
from ..leanmod import lakefile_options
from . import lean_probes
from ..config import CONFIG_PATH, RULES_INPUT, Config, ConfigError, parse_config, rules_digest
from ..machine import REVISIONS, Machine, MachineError, file_sha256 as machine_file_sha256, load as load_machine
from ..machine import read_revisions, toolchain_key
from ..records import ASSURANCE, Item, RecordError, parse_item, sha256_hex
from .base import CheckOutcome, CheckRequest

NAME = "lean-comparator"
# Guards a test may disable to prove each one matters. The first three decide the verdict; the others are the
# statement probes, the copy of the challenge build they read, and the command lints, which never decide it.
VERDICT_GUARDS = frozenset({"trusted_target", "trusted_definitions", "permitted_axioms"})
PROBE_GUARDS = frozenset({"probe_trivial", "probe_vacuous", "probe_snapshot", "lints"})
GUARDS = VERDICT_GUARDS | PROBE_GUARDS
CHALLENGE = "VLChallenge"
SOLUTION = "VLSolution"
TRUSTED_PREFIX = "VLTrusted"
NANODA_KERNEL = "nanoda"   # the external kernel's name in Comparator's config: it must contain "noda"
PROJECT_FILES = ("lean-toolchain", "lake-manifest.json", "lakefile.toml", "lakefile.lean")
# tool -> (env var, binary name on PATH). The comparator binary itself has no upstream env var.
TOOLS = {
    "comparator": ("COMPARATOR_BIN", "comparator"),
    "landrun": ("COMPARATOR_LANDRUN", "landrun"),
    "lean4export": ("COMPARATOR_LEAN4EXPORT", "lean4export"),
    "nanoda": ("COMPARATOR_NANODA", "nanoda_bin"),
}
_SPEC_KEYS = {"target", "theorems", "proofs", "imports", "solution", "witnesses"}
_MAX_AXIOM_RERUNS = 12
MAX_SOURCE_BYTES = 64 * 1024 * 1024   # a Lean file, project file or target the check reads from the worktree


class _Stop(Exception):
    def __init__(self, verdict: str, *reasons: str):
        super().__init__(verdict, reasons)
        self.verdict = verdict
        self.reasons = list(reasons)


# The [lean] table ------------------------------------------------------------------------------

@dataclass(frozen=True)
class LeanSpec:
    target: str
    theorems: tuple[str, ...]
    proofs: dict[str, str] | None
    imports: tuple[str, ...]
    solution: str | None
    witnesses: tuple[str, ...] = ()   # target theorems that instantiate the hypotheses of the others


def witness_problems(table: dict[str, Any], theorems: tuple[str, ...]) -> list[str]:
    """`[lean] witnesses`: a list of target theorems (each also in `theorems`, so it is checked) whose statements
    show that the hypotheses of the other target theorems can hold together."""
    if "witnesses" not in table:
        return []
    raw = table["witnesses"]
    if not isinstance(raw, list) or not all(isinstance(w, str) and leanmod.is_valid_decl_name(w) for w in raw):
        return ["[lean] witnesses must be a list of theorem names"]
    problems = [f"[lean] witnesses names '{w}', which is not in 'theorems'" for w in raw if w not in theorems]
    if len(set(raw)) != len(raw):
        problems.append("[lean] witnesses lists a name twice")
    return problems


def _target_and_theorems(table: dict[str, Any], targets_dir: str, problems: list[str]) -> tuple[str, tuple[str, ...]]:
    target = table.get("target")
    if not isinstance(target, str) or not target:
        problems.append("[lean] target must be a path string")
        target = ""
    elif (target.startswith("/") or "\\" in target or posixpath.normpath(target) != target
          or not target.startswith(targets_dir + "/") or not target.endswith(".lean")):
        problems.append(f"[lean] target '{target}' must be a normalized .lean path under {targets_dir}/")
    theorems = table.get("theorems")
    if not isinstance(theorems, list) or not theorems:
        problems.append("[lean] theorems must be a non-empty list of theorem names")
        theorems = []
    else:
        bad = [t for t in theorems if not isinstance(t, str) or not leanmod.is_valid_decl_name(t)]
        if bad:
            problems.append(f"[lean] theorems holds invalid names {bad}")
        if len(set(map(str, theorems))) != len(theorems):
            problems.append("[lean] theorems lists a name twice")
    return target, tuple(str(t) for t in theorems)


def parse_spec(table: dict[str, Any], targets_dir: str) -> tuple[LeanSpec | None, list[str]]:
    """Validate an item's `[lean]` table. Returns (spec, []) or (None, problems)."""
    problems: list[str] = []
    if not isinstance(table, dict) or not table:
        return None, ["the item has no [lean] table"]
    unknown = sorted(set(table) - _SPEC_KEYS)
    if unknown:
        problems.append(f"[lean] has unknown key(s) {unknown}")
    target, theorems = _target_and_theorems(table, targets_dir, problems)
    has_proofs, has_solution = "proofs" in table, "solution" in table
    proofs: dict[str, str] | None = None
    solution: str | None = None
    imports: tuple[str, ...] = ()
    if has_proofs == has_solution:
        problems.append("[lean] needs exactly one of 'proofs' (a term per theorem) or 'solution' (a module)")
    if has_proofs:
        raw = table["proofs"]
        if not isinstance(raw, dict) or not all(isinstance(k, str) and isinstance(v, str) and v.strip()
                                                for k, v in raw.items()):
            problems.append("[lean] proofs must map theorem names to non-empty proof terms")
        else:
            proofs = dict(raw)
            missing = [t for t in theorems if t not in proofs]
            extra = [k for k in proofs if k not in theorems]
            if missing:
                problems.append(f"[lean] proofs has no term for {missing}")
            if extra:
                problems.append(f"[lean] proofs names theorems not listed in 'theorems': {extra}")
    if "imports" in table:
        raw = table["imports"]
        if not has_proofs:
            problems.append("[lean] imports is only used together with 'proofs'")
        elif not isinstance(raw, list) or not all(isinstance(m, str) and leanmod.is_valid_module_name(m) for m in raw):
            problems.append("[lean] imports must be a list of plain module names")
        else:
            imports = tuple(raw)
    if has_solution:
        raw = table["solution"]
        if not isinstance(raw, str) or not leanmod.is_valid_module_name(raw):
            problems.append("[lean] solution must be a plain module name")
        else:
            solution = raw
    reserved = [m for m in (*imports, *([solution] if solution else [])) if leanmod.is_reserved(m)]
    if reserved:
        problems.append(f"[lean] names reserved check modules {reserved}")
    problems += witness_problems(table, theorems)
    if problems:
        return None, problems
    return LeanSpec(target, theorems, proofs, imports, solution, tuple(table.get("witnesses", ()))), []


# Tools and toolchain ---------------------------------------------------------------------------

_HASH_CACHE: dict[tuple[str, int, int], str] = {}


def file_sha256(path: Path) -> str:
    """`machine.file_sha256`, remembered per path, size and mtime: tools are hundreds of MB, hashed at every check."""
    st = path.stat()
    key = (str(path), st.st_size, st.st_mtime_ns)
    if key not in _HASH_CACHE:
        _HASH_CACHE[key] = machine_file_sha256(path)
    return _HASH_CACHE[key]


def _executable(path: Path) -> bool:
    return path.is_file() and os.access(path, os.X_OK)


def resolve_tool(name: str, configured: dict[str, str], root: Path, env: dict[str, str] | None = None,
                 machine: Machine | None = None, toolchain: str | None = None) -> Path | None:
    """Exploratory checks (and tests): config `[tools] <name>`, then the COMPARATOR_* environment variable, then the
    machine configuration, then PATH. A relative path is taken relative to `root`."""
    env = os.environ if env is None else env
    var, binary = TOOLS[name]
    machine_paths = [] if machine is None else [machine.tools.get(name)] + (
        [str(machine.store(toolchain) / name)] if toolchain else [])
    for raw in (configured.get(name), env.get(var), *machine_paths, shutil.which(binary, path=env.get("PATH"))):
        if not raw:
            continue
        path = Path(raw).expanduser()
        if not path.is_absolute():
            path = root / path
        if _executable(path):
            return path.resolve()
    return None


@dataclass(frozen=True)
class Tool:
    path: Path              # resolved
    configured: str         # as written where it was found
    source: str             # machine.toml, the tools store, or the project's [tools] (deprecated)
    pin: str | None         # the REVISIONS file that pins it, if any


def pin_of(path: Path) -> tuple[str | None, str | None]:
    """(the REVISIONS file that pins `path`, why it does not): a tool is pinned when the REVISIONS file next to it
    names it with the sha256 of its content."""
    revisions = path.parent / REVISIONS
    pins = read_revisions(path.parent)
    if path.name not in pins:
        return None, f"{path} is not pinned: no sha256 for '{path.name}' in {revisions}"
    actual = file_sha256(path.resolve())
    if actual != pins[path.name]:
        return None, (f"sha256 of {path} is {actual}, not the {pins[path.name]} pinned in {revisions}; refusing to "
                      "run a changed tool")
    return str(revisions), None


def protected_tool(name: str, machine: Machine, toolchain: str, project_tools: dict[str, str],
                   repositories: Sequence[Path] = ()) -> tuple[Tool | None, str | None]:
    """(tool, why it is unusable). Protected checks look only at machine.toml, then the tools store of the toolchain,
    then (deprecated) the trusted `[tools]`; the first place that names the tool decides. Only absolute paths outside
    `repositories` (the checkout and its main worktree, which candidates write), and only a tool pinned by sha256 in
    the REVISIONS file next to it."""
    store = machine.store(toolchain) / name
    for source, raw in ((f"machine.toml [tools] {name}", machine.tools.get(name)),
                        (f"tools store {store.parent}", str(store) if store.exists() else None),
                        (f"research/vl.toml [tools] {name} (deprecated: move it to {machine.path})",
                         project_tools.get(name))):
        if not raw:
            continue
        path = Path(raw)
        if not path.is_absolute():
            return None, (f"{name}: {source} is the relative path '{raw}'; a protected check runs tools only from "
                          "absolute paths outside the repository")
        if not _executable(path):
            return None, f"{name}: {source} names {raw}, which is not an executable file"
        inside = [str(r) for r in repositories if path.resolve().is_relative_to(Path(r).resolve())
                  or path.parent.resolve().is_relative_to(Path(r).resolve())]
        if inside:
            return None, (f"{name}: {source} names {raw}, inside the repository {inside[0]} that candidates write; a "
                          "protected check runs tools only from outside it")
        pin, problem = pin_of(path)
        if problem:
            return None, (f"{name} ({source}): {problem}. A protected check runs only pinned tools: "
                          f"`vl init --tools --{name} {path}` copies it into the tools store and pins it")
        return Tool(path.resolve(), raw, source, pin), None
    return None, None


def toolchain_dir(spec: str, env: dict[str, str] | None = None, elan_home: Path | None = None) -> Path:
    """`leanprover/lean4:v4.34.0-rc2` -> `<elan home>/toolchains/leanprover--lean4---v4.34.0-rc2`. The elan home is
    `elan_home` when given (protected checks: the machine's), else `$ELAN_HOME`, else ~/.elan."""
    env = os.environ if env is None else env
    dirname = toolchain_key(spec)
    home = elan_home or Path(env.get("ELAN_HOME") or Path.home() / ".elan")
    return home / "toolchains" / dirname


# Generated lakefile --------------------------------------------------------------------------------

def _toml_key(key: str) -> str:
    return key if re.fullmatch(r"[A-Za-z0-9_-]+", key) else json.dumps(key)


def _toml_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str):
        return json.dumps(value)
    raise ValueError(f"unsupported Lean option value {value!r}")


def _flatten(options: dict[str, Any], prefix: tuple[str, ...] = ()) -> list[tuple[tuple[str, ...], Any]]:
    flat = []
    for key, value in options.items():
        if isinstance(value, dict):
            flat += _flatten(value, (*prefix, key))
        else:
            flat.append(((*prefix, key), value))
    return flat


def _inline_options(options: dict[str, Any]) -> str:
    parts = [".".join(_toml_key(k) for k in keys) + " = " + _toml_value(v) for keys, v in _flatten(options)]
    return "{ " + ", ".join(parts) + " }"


def render_lakefile(packages_dir: Path | None, requires: list[dict[str, Any]], package_options: dict[str, Any],
                    libs: list[tuple[str, list[str], dict[str, Any]]]) -> str:
    lines = ['name = "vlcheck"', 'version = "0.1.0"', "defaultTargets = []"]
    if packages_dir is not None:
        lines.append(f"packagesDir = {json.dumps(str(packages_dir))}")
    if package_options:
        lines.append(f"leanOptions = {_inline_options(package_options)}")
    for req in requires:
        lines += ["", "[[require]]", f"name = {json.dumps(req['name'])}"]
        if req.get("scope"):
            lines.append(f"scope = {json.dumps(req['scope'])}")
        lines.append(f"git = {json.dumps(req['url'])}")
        lines.append(f"rev = {json.dumps(req.get('inputRev') or req['rev'])}")
        if req.get("subDir"):
            lines.append(f"subDir = {json.dumps(req['subDir'])}")
    for name, roots, options in libs:
        lines += ["", "[[lean_lib]]", f"name = {json.dumps(name)}"]
        if roots:
            lines.append("roots = [" + ", ".join(json.dumps(r) for r in roots) + "]")
        if options:
            lines.append(f"leanOptions = {_inline_options(options)}")
    return "\n".join(lines) + "\n"



def dependency_identity(packages_dir: Path, packages: list[dict[str, Any]], modules: Sequence[str]) -> dict[str, Any]:
    """What the shared build cache held for this check, as git and Lake describe it: each package's checked-out
    revision next to the one the manifest pins, and, for each dependency module the trusted side imports, Lake's
    trace of its build (its `depHash` and the sha256 of the trace file). Recorded in the receipt, not compared at
    status time: it is what a replay of the receipt (the planned deep check) compares against."""
    revisions = {}
    for package in packages:
        name = package.get("name")
        folder = packages_dir / str(name)
        head = gitref.head(folder) if (folder / ".git").exists() else None
        revisions[str(name)] = {"head": head, "manifest": package.get("rev"),
                                "matches": head is not None and head == package.get("rev")}
    traces = {}
    for module in modules:
        rel = Path(leanmod.module_to_path(module)).with_suffix(".trace")
        for package in packages:
            trace = packages_dir / str(package.get("name")) / ".lake" / "build" / "lib" / "lean" / rel
            data, _ = candidate_exec.read_regular_file(trace, 1 << 20) if trace.is_file() else (None, None)
            if data is None:
                continue
            try:
                dep_hash = json.loads(data).get("depHash")
            except (ValueError, AttributeError):
                dep_hash = None
            traces[module] = {"package": package.get("name"), "depHash": dep_hash, "sha256": sha256_hex(data)}
            break
    return {"revisions": revisions, "traces": traces}


# Comparator output --------------------------------------------------------------------------------

_DIAG = re.compile(r"^error: .*\.lean:\d+:\d+:")


def _phase(stdout: str) -> tuple[str, str] | None:
    phase = None
    for line in stdout.splitlines():
        if line.startswith("Building "):
            phase = ("build", line[len("Building "):].strip())
        elif line.startswith("Exporting ") and " from " in line:
            phase = ("export", line.rsplit(" from ", 1)[1].strip())
        elif line.startswith("Running ") and "kernel" in line:
            phase = ("kernel", line.strip())
    return phase


_ANY_ERROR = re.compile(r"^error: (?!build failed)")


def _diagnostics(stdout: str, marker: str, limit: int = 3, pattern: re.Pattern[str] = _DIAG) -> list[str]:
    """Up to `limit` error blocks printed after the `marker` line (Lean diagnostics by default)."""
    lines = stdout.splitlines()
    start = max((k for k, line in enumerate(lines) if line.strip() == marker), default=0)
    blocks: list[str] = []
    k = start
    while k < len(lines) and len(blocks) < limit:
        if pattern.match(lines[k]):
            block = [lines[k]]
            j = k + 1
            while j < len(lines) and j < k + 6 and not lines[j].startswith(("error:", "warning:", "✔", "⚠", "✖", "info:")):
                block.append(lines[j].strip())
                j += 1
            blocks.append(candidate_exec.candidate_text(" ".join(b for b in block if b), 400))
            k = j
        else:
            k += 1
    return blocks


def _axiom_reason(axiom: str) -> str:
    if axiom == "sorryAx":
        return "the solution contains `sorry` (axiom 'sorryAx'), which is not a permitted axiom"
    if "._native.native_decide." in axiom or axiom in ("Lean.ofReduceBool", "Lean.trustCompiler"):
        return (f"the solution relies on `native_decide` / compiled evaluation (axiom '{axiom}'), "
                "which trusts the compiler and is not a permitted axiom")
    return f"the solution uses axiom '{axiom}', which is not in permitted_axioms"


_EXPORTED_BOTH = ("(the kernel's builtins such as Nat, String.mk and Char.ofNat, every listed theorem and every "
                  "permitted axiom)")
_NOT_A_THEOREM = "'{0}' is not a theorem in the solution (e.g. only a definition of type Prop)"
_TOOL = "; a tool or configuration failure, not a verdict on the solution"
# Every message Comparator throws (printed as `uncaught exception: <message>`), from its source at rev 19e111e:
# Comparator/Compare.lean, Comparator/Axioms.lean and Main.lean. Only failures that are certainly the
# candidate's are `fail`; the trusted side, the tools and vl's own configuration give `error`.
EXCEPTIONS: tuple[tuple[re.Pattern[str], str, Callable[..., str]], ...] = tuple(
    (re.compile(pattern), verdict, reason) for pattern, verdict, reason in (
        (r"Illegal axiom detected: '(.+)'", "fail", _axiom_reason),
        (r"Challenge and solution theorem statement do not match: '(.+)'", "fail",
         "the solution's statement of '{0}' differs from the target's (the statement was altered or weakened)".format),
        (r"Challenge and solution constant kind don't match: '(.+)'", "fail", _NOT_A_THEOREM.format),
        (r"Solution constant is not a theorem: '(.+)'", "fail", _NOT_A_THEOREM.format),
        (r"Solution constant is not a definition: '(.+)'", "fail",
         "'{0}' is not a definition in the solution, but the challenge leaves it as a definition hole".format),
        (r"Const does not match between challenge and target '(.+)'", "fail",
         ("declaration '{0}', used by the target statement, differs between the trusted definitions and the "
          "candidate's (a definition the statement depends on was altered; if only a proof inside it changed, "
          "integrate that module first and check again)").format),
        (r"Const not found in solution:? '(.+)'", "fail", "the solution does not declare '{0}'".format),
        (r"Constant not found in solution '(.+)'", "error",
         ("Comparator's axiom check reached '{0}', which the solution's own export does not contain: the export "
          "is inconsistent (a lean4export/Comparator mismatch or a crafted environment), so nothing was verified"
          ).format),
        (r"Const not found in challenge:? '(.+)'", "error",
         ("'{0}' is missing from the challenge's export (the trusted side)" + _TOOL).format),
        (r"Challenge constant is not a definition: '(.+)'", "error",
         ("the challenge's '{0}' is listed as a definition hole but is not a definition" + _TOOL).format),
        (r"Error while interacting with (.+) kernel: (.*)", "error",
         ("the {0} kernel could not be run ({1})" + _TOOL).format),
        (r"Cannot use enable_nanoda and an external kernel list at the same time.*", "error",
         ("Comparator refused the configuration: enable_nanoda together with external_kernels" + _TOOL).format),
        (r"(.+) has an empty command", "error", ("external kernel '{0}' has an empty command" + _TOOL).format),
        (r"Expected config file path as first argument\.", "error",
         ("Comparator was started without its configuration file" + _TOOL).format),
    ))


def accepted_kernels(lines: Sequence[str]) -> list[str]:
    """Kernels that printed their acceptance line: 'lean' for Lean's own, the configured name for the others."""
    names = [m.group(1) for line in lines if (m := re.fullmatch(r"(.+) kernel accepts the solution", line))]
    return ["lean" if name == "Lean default" else name for name in names]


def _after_export(stdout: str, solution: str) -> list[str]:
    """Comparator's own lines: those after it exported the solution. The candidate's build prints before that line
    (its `#eval`, `IO.println` or a crafted message), so nothing it prints can stand for a kernel's verdict."""
    lines = [line.strip() for line in stdout.splitlines()]
    exported = [k for k, line in enumerate(lines) if line.startswith("Exporting ") and line.endswith(f" from {solution}")]
    return lines[exported[-1] + 1:] if exported else []


@dataclass(frozen=True)
class Classified:
    verdict: str
    reason: str
    message: str
    phase: str
    axiom: str | None = None


def classify(stdout: str, stderr: str, returncode: int, theorems: tuple[str, ...],
             kernels: tuple[str, ...] = ("lean",), challenge: str = CHALLENGE, solution: str = SOLUTION) -> Classified:
    """Map Comparator's output to a verdict. Only failures that are certainly the candidate's are
    `fail`; anything else (tool trouble, resource limits, a broken trusted side) is `error`. A pass needs
    every kernel in `kernels` to have printed its acceptance line; a kernel's rejection counts only from Comparator's
    own lines after the solution's export, and only when Comparator exited normally (a non-zero code below 128)."""
    exc = [line[len("uncaught exception: "):].strip() for line in stderr.splitlines()
           if line.startswith("uncaught exception: ")]
    message = exc[-1] if exc else ""
    phase = _phase(stdout)
    phase_text = f"{phase[0]} {phase[1]}" if phase else "start"
    verdict_lines = _after_export(stdout, solution)
    if returncode == 0 and "Your solution is okay!" in verdict_lines:
        missing = [k for k in kernels if k not in accepted_kernels(verdict_lines)]
        if missing:
            return Classified("error", f"Comparator reported success, but kernel(s) {missing} did not report "
                              "accepting the solution", "Your solution is okay!", phase_text)
        return Classified("pass", "Comparator accepted the solution", "Your solution is okay!", phase_text)

    def fail(reason: str, axiom: str | None = None) -> Classified:
        return Classified("fail", reason, message, phase_text, axiom)

    if not isinstance(returncode, int) or not 0 < returncode < 128:
        # Killed by a signal (bubblewrap reports 128 + the signal) or a resource limit: Comparator did not finish,
        # so no line it or the candidate printed, and no exception text, is a verdict on the solution.
        return Classified("error", f"Comparator failed during {phase_text}: it did not exit normally (exit code "
                          f"{returncode}: a signal, the memory cap or the OOM killer){_TOOL}", message, phase_text)
    kernel = next((m for line in verdict_lines if (m := re.fullmatch(r"(.*) kernel rejected the solution", line))), None)
    if (kernel or "Lean default kernel rejects the solution" in verdict_lines
            or any(line.startswith("Quotient post-check rejects") for line in verdict_lines)):
        who = kernel.group(1) if kernel else "Lean default"
        return fail(f"the {who} kernel rejected the solution: {message}")
    for pattern, verdict, reason in EXCEPTIONS:
        if m := pattern.fullmatch(message):
            axiom = m.group(1) if pattern.pattern.startswith("Illegal axiom") else None
            return Classified(verdict, reason(*m.groups()), message, phase_text, axiom)
    if phase == ("build", solution) and message.startswith("Child exited"):
        diags = _diagnostics(stdout, f"Building {solution}")
        if diags:
            return fail("the solution does not compile: " + " | ".join(diags))
        return Classified("error", f"building the solution failed without a Lean diagnostic ({message}); "
                          "resource limit or tool failure", message, phase_text)
    if phase == ("export", solution):
        missing = re.search(r"Constant (\S+) not found in environment", stderr)
        if missing and missing.group(1) in theorems:
            return fail(f"the solution does not declare target theorem '{missing.group(1)}' "
                        "(only helpers or a Prop-valued definition were supplied?)")
        if missing:
            return Classified("error", f"the solution's environment lacks '{missing.group(1)}', which Comparator "
                              f"exports from both sides {_EXPORTED_BOTH}; nothing was verified", message, phase_text)
    if phase == ("export", challenge):
        missing = re.search(r"Constant (\S+) not found in environment", stderr)
        if missing:
            return Classified("error", f"'{missing.group(1)}' does not exist in the challenge environment, but Comparator "
                              f"exports it from both sides {_EXPORTED_BOTH}: an axiom declared only by the candidate "
                              "can never be permitted", message, phase_text)
    if phase == ("build", challenge):
        diags = _diagnostics(stdout + "\n" + stderr, f"Building {challenge}", pattern=_ANY_ERROR)
        return Classified("error", "the challenge (trusted target and definitions) did not build"
                          + (": " + " | ".join(diags) if diags else f" ({message or returncode})"),
                          message, phase_text)
    return Classified("error", f"Comparator failed during {phase_text}: {message or f'exit code {returncode}'}",
                      message, phase_text)


def comparator_config(theorems: tuple[str, ...], axioms: list[str], nanoda: Path | None) -> dict[str, Any]:
    """Comparator's configuration file (Main.lean `Config`). nanoda is registered as an external kernel whose
    name contains "noda", which makes Comparator hand it a nanoda-style configuration (Main.lean
    `isNanodaKernel`); the legacy `enable_nanoda` flag and the COMPARATOR_NANODA variable are not used."""
    config: dict[str, Any] = {"challenge_module": CHALLENGE, "solution_module": SOLUTION,
                              "theorem_names": list(theorems), "permitted_axioms": list(axioms)}
    if nanoda is not None:
        config["external_kernels"] = {NANODA_KERNEL: [str(nanoda)]}
    return config


# The jailed Comparator run: the adapter and the known-answer gate (tests/test_comparator_known.py) share it --------

def comparator_jail(workdir: Path, tc_dir: Path, tools: dict[str, Path], packages_dir: Path | None) -> jail.Jail:
    """bwrap with no network and a scrubbed environment; `workdir` writable, toolchain, tools and Lake packages
    read-only. Comparator reads landrun and lean4export only from its COMPARATOR_* variables."""
    env = jail.filter_env(os.environ, ("LANG", "LC_ALL", "LC_CTYPE"))
    env["PATH"] = f"{tc_dir / 'bin'}:/usr/bin:/bin"
    env["COMPARATOR_LANDRUN"] = str(tools["landrun"])
    env["COMPARATOR_LEAN4EXPORT"] = str(tools["lean4export"])
    read_only = [tc_dir, *tools.values(), *([packages_dir] if packages_dir is not None else [])]
    return jail.Jail(workdir=workdir, read_only=tuple(read_only), env=env)


@dataclass(frozen=True)
class LandlockProbe:
    enforced: bool
    log: str
    landlock: str | None


def landlock_probe(box: jail.Jail, landrun: Path, folder: Path, memory_max: str | None,
                   memory_total: str | None, timeout: float = 60.0) -> LandlockProbe:
    """Prove that landrun's Landlock sandbox is enforced inside the jail: it must deny a write that bwrap
    itself allows. landrun runs with --best-effort, which would otherwise degrade silently."""
    allowed = folder / "allowed"
    allowed.mkdir(parents=True)
    denied = folder / "denied"
    sh = jail.program("sh") or "/bin/sh"
    script = (f'echo ok > "{allowed}/w"; if echo bad > "{denied}" 2>/dev/null; '
              'then echo PROBE-WROTE; else echo PROBE-DENIED; fi')
    argv = [str(landrun), "--log-level", "debug", "--best-effort", "--ro", "/", "--rw", "/dev", "-ldd", "-add-exec",
            "--rwx", str(allowed), "--", sh, "-c", script]
    proc = jail.run(box, argv, timeout=timeout, memory_max=memory_max, memory_total=memory_total)
    out = proc.stdout.decode("utf-8", "replace") + proc.stderr.decode("utf-8", "replace")
    enforced = "PROBE-DENIED" in out and (allowed / "w").exists() and not denied.exists()
    landlock = re.search(r"Landlock V\d+", out)
    return LandlockProbe(enforced, out, landlock.group(0) if landlock else None)


@dataclass(frozen=True)
class ComparatorRun:
    returncode: int | None                       # None: the timeout killed it
    stdout: str
    stderr: str
    lines: tuple[tuple[float, str, str], ...]    # (seconds since start, stream, line), as they arrived
    seconds: float
    memory_peak: int | None                      # bytes, the memory unit's MemoryPeak
    line_buffered: bool                          # Comparator's stdout reached us line by line (stdbuf -oL)
    command: tuple[str, ...]


def comparator_command(comparator: Path, project: Path, config_path: Path) -> list[str]:
    """`lake env comparator config.json` in `project`, as Comparator's README prescribes, with Comparator's stdout
    line-buffered by coreutils' `stdbuf -oL` when it is installed. Lean block-buffers stdout on a pipe: Comparator's
    progress lines would otherwise arrive only when it next starts a child that inherits stdout, or exits, and the
    phase timings would be wrong. stdbuf changes buffering only; landrun passes it to no sandboxed child."""
    stdbuf = jail.program("stdbuf")
    line_buffered = [stdbuf, "-oL"] if stdbuf else []
    return [jail.program("env") or "/usr/bin/env", "-C", str(project), "lake", "env", *line_buffered, str(comparator),
            str(config_path)]


def run_comparator(box: jail.Jail, comparator: Path, project: Path, config: dict[str, Any], config_path: Path, *,
                   timeout: float, memory_max: str | None, memory_total: str | None) -> ComparatorRun:
    """Write `config` to `config_path` and run Comparator on `project` inside `box`, reading its output as it comes."""
    config_path.write_text(json.dumps(config, indent=1) + "\n")
    command = comparator_command(comparator, project, config_path)
    run = jail.stream(box, command, timeout=timeout, memory_max=memory_max, memory_total=memory_total)
    return ComparatorRun(run.returncode, run.stdout.decode("utf-8", "replace"), run.stderr.decode("utf-8", "replace"),
                         run.lines, run.seconds, run.memory_peak, "-oL" in command, run.argv)


_LAKE_JOB = re.compile(r"^\S \[(\d+)/(\d+)\](?: \(Optional\))? (\w+) (\S+)(?: \((\d+(?:\.\d+)?)(ms|s)\))?$")
_LAKE_DONE = re.compile(r"^(?:Build completed successfully|All targets up-to-date) \((\d+) jobs?\)\.$")
_PHASE_END = re.compile(r"^(?:.+ kernel (?:accepts|rejects|rejected) the solution|Your solution is okay!"
                        r"|Quotient post-check rejects the solution|Error while interacting with .+ kernel)$")


def timings(lines: Sequence[tuple[float, str, str]], end: float, challenge: str = CHALLENGE,
            solution: str = SOLUTION) -> dict[str, Any]:
    """Where a Comparator run spent its time, from its own output only. `phases`: seconds between Comparator's
    progress lines (`Building M`, `Exporting … from M`, `Running K kernel on solution`; it prints nothing between
    exporting the solution and the first kernel, so that phase also holds the statement and axiom comparison).
    `modules`: Lake's own `Built X (Nms)` lines, slowest first. `jobs`: Lake's job count per build."""
    phases: dict[str, float] = {}
    modules: list[dict[str, Any]] = []
    jobs: dict[str, int] = {}
    current, since = "comparator_start", 0.0

    def close(at: float) -> None:
        if current:
            phases[current] = round(phases.get(current, 0.0) + at - since, 2)

    for at, where, line in lines:
        if where != "stdout":
            continue
        name = None
        if line == f"Building {challenge}":
            name = "build_challenge"
        elif line == f"Building {solution}":
            name = "build_solution"
        elif line.startswith("Exporting ") and line.endswith(f" from {challenge}"):
            name = "export_challenge"
        elif line.startswith("Exporting ") and line.endswith(f" from {solution}"):
            name = "export_solution_and_compare"
        elif m := re.fullmatch(r"Running (.+) kernel on solution\.?", line):
            name = "kernel_" + ("lean" if m.group(1) == "Lean default" else m.group(1))
        elif _PHASE_END.match(line):
            close(at)
            current = None
            continue
        if name:
            close(at)
            current, since = name, at
        elif current in ("build_challenge", "build_solution"):
            if (m := _LAKE_JOB.match(line)) and m.group(3) == "Built" and m.group(5):
                ms = round(float(m.group(5)) * (1 if m.group(6) == "ms" else 1000))
                modules.append({"module": m.group(4), "ms": ms, "side": current.removeprefix("build_")})
            if m := _LAKE_JOB.match(line):
                jobs[current] = max(jobs.get(current, 0), int(m.group(2)))
            elif m := _LAKE_DONE.match(line):
                jobs[current] = int(m.group(1))
    close(end)
    modules.sort(key=lambda row: -row["ms"])
    return {"phases": phases, "modules": modules[:20], "jobs": jobs}


# The check -----------------------------------------------------------------------------------------

class _Check:
    def __init__(self, request: CheckRequest):
        self.req = request
        self.root = request.repo.root
        self.protected = request.assurance == "protected"
        self.started = time.monotonic()
        self.files: dict[str, str] = {}
        self.trusted_files: dict[str, str] = {}
        self.target: dict[str, Any] = {}
        self.environment: dict[str, Any] = {}
        self.checked: dict[str, Any] = {"guards_disabled": sorted(request.guards)}
        self.command: list[str] = []
        self.log: list[str] = []
        self.extra: dict[str, Any] = {}
        self.notes: list[str] = []
        self.prebuilt: dict[str, Any] = {"modules": [], "jobs": {}}   # Lake's timings of the challenge pre-build

    def on(self, guard: str) -> bool:
        return guard not in self.req.guards

    def remaining(self, what: str) -> float:
        """Seconds left of the check's timeout, the one budget every step runs under; none left stops the check."""
        left = self.req.timeout - (time.monotonic() - self.started)
        if left <= 0:
            raise _Stop("error", f"timed out after {self.req.timeout:.0f} s, before {what}")
        return left

    # Sources ---------------------------------------------------------------------------------------

    def read_trusted(self, rel: str) -> bytes | None:
        return gitref.show(self.root, self.req.trusted_commit, rel)

    def read_worktree(self, rel: str) -> bytes | None:
        """A worktree file, None when it is absent or not a regular file; bounded by MAX_SOURCE_BYTES."""
        path = self.root / rel
        if not path.exists():
            return None
        resolved = path.resolve()
        try:
            resolved.relative_to(self.root.resolve())
        except ValueError:
            raise _Stop("error", f"{rel} resolves outside the repository (symbolic link); refused")
        if not resolved.is_file():
            return None
        data, problem = candidate_exec.read_regular_file(resolved, MAX_SOURCE_BYTES)
        if data is None and problem != "missing":
            raise _Stop("error", f"{rel}: {problem}; refused")
        return data

    def note_input(self, rel: str, data: bytes, trusted: bool) -> None:
        (self.trusted_files if trusted else self.files)[rel] = sha256_hex(data)

    # Steps -----------------------------------------------------------------------------------------

    def config(self) -> Config:
        """The configuration, recorded as an input only through what decides a verdict (RULES_INPUT)."""
        if not self.protected:
            config = self.req.repo.worktree_config()
            self.files[RULES_INPUT] = rules_digest(config)
            return config
        data = self.read_trusted(CONFIG_PATH)
        if data is None:
            raise _Stop("error", f"{CONFIG_PATH} is not on the trusted commit {self.req.trusted_commit[:12]}")
        try:
            config = parse_config(self.root, data.decode("utf-8"))
        except (ConfigError, UnicodeDecodeError) as exc:
            raise _Stop("error", f"trusted {CONFIG_PATH}: {exc}")
        self.trusted_files[RULES_INPUT] = rules_digest(config)
        return config

    def spec(self, config: Config) -> LeanSpec:
        item: Item = self.req.item
        targets_dir = config.rel("targets")
        spec, problems = parse_spec(item.lean, targets_dir)
        if spec is None:
            raise _Stop("error", *[f"malformed [lean] table of item '{item.id}': {p}" for p in problems])
        if not (self.protected and self.on("trusted_target")):
            return spec
        rel = config.rel("items", f"{item.id}.md")
        data = self.read_trusted(rel)
        if data is None:
            raise _Stop("error", f"item '{item.id}' is not on the trusted commit; a protected check reads the "
                                 "target and theorem list from the trusted item")
        try:
            trusted_item = parse_item(data, rel)
        except RecordError as exc:
            raise _Stop("error", f"trusted item: {exc}")
        problems = []
        target, theorems = _target_and_theorems(trusted_item.lean, targets_dir, problems)
        problems += witness_problems(trusted_item.lean, theorems)
        if problems:
            raise _Stop("error", *[f"trusted item '{item.id}': {p}" for p in problems])
        ignored = {}
        if spec.target != target:
            ignored["target"] = spec.target
        if spec.theorems != theorems:
            ignored["theorems"] = list(spec.theorems)
        if ignored:
            self.extra["ignored_candidate_fields"] = ignored
            self.notes.append(f"the candidate item changes [lean] {sorted(ignored)}; the trusted values were used")
        proofs = spec.proofs
        if proofs is not None:
            missing = [t for t in theorems if t not in proofs]
            if missing:
                raise _Stop("fail", f"the solution gives no proof for trusted target theorem(s) {missing}")
            proofs = {t: proofs[t] for t in theorems}
        return replace(spec, target=target, theorems=theorems, proofs=proofs,
                       witnesses=tuple(trusted_item.lean.get("witnesses", ())))

    def lints(self, spec: LeanSpec) -> list[dict[str, Any]]:
        """Command lints (`leanlint`) over what the candidate brings: every Lean file the check read from the
        worktree that is not byte-identical on the trusted ref, and the proof terms. Warnings, never a verdict."""
        files: dict[str, str] = {}
        for rel in sorted(self.files):
            if not rel.endswith(".lean"):
                continue
            data = self.read_worktree(rel)
            if data is None or data == self.read_trusted(rel):
                continue
            files[rel] = data.decode("utf-8", "replace")
        for name, term in (spec.proofs or {}).items():
            files[f"[lean] proofs.{name}"] = term
        return leanlint.lint_files(files)

    def project_rel(self, config: Config) -> Callable[[str], str]:
        project = config.lean.project.strip() or "."
        norm = posixpath.normpath(project)
        if project.startswith("/") or norm.startswith("..") or "\\" in project:
            raise _Stop("error", f"[lean] project '{project}' must be a path inside the repository")
        return (lambda p: p) if norm == "." else (lambda p: f"{norm}/{p}")

    def run(self) -> CheckOutcome:
        req = self.req
        if req.assurance not in ASSURANCE:
            raise _Stop("error", f"assurance '{req.assurance}' not in {ASSURANCE}")
        unknown = sorted(set(req.guards) - GUARDS)
        if unknown:
            raise _Stop("error", f"unknown guard(s) {unknown}; known: {sorted(GUARDS)}")
        config = self.config()
        spec = self.spec(config)
        prel = self.project_rel(config)
        roots = config.lean.roots
        if not roots:
            raise _Stop("error", "[lean] roots is empty: list the project's top-level module names")
        bad_roots = [r for r in roots if not leanmod.is_valid_module_name(r) or leanmod.is_reserved(r)]
        if bad_roots:
            raise _Stop("error", f"[lean] roots holds invalid or reserved names {bad_roots}")

        # Target: trusted commit, unless exploratory or the guard is disabled.
        trusted_target = self.protected and self.on("trusted_target")
        raw = self.read_trusted(spec.target) if trusted_target else self.read_worktree(spec.target)
        where = "trusted commit" if trusted_target else "worktree"
        if raw is None:
            raise _Stop("error", f"target {spec.target} is not in the {where}")
        self.note_input(spec.target, raw, trusted=trusted_target)
        self.target = {"path": spec.target, "sha256": sha256_hex(raw), "theorems": list(spec.theorems),
                       "source": "trusted-commit" if trusted_target else "worktree"}
        if self.protected:
            self.target["commit"] = req.trusted_commit
        if trusted_target and self.read_worktree(spec.target) != raw:
            self.extra["worktree_target_differs"] = True
            self.notes.append(f"the worktree copy of {spec.target} differs from the trusted one and was ignored")
        try:
            target_text = leanmod.decode(raw, spec.target)
            header = leanmod.parse_header(target_text)
        except leanmod.LeanModError as exc:
            raise _Stop("error", f"target {spec.target}: {exc}")
        problems = leanmod.target_problems(target_text, spec.theorems)
        if problems:
            raise _Stop("error", *[f"{spec.target}: {p}" for p in problems])
        target_imports = [m for m in header.modules if leanmod.in_roots(m, roots)]
        trusted_external = {m for m in header.modules if not leanmod.in_roots(m, roots)}
        if any(leanmod.is_reserved(m) for m in header.modules):
            raise _Stop("error", f"target {spec.target} imports a reserved check module")

        # Trusted side: definitions the statement uses, renamed under VLTrusted.
        trusted_defs = self.on("trusted_definitions")
        defs_from_commit = self.protected and trusted_defs
        trusted_sources: dict[str, str] = {}
        shared_closure: dict[str, bytes] | None = None
        if trusted_defs:
            def read_def(path: str) -> bytes | None:
                rel = prel(path)
                return self.read_trusted(rel) if defs_from_commit else self.read_worktree(rel)
            try:
                tclosure = leanmod.closure(target_imports, roots, read_def)
                trusted_external |= tclosure.external
                for module, data in tclosure.modules.items():
                    path = leanmod.module_to_path(module)
                    self.note_input(prel(path), data, trusted=defs_from_commit)
                    text = leanmod.decode(data, path)
                    trusted_sources[f"{TRUSTED_PREFIX}/{path}"] = leanmod.prefix_imports(text, TRUSTED_PREFIX, roots)
                    if any(leanmod.is_reserved(m) for m in leanmod.imports_of(text)):
                        raise _Stop("error", f"trusted module {module} imports a reserved check module")
                challenge_text = leanmod.prefix_imports(target_text, TRUSTED_PREFIX, roots)
            except leanmod.LeanModError as exc:
                raise _Stop("error", f"trusted definitions ({'trusted commit' if defs_from_commit else 'worktree'}): {exc}")
            # Shared mode: when the candidate has not changed any module the target depends on, the challenge
            # imports those modules under their own names. Renaming is then unnecessary, and it would make
            # module-dependent auxiliary names (private helpers, generated lemmas) look like altered definitions.
            self.checked["trusted_closure"] = sorted(leanmod.module_to_path(m) for m in tclosure.modules)
            changed = sorted(m for m, data in tclosure.modules.items()
                             if self.read_worktree(prel(leanmod.module_to_path(m))) != data)
            if not changed:
                shared_closure = dict(tclosure.modules)
                trusted_sources = {}
                challenge_text = target_text
            else:
                self.checked["changed_definition_modules"] = changed
        else:
            challenge_text = target_text
        self.checked["definitions_mode"] = (
            "shared: target closure identical to the trusted one" if shared_closure is not None
            else "renamed under VLTrusted: the candidate changed modules the target depends on" if trusted_defs
            else "candidate modules (trusted_definitions guard disabled)")
        self.checked["definitions_source"] = (
            "trusted-commit" if defs_from_commit else "worktree" if trusted_defs
            else "candidate modules (trusted_definitions guard disabled)")
        self.checked["trusted_modules"] = sorted(trusted_sources)

        # Candidate side.
        if spec.solution is not None and not leanmod.in_roots(spec.solution, roots):
            raise _Stop("error", f"[lean] solution module {spec.solution} is not under the project roots {list(roots)}")
        start = [*target_imports, *spec.imports] if spec.proofs is not None else [spec.solution]
        if not trusted_defs or shared_closure is not None:
            start += target_imports
        try:
            cclosure = leanmod.closure(start, roots, lambda path: self.read_worktree(prel(path)))
        except leanmod.LeanModError as exc:
            verdict = "fail" if " not found at " in str(exc) else "error"
            raise _Stop(verdict, f"candidate modules: {exc}")
        reserved = sorted(m for m in cclosure.external if leanmod.is_reserved(m))
        if reserved:
            raise _Stop("error", f"candidate modules import reserved check modules {reserved}")
        candidate_sources: dict[str, bytes] = {}
        for module, data in cclosure.modules.items():
            path = leanmod.module_to_path(module)
            candidate_sources[path] = data
            self.note_input(prel(path), data, trusted=False)
        for name in PROJECT_FILES:
            data = self.read_worktree(prel(name))
            if data is not None:
                self.note_input(prel(name), data, trusted=False)
        self.checked["candidate_modules"] = sorted(cclosure.modules)
        if shared_closure is not None:
            # The bytes the build will use must be the trusted bytes (guards against a change between reads).
            drift = sorted(m for m, data in shared_closure.items() if candidate_sources.get(
                leanmod.module_to_path(m)) not in (None, data))
            if drift:
                raise _Stop("error", f"modules {drift} changed in the worktree during the check; run it again")
        if spec.proofs is not None:
            try:
                solution_text = leanmod.insert_imports(leanmod.fill_sorries(target_text, spec.proofs), spec.imports)
            except leanmod.LeanModError as exc:
                raise _Stop("error", f"cannot generate the solution: {exc}")
            self.checked["solution_form"] = "proofs"
        else:
            solution_text = f"import {spec.solution}\n"
            self.checked["solution_form"] = "module"
            self.checked["solution"] = spec.solution
        self.checked["imports"] = list(spec.imports)
        if self.on("lints"):
            self.checked["lints"] = self.lints(spec)

        # Build inputs (trusted in protected mode), tools, toolchain.
        read_build = self.read_trusted if self.protected else self.read_worktree
        build_files = {name: read_build(prel(name)) for name in PROJECT_FILES}
        for name, data in build_files.items():
            if data is not None and self.protected:
                self.note_input(prel(name), data, trusted=True)
        if build_files["lean-toolchain"] is None:
            raise _Stop("error", f"no {prel('lean-toolchain')} in the {'trusted commit' if self.protected else 'worktree'}")
        toolchain = build_files["lean-toolchain"].decode("utf-8", "replace").strip()
        try:
            machine = load_machine()
        except MachineError as exc:
            raise _Stop("error", f"machine configuration: {exc}")
        try:
            tc_dir = toolchain_dir(toolchain, elan_home=machine.protected_elan_home() if self.protected else None)
        except ValueError as exc:
            raise _Stop("unsupported", str(exc))
        if not (tc_dir / "bin" / "lake").is_file() or not (tc_dir / "bin" / "lean").is_file():
            raise _Stop("unsupported", f"Lean toolchain {toolchain} is not installed at {tc_dir} (vl never downloads)")
        if not jail.available():
            raise _Stop("unsupported", "bubblewrap (bwrap) is not installed; Lean checks run only inside the jail")
        tools, sources = self.tools(machine, toolchain, config)
        missing = [n for n in ("comparator", "landrun", "lean4export") if tools[n] is None]
        if missing:
            where = (f"[tools] in {machine.path}, or `vl init --tools`" if self.protected
                     else "[tools] in machine.toml or research/vl.toml, or the COMPARATOR_* variables")
            raise _Stop("unsupported", "tool(s) not found: " + ", ".join(missing) + f" (set them in {where})")
        use_nanoda = config.lean.external_kernels and tools["nanoda"] is not None
        if config.lean.external_kernels and not use_nanoda:
            if self.protected:   # a pass replayed by one kernel where the rules ask for two would not count
                raise _Stop("unsupported", "[lean] external_kernels is on, but nanoda, the second kernel, is not "
                                           f"installed on this machine: set [tools] nanoda in {machine.path} or run "
                                           "`vl init --tools --nanoda PATH`, or set external_kernels = false on the "
                                           "trusted ref to check with the Lean kernel only")
            self.notes.append("external_kernels is on but nanoda is not resolvable; only the Lean kernel was used")
        used_tools = {n: p for n, p in tools.items() if p is not None and (n != "nanoda" or use_nanoda)}
        self.environment = {
            "toolchain": toolchain,
            "toolchain_dir": str(tc_dir),
            "lean_version": _lean_version(tc_dir),
            "tools": {n: {"path": str(p), "sha256": file_sha256(p), **sources.get(n, {})} for n, p in used_tools.items()},
            "machine_config": machine.record(),
        }

        # Lake packages: reused read-only, never downloaded.
        manifest = None
        packages_dir: Path | None = None
        requires: list[dict[str, Any]] = []
        if build_files["lake-manifest.json"] is not None:
            try:
                manifest = json.loads(build_files["lake-manifest.json"])
            except json.JSONDecodeError as exc:
                raise _Stop("error", f"lake-manifest.json: {exc}")
        packages = (manifest or {}).get("packages", []) or []
        if packages:
            bad = [p.get("name") for p in packages if p.get("type") != "git"]
            if bad:
                raise _Stop("unsupported", f"non-git Lake dependencies are not supported: {bad}")
            odd = [p.get("name") for p in packages
                   if not isinstance(p.get("name"), str) or not re.fullmatch(r"[A-Za-z0-9_-][A-Za-z0-9._-]*", p["name"])]
            if odd:
                raise _Stop("error", f"lake-manifest.json: unsupported package names {odd}")
            lean_raw = config.raw.get("lean", {}) or {}
            machine_cache = machine.cache_for(gitref.main_worktree(self.root))
            configured = (str(Path(machine_cache) / "packages") if machine_cache else None) or lean_raw.get("packages") or (
                str(Path(str(lean_raw["cache"])) / "packages") if lean_raw.get("cache") else None)
            if machine_cache:
                self.notes.append(f"Lake packages from the build cache {machine_cache} set in {machine.path}")
            if configured:
                packages_dir = Path(str(configured)).expanduser()
                packages_dir = packages_dir if packages_dir.is_absolute() else self.root / packages_dir
            else:
                packages_dir = self.root / prel(manifest.get("packagesDir") or ".lake/packages")
            packages_dir = packages_dir.resolve()
            absent = [p["name"] for p in packages if not (packages_dir / p["name"]).is_dir()]
            if absent:
                raise _Stop("unsupported", f"Lake packages {absent} are missing from {packages_dir}; "
                                           "vl never downloads packages")
            requires = [p for p in packages if not p.get("inherited")]
            manifest = {**manifest, "packagesDir": str(packages_dir), "name": "vlcheck"}
        self.environment["packages"] = None if packages_dir is None else {
            "dir": str(packages_dir), "mount": "read-only bind at its own path (bwrap --ro-bind)",
            "lake": "packagesDir set to that absolute path in the generated lakefile and manifest"}

        try:
            package_options, root_options, options_note = lakefile_options(build_files["lakefile.toml"], roots)
        except Exception as exc:                           # tomllib / decoding problems
            raise _Stop("error", f"lakefile.toml: {exc}")
        if build_files["lakefile.toml"] is None and build_files["lakefile.lean"] is not None:
            options_note = "lakefile.lean: leanOptions not propagated"
        self.checked["lean_options_source"] = options_note

        # Write the fresh check project.
        scratch = req.scratch
        scratch.mkdir(parents=True, exist_ok=True)
        project = scratch / "project"
        if project.exists():
            raise _Stop("error", f"{project} already exists: a check needs a fresh project (Comparator assumption 2)")
        libs: list[tuple[str, list[str], dict[str, Any]]] = [(CHALLENGE, [], {}), (SOLUTION, [], {})]
        for k, root in enumerate(roots):
            libs.append((f"VLRoot{k}", [root], root_options.get(root, {})))
            if trusted_defs:
                libs.append((f"VLTrusted{k}", [f"{TRUSTED_PREFIX}.{root}"], root_options.get(root, {})))
        try:
            lakefile = render_lakefile(packages_dir, requires, package_options, libs)
        except ValueError as exc:
            raise _Stop("unsupported", f"lakefile.toml: {exc}")
        outputs: dict[str, bytes] = {
            "lean-toolchain": (toolchain + "\n").encode(),
            "lakefile.toml": lakefile.encode(),
            "lake-manifest.json": (json.dumps(manifest or {"version": "1.2.0", "packagesDir": ".lake/packages",
                                                          "packages": [], "name": "vlcheck", "lakeDir": ".lake"},
                                              indent=1) + "\n").encode(),
            f"{CHALLENGE}.lean": challenge_text.encode(),
            f"{SOLUTION}.lean": solution_text.encode(),
            **{p: t.encode() for p, t in trusted_sources.items()},
            **candidate_sources,
        }
        for rel, data in outputs.items():
            dest = project / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)

        # Jail, isolation probe, Comparator.
        box = replace(comparator_jail(scratch, tc_dir, used_tools, packages_dir), deny_unix=self.protected)
        mark = time.monotonic()
        phases = {"prepare": round(mark - self.started, 2)}
        self.extra["phases"] = phases
        dependencies = sorted(m for m in trusted_external if not (tc_dir / "lib" / "lean" / f"{m.split('.')[0]}.olean").is_file())
        if packages_dir is not None:
            self.environment["packages"]["identity"] = dependency_identity(packages_dir, packages, dependencies)
        if packages_dir is not None and dependencies:
            lakefile_deps = render_lakefile(packages_dir, requires, {}, [])
            self.preflight(box, scratch / "preflight", toolchain, lakefile_deps, manifest, packages_dir, dependencies)
            phases["preflight"] = round(time.monotonic() - mark, 2)
            mark = time.monotonic()
        self.check_isolation(box, used_tools["landrun"])
        phases["isolation_probe"] = round(time.monotonic() - mark, 2)
        probe_dir = None
        if self.on("probe_snapshot"):
            mark = time.monotonic()
            probe_dir = self.prebuild_challenge(box, project, tc_dir, used_tools["landrun"], scratch / "probe")
            phases["prebuild_challenge"] = round(time.monotonic() - mark, 2)

        axioms = list(config.lean.permitted_axioms)
        kernels = ("lean", *([NANODA_KERNEL] if use_nanoda else []))
        self.checked.update(theorems=list(spec.theorems), witnesses=list(spec.witnesses), kernels=list(kernels),
                            challenge_module=CHALLENGE, solution_module=SOLUTION)
        runs = 0
        while True:
            cfg = comparator_config(spec.theorems, axioms, used_tools["nanoda"] if use_nanoda else None)
            remaining = req.timeout - (time.monotonic() - self.started)
            if remaining <= 0:
                raise _Stop("error", f"timed out after {req.timeout:.0f} s")
            runs += 1
            config_path = scratch / "comparator.json"
            run = run_comparator(box, used_tools["comparator"], project, cfg, config_path,
                                 timeout=remaining, memory_max=req.memory_max, memory_total=req.memory_total)
            self.command = list(run.command)
            self.log.append(f"=== comparator run {runs}: {json.dumps(cfg)}\n"
                            f"--- exit {run.returncode} --- stdout\n{run.stdout}\n--- stderr\n{run.stderr}")
            measured = timings(run.lines, run.seconds)          # of the last run (more runs only with a guard off)
            if run.line_buffered:                                # otherwise Comparator's phase lines arrive late
                self.extra["phases"] = {**phases, **measured["phases"]}
            modules = sorted([*self.prebuilt["modules"], *measured["modules"]], key=lambda row: -row["ms"])[:20]
            jobs = {k: max(measured["jobs"].get(k, 0), self.prebuilt["jobs"].get(k, 0))
                    for k in {*measured["jobs"], *self.prebuilt["jobs"]}}
            for key, value in (("modules", modules), ("jobs", dict(sorted(jobs.items())))):
                if value:
                    self.extra[key] = value
            if run.memory_peak is not None:
                self.extra["memory_peak_bytes"] = max(run.memory_peak, self.extra.get("memory_peak_bytes", 0))
            if run.returncode is None:
                last = list(measured["phases"])[-1] if measured["phases"] else "start"
                raise _Stop("error", f"Comparator timed out after {req.timeout:.0f} s (in phase {last})")
            result = classify(run.stdout, run.stderr, run.returncode, spec.theorems, kernels)
            if (not self.on("permitted_axioms") and result.axiom and result.axiom not in axioms
                    and runs <= _MAX_AXIOM_RERUNS):
                axioms.append(result.axiom)                 # test-only: permit what the solution uses
                continue
            break
        self.checked["permitted_axioms"] = axioms
        self.checked["comparator"] = {"result": {"pass": "accepted", "fail": "rejected"}.get(result.verdict, "failed"),
                                      "message": result.message, "phase": result.phase, "runs": runs}
        self.extra["comparator_seconds"] = round(time.monotonic() - self.started, 2)
        if result.verdict == "pass":
            mark = time.monotonic()
            names = [p.get("name") for p in (manifest or {}).get("packages", []) or []]
            try:
                if not self.on("probe_snapshot"):        # test-only: probe the project as the candidate left it
                    probe_dir = self.probe_workspace(project, scratch / "probe")
                self.run_probes(box, probe_dir, spec.theorems, config, packages_dir, names)
            except Exception as exc:                       # a probe problem is never a verdict
                self.checked.setdefault("probe_run", {}).setdefault("problems", []).append(
                    f"internal error in the probe runner: {type(exc).__name__}: {exc}")
            self.extra["phases"]["probes"] = round(time.monotonic() - mark, 2)
            return self.outcome("pass", [f"{result.reason}: {len(spec.theorems)} theorem(s), "
                                         f"kernels {', '.join(kernels)}, axioms within {axioms}"])
        raise _Stop(result.verdict, result.reason)

    def prebuild_challenge(self, box: jail.Jail, project: Path, tc_dir: Path, landrun: Path, probe_dir: Path) -> Path | None:
        """Build the challenge exactly as Comparator does (`lake build VLChallenge` under landrun, only the
        project's `.lake` writable), before any candidate code is compiled, and copy its build products into
        `probe_dir`, a Lake workspace with the same lakefile and manifest and no sources. Returns None (probes
        skipped, Comparator unaffected: it builds the challenge itself) when the build fails."""
        (project / ".lake").mkdir(exist_ok=True)
        git = jail.program("git")
        command = [jail.program("env") or "/usr/bin/env", "-C", str(project), "LEAN_ABORT_ON_PANIC=1", str(landrun),
                   "--best-effort", "--ro", "/", "--rw", "/dev", "-ldd", "-add-exec",
                   "--env", "PATH", "--env", "HOME", "--env", "LEAN_ABORT_ON_PANIC",
                   "--ro", str(project), "--rwx", str(project / ".lake"), "--rox", str(tc_dir),
                   *(["--rox", git] if git else []), "--", "lake", "build", CHALLENGE]
        remaining = self.remaining("building the challenge")
        try:
            proc = jail.run(box, command, timeout=remaining, memory_max=self.req.memory_max,
                            memory_total=self.req.memory_total)
        except subprocess.TimeoutExpired:
            raise _Stop("error", f"timed out after {self.req.timeout:.0f} s building the challenge")
        except RuntimeError as exc:
            raise _Stop("error", f"building the challenge failed to run: {exc}")
        out = (proc.stdout + proc.stderr).decode("utf-8", "replace")
        self.log.append(f"=== challenge build for the statement probes: lake build {CHALLENGE}\n"
                        f"--- exit {proc.returncode}\n{out}")
        if proc.returncode != 0:
            self.checked["probe_run"] = {"skipped": "the challenge did not build before Comparator ran"}
            return None
        # Lake's per-module times of this build, as if Comparator had printed them while building the challenge.
        self.prebuilt = timings([(0.0, "stdout", f"Building {CHALLENGE}"),
                                 *((0.0, "stdout", line) for line in out.splitlines())], 0.0)
        return self.probe_workspace(project, probe_dir)

    def probe_workspace(self, project: Path, probe_dir: Path) -> Path | None:
        """A Lake workspace for the probes: the project's lakefile, manifest and toolchain, and a copy of its
        build products as they are now. Never a link: a later write into the project must not reach the copy."""
        try:
            (probe_dir / ".lake" / "build" / "lib").mkdir(parents=True)
            shutil.copytree(project / ".lake" / "build" / "lib" / "lean", probe_dir / ".lake" / "build" / "lib" / "lean",
                            symlinks=True)
            for name in ("lean-toolchain", "lakefile.toml", "lake-manifest.json"):
                shutil.copy2(project / name, probe_dir / name)
        except OSError as exc:
            self.checked["probe_run"] = {"skipped": f"could not copy the challenge build: {exc}"}
            return None
        return probe_dir

    def run_probes(self, box: jail.Jail, probe_dir: Path | None, theorems: tuple[str, ...], config: Config,
                   packages_dir: Path | None, package_names: list[Any]) -> None:
        """Triviality and vacuity of each target theorem, on the pre-built challenge copy. Never changes the
        verdict: a crash, an unreadable output or a timeout is recorded under `probe_run.problems`."""
        if probe_dir is None:
            return
        tactics, imports = lean_probes.battery(packages_dir, [n for n in package_names if isinstance(n, str)])
        nonce = secrets.token_hex(8)
        run_info: dict[str, Any] = {"battery": tactics, "imports": imports,
                                    "heartbeats_per_attempt": config.probe_heartbeats,
                                    "timeout_seconds": config.probe_timeout, "problems": []}
        self.checked["probe_run"] = run_info
        remaining = self.req.timeout - (time.monotonic() - self.started)
        timeout = min(config.probe_timeout, remaining)
        if timeout < 1:
            run_info["problems"].append("no time left for the probes within the check's timeout")
            return
        text = lean_probes.probe_file(CHALLENGE, theorems, tactics, imports, config.lean.permitted_axioms,
                                      config.probe_heartbeats, nonce, trivial=self.on("probe_trivial"),
                                      vacuity=self.on("probe_vacuous"))
        (probe_dir / "VLProbe.lean").write_text(text, encoding="utf-8")
        stdbuf = jail.program("stdbuf")
        command = [jail.program("env") or "/usr/bin/env", "-C", str(probe_dir), "lake", "env",
                   *([stdbuf, "-oL"] if stdbuf else []), "lean", "VLProbe.lean"]
        try:
            run = jail.stream(box, command, timeout=timeout, memory_max=self.req.memory_max,
                              memory_total=self.req.memory_total)
        except RuntimeError as exc:
            run_info["problems"].append(f"the probes failed to run: {exc}")
            return
        stdout, stderr = run.stdout.decode("utf-8", "replace"), run.stderr.decode("utf-8", "replace")
        self.log.append(f"=== statement probes (nonce {nonce})\n--- exit {run.returncode}\n{stdout}\n--- stderr\n{stderr}")
        results, problems = lean_probes.parse_output(stdout, nonce, theorems)
        if run.returncode is None:
            problems.insert(0, f"the probes timed out after {timeout:.0f} s; unfinished theorems record null")
        elif run.returncode != 0:
            tail = " | ".join((stdout + stderr).strip().splitlines()[-3:])[:400]
            problems.insert(0, f"the probe run exited with {run.returncode}: {tail}")
        run_info["problems"] = problems
        run_info["seconds"] = round(run.seconds, 2)
        self.checked["probes"] = results

    def preflight(self, box: jail.Jail, folder: Path, toolchain: str, lakefile: str, manifest: dict[str, Any],
                  packages_dir: Path, modules: list[str]) -> None:
        """Lake's own up-to-date check of the dependency modules the trusted side imports, in a workspace that
        holds only the generated lakefile and manifest (no candidate file), inside the jail where the packages
        are read-only: `lake build --no-build` exits 3 when anything would have to be rebuilt."""
        folder.mkdir(parents=True)
        (folder / "lean-toolchain").write_text(toolchain + "\n")
        (folder / "lakefile.toml").write_text(lakefile)
        (folder / "lake-manifest.json").write_text(json.dumps(manifest, indent=1) + "\n")
        command = [jail.program("env") or "/usr/bin/env", "-C", str(folder), "lake", "build", "--no-build",
                   *(f"+{m}" for m in modules)]
        try:
            proc = jail.run(box, command, timeout=self.remaining("the dependency preflight"),
                            memory_max=self.req.memory_max, memory_total=self.req.memory_total)
        except (subprocess.TimeoutExpired, RuntimeError) as exc:
            raise _Stop("error", f"dependency preflight (lake build --no-build) failed to run: {exc}")
        out = (proc.stdout + proc.stderr).decode("utf-8", "replace")
        self.log.append(f"=== dependency preflight: lake build --no-build {' '.join(modules)}\n"
                        f"--- exit {proc.returncode}\n{out}")
        if proc.returncode == 3:
            owner = (str(packages_dir.parent.parent) if (packages_dir.name, packages_dir.parent.name) == ("packages", ".lake")
                     else f"the project whose packages directory is {packages_dir}")
            stale = re.findall(r"^- (.+)$", out, re.M)
            raise _Stop("error", f"shared build cache is incomplete: run `lake build` in {owner}"
                                 + (f" (Lake would rebuild {', '.join(stale[:5])})" if stale else ""))
        if proc.returncode != 0:
            raise _Stop("error", f"dependency preflight `lake build --no-build` failed (exit {proc.returncode}): "
                                 + " | ".join(out.strip().splitlines()[-3:]))

    def tools(self, machine: Machine, toolchain: str, config: Config) -> tuple[dict[str, Path | None], dict[str, dict]]:
        """The tool paths, and where each came from. Protected: `protected_tool` (no environment, no relative path,
        pinned tools only). Exploratory: also the COMPARATOR_* variables and PATH, pinned or not (a note says which
        are not)."""
        if not self.protected:
            tools = {name: resolve_tool(name, config.tools, self.root, machine=machine, toolchain=toolchain)
                     for name in TOOLS}
            sources = {}
            for name, path in tools.items():
                if path is None:
                    continue
                pin, problem = pin_of(path)
                sources[name] = {"source": "exploratory resolution", "pinned_by": pin}
                if problem and (name != "nanoda" or config.lean.external_kernels):
                    self.notes.append(f"exploratory: {name} {problem}; a protected check would refuse it")
            return tools, sources
        tools: dict[str, Path | None] = {}
        sources: dict[str, dict] = {}
        repositories = (self.root, gitref.main_worktree(self.root))
        for name in TOOLS:
            tool, problem = protected_tool(name, machine, toolchain, config.tools, repositories)
            if problem and (name != "nanoda" or config.lean.external_kernels):
                raise _Stop("error", problem)
            tools[name] = tool.path if tool else None
            if tool is not None:
                sources[name] = {"source": tool.source, "pinned_by": tool.pin}
                if "deprecated" in tool.source:
                    self.notes.append(f"{name} came from {tool.source}")
        return tools, sources

    def check_isolation(self, box: jail.Jail, landrun: Path) -> None:
        try:
            probe = landlock_probe(box, landrun, self.req.scratch / "isolation-probe", self.req.memory_max,
                                    self.req.memory_total, timeout=min(60.0, self.remaining("the isolation probe")))
        except (subprocess.TimeoutExpired, RuntimeError) as exc:
            raise _Stop("error", f"isolation probe failed to run: {exc}")
        self.log.append(f"=== isolation probe (landrun inside bwrap)\n{probe.log}")
        enforced = probe.enforced
        capped = jail.cap_problem(self.req.memory_max) is None
        self.environment["isolation"] = {
            "kind": "bwrap+landrun" if enforced else "bwrap only (landrun NOT enforcing)",
            "bwrap": "--unshare-all (no network, own pid/ipc/uts/user namespaces), --clearenv, explicit mounts",
            "landrun": f"nested inside bwrap; probe: {'write outside allowed paths denied' if enforced else 'NOT denied'}"
                       + (f" ({probe.landlock})" if probe.landlock else ""),
            "memory_max": self.req.memory_max,
            "memory_total": self.req.memory_total,
            "systemd_scope": capped and not box.deny_unix,
            "systemd_service": box.deny_unix,
            "restrict_address_families": "~AF_UNIX (socket probe enforced before each exec)" if box.deny_unix else None,
            "slice": jail.SLICE if capped or box.deny_unix else None,
        }
        if not enforced:
            raise _Stop("unsupported", "landrun did not enforce Landlock inside bwrap (probe write was not "
                                       "denied); refusing to run Comparator without its sandbox")

    def outcome(self, verdict: str, reasons: list[str]) -> CheckOutcome:
        if verdict != "pass":
            reasons = reasons + [n for n in self.notes if n not in reasons]
        elif self.notes:
            self.checked["notes"] = list(self.notes)
        self.extra.setdefault("seconds", round(time.monotonic() - self.started, 2))
        return CheckOutcome(
            verdict=verdict, reasons=reasons, files=dict(sorted(self.files.items())),
            trusted_files=dict(sorted(self.trusted_files.items())), target=self.target,
            environment=self.environment, checked=self.checked, command=self.command,
            log="\n".join(self.log), extra=self.extra,
        )


def _lean_version(tc_dir: Path) -> str:
    try:
        proc = subprocess.run([str(tc_dir / "bin" / "lean"), "--version"], capture_output=True, timeout=30)
        return proc.stdout.decode("utf-8", "replace").strip()
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"unknown ({exc})"


class LeanComparatorAdapter:
    """Adapter for items with a `[lean]` table. Never raises: bad items and tool problems become
    `error` / `unsupported`; a solution that does not prove the trusted target is `fail`."""

    name = NAME

    def applies(self, item: Item) -> bool:
        return bool(item.lean)

    def check(self, request: CheckRequest) -> CheckOutcome:
        check = _Check(request)
        try:
            return check.run()
        except _Stop as stop:
            return check.outcome(stop.verdict, stop.reasons)
        except Exception as exc:                               # never raise out of an adapter
            return check.outcome("error", [f"internal error in {NAME}: {type(exc).__name__}: {exc}"])
