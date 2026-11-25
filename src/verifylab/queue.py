"""The machine's job queue: a heavy job starts only when memory and a CPU slot are free for it, else it waits.

A job declares a reservation, the memory it expects to need. It is admitted when, all three at once:

* fewer jobs than `[queue] slots` run (default: the number of CPUs divided by `[queue] threads`, the CPUs one check
  may use, so that the checks admitted together never ask for more CPUs than there are: machine.Machine.threads);
* the reservations of the running jobs plus its own fit the cap of all vl runs (machine.toml `[check] memory_total`);
* MemAvailable, minus the part of each running job's reservation it does not use yet, minus its own reservation,
  leaves at least `[queue] keep_free` (default 3G). What a job uses is the anonymous memory of its own slice in
  vl.slice (`jail.job_slice`), where its capped runs live, or 0 when that cannot be read; page cache is not
  counted, as MemAvailable counts most of it as free. Each job is charged its own: a job beyond its reservation does
  not hide another's growth to come.

Else it waits, polling, with one line on stderr, up to `max_wait` seconds when one is set. A reservation that, with
`keep_free`, exceeds the machine's RAM (MemTotal) could never start, and would hold up every job behind it as the head
of the line: it is refused at once. The queue only orders
starts: a job that grows beyond its reservation is still bounded by its own MemoryMax and the slice's cap. The oldest
waiting job's reservation also counts against every other job, so a big job is not starved by a stream of small ones
that always fit beside the running jobs.

State lives in files under `$XDG_STATE_HOME/verifylab/queue/` (default `~/.local/state/verifylab/queue/`), held
with flock: a running job is a file in `jobs/` its process holds locked, a waiting one a file in `waiting/`, so a
holder that dies releases its place at once and its file is swept at the next admission. The job inherits the lock
from the `vl` process that runs it (`Ticket.run`), so killing that wrapper keeps the place until the job ends. A job
whose holder died while runs of its slice go on (a check's systemd services, which are not its children) still counts,
as running, with what it uses as its reservation, until its slice is empty. An empty job slice whose unit systemd
stopped but whose folder it left behind is removed at the next admission, which would otherwise read every one.
`admission.lock` serialises the decisions. A process started under `vl queue run` names its job in VL_QUEUE_JOB;
a vl command in it that would queue again runs inside that admission instead of waiting for a second one, and in its slice.
"""

from __future__ import annotations

import fcntl
import json
import os
import secrets
import subprocess
import sys
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from . import jail, machine

DEFAULTS = {"keep_free": "3G", "default_reservation": "2G"}
ENV = jail.JOB_ENV
POLL = 1.0


class QueueError(RuntimeError):
    """The job was not admitted in time."""


def gib(size: int | None) -> str:
    return "unbounded" if size is None else f"{size / (1 << 30):.1f}G"


def state_root() -> Path:
    return machine.queue_state_root()


def slice_cgroup() -> Path:
    uid = os.getuid()
    return Path(f"/sys/fs/cgroup/user.slice/user-{uid}.slice/user@{uid}.service/{jail.SLICE}")


