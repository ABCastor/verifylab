"""Untrusted-candidate runner, executed as a throwaway child process (inside the jail in production).

Invoked by `verifylab.candidate_exec.run_candidate_calls` as::

    python -I -B _candidate_child.py <request.json> <result.json>

The request carries `{module_path, symbol, inputs, sys_path, case_timeout, max_file_bytes}`: the call
INPUTS only, never the expected answers. This process imports the candidate module, calls `symbol` once
per input and writes the raw outputs to `result.json`. The parent does no scoring either: a separate
judge process does. Nothing trusted lives here, so a candidate that does `import __main__` and rebinds
globals, or patches builtins or `json`, reaches only this disposable runner.

Stdlib only and free of any `verifylab` import on purpose: it is copied alone into the jail and must
not drag trusted code into the candidate's address space. The writer primitives are bound as locals
BEFORE the candidate is imported, so import-time monkeypatching cannot redirect the result channel.
Even so, everything here is the candidate's territory: whatever it does, it can only supply output
values, and the judge compares those against answers this process never sees.
"""
from __future__ import annotations

import importlib.util
import json
import resource
import signal
import sys
import traceback


class _CaseTimeout(BaseException):
    """Raised by the per-case timer. BaseException so a bare `except Exception` in a candidate does not eat it."""


def _exc_line(exc: BaseException) -> str:
    return traceback.format_exception_only(type(exc), exc)[-1].strip()[:500]


def _alarm(signum, frame):  # noqa: ARG001
    raise _CaseTimeout()


def _run() -> int:
    req_path, res_path = sys.argv[1], sys.argv[2]

    # Capture the primitives before any untrusted import can rebind them.
    _open = open
    _json_dump = json.dump
    _json_dumps = json.dumps
    _setitimer = signal.setitimer
    _signal = signal.signal

    def _write(payload: dict) -> None:
        with _open(res_path, "w", encoding="utf-8") as handle:
            _json_dump(payload, handle)

    with _open(req_path, "r", encoding="utf-8") as handle:
        request = json.load(handle)
    module_path = request["module_path"]
    symbol = request["symbol"]
    inputs = request["inputs"]
    case_timeout = request.get("case_timeout")
    max_file_bytes = request.get("max_file_bytes")

    if max_file_bytes:
        # Hard limit, set before the candidate runs: it cannot raise it again (no capabilities in the jail).
        resource.setrlimit(resource.RLIMIT_FSIZE, (max_file_bytes, max_file_bytes))
    for entry in reversed(request.get("sys_path", [])):
        sys.path.insert(0, entry)

    # Marker written before the candidate is imported: a result file that still says "started" means the
    # candidate killed or corrupted the runner (a candidate failure); no file at all means the runner
    # never started (a tool failure).
    _write({"status": "started", "error": None, "outcomes": []})

    try:
        spec = importlib.util.spec_from_file_location("candidate_main", module_path)
        if spec is None or spec.loader is None:
            raise ImportError(f"cannot create import spec for {module_path}")
        module = importlib.util.module_from_spec(spec)
        # Register before exec so a candidate using @dataclass / typing sees itself in sys.modules.
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
    except Exception as exc:  # untrusted candidate: any import failure is a load error
        _write({"status": "import_error", "error": _exc_line(exc), "outcomes": []})
        return 0

    fn = getattr(module, symbol, None)
    if not callable(fn):
        _write({"status": "attr_error", "error": f"candidate must define a callable {symbol!r}", "outcomes": []})
        return 0

    outcomes: list[dict] = []
    for args in inputs:
        try:
            if case_timeout:
                _signal(signal.SIGALRM, _alarm)
                _setitimer(signal.ITIMER_REAL, case_timeout)
            try:
                value = fn(*args)
            finally:
                if case_timeout:
                    _setitimer(signal.ITIMER_REAL, 0)
        except _CaseTimeout:
            outcomes.append({"ok": False, "value": None, "timeout": True,
                             "error": f"timed out after {case_timeout}s"})
            continue
        except Exception as exc:
            outcomes.append({"ok": False, "value": None, "error": _exc_line(exc)})
            continue
        try:
            _json_dumps(value, allow_nan=False)  # the transport is strict JSON; reject anything else
        except (TypeError, ValueError, RecursionError):
            outcomes.append({"ok": False, "value": None, "error": f"unserializable return: {type(value).__name__}"})
            continue
        outcomes.append({"ok": True, "value": value, "error": None})

    _write({"status": "ok", "error": None, "outcomes": outcomes})
    return 0


if __name__ == "__main__":
    raise SystemExit(_run())
