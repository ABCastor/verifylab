"""Process-isolated execution of untrusted candidate code.

Why: a verifier that imports the candidate into its own interpreter and then calls a module-global scorer
can be subverted: an import-time side effect such as `import __main__; __main__._run = lambda ...` rebinds the
scorer, and a function returning nonsense scores full marks (cheat R1 in docs/CHEATS.md).

The candidate is imported and called in a SEPARATE child process (`_candidate_child.py`) that receives
only the call INPUTS and returns the raw OUTPUTS as JSON. Expected answers, the evaluator and the judge
never enter that process. Whatever the candidate does there (rebind `__main__`, patch `json`, overwrite
the result file), it can only supply output values. Any child anomaly is fail-closed.

Beyond the separate process: the child can run inside a sandbox (the python-eval adapter wraps it in the
bwrap jail), the child script is copied next to the request so the sandbox needs no access to this
package, a per-case timer runs inside the child, the result file is read without following symlinks and
with a size bound, and stdout/stderr go to files outside the sandbox so a chatty candidate cannot exhaust
parent memory.
"""
from __future__ import annotations

import json
import os
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Sequence

# Status values on CandidateRun.status.
STATUS_OK = "ok"
STATUS_MISSING_FILE = "missing_file"
STATUS_IMPORT_ERROR = "import_error"
STATUS_ATTR_ERROR = "attr_error"
STATUS_TIMEOUT = "timeout"
STATUS_RUNNER_ERROR = "runner_error"

CHILD_SCRIPT = Path(__file__).with_name("_candidate_child.py")
RESULT_NAME = ".vl-candidate-result.json"

DEFAULT_TIMEOUT_SECONDS = 120.0
MAX_RESULT_BYTES = 64 * 1024 * 1024    # also the per-file write limit inside the child
LOG_TAIL_BYTES = 32 * 1024
MAX_TEXT = 500                         # characters of a candidate-written message kept in reasons and receipts


def candidate_text(value: str, limit: int = MAX_TEXT) -> str:
    """Text the candidate controls, safe to put in a reason, a receipt or a terminal: cut at `limit` characters,
    with every non-printable character (newline, escape, bidi control) shown as its Python escape."""
    shown = "".join(ch if ch.isprintable() else ascii(ch)[1:-1] for ch in value[:limit])
    return shown + (f" [... {len(value) - limit} more characters cut]" if len(value) > limit else "")


def killed(returncode: int | None) -> bool:
    """Whether a process was ended by a signal: negative for a direct child, 128 + the signal through bubblewrap or a
    shell. The memory cap, the OOM killer and the task cap end a process this way, so it says nothing about what the
    process computed."""
    return isinstance(returncode, int) and (returncode < 0 or returncode >= 128)


@dataclass(frozen=True)
class CallOutcome:
    """Result of one candidate call. `ok` distinguishes a returned value from a raised exception, a
    per-case timeout or an unserializable return; a judge decides pass/fail from `value`."""

    ok: bool
    value: Any = None
    error: str | None = None
    timeout: bool = False


@dataclass(frozen=True)
class CandidateRun:
    """Outcome of importing the candidate and running every requested call in one isolated child."""

    status: str
    error: str | None = None
    outcomes: list[CallOutcome] = field(default_factory=list)
    stdout: str = ""
    stderr: str = ""
    started: bool = False          # the runner got as far as importing the candidate
    returncode: int | None = None
    argv: tuple[str, ...] = ()


@dataclass(frozen=True)
class RunLayout:
    """Directories of one run. A sandbox mounts `code_dir` and `runner_dir` read-only and `work_dir`
    read-write; `log_dir` stays outside the sandbox."""

    code_dir: Path
    runner_dir: Path
    work_dir: Path
    log_dir: Path

    @property
    def result_path(self) -> Path:
        return self.work_dir / RESULT_NAME


Sandbox = Callable[[list[str], RunLayout], list[str]]


