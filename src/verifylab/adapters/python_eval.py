"""`python-eval` adapter: run a candidate function on an evaluator's cases and let the evaluator judge.

Item schema (the `[python]` table of an item)::

    [python]
    evaluator = "research/evaluators/<name>.py"   # trusted: read from the trusted commit in protected mode
    candidate = "experiments/x/solve.py"          # candidate module in the worktree
    entry = "solve"                               # callable in the candidate module
    files = ["experiments/x/helpers.py"]          # optional extra candidate files

Evaluator protocol: the evaluator module defines `CASES` (a list of JSON-serialisable inputs) and
`judge(case, output) -> bool | (bool, str)`, and optionally `TIMEOUT_S`, the per-case time limit.

Three separations are the point:
1. The candidate runs in its own jailed process (`candidate_exec` + `_candidate_child`). The jail holds
   copies of the candidate files and a private work dir. The evaluator is not mounted, so the candidate
   can neither read the expected answers nor touch the judge. Its outputs come back as JSON.
2. The judge runs in a second, separate jailed process. It loads the evaluator from a copy of the
   trusted commit's content (protected) or of the worktree's (exploratory), receives the outputs as
   JSON on stdin and returns per-case verdicts as JSON. It never imports or runs candidate code.
3. This adapter aggregates: `pass` only if every case passed and nothing errored.

Guards that tests may DISABLE through `request.guards` (each one is shown to matter by a planted defect
that passes once it is off): `trusted_evaluator`, `separate_judge`, `candidate_jail`, `evaluator_binding`.
"""

from __future__ import annotations

import functools
import json
import math
import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from .. import candidate_exec, gitref, jail
from ..records import RecordError, parse_item, sha256_hex
from ..repo import EVALUATORS
from .base import CheckOutcome, CheckRequest

NAME = "python-eval"
GUARDS = frozenset({"trusted_evaluator", "separate_judge", "candidate_jail", "evaluator_binding"})
DEFAULT_INTERPRETER = "/usr/bin/python3"
DEFAULT_CASE_TIMEOUT = 10.0
STARTUP_SLACK = 5.0
MAX_INPUT_BYTES = 8 * 1024 * 1024
MAX_JUDGE_BYTES = 64 * 1024 * 1024
MAX_REASONS = 8
MAX_CASE_RESULTS = 200
MAX_MESSAGE = 500
_KEYS = ("evaluator", "candidate", "entry", "files")

