"""`vl queue`: the machine's job queue (queue.py) for any heavy job, and what it holds now.

`vl queue run -- CMD` waits for admission like a check, then runs CMD in a systemd scope in the job's slice in
vl.slice, with the reservation as MemoryMax; CMD is the caller's own job, so it runs outside the jail, with network
and files.
"""

from __future__ import annotations

import argparse
import sys

from .. import jail, machine, queue as jobs
from ..output import emit, fail
from . import JSON_HELP, action_flags, seconds

RUN_JSON = "refused: `vl queue run` passes the command's own output and exit code through unchanged"


def size(text: str) -> int:
    """An argparse type: a memory size such as 2G or 512M, in bytes."""
    try:
        value = machine.size_bytes(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from None
    if not value:
        raise argparse.ArgumentTypeError(f"must be a positive size such as 2G, not {text!r}")
    return value


def register(sub):
    p = sub.add_parser("queue", help="the machine's job queue: wait for memory and a CPU slot, then run")
    actions = p.add_subparsers(dest="action", required=True)
    run_ = actions.add_parser("run", help="run a command once the queue admits it. With systemd it runs under a memory "
                                          "cap in vl.slice; otherwise it warns and runs uncapped")
    run_.add_argument("cmd", nargs="+", help="command after --, e.g. -- python train.py")
    run_.add_argument("--mem", type=size, help="memory to reserve and, with systemd, the command's MemoryMax (default: machine.toml "
                                               "[queue] default_reservation)")
    run_.add_argument("--max-wait", type=seconds, help="give up (exit 2) after waiting this many seconds")
    status = actions.add_parser("status", help="running jobs, reservations, MemAvailable, the floor and how many wait")
    status.add_argument("--explain", action="store_true", help="also print each limit, its value and where it came "
                        "from: a flag, machine.toml or the default")
    status.add_argument("--mem", type=size, help="with --explain: the limits of a `vl queue run --mem SIZE`")
    status.add_argument("--max-wait", type=seconds, help="with --explain: the limits of a run with this --max-wait")
    for name, action in actions.choices.items():
        action_flags(action, RUN_JSON if name == "run" else JSON_HELP, trusted_ref=False)
    return p


def _run(args) -> int:
    if getattr(args, "json", False):
        return fail(f"--json: {RUN_JSON}; run it without --json")
    command = args.cmd[1:] if args.cmd[0] == "--" else args.cmd
    if not command:
        return fail("no command: vl queue run [--mem SIZE] -- CMD ...")
    loaded = machine.load()
    queue = jobs.Queue.from_machine(loaded)
    reservation = args.mem or queue.default_reservation
    uncapped = jail.cap_problem(str(reservation))
    if uncapped:
        sys.stderr.write(f"vl: warning: this command runs without a memory cap ({uncapped})\n")
    try:
        with jobs.admit(" ".join(command)[:200], reservation, args.max_wait, queue) as ticket, jobs.inside(ticket):
            return ticket.run(jail.memory_capped(command, str(reservation), loaded.total_memory_cap())).returncode
    except (RuntimeError, OSError) as exc:     # QueueError, a slice that cannot be capped, no such program
        return fail(str(exc))


def _value(name: str, value) -> str:
    if name == "max_wait":
        return "no limit" if value is None else f"{value:g}s"
    return str(value) if name in ("slots", "threads") else jobs.gib(value)


def _status(args) -> int:
    if (args.mem or args.max_wait) and not args.explain:
        return fail("--mem and --max-wait on status need --explain")
    queue = jobs.Queue.from_machine()
    now = jobs.state(queue)
    lines = [f"queue: {now.summary(queue)}, {now.waiting} waiting; vl.slice uses {jobs.gib(now.in_use)}",
             *(f"  {job.pid}  {jobs.gib(job.reservation)}  since {job.since}  {job.label}" for job in now.running)]
    explained = jobs.limits(mem=args.mem, max_wait=args.max_wait) if args.explain else []
    if explained:
        lines += ["limits (flag > machine.toml > default):",
                  *(f"  {r['name']:<20} {_value(r['name'], r['value']):<10} {r['source']}: {r['where']}"
                    for r in explained)]
    emit(args, "\n".join(lines), {
        "slots": queue.slots, "threads": queue.threads, "keep_free": queue.keep_free, "memory_total": queue.memory_total,
        "mem_available": now.available, "slice_in_use": now.in_use, "reserved": now.reserved,
        "waiting": now.waiting, "state": str(queue.root),
        "running": [{"pid": j.pid, "label": j.label, "reservation": j.reservation, "since": j.since}
                    for j in now.running],
        **({"limits": explained} if args.explain else {})})
    return 0


def run(args) -> int:
    return {"run": _run, "status": _status}[args.action](args)
