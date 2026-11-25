"""`vl check --all | --stale | --ids FILE`: protected checks of many items, `-j N` at a time, one summary.

Each check is an ordinary `vl check ID --json` process, so it waits in the machine's job queue (queue.py) like any
other and writes its own receipt; this module only selects the items, starts the processes and reads their answers.
The queue, not `-j`, bounds what runs at once. Only protected checks: the receipts are the integrator's to admit
(`vl admit`). Not under `vl queue run`: its one admission would hold every check of the batch, beside each other.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

from .. import jail, queue
from ..output import EXIT_FAIL, EXIT_PASS, EXIT_TOOL, emit, fail, listed
from ..records import Item
from ..repo import Repo
from ..status import derive

PIN_PREFIX = "# trusted commit "      # a line of a `vl jobs shard` file: the commit its jobs were cut for
# Statuses a protected check can improve. A `failed` item counts too once its answer changed (see `stale`).
STALE_LABELS = ("unverified", "verified-stale", "explored", "check-error", "check-unsupported")
VERDICT_OF_EXIT = {EXIT_PASS: ("pass",), EXIT_FAIL: ("fail",), EXIT_TOOL: ("error", "unsupported")}


@dataclass(frozen=True)
class Result:
    item: str
    verdict: str                 # pass, fail, error or unsupported (error also: the check did not run)
    seconds: float
    wait: float | None           # seconds in the machine's queue; None when unknown
    receipt: str | None
    reasons: tuple[str, ...]


def checkable(repo: Repo, items: dict[str, Item], load_adapters) -> list[str]:
    """Ids of the items a protected check can ask: a checker applies and the item is on the trusted ref."""
    on_trusted = repo.trusted_items()[0]
    return sorted(i for i, item in items.items()
                  if i in on_trusted and any(a.applies(item) for a in load_adapters()))


def stale(repo: Repo, item: Item, items: dict[str, Item]) -> bool:
    """Whether a protected check could change the item's status: never checked, stale, only explored, inconclusive,
    or failed on an answer that has since changed (a fail stays `failed` after the answer changes) or only
    exploratorily."""
    status = derive(repo, item, items)
    if status.label in STALE_LABELS:
        return True
    if status.label != "failed":
        return False
    record = next((r for r in repo.receipts(item.id) if r.path == status.receipt), None)
    return record is not None and (record.data["assurance"] != "protected"
                                   or bool(repo.stale_inputs(record.data, item, record.admitted)))


def read_ids(path: str) -> list[str]:
    """One item id per line of `path`; blank lines and `#` comments are ignored; repeated ids count once. ValueError
    when the file cannot be read."""
    try:
        lines = (line.split("#", 1)[0].strip() for line in Path(path).read_text().splitlines())
        return list(dict.fromkeys(line for line in lines if line))
    except OSError as exc:
        raise ValueError(f"--ids: cannot read {path}: {exc.strerror}") from exc


def pin_warning(path: str, repo: Repo) -> str | None:
    """Why the ids file `path` (a `vl jobs shard` file) does not fit this repository: it was cut for a trusted
    commit that is not the one the trusted ref is at, so its checks answer other questions than its jobs asked."""
    try:
        pinned = next((line[len(PIN_PREFIX):].strip() for line in Path(path).read_text().splitlines()
                       if line.startswith(PIN_PREFIX)), None)
    except OSError:
        return None
    if pinned is None or pinned == repo.trusted_commit:
        return None
    return (f"{path} was cut for the trusted commit {pinned[:12]}, but the trusted ref is at "
            f"{(repo.trusted_commit or 'no commit')[:12]}: these checks answer the questions there, and `vl jobs "
            "missing` keeps listing the jobs of that list; check out or fetch that commit, or cut the list again")


def select(args, repo: Repo, items: dict[str, Item], load_adapters) -> list[str]:
    """The ids to check: all that are checkable, only the stale ones, or exactly those listed in the file."""
    if args.ids:
        ids = read_ids(args.ids)
        unknown = [i for i in ids if i not in items]
        if unknown:
            raise ValueError(f"--ids: no such item: {listed(unknown)}")
        return ids
    ids = checkable(repo, items, load_adapters)
    return [i for i in ids if stale(repo, items[i], items)] if args.stale else ids


def command(item_id: str, args) -> list[str]:
    """The protected check of one item, as a process of this same Python. `-P`: the working directory, which holds
    the candidate's files, is not put on sys.path, so a `verifylab/` there is never imported as `vl`."""
    argv = [sys.executable, "-P", "-m", "verifylab.cli", "check", item_id, "--json", "--timeout", str(args.timeout)]
    if args.max_wait:
        argv += ["--max-wait", str(args.max_wait)]
    if anchor := getattr(args, "_batch_anchor", None):
        argv[2:4] = ["-c", "from verifylab.cli import main; raise SystemExit(main(_batch_worker=True))"]
        argv += ["--_batch-anchor", *anchor]
    elif args.trusted_ref:
        argv += ["--trusted-ref", args.trusted_ref]
    return argv