JUDGE_RUNNER = r'''"""Judge runner of verifylab's python-eval adapter. Copied alone into the judge jail; stdlib only.

    describe  EVALUATOR             -> {"status": "ok", "cases", "digest", "timeout_s", "python"}
    judge     EVALUATOR < outputs   -> {"status": "ok", "results": [{"index", "passed", "message"} | {"index", "error"}]}
    inprocess EVALUATOR < request   -> TEST ONLY (guard `separate_judge` disabled): imports the candidate into
                                       this very process and judges it here, the in-process design
                                       the separate judge exists to prevent.

In describe and judge modes this process never imports or runs candidate code; it sees outputs as JSON.
The outputs are parsed strictly: an output holding NaN or Infinity, which JSON forbids and the candidate's runner
rejects but a candidate can still write into its own result file, fails its case here and never reaches the
evaluator, whose tolerance test NaN would slip through. The result goes to the original stdout; whatever the
evaluator prints goes to stderr.
"""
import hashlib
import importlib.util
import json
import os
import sys
import traceback

MAX_MESSAGE = 500


def _exc(exc):
    return traceback.format_exception_only(type(exc), exc)[-1].strip()[:MAX_MESSAGE]


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError("cannot create an import spec for " + path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _cases(module):
    cases = getattr(module, "CASES", None)
    if not isinstance(cases, (list, tuple)):
        raise TypeError("the evaluator must define CASES as a list")
    text = json.dumps(list(cases), sort_keys=True, separators=(",", ":"), allow_nan=False)
    return json.loads(text), hashlib.sha256(text.encode("utf-8")).hexdigest()


def _verdict(result):
    if type(result) is bool:
        return result, ""
    if isinstance(result, tuple) and len(result) == 2 and type(result[0]) is bool and isinstance(result[1], str):
        return result[0], result[1][:MAX_MESSAGE]
    raise TypeError("judge must return bool or (bool, str), got " + type(result).__name__)


class _NonFinite:
    """A NaN or Infinity constant in the candidate's outputs (json's parse_constant)."""

    def __init__(self, name):
        self.name = name


def _non_finite(value):
    stack = [value]
    while stack:
        item = stack.pop()
        if isinstance(item, _NonFinite):
            return item.name
        if isinstance(item, list):
            stack.extend(item)
        elif isinstance(item, dict):
            stack.extend(item.values())
    return None


def _judge_all(module, cases, outputs):
    results = []
    for index, value in outputs:
        constant = _non_finite(value)
        if constant is not None:
            results.append({"index": index, "passed": False,
                            "message": "the output holds " + constant + ", which strict JSON forbids; not judged"})
            continue
        try:
            passed, message = _verdict(module.judge(cases[index], value))
            results.append({"index": index, "passed": passed, "message": message})
        except Exception as exc:
            results.append({"index": index, "error": _exc(exc)})
    return results


def main():
    mode, path = sys.argv[1], sys.argv[2]
    out = os.fdopen(os.dup(1), "w", encoding="utf-8")
    os.dup2(2, 1)

    def emit(payload):
        out.write(json.dumps(payload))
        out.flush()

    try:
        module = _load(path, "vl_evaluator")
    except BaseException as exc:
        emit({"status": "evaluator_error", "error": "the evaluator failed to import: " + _exc(exc)})
        return 0
    try:
        cases, digest = _cases(module)
    except Exception as exc:
        emit({"status": "evaluator_error", "error": "bad CASES: " + _exc(exc)})
        return 0
    if not callable(getattr(module, "judge", None)):
        emit({"status": "evaluator_error", "error": "the evaluator must define judge(case, output)"})
        return 0

    if mode == "describe":
        timeout = getattr(module, "TIMEOUT_S", None)
        if timeout is not None and (type(timeout) not in (int, float)):
            timeout = repr(timeout)[:100]
        emit({"status": "ok", "cases": cases, "digest": digest, "timeout_s": timeout,
              "python": sys.version.split()[0]})
        return 0

    request = json.loads(sys.stdin.read(), parse_constant=_NonFinite)
    if request.get("digest") != digest:
        emit({"status": "evaluator_error",
              "error": "CASES differ between the describe and judge steps (the evaluator is not deterministic)"})
        return 0
    if mode == "judge":
        outputs = [(rec["index"], rec["value"]) for rec in request["outputs"]]
        emit({"status": "ok", "results": _judge_all(module, cases, outputs)})
        return 0
    if mode == "inprocess":
        sys.path[:0] = request["sys_path"]
        try:
            candidate = _load(request["candidate"], "candidate_main")
            fn = getattr(candidate, request["entry"])
        except Exception as exc:
            emit({"status": "candidate_error", "error": _exc(exc)})
            return 0
        outcomes, outputs = [], []
        for index, case in enumerate(cases):
            try:
                value = fn(case)
                json.dumps(value, allow_nan=False)
            except Exception as exc:
                outcomes.append({"ok": False, "value": None, "error": _exc(exc)})
                continue
            outcomes.append({"ok": True, "value": value, "error": None})
            outputs.append((index, value))
        emit({"status": "ok", "outcomes": outcomes, "results": _judge_all(module, cases, outputs)})
        return 0
    emit({"status": "usage_error", "error": "unknown mode " + mode})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
'''


class SpecError(ValueError):
    """The `[python]` table is malformed."""


class InputError(ValueError):
    """A named input cannot be read safely."""