def read_regular_file(path: Path, limit: int) -> tuple[bytes | None, str | None]:
    """Read a file the candidate could have tampered with: no symlinks, no fifos, bounded size."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return None, "missing"
    except OSError as exc:
        return None, f"cannot open ({exc.strerror or exc})"
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            return None, "not a regular file"
        if info.st_size > limit:
            return None, f"larger than {limit} bytes"
        chunks, size = [], 0
        while size <= limit:
            chunk = os.read(fd, 1 << 20)
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
        if size > limit:
            return None, f"larger than {limit} bytes"
        return b"".join(chunks), None
    finally:
        os.close(fd)


def read_tail(path: Path, limit: int = LOG_TAIL_BYTES) -> str:
    """Last `limit` bytes of a parent-owned log file, with a note when truncated."""
    try:
        size = path.stat().st_size
        with path.open("rb") as handle:
            if size > limit:
                handle.seek(size - limit)
            data = handle.read(limit)
    except OSError:
        return ""
    text = data.decode("utf-8", errors="replace")
    return f"[... {size - limit} earlier bytes omitted ...]\n{text}" if size > limit else text


def run_process(argv: Sequence[str], *, cwd: Path, stdout: Path, stderr: Path, timeout: float,
                stdin: Path | None = None) -> tuple[int | None, bool]:
    """Run `argv` with output to files; on timeout kill its whole process group. Returns (returncode, timed_out)."""
    with open(stdout, "wb") as out, open(stderr, "wb") as err, \
            (open(stdin, "rb") if stdin else open(os.devnull, "rb")) as inp:
        proc = subprocess.Popen(list(argv), stdin=inp, stdout=out, stderr=err, cwd=cwd, start_new_session=True)
        try:
            return proc.wait(timeout=max(timeout, 0.001)), False
        except subprocess.TimeoutExpired:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            proc.wait()
            return None, True


def parse_outcomes(raw: Any) -> list[CallOutcome] | None:
    if not isinstance(raw, list):
        return None
    outcomes = []
    for rec in raw:
        if not isinstance(rec, dict):
            return None
        error = rec.get("error")
        outcomes.append(CallOutcome(
            ok=rec.get("ok") is True,
            value=rec.get("value"),
            error=candidate_text(error) if isinstance(error, str) else None,
            timeout=rec.get("timeout") is True,
        ))
    return outcomes


def run_candidate_calls(
    *,
    code_dir: Path | str,
    symbol: str,
    inputs: Sequence[Sequence[Any]],
    module_filename: str = "main.py",
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    case_timeout: float | None = None,
    run_dir: Path | None = None,
    interpreter: str | None = None,
    sandbox: Sandbox | None = None,
    max_file_bytes: int = MAX_RESULT_BYTES,
) -> CandidateRun:
    """Import `<code_dir>/<module_filename>` and call `symbol` once per entry of `inputs` (each entry is
    the positional argument list of one call), all inside one isolated child process.

    `inputs` carries only call arguments, never expected answers. `timeout` bounds the whole child
    process and is enforced here; `case_timeout` is a per-call timer inside the child (a candidate can
    defeat it, which only turns its run into a whole-process timeout). `sandbox` wraps the command (the
    adapter passes the jail); without it the child is a plain subprocess.
    When `status == "ok"` there is exactly one CallOutcome per input, in order.
    """
    code_dir = Path(code_dir)
    module_path = code_dir / module_filename
    if not module_path.is_file():
        return CandidateRun(status=STATUS_MISSING_FILE, error=f"candidate file missing: {module_path}")

    if run_dir is None:
        with tempfile.TemporaryDirectory(prefix="vl_candidate_") as tmp:
            return _run(code_dir, module_path, symbol, inputs, timeout, case_timeout, Path(tmp),
                        interpreter, sandbox, max_file_bytes)
    return _run(code_dir, module_path, symbol, inputs, timeout, case_timeout, Path(run_dir),
                interpreter, sandbox, max_file_bytes)


def _run(code_dir: Path, module_path: Path, symbol: str, inputs: Sequence[Sequence[Any]], timeout: float,
         case_timeout: float | None, run_dir: Path, interpreter: str | None, sandbox: Sandbox | None,
         max_file_bytes: int) -> CandidateRun:
    layout = RunLayout(code_dir=code_dir.resolve(), runner_dir=(run_dir / "runner").resolve(),
                       work_dir=(run_dir / "work").resolve(), log_dir=(run_dir / "logs").resolve())
    for folder in (layout.runner_dir, layout.work_dir, layout.log_dir):
        folder.mkdir(parents=True, exist_ok=True)
    runner = layout.runner_dir / CHILD_SCRIPT.name
    shutil.copyfile(CHILD_SCRIPT, runner)
    request = {
        "module_path": str(module_path.resolve()),
        "symbol": symbol,
        "inputs": [list(args) for args in inputs],
        "sys_path": [str(module_path.resolve().parent)],
        "case_timeout": case_timeout,
        "max_file_bytes": max_file_bytes,
    }
    request_path = layout.runner_dir / "request.json"
    request_path.write_text(json.dumps(request), encoding="utf-8")

    command = [interpreter or sys.executable, "-I", "-B", str(runner), str(request_path), str(layout.result_path)]
    argv = sandbox(command, layout) if sandbox else command
    out_path, err_path = layout.log_dir / "candidate.stdout", layout.log_dir / "candidate.stderr"
    try:
        returncode, timed_out = run_process(argv, cwd=layout.work_dir, stdout=out_path, stderr=err_path,
                                            timeout=timeout)
    except OSError as exc:
        return CandidateRun(status=STATUS_RUNNER_ERROR, error=f"cannot launch the runner: {exc}", argv=tuple(argv))
    stdout, stderr = read_tail(out_path), read_tail(err_path)
    common = {"stdout": stdout, "stderr": stderr, "returncode": returncode, "argv": tuple(argv)}

    data, problem = read_regular_file(layout.result_path, max_file_bytes)
    started = problem != "missing"
    if timed_out:
        return CandidateRun(status=STATUS_TIMEOUT, error=f"candidate timed out after {timeout:.1f}s",
                            started=started, **common)
    if data is None:
        tail = candidate_text((stderr or stdout)[-MAX_TEXT:])
        return CandidateRun(status=STATUS_RUNNER_ERROR, started=started,
                            error=f"child produced no readable result ({problem}; exit {returncode}): {tail}",
                            **common)
    try:
        payload = json.loads(data)
    except (ValueError, RecursionError) as exc:
        return CandidateRun(status=STATUS_RUNNER_ERROR, started=True,
                            error=f"child result not readable JSON: {exc}", **common)
    if not isinstance(payload, dict):
        return CandidateRun(status=STATUS_RUNNER_ERROR, started=True, error="child result is not an object",
                            **common)

    child_status = payload.get("status")
    child_error = candidate_text(payload["error"]) if isinstance(payload.get("error"), str) else None
    if child_status == "import_error":
        return CandidateRun(status=STATUS_IMPORT_ERROR, error=child_error, started=True, **common)
    if child_status == "attr_error":
        return CandidateRun(status=STATUS_ATTR_ERROR, error=child_error, started=True, **common)
    if child_status == "started":
        return CandidateRun(status=STATUS_RUNNER_ERROR, started=True,
                            error=f"candidate process died before finishing (exit {returncode})", **common)
    if child_status != "ok":
        return CandidateRun(status=STATUS_RUNNER_ERROR, started=True,
                            error=f"unexpected child status {candidate_text(repr(child_status))}", **common)
    outcomes = parse_outcomes(payload.get("outcomes"))
    if outcomes is None or len(outcomes) != len(inputs):
        return CandidateRun(status=STATUS_RUNNER_ERROR, started=True,
                            error=f"child returned a malformed outcome list for {len(inputs)} inputs", **common)
    if returncode != 0:
        # A result counts only from a process that finished: one that exits non-zero (or is killed) after writing it
        # did not complete the run the result describes.
        return CandidateRun(status=STATUS_RUNNER_ERROR, started=True,
                            error=f"the candidate process exited with {returncode} after writing its result, which "
                                  "therefore does not count", **common)
    return CandidateRun(status=STATUS_OK, outcomes=outcomes, started=True, **common)