class _Children:
    """Running check subprocesses of one batch, stopped when the batch is cancelled or interrupted."""

    def __init__(self, env: dict[str, str], pass_fds: tuple[int, ...]):
        self.env, self.pass_fds = env, pass_fds
        self.cancelled = threading.Event()
        self._lock = threading.Lock()
        self._procs: set[subprocess.Popen[str]] = set()

    def start(self, argv: list[str]) -> subprocess.Popen[str] | None:
        with self._lock:
            if self.cancelled.is_set():
                return None
            proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                    env=self.env, pass_fds=self.pass_fds)
            self._procs.add(proc)
            return proc

    def discard(self, proc: subprocess.Popen[str]) -> None:
        with self._lock:
            self._procs.discard(proc)

    def stop_all(self, grace: float = 5.0) -> None:
        """Stop every running child check: SIGTERM first so it cleans owned systemd units, then SIGKILL."""
        with self._lock:
            self.cancelled.set()
            active = list(self._procs)
        for proc in active:
            if proc.poll() is None:
                try:
                    proc.terminate()
                except OSError:
                    pass
        deadline = time.monotonic() + grace
        for proc in active:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            if proc.poll() is None:
                try:
                    proc.wait(timeout=remaining)
                except subprocess.TimeoutExpired:
                    break
        for proc in active:
            if proc.poll() is None:
                try:
                    proc.kill()
                except OSError:
                    pass
                try:
                    proc.wait(timeout=2)
                except (OSError, subprocess.TimeoutExpired):
                    pass


def run_one(item_id: str, args, _children: _Children | None = None) -> Result:
    """Run one check and read its answer. A verdict counts only when it agrees with the exit code (a `fail` without a
    receipt, a check that stopped before reading any candidate file because one is missing, exits 3); a check that
    printed nothing usable (exit 2, a crash, a kill) is an `error` that carries the last line it wrote."""
    start = time.monotonic()
    try:
        if _children is None:
            proc = subprocess.run(command(item_id, args), capture_output=True, text=True)
            returncode, stdout_text, stderr_text = proc.returncode, proc.stdout, proc.stderr
        else:
            child = _children.start(command(item_id, args))
            if child is None:
                return Result(item_id, "error", 0.0, None, None, ("batch cancelled",))
            try:
                stdout_text, stderr_text = child.communicate()
                returncode = child.returncode
            finally:
                _children.discard(child)
    except OSError as exc:
        return Result(item_id, "error", 0.0, None, None, (f"cannot start vl check: {exc.strerror or exc}",))
    seconds = round(time.monotonic() - start, 1)
    try:
        data = json.loads(stdout_text)
    except ValueError:
        data = None
    agrees = VERDICT_OF_EXIT.get(returncode, ())
    if returncode == EXIT_TOOL and isinstance(data, dict) and data.get("receipt") is None:
        agrees += ("fail",)
    if isinstance(data, dict) and data.get("verdict") in agrees:
        wait = data.get("queue_wait_seconds")
        return Result(item_id, data["verdict"], seconds, wait if isinstance(wait, (int, float)) else None,
                      data.get("receipt"), tuple(str(r) for r in data.get("reasons") or ()))
    stderr = [line for line in stderr_text.strip().splitlines() if line.strip()]
    return Result(item_id, "error", seconds, None, None,
                  (stderr[-1] if stderr else f"vl check exited {returncode} without a verdict",))