@dataclass(frozen=True)
class PythonSpec:
    evaluator: str
    candidate: str
    entry: str
    files: tuple[str, ...]

    @property
    def candidate_files(self) -> tuple[str, ...]:
        return (self.candidate, *self.files)


def _clean_path(value: Any, key: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SpecError(f"[python] {key} must be a non-empty repo-relative path")
    parts = value.split("/")
    if value.startswith("/") or "\\" in value or "\0" in value or any(p in ("", ".", "..") for p in parts):
        raise SpecError(f"[python] {key} = {value!r} must be a plain repo-relative path (no '..', no absolute path)")
    return str(PurePosixPath(value))


def parse_spec(table: dict[str, Any], evaluators_dir: str) -> PythonSpec:
    """Validate the `[python]` table. `evaluators_dir` is the repo-relative evaluators folder."""
    unknown = sorted(set(table) - set(_KEYS))
    if unknown:
        raise SpecError(f"[python] has unknown key(s) {unknown}; allowed: {list(_KEYS)}")
    for key in ("evaluator", "candidate", "entry"):
        if key not in table:
            raise SpecError(f"[python] needs '{key}'")
    evaluator = _clean_path(table["evaluator"], "evaluator")
    candidate = _clean_path(table["candidate"], "candidate")
    prefix = evaluators_dir.rstrip("/") + "/"
    if not evaluator.startswith(prefix) or not evaluator.endswith(".py"):
        raise SpecError(f"[python] evaluator must be a .py file under {prefix} (got {evaluator!r})")
    if not candidate.endswith(".py"):
        raise SpecError(f"[python] candidate must be a .py file (got {candidate!r})")
    entry = table["entry"]
    if not isinstance(entry, str) or not entry.isidentifier():
        raise SpecError(f"[python] entry must be a Python identifier (got {entry!r})")
    raw_files = table.get("files", [])
    if not isinstance(raw_files, list):
        raise SpecError("[python] files must be a list of repo-relative paths")
    files = tuple(dict.fromkeys(f for f in (_clean_path(v, "files") for v in raw_files) if f != candidate))
    for path in (candidate, *files):
        if path.startswith(prefix):
            raise SpecError(f"[python] candidate file {path!r} lies under {prefix}: the evaluator folder is "
                            "never mounted into the candidate's jail")
    return PythonSpec(evaluator=evaluator, candidate=candidate, entry=entry, files=files)


def _read_worktree(root: Path, rel: str) -> bytes:
    path = root
    for part in PurePosixPath(rel).parts:         # no symlink anywhere on the way, not only at the end
        path = path / part
        if path.is_symlink():
            where = path.relative_to(root).as_posix()
            raise InputError(f"{where} is a symlink; candidate-side inputs must be regular files reached without "
                             f"symlinks (reading {rel})")
    try:
        inside = path.resolve().is_relative_to(root.resolve())
    except OSError as exc:
        raise InputError(f"{rel}: {exc}") from exc
    if not inside:
        raise InputError(f"{rel} resolves outside the repository")
    data, problem = candidate_exec.read_regular_file(path, MAX_INPUT_BYTES)
    if data is None:
        raise InputError(f"{rel}: {'missing in the worktree' if problem == 'missing' else problem}")
    return data


def _write(path: Path, data: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def _short(value: Any, limit: int = 60) -> str:
    text = json.dumps(value, sort_keys=True, ensure_ascii=False)
    return text if len(text) <= limit else text[: limit - 3] + "..."


@functools.cache
def _bwrap_version() -> str:
    try:
        proc = subprocess.run([jail.program("bwrap") or "bwrap", "--version"], capture_output=True, text=True,
                              timeout=10)
        return proc.stdout.strip() or "unknown"
    except (OSError, subprocess.SubprocessError):
        return "unknown"


class PythonEvalAdapter:
    name = NAME

    def applies(self, item) -> bool:
        return bool(item.python)

    def check(self, request: CheckRequest) -> CheckOutcome:
        run = _Check(request)
        try:
            return run.run()
        except Exception as exc:  # never raise for a tool problem: report it
            return run.outcome("error", [f"internal error in the python-eval adapter: {type(exc).__name__}: {exc}"])


class _Check:
    """State of one check, so that every exit path returns a fully populated outcome."""

    def __init__(self, request: CheckRequest):
        self.request = request
        self.root = request.repo.root
        self.disabled = frozenset(request.guards)
        timeout = request.timeout if type(request.timeout) in (int, float) else 0.0
        self.deadline = time.monotonic() + timeout
        self.files: dict[str, str] = {}
        self.trusted_files: dict[str, str] = {}
        self.target: dict[str, Any] = {"kind": "python-evaluator"}
        self.environment: dict[str, Any] = {"adapter": NAME, "guards_disabled": sorted(self.disabled)}
        self.checked: dict[str, Any] = {}
        self.command: list[str] = [NAME, "--assurance", str(request.assurance)]
        self.notes: list[str] = []
        self.log: list[tuple[str, str]] = []

    # Plumbing -----------------------------------------------------------------

    def remaining(self) -> float:
        return self.deadline - time.monotonic()

    def guard(self, name: str) -> bool:
        """True when the guard is ON (not disabled by a test)."""
        return name not in self.disabled

    def outcome(self, verdict: str, reasons: list[str]) -> CheckOutcome:
        log = "\n".join(f"== {title} ==\n{text.rstrip()}" for title, text in self.log if text.strip())
        return CheckOutcome(
            verdict=verdict, reasons=reasons + self.notes, files=dict(self.files),
            trusted_files=dict(self.trusted_files), target=dict(self.target), environment=dict(self.environment),
            checked=dict(self.checked), command=list(self.command), log=log,
            extra={"guards_disabled": sorted(self.disabled), "notes": list(self.notes)},
        )

    def jail_argv(self, command: list[str], work: Path, read_only: tuple[Path, ...]) -> list[str]:
        box = jail.Jail(workdir=work, read_only=read_only, env=jail.filter_env(os.environ, jail.DEFAULT_ENV))
        return jail.memory_capped(box.argv(command), self.request.memory_max, self.request.memory_total)

    def judge_process(self, mode: str, stdin: dict[str, Any] | None, extra_ro: tuple[Path, ...] = (),
                      work_name: str = "work") -> tuple[dict[str, Any] | None, str | None]:
        """Run the judge runner in its own jail. Returns (payload, problem)."""
        budget = self.remaining()
        if budget <= 0:
            return None, f"no time left for the {mode} step (overall timeout {self.request.timeout}s)"
        work = self.judge_dir / work_name
        work.mkdir(parents=True, exist_ok=True)
        logs = self.judge_dir / "logs"
        logs.mkdir(parents=True, exist_ok=True)
        stdin_path = None
        if stdin is not None:
            stdin_path = _write(logs / f"{mode}.stdin.json", json.dumps(stdin).encode("utf-8"))
        command = [self.interpreter, "-I", "-B", str(self.judge_runner), mode, str(self.evaluator_copy)]
        argv = self.jail_argv(command, work, (self.evaluator_copy.parent, self.judge_runner.parent, *extra_ro))
        out, err = logs / f"{mode}.stdout", logs / f"{mode}.stderr"
        self.log.append((f"{mode} (judge process) argv", " ".join(argv)))
        returncode, timed_out = candidate_exec.run_process(argv, cwd=work, stdout=out, stderr=err,
                                                           timeout=budget, stdin=stdin_path)
        self.log.append((f"{mode} (judge process) stderr", candidate_exec.read_tail(err)))
        if timed_out:
            return None, f"the {mode} step exceeded the remaining time budget ({budget:.1f}s)"
        data, problem = candidate_exec.read_regular_file(out, MAX_JUDGE_BYTES)
        if not data:
            tail = candidate_exec.read_tail(err)[-MAX_MESSAGE:]
            return None, f"the {mode} step produced no result ({problem or 'empty'}; exit {returncode}): {tail}"
        try:
            payload = json.loads(data)
        except (ValueError, RecursionError) as exc:
            return None, f"the {mode} step returned unreadable JSON: {exc}"
        if not isinstance(payload, dict) or payload.get("status") != "ok":
            error = payload.get("error") if isinstance(payload, dict) else None
            return None, str(error or f"the {mode} step failed: {str(payload)[:MAX_MESSAGE]}")
        if returncode != 0:
            tail = candidate_exec.read_tail(err)[-MAX_MESSAGE:]
            return None, (f"the {mode} step exited with {returncode} after writing its result, which therefore does "
                          f"not count: {tail}")
        return payload, None

    # The check ------------------------------------------------------------------

    def run(self) -> CheckOutcome:
        request = self.request
        item = request.item
        if not item.python:
            return self.outcome("unsupported", [f"item '{item.id}' has no [python] table"])
        unknown = sorted(self.disabled - GUARDS)
        if unknown:
            return self.outcome("error", [f"unknown guard(s) {unknown}; known: {sorted(GUARDS)}"])
        if request.assurance not in ("protected", "exploratory"):
            return self.outcome("error", [f"assurance must be 'protected' or 'exploratory', got {request.assurance!r}"])
        if type(request.timeout) not in (int, float) or not request.timeout > 0:
            return self.outcome("error", [f"timeout must be a positive number of seconds, got {request.timeout!r}"])
        if self.disabled:
            self.notes.append(f"TEST ONLY: guards disabled {sorted(self.disabled)}; this outcome proves nothing")
        try:
            spec = parse_spec(item.python, request.repo.rel(EVALUATORS))
        except SpecError as exc:
            return self.outcome("error", [f"{item.path}: {exc}"])
        self.command += ["--evaluator", spec.evaluator, "--candidate", spec.candidate, "--entry", spec.entry]
        self.target.update({"evaluator": spec.evaluator, "candidate": spec.candidate, "entry": spec.entry})

        if not jail.available():
            return self.outcome("unsupported", ["bubblewrap (bwrap) is not installed: python-eval runs candidate "
                                                "and judge only inside the jail"])
        self.interpreter = os.path.normpath(str(request.repo.config.tools.get("python", DEFAULT_INTERPRETER)))
        if not (Path(self.interpreter).is_absolute()
                and any(self.interpreter.startswith(p + "/") for p in jail.SYSTEM_RO)
                and os.access(self.interpreter, os.X_OK)):
            return self.outcome("unsupported", [f"interpreter {self.interpreter!r} is not an executable under "
                                                f"{list(jail.SYSTEM_RO)}, so the jail cannot see it"])
        self.environment.update({"interpreter": self.interpreter, "bwrap": _bwrap_version(),
                                 "memory_max": request.memory_max, "memory_total": request.memory_total,
                                 "timeout_s": request.timeout})

        # Candidate-side inputs: hashed and copied from the same bytes, never re-read.
        scratch = Path(request.scratch).resolve()
        code_dir = scratch / "candidate" / "code"
        try:
            for rel in spec.candidate_files:
                data = _read_worktree(self.root, rel)
                self.files[rel] = sha256_hex(data)
                _write(code_dir / rel, data)
        except InputError as exc:
            return self.outcome("error", [f"candidate input: {exc}"])

        # Evaluator: trusted commit (protected) or worktree (exploratory, or guard disabled).
        problem = self.load_evaluator(spec, scratch)
        if problem:
            return self.outcome("error", [problem])

        described, problem = self.judge_process("describe", None)
        if problem:
            return self.outcome("error", [f"evaluator {spec.evaluator}: {problem}"])
        self.environment["python_version"] = described.get("python")
        cases, digest = described.get("cases"), described.get("digest")
        if not isinstance(cases, list) or not cases:
            return self.outcome("error", [f"evaluator {spec.evaluator} defines no CASES; nothing would be checked"])
        case_timeout = described.get("timeout_s")
        if case_timeout is None:
            case_timeout = DEFAULT_CASE_TIMEOUT
        if type(case_timeout) not in (int, float) or not math.isfinite(case_timeout) or case_timeout <= 0:
            return self.outcome("error", [f"evaluator {spec.evaluator}: TIMEOUT_S must be a positive number, "
                                          f"got {case_timeout!r}"])
        case_timeout = float(case_timeout)
        self.target["cases_sha256"] = digest
        self.environment["case_timeout_s"] = case_timeout
        self.checked = {"cases": len(cases), "cases_sha256": digest, "case_timeout_s": case_timeout}

        if not self.guard("separate_judge"):
            return self.run_in_process(spec, cases, digest, code_dir)
        return self.run_separated(spec, cases, digest, case_timeout, code_dir, scratch)

    def load_evaluator(self, spec: PythonSpec, scratch: Path) -> str | None:
        request = self.request
        self.judge_dir = scratch / "judge"
        if request.assurance == "protected" and self.guard("trusted_evaluator"):
            if self.guard("evaluator_binding"):
                problem = self.binding_problem(spec)
                if problem:
                    return problem
            data = gitref.show(self.root, request.trusted_commit, spec.evaluator)
            if data is None:
                return (f"evaluator {spec.evaluator} does not exist at the trusted commit "
                        f"{request.trusted_commit[:12]}; protected checks use only trusted evaluators")
            digest = sha256_hex(data)
            self.trusted_files[spec.evaluator] = digest
            self.target.update({"sha256": digest, "source": "trusted-commit", "commit": request.trusted_commit})
        else:
            try:
                data = _read_worktree(self.root, spec.evaluator)
            except InputError as exc:
                return f"evaluator: {exc}"
            digest = sha256_hex(data)
            self.files[spec.evaluator] = digest  # candidate-side: whoever edits the worktree controls it
            self.target.update({"sha256": digest, "source": "worktree"})
            if request.assurance == "exploratory":
                self.notes.append("exploratory: the evaluator was read from the worktree, so the candidate side "
                                  "controls it; this outcome can never make the item verified")
        self.evaluator_copy = _write(self.judge_dir / "evaluator" / PurePosixPath(spec.evaluator).name, data)
        self.judge_runner = _write(self.judge_dir / "runner" / "judge_runner.py", JUDGE_RUNNER.encode("utf-8"))
        return None

    def binding_problem(self, spec: PythonSpec) -> str | None:
        """Protected mode: which evaluator judges an item is a trusted decision, so it must match the trusted item."""
        rel = self.request.item.path
        data = gitref.show(self.root, self.request.trusted_commit, rel)
        if data is None:
            self.notes.append(f"{rel} is not on the trusted commit yet: the evaluator binding comes from the "
                              "worktree item, so a fidelity review must confirm that this evaluator fits the claim")
            return None
        try:
            trusted_item = parse_item(data, rel)
        except RecordError as exc:
            return f"the trusted version of {rel} does not parse: {exc}"
        trusted_evaluator = trusted_item.python.get("evaluator")
        if trusted_evaluator != spec.evaluator:
            return (f"evaluator binding changed: the worktree item names {spec.evaluator!r} but the trusted item "
                    f"names {trusted_evaluator!r}; a protected check only uses the trusted binding")
        return None

    def run_separated(self, spec: PythonSpec, cases: list[Any], digest: str, case_timeout: float,
                      code_dir: Path, scratch: Path) -> CheckOutcome:
        jailed = self.guard("candidate_jail")
        self.environment["isolation"] = {
            "candidate": ("bwrap jail: no network, clean environment, candidate files and runner read-only, "
                          "private work dir; evaluator not mounted") if jailed
                         else "NONE: plain subprocess with the host environment (guard candidate_jail disabled)",
            "judge": "separate bwrap jail: evaluator copy and runner read-only, private work dir; no candidate code",
        }
        own_bound = len(cases) * case_timeout + STARTUP_SLACK      # the evaluator's limit for this candidate
        budget = min(self.remaining(), own_bound)
        if budget <= 0:
            return self.outcome("error", [f"no time left to run the candidate (overall timeout {self.request.timeout}s)"])
        sandbox = (lambda cmd, layout: self.jail_argv(cmd, layout.work_dir, (layout.code_dir, layout.runner_dir))) \
            if jailed else None
        run = candidate_exec.run_candidate_calls(
            code_dir=code_dir, symbol=spec.entry, inputs=[[case] for case in cases], module_filename=spec.candidate,
            timeout=budget, case_timeout=case_timeout, run_dir=scratch / "candidate", interpreter=self.interpreter,
            sandbox=sandbox,
        )
        self.log.append(("candidate argv", " ".join(run.argv)))
        self.log.append(("candidate stdout", run.stdout))
        self.log.append(("candidate stderr", run.stderr))
        self.checked["candidate_status"] = run.status
        if run.status == candidate_exec.STATUS_IMPORT_ERROR:
            return self.outcome("fail", [f"candidate {spec.candidate} failed to import: {run.error}"])
        if run.status == candidate_exec.STATUS_ATTR_ERROR:
            return self.outcome("fail", [f"candidate {spec.candidate} does not define a callable {spec.entry!r}"])
        if run.status == candidate_exec.STATUS_TIMEOUT and budget < own_bound:
            return self.outcome("error", [f"the check's overall timeout ({self.request.timeout:g}s) stopped the "
                                          f"candidate after {budget:.1f}s, before the evaluator's own bound of "
                                          f"{own_bound:.1f}s ({len(cases)} cases x {case_timeout:g}s + "
                                          f"{STARTUP_SLACK:g}s); inconclusive: run it again with a longer --timeout"])
        if run.status == candidate_exec.STATUS_TIMEOUT:
            return self.outcome("fail", [f"candidate process exceeded its time budget of {budget:.1f}s and was "
                                         "killed; no case counts as passed"])
        if run.status == candidate_exec.STATUS_RUNNER_ERROR and run.started and candidate_exec.killed(run.returncode):
            return self.outcome("error", [f"the candidate process was killed (exit {run.returncode}: a signal, the "
                                          "memory or task cap, or the OOM killer); inconclusive, not a verdict on "
                                          f"the candidate: {run.error}"])
        if run.status == candidate_exec.STATUS_RUNNER_ERROR and run.started:
            return self.outcome("fail", [f"candidate process crashed or corrupted its result channel: {run.error}"])
        if run.status != candidate_exec.STATUS_OK:
            return self.outcome("error", [f"the candidate runner did not start ({run.status}): {run.error}"])

        outputs = [{"index": i, "value": o.value} for i, o in enumerate(run.outcomes) if o.ok]
        judged: dict[int, dict[str, Any]] = {}
        if outputs:
            payload, problem = self.judge_process("judge", {"digest": digest, "outputs": outputs})
            if problem:
                return self.outcome("error", [f"judge: {problem}"])
            judged, problem = _parse_results(payload.get("results"), {o["index"] for o in outputs})
            if problem:
                return self.outcome("error", [f"judge: {problem}"])
        return self.aggregate(cases, run.outcomes, judged)

    def run_in_process(self, spec: PythonSpec, cases: list[Any], digest: str, code_dir: Path) -> CheckOutcome:
        """TEST ONLY (guard `separate_judge` disabled): judge inside the candidate's process."""
        self.environment["isolation"] = {"candidate": "bwrap jail shared with the judge",
                                         "judge": "SAME PROCESS as the candidate (guard separate_judge disabled)"}
        module = (code_dir / spec.candidate).resolve()
        payload, problem = self.judge_process(
            "inprocess",
            {"digest": digest, "candidate": str(module), "entry": spec.entry, "sys_path": [str(module.parent)]},
            extra_ro=(code_dir.resolve(),), work_name="inprocess-work")
        if problem:
            return self.outcome("fail", [f"in-process run: {problem}"])
        outcomes = candidate_exec.parse_outcomes(payload.get("outcomes"))
        if outcomes is None or len(outcomes) != len(cases):
            return self.outcome("fail", ["in-process run returned a malformed outcome list"])
        judged, problem = _parse_results(payload.get("results"), {i for i, o in enumerate(outcomes) if o.ok})
        if problem:
            return self.outcome("error", [f"judge: {problem}"])
        return self.aggregate(cases, outcomes, judged)

    def aggregate(self, cases: list[Any], outcomes: list[candidate_exec.CallOutcome],
                  judged: dict[int, dict[str, Any]]) -> CheckOutcome:
        results, failures, judge_errors = [], [], []
        for index, (case, outcome) in enumerate(zip(cases, outcomes)):
            label = f"case {index} (input {_short(case)})"
            if not outcome.ok:
                status = "timeout" if outcome.timeout else "candidate-error"
                message = outcome.error or "no output"
            else:
                verdict = judged.get(index)
                if verdict is None:
                    status, message = "judge-error", "the judge returned no verdict for this case"
                elif "error" in verdict:
                    status, message = "judge-error", f"the evaluator raised: {verdict['error']}"
                elif verdict["passed"]:
                    status, message = "pass", verdict["message"]
                else:
                    status, message = "fail", verdict["message"] or "judge returned False"
            message = candidate_exec.candidate_text(message, MAX_MESSAGE)
            results.append({"index": index, "status": status, "message": message})
            if status == "judge-error":
                judge_errors.append(f"{label}: {message}")
            elif status != "pass":
                failures.append(f"{label}: {message}")
        n = len(cases)
        self.checked.update({
            "passed": sum(r["status"] == "pass" for r in results), "failed": len(failures),
            "judge_errors": len(judge_errors), "results": results[:MAX_CASE_RESULTS],
            "results_truncated": len(results) > MAX_CASE_RESULTS,
        })
        if judge_errors:
            reasons = [f"the evaluator failed on {len(judge_errors)} of {n} case(s); the check is inconclusive",
                       *_first(judge_errors)]
            if failures:
                reasons += [f"{len(failures)} other case(s) did not pass", *_first(failures)]
            return self.outcome("error", reasons)
        if failures:
            return self.outcome("fail", [f"{len(failures)} of {n} case(s) did not pass", *_first(failures)])
        return self.outcome("pass", [f"all {n} cases passed"])


def _first(lines: list[str]) -> list[str]:
    shown = lines[:MAX_REASONS]
    return shown + ([f"... and {len(lines) - len(shown)} more"] if len(lines) > len(shown) else [])


def _parse_results(raw: Any, sent: set[int]) -> tuple[dict[int, dict[str, Any]], str | None]:
    """Validate the judge's per-case verdicts: exactly one per case it was sent."""
    if not isinstance(raw, list):
        return {}, "results must be a list"
    judged: dict[int, dict[str, Any]] = {}
    for rec in raw:
        if not isinstance(rec, dict) or type(rec.get("index")) is not int:
            return {}, f"malformed verdict {str(rec)[:100]}"
        index = rec["index"]
        if index not in sent or index in judged:
            return {}, f"verdict for unexpected or repeated case {index}"
        if "error" in rec:
            judged[index] = {"error": str(rec["error"])[:MAX_MESSAGE]}
        elif type(rec.get("passed")) is bool:
            judged[index] = {"passed": rec["passed"], "message": str(rec.get("message") or "")[:MAX_MESSAGE]}
        else:
            return {}, f"malformed verdict {str(rec)[:100]}"
    if set(judged) != sent:
        return {}, f"no verdict for case(s) {sorted(sent - set(judged))[:10]}"
    return judged, None