@dataclass(frozen=True)
class Queue:
    """The queue's limits and where it reads them; the paths are parameters so tests can plant them."""

    slots: int
    keep_free: int
    default_reservation: int
    memory_total: int | None                     # None: unbounded
    max_wait: float | None = None                # None: wait as long as it takes
    root: Path = Path()
    meminfo: Path = Path("/proc/meminfo")
    cgroup: Path = Path()                        # vl.slice's cgroup folder
    poll: float = POLL
    threads: int = 1                             # CPUs one check may use (machine.Machine.threads)

    @classmethod
    def from_machine(cls, loaded: machine.Machine | None = None) -> "Queue":
        loaded = loaded if loaded is not None else machine.load()
        conf = {**DEFAULTS, **loaded.queue}
        total = loaded.total_memory_cap()
        try:
            memory_total = machine.size_bytes(total) if total else None
        except ValueError as exc:
            raise machine.MachineError(f"{loaded.path}: [check] memory_total: {exc}") from None
        threads = loaded.threads()
        return cls(slots=int(conf.get("slots") or max(1, (os.cpu_count() or 1) // threads)), threads=threads,
                   keep_free=machine.size_bytes(conf["keep_free"]),
                   default_reservation=machine.size_bytes(conf["default_reservation"]),
                   memory_total=memory_total, max_wait=conf.get("max_wait"), root=state_root(),
                   cgroup=slice_cgroup())


def limits(mem: int | None = None, max_wait: float | None = None) -> list[dict]:
    """Every limit the queue applies to a run, its value and where it came from, highest source first: `flag` (`mem`
    and `max_wait` stand for the `--mem`/`--reserve` and `--max-wait` of the run in question), `machine.toml`, else
    `default`."""
    loaded = machine.load()
    queue = Queue.from_machine(loaded)

    def row(name: str, value, entry: str | None, default: str, flag: str | None = None) -> dict:
        source, where = ("flag", flag) if flag else ("machine.toml", entry) if entry else ("default", default)
        return {"name": name, "value": value, "source": source, "where": where}

    def listed(key: str) -> str | None:
        return f"[queue] {key}" if key in loaded.queue else None

    return [
        row("threads", queue.threads, listed("threads"), f"{machine.DEFAULT_THREADS}, at most the number of CPUs"),
        row("slots", queue.slots, listed("slots"), "the number of CPUs divided by threads"),
        row("memory_total", queue.memory_total, "[check] memory_total" if loaded.memory_total else None,
            f"{machine.MEMORY_TOTAL_SHARE:.0%} of RAM"),
        row("keep_free", queue.keep_free, listed("keep_free"), DEFAULTS["keep_free"]),
        row("default_reservation", queue.default_reservation, listed("default_reservation"),
            DEFAULTS["default_reservation"]),
        *([row("reservation", mem, None, "", "--mem")] if mem is not None else []),
        row("max_wait", max_wait if max_wait is not None else queue.max_wait, listed("max_wait"), "no limit",
            "--max-wait" if max_wait is not None else None),
    ]


@dataclass(frozen=True)
class Job:
    pid: int
    label: str
    reservation: int
    since: str
    in_use: int = 0                              # anonymous memory of its own slice; 0 when it cannot be read


@dataclass(frozen=True)
class State:
    running: tuple[Job, ...]
    waiting: int
    available: int | None                        # MemAvailable; None when it cannot be read
    in_use: int                                  # anonymous memory of vl.slice

    @property
    def reserved(self) -> int:
        return sum(job.reservation for job in self.running)

    @property
    def pending(self) -> int:
        """What the running jobs reserved and do not use yet, each job counted against its own usage."""
        return sum(max(job.reservation - job.in_use, 0) for job in self.running)

    def summary(self, queue: Queue) -> str:
        return (f"{len(self.running)} of {queue.slots} slots busy, {gib(self.reserved)} reserved of "
                f"{gib(queue.memory_total)}, MemAvailable {gib(self.available)}, floor {gib(queue.keep_free)}")


def _held(path: Path) -> bool:
    """Whether a live process holds `path` locked; False when it is gone."""
    try:
        with path.open("rb") as handle:
            try:
                fcntl.flock(handle, fcntl.LOCK_SH | fcntl.LOCK_NB)
            except BlockingIOError:
                return True
            return False
    except FileNotFoundError:
        return False


def _unusable(folder: Path, exc: OSError) -> QueueError:
    return QueueError(f"queue: cannot use {folder} ({exc}); set XDG_STATE_HOME to a writable folder shared by every "
                      "vl on this machine")


def _hold(folder: Path, data: dict, waited: float | None = None) -> "Ticket":
    """A new file in `folder` with `data`, locked by this process before any reader can see it."""
    token = f"{os.getpid()}-{secrets.token_hex(6)}"
    tmp = folder / f".{token}.tmp"
    try:
        folder.mkdir(parents=True, exist_ok=True)
        handle = tmp.open("w")
    except OSError as exc:
        raise _unusable(folder, exc) from exc
    fcntl.flock(handle, fcntl.LOCK_EX)
    handle.write(json.dumps(data))
    handle.flush()
    path = folder / f"{token}.json"
    tmp.replace(path)                            # the lock follows the inode
    return Ticket(path, handle, waited)


def _live(folder: Path, sweep: bool) -> list[Path]:
    """The held files of `folder`; with `sweep` (under the admission lock), those of dead holders are removed."""
    live = []
    for path in sorted(folder.glob("*.json")) if folder.is_dir() else ():
        if _held(path):
            live.append(path)
        elif sweep:
            path.unlink(missing_ok=True)
    return live


def state(queue: Queue, sweep: bool = False) -> State:
    running = []
    places = _live(queue.root / "jobs", sweep)
    for path in places:
        try:
            data = json.loads(path.read_text())
            running.append(Job(int(data["pid"]), str(data["label"]), int(data["reservation"]), str(data["since"]),
                               jail.anonymous(queue.cgroup / jail.job_slice(path.stem)) or 0))
        except (OSError, ValueError, KeyError, TypeError):
            continue                             # released while being read
    held = {jail.job_slice(path.stem) for path in places}
    idle = []
    for left in sorted(queue.cgroup.glob("vl-*.slice")):      # runs of a job whose holder died: they still take room
        if left.name in held:
            continue
        try:
            busy = "populated 1" in (left / "cgroup.events").read_text()
        except OSError:
            continue
        if busy:
            used = jail.anonymous(left) or 0
            running.append(Job(0, f"runs left by a job that ended ({left.name})", used, "", used))
        elif sweep:
            idle.append(left)
    if idle:
        jail.remove_stopped_slices(idle)
    return State(tuple(running), len(_live(queue.root / "waiting", sweep)),
                 machine.meminfo_field("MemAvailable", queue.meminfo), jail.anonymous(queue.cgroup) or 0)


def blocked(queue: Queue, now: State, reservation: int) -> str | None:
    """Why a job reserving `reservation` bytes cannot start now, or None."""
    if len(now.running) >= queue.slots:
        return f"all {queue.slots} slots are busy"
    if queue.memory_total is not None and now.reserved + reservation > queue.memory_total:
        return f"{gib(reservation)} more would exceed memory_total"
    if now.available is not None:
        if now.available - now.pending - reservation < queue.keep_free:
            return f"{gib(reservation)} more would leave less than {gib(queue.keep_free)} free"
    return None


class Ticket:
    """An admission: the job's place in `jobs/`, held until `release` (or until its process ends)."""

    def __init__(self, path: Path | None, handle, waited: float | None):
        self.path, self.handle, self.waited = path, handle, waited

    @property
    def token(self) -> str | None:
        return self.path.stem if self.path else None

    def release(self) -> None:
        if self.handle is not None:
            self.path.unlink(missing_ok=True)
            self.handle.close()
            self.handle = None

    def run(self, argv, *, pass_fds: tuple[int, ...] = (), **kwargs) -> subprocess.CompletedProcess:
        """`subprocess.run` with this place's lock passed to the child: the place stays held until the job (and
        anything that inherited the lock) ends, also when this process is killed meanwhile."""
        held = (self.handle.fileno(),) if self.handle else ()
        return subprocess.run(argv, pass_fds=held + pass_fds, **kwargs)

    def __enter__(self) -> "Ticket":
        return self

    def __exit__(self, *exc) -> None:
        self.release()


@contextmanager
def inside(ticket: Ticket, threads: int | None = None, memory_max: str | None = None):
    """While the ticket is held, this process and its children (VL_QUEUE_JOB) run their capped commands in the job's
    own slice, which `state` reads; then that slice is stopped once nothing runs in it. With `threads`, the slice as a
    whole is capped at that many CPUs and at `memory_max` (jail.cap_job); a slice that cannot be capped is said on
    stderr, and each run keeps its own caps."""
    if ticket.token is None:
        yield
        return
    before = os.environ.get(ENV)
    os.environ[ENV] = ticket.token
    capped = False
    if threads:
        why = jail.cap_job(ticket.token, threads, memory_max)
        capped = why is None
        if why:
            sys.stderr.write(f"vl: warning: the job's slice is not capped at {threads} CPUs ({why})\n")
    try:
        yield
    finally:
        if before is None:
            os.environ.pop(ENV, None)
        else:
            os.environ[ENV] = before
        jail.drop_slice(slice_cgroup() / jail.job_slice(ticket.token), capped)


def inherited(queue: Queue) -> bool:
    """Whether this process runs inside a live admission (VL_QUEUE_JOB, set by `vl queue run`)."""
    token = os.environ.get(ENV, "")
    return bool(token) and "/" not in token and _held(queue.root / "jobs" / f"{token}.json")


def _head(queue: Queue, mine: Ticket | None) -> tuple[str, int] | None:
    """The label and reservation of the oldest live waiting job (the head of the line), None when none waits or the
    oldest is `mine`."""
    waiting = []
    for path in _live(queue.root / "waiting", False):
        try:
            data = json.loads(path.read_text())
            waiting.append((float(data["since"]), path, str(data["label"]), int(data["reservation"])))
        except (OSError, ValueError, KeyError, TypeError):
            continue                             # released while being read
    if not waiting:
        return None
    _, path, label, reservation = min(waiting, key=lambda found: found[:2])
    return None if mine and path == mine.path else (label, reservation)


def admit(label: str, reservation: int | None = None, max_wait: float | None = None,
          queue: Queue | None = None) -> Ticket:
    """Wait until the queue admits the job, then hold its place. `reservation` defaults to `[queue]
    default_reservation` and is at most memory_total; `max_wait` to `[queue] max_wait`. Raises QueueError when
    the wait exceeds it. The oldest waiting job's reservation counts against every other job, so a big one is not
    starved by a stream of small ones. Inside an admission (VL_QUEUE_JOB) it returns at once, holding nothing. A
    reservation that could not start even on an idle machine raises QueueError at once."""
    queue = queue or Queue.from_machine()
    if inherited(queue):
        return Ticket(None, None, None)
    own = reservation if reservation is not None else queue.default_reservation
    if queue.memory_total is not None:
        own = min(own, queue.memory_total)
    ram = machine.meminfo_field("MemTotal", queue.meminfo)
    if ram is not None and own + queue.keep_free > ram:
        raise QueueError(f"queue: {label} reserves {gib(own)} and keep_free leaves {gib(queue.keep_free)} free, more "
                         f"than this machine's {gib(ram)} of RAM: it could never start. Lower the reservation (--reserve, "
                         "--mem, [queue] default_reservation) or [queue] keep_free")
    max_wait = max_wait if max_wait is not None else queue.max_wait
    start, arrival = time.monotonic(), time.time()
    waiter = None
    try:
        while True:
            try:
                queue.root.mkdir(parents=True, exist_ok=True)
                lock = (queue.root / "admission.lock").open("w")
            except OSError as exc:
                raise _unusable(queue.root, exc) from exc
            with lock:
                fcntl.flock(lock, fcntl.LOCK_EX)
                now = state(queue, sweep=True)
                head = _head(queue, waiter)
                why = blocked(queue, now, own + (head[1] if head else 0))
                if head and blocked(queue, now, own) is None:
                    if why is not None:
                        why += f" (counting the {gib(head[1])} held for {head[0]}, which has waited longer)"
                    elif len(now.running) + 1 >= queue.slots:
                        why = f"the last slot is held for {head[0]}, which has waited longer"
                if why is None:
                    since = datetime.now(timezone.utc).isoformat(timespec="seconds")
                    return _hold(queue.root / "jobs", {"pid": os.getpid(), "label": label, "reservation": own,
                                                       "since": since}, round(time.monotonic() - start, 3))
                first = waiter is None
                if first:                        # in line under the admission lock: younger jobs see it
                    waiter = _hold(queue.root / "waiting", {"pid": os.getpid(), "label": label, "reservation": own,
                                                            "since": arrival})
            waited = time.monotonic() - start
            if max_wait is not None and waited >= max_wait:
                raise QueueError(f"queue: {label} waited {waited:.0f}s, its maximum, and did not start: {why} "
                                 f"({now.summary(queue)})")
            if first:
                sys.stderr.write(f"vl: queue: {label} waits: {why} ({now.summary(queue)})\n")
                sys.stderr.flush()
            time.sleep(queue.poll if max_wait is None else min(queue.poll, max(max_wait - waited, 0.01)))
    finally:
        if waiter is not None:
            waiter.release()