def table(results: list[Result]) -> str:
    width = max([4, *(len(r.item) for r in results)])
    lines = [f"{'item':<{width}}  {'verdict':<11}  {'seconds':>8}  {'wait':>7}"]
    lines += [f"{r.item:<{width}}  {r.verdict:<11}  {r.seconds:>8.1f}  "
              f"{'-' if r.wait is None else format(r.wait, '.1f'):>7}" for r in results]
    return "\n".join(lines)


def exit_code(results: list[Result]) -> int:
    verdicts = {r.verdict for r in results}
    return EXIT_FAIL if "fail" in verdicts else EXIT_PASS if verdicts <= {"pass"} else EXIT_TOOL


def run(args, load_adapters, repo: Repo) -> int:
    for flag, used in (("--explore", args.assurance == "exploratory"), ("--keep-scratch", args.keep_scratch),
                       ("--reserve", args.reserve is not None)):
        if used:
            return fail(f"{flag}: not for a batch of protected checks; use `vl check ID {flag}` on one item")
    if args.jobs is not None and args.jobs < 1:
        return fail("-j: at least 1")
    machine_queue = queue.Queue.from_machine()
    if queue.inherited(machine_queue):
        return fail(f"{queue.ENV} names a running job: a batch queues each of its checks itself, so run it outside "
                    "`vl queue run`, whose single admission would let every check start at once")
    items, problems = repo.load_items()
    try:
        ids = select(args, repo, items, load_adapters)
    except ValueError as exc:
        return fail(str(exc))
    if problems:
        sys.stderr.write(f"vl: warning: {len(problems)} item file(s) do not parse and are left out (vl validate)\n")
    if args.ids and (why := pin_warning(args.ids, repo)):
        sys.stderr.write(f"vl: warning: {why}\n")
    if ids and repo.trusted_commit is None:
        return fail(f"the trusted ref '{repo.config.trusted_ref}' does not resolve to a commit")
    args._batch_anchor = (repo.trusted_commit, repo.config.trusted_ref, repo.config.trusted_ref_source)
    jobs = min(args.jobs or machine_queue.slots, max(len(ids), 1))
    results: dict[str, Result] = {}
    coord_r, coord_w = os.pipe()
    children = _Children(env={**os.environ, jail.COORDINATOR_FD_ENV: str(coord_r)}, pass_fds=(coord_r,))
    try:
        with jail.terminate_on_sigterm(), ThreadPoolExecutor(max_workers=jobs) as pool:
            futures = {pool.submit(run_one, i, args, children): i for i in ids}
            try:
                for done, future in enumerate(as_completed(futures), 1):
                    result = results[futures[future]] = future.result()
                    sys.stderr.write(f"vl: [{done}/{len(ids)}] {result.item}: {result.verdict} ({result.seconds:.0f}s)\n")
            except BaseException:     # Cancel/interrupt: stop running workers and drop pending futures
                children.stop_all()
                pool.shutdown(wait=False, cancel_futures=True)
                raise
    finally:
        for fd in (coord_r, coord_w):
            try:
                os.close(fd)
            except OSError:
                pass
    ordered = [results[i] for i in ids]
    counts = {v: sum(r.verdict == v for r in ordered) for v in ("pass", "fail", "error", "unsupported")}
    summary = f"{len(ordered)} checked: " + ", ".join(f"{n} {v}" for v, n in counts.items() if n) \
        if ordered else "nothing to check"
    text = "\n".join([*([table(ordered)] if ordered else []), *(
        f"  {r.item}: {r.reasons[0][:200]}" for r in ordered if r.verdict != "pass" and r.reasons), summary])
    if repo.trusted_commit:
        text += f"\nTrusted commit: {repo.trusted_commit}"
    emit(args, text, {"ok": exit_code(ordered) == EXIT_PASS, "trusted_commit": repo.trusted_commit,
                      "counts": counts, "checks": [
        {"item": r.item, "verdict": r.verdict, "seconds": r.seconds, "queue_wait_seconds": r.wait,
         "receipt": r.receipt, "reasons": list(r.reasons)} for r in ordered]})
    return exit_code(ordered)
