"""Isolated execution for candidate code: bubblewrap, no network, scrubbed environment, explicit mounts.

Inside a jail: new namespaces (`--unshare-all`: no network), `/usr`, `/bin`, `/lib`, `/lib64`, `/sbin` and `/etc`
read-only, a private `/tmp`, the listed folders and overlays, and only the given environment plus `HOME` (the work
folder), `TMPDIR=/tmp` and `VL_JAIL=1`. The home folder and the repository are not mounted unless listed.
The jail limits what a fallible candidate can touch; it does not defend against the host's administrator, nor
against another process of the same user outside it. bubblewrap, systemd-run and systemctl are started by absolute
path (`program`: machine.toml `[launchers]`, else a system folder), never looked up in the caller's PATH.
"""

from __future__ import annotations

import atexit
import contextlib
import functools
import os
import re
import selectors
import secrets
import signal
import subprocess
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

from . import machine

DENY_PATTERNS = (
    r".*_API_KEY$", r".*APIKEY$", r".*_TOKEN$", r".*_SECRET.*", r".*SECRET_.*", r".*PASSWORD.*",
    r".*PASSWD.*", r".*_CREDENTIALS.*", r"^AWS_.*", r"^GOOGLE_.*KEY$", r"^GEMINI_.*KEY$",
    r"^OPENAI_.*KEY$", r"^ANTHROPIC_.*KEY$", r"^HF_TOKEN$", r"^HUGGINGFACE.*", r"^SLACK_.*",
)
_DENY = [re.compile(p, re.IGNORECASE) for p in DENY_PATTERNS]
DEFAULT_ENV = ("PATH", "LANG", "LC_ALL", "LC_CTYPE", "TERM")
SYSTEM_RO = ("/usr", "/bin", "/lib", "/lib64", "/sbin", "/etc")
NO_BWRAP = "bubblewrap (bwrap) is not installed in a system folder (or set [launchers] bwrap in machine.toml)"
SLICE = "vl.slice"   # every capped vl run (lane execs, checks) shares this systemd user slice
JOB_ENV = "VL_QUEUE_JOB"   # the queue job (queue.py) this process runs in: its capped runs go in the job's slice
TASKS_MAX = 4096     # processes and threads of one capped run: a fork bomb stops here, not at the user's limit
SLICE_CPU_WEIGHT = 20   # vl.slice's share of contended CPU against the owner's apps and session (systemd default 100)
# What `stream` keeps of a command's output, whatever it prints: the first and the last STREAM_KEEP bytes of each
# stream, the first and the last STREAM_LINES lines, and LINE_MAX bytes of any one line.
STREAM_KEEP = 1 << 20
STREAM_LINES = 10_000
LINE_MAX = 1 << 14
COORDINATOR_FD_ENV = "VL_COORDINATOR_FD"   # read end of the batch coordinator's pipe: EOF means the coordinator died

_OWNED_LOCK = threading.Lock()
_OWNED_UNITS: set[str] = set()             # run-vl-*.service units created by this process only
_OWNED_PROCS: set[subprocess.Popen] = set()


def _register_owned(proc: subprocess.Popen, unit: str | None) -> None:
    with _OWNED_LOCK:
        _OWNED_PROCS.add(proc)
        if unit:
            _OWNED_UNITS.add(unit)


def _unregister_owned(proc: subprocess.Popen, unit: str | None) -> None:
    with _OWNED_LOCK:
        _OWNED_PROCS.discard(proc)
        if unit:
            _OWNED_UNITS.discard(unit)


def stop_owned() -> None:
    """Stop every jailed process and systemd unit created by this process, never touching unrelated units."""
    with _OWNED_LOCK:
        procs = list(_OWNED_PROCS)
        units = list(_OWNED_UNITS)
    for proc in procs:
        if proc.poll() is None:
            try:
                proc.kill()
            except OSError:
                pass
    for unit in units:
        try:
            _stop_service(unit)
        except (OSError, subprocess.TimeoutExpired, RuntimeError):
            pass


atexit.register(stop_owned)


@contextlib.contextmanager
def terminate_on_sigterm():
    """Turn SIGTERM into SystemExit on the main thread so cleanup `finally` blocks stop child runs and units."""
    if threading.current_thread() is not threading.main_thread():
        yield
        return
    previous = signal.getsignal(signal.SIGTERM)
    if previous not in (signal.SIG_DFL, signal.default_int_handler):
        yield
        return
    triggered = False

    def _handle(signum, _frame):
        nonlocal triggered
        if not triggered:
            triggered = True
            raise SystemExit(128 + signum)

    signal.signal(signal.SIGTERM, _handle)
    try:
        yield
    finally:
        signal.signal(signal.SIGTERM, previous)


@contextlib.contextmanager
def watch_coordinator():
    """Stop this worker if its batch coordinator disappears (including SIGKILL): EOF on `VL_COORDINATOR_FD` sends
    SIGTERM to this process so `terminate_on_sigterm` unwinds and cleans its owned units."""
    raw = os.environ.pop(COORDINATOR_FD_ENV, None)
    if not raw:
        yield
        return
    try:
        coord_fd = int(raw)
    except ValueError:
        yield
        return
    wake_r, wake_w = os.pipe()
    stop = threading.Event()

    def _watch() -> None:
        try:
            with selectors.DefaultSelector() as sel:
                sel.register(coord_fd, selectors.EVENT_READ, "coord")
                sel.register(wake_r, selectors.EVENT_READ, "wake")
                while not stop.is_set():
                    for key, _ in sel.select():
                        if key.data == "wake":
                            return
                        try:
                            chunk = os.read(coord_fd, 1)
                        except OSError:
                            chunk = b""
                        if not chunk:
                            os.kill(os.getpid(), signal.SIGTERM)
                            return
        finally:
            for fd in (coord_fd, wake_r):
                try:
                    os.close(fd)
                except OSError:
                    pass

    thread = threading.Thread(target=_watch, name="vl-coordinator-watch", daemon=True)
    thread.start()
    try:
        yield
    finally:
        stop.set()
        try:
            os.write(wake_w, b"x")
        except OSError:
            pass
        thread.join(timeout=1)
        try:
            os.close(wake_w)
        except OSError:
            pass


def is_denied(name: str) -> bool:
    return any(rx.match(name) for rx in _DENY)


def filter_env(host_env: Mapping[str, str], allowlist: Sequence[str]) -> dict[str, str]:
    """Allowlisted, present, not secret-shaped, single-line variables only. The denylist always wins."""
    return {
        name: host_env[name]
        for name in allowlist
        if name in host_env and not is_denied(name) and "\n" not in host_env[name] and "\r" not in host_env[name]
    }


def program(name: str) -> str | None:
    """Absolute path of a system program vl starts (machine.LAUNCHER_NAMES); never from the caller's PATH."""
    return machine.launcher(name)


def available() -> bool:
    return program("bwrap") is not None


@dataclass(frozen=True)
class Jail:
    """Mounts and environment for one jailed command. Everything not listed is invisible."""

    workdir: Path
    read_write: tuple[Path, ...] = ()
    read_only: tuple[Path, ...] = ()
    env: dict[str, str] = field(default_factory=dict)
    # (lower, upper, work, mountpoint): copy-on-write view of a read-only directory; writes land in upper
    overlays: tuple[tuple[Path, Path, Path, Path], ...] = ()
    # Read-only overmounts applied after writable mounts, e.g. a lane's Git pointer within its writable workdir.
    protected: tuple[Path, ...] = ()
    # Comparator's upstream mitigation must be inherited before bubblewrap starts.
    deny_unix: bool = False
    # A run that gives way: when the memory its job shares runs out (jail.cap_job), the kernel kills it first.
    yields: bool = False

    def argv(self, command: Sequence[str], *, setup_fd: int | None = None) -> list[str]:
        bwrap = program("bwrap")
        if bwrap is None:               # never `bwrap` from the caller's PATH
            raise RuntimeError(NO_BWRAP)
        args = [bwrap, "--unshare-all", "--die-with-parent", "--new-session", "--clearenv",
                "--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp"]
        for path in SYSTEM_RO:
            if os.path.exists(path):
                args += ["--ro-bind", path, path]
        for path in self.read_only:
            args += ["--ro-bind", str(path), str(path)]
        for path in (self.workdir, *self.read_write):
            args += ["--bind", str(path), str(path)]
        for lower, upper, work, mountpoint in self.overlays:
            args += ["--overlay-src", str(lower), "--overlay", str(upper), str(work), str(mountpoint)]
        for path in self.protected:
            args += ["--ro-bind", str(path), str(path)]
        if setup_fd is not None:
            args += ["--file", str(setup_fd), "/tmp/.vl-setup-complete"]
        for key, value in sorted(self.env.items()):
            args += ["--setenv", key, value]
        args += ["--setenv", "HOME", str(self.workdir), "--setenv", "TMPDIR", "/tmp",
                 "--setenv", "VL_JAIL", "1", "--chdir", str(self.workdir), "--", *command]
        return args


def cap_slice(memory_total: str) -> None:
    """Cap all of vl.slice at `memory_total` for this login session (a runtime property; nothing persists), and give
    it a low CPU weight (SLICE_CPU_WEIGHT), so the owner's own programs stay responsive while checks build."""
    systemctl = program("systemctl")
    if systemctl is None:
        raise RuntimeError(f"cannot cap {SLICE} at {memory_total}: systemctl is not installed in a system folder")
    try:
        proc = subprocess.run([systemctl, "--user", "set-property", "--runtime", SLICE, f"MemoryMax={memory_total}",
                               f"CPUWeight={SLICE_CPU_WEIGHT}"], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"cannot cap {SLICE} at {memory_total}: {exc}") from exc
    if proc.returncode != 0:
        raise RuntimeError(f"cannot cap {SLICE} at {memory_total}: {proc.stderr.strip() or proc.returncode}")


@functools.cache
def _scope_problem(systemd_run: str) -> str | None:
    """Why `systemd-run --user --scope` does not work here (no user manager, no session bus), or None. Asked once."""
    try:
        proc = subprocess.run([systemd_run, "--user", "--scope", "--quiet", "--collect", "true"],
                              capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"systemd-run --user cannot run: {exc}"
    if proc.returncode != 0:
        return f"no systemd user manager: {proc.stderr.strip() or f'systemd-run exited with {proc.returncode}'}"
    return None


def service_problem() -> str | None:
    """Why a protected Lean check cannot run here, or None: its AF_UNIX-denying service (`guarded_service`) needs
    systemd-run, systemctl and a systemd user manager."""
    systemd_run = program("systemd-run")
    if systemd_run is None or program("systemctl") is None:
        return "systemd-run and systemctl are not installed in a system folder"
    return _scope_problem(systemd_run)


def cap_problem(memory_max: str | None) -> str | None:
    """Why a run with `memory_max` gets no memory cap here, or None when it is capped."""
    if not memory_max:
        return "no memory_max is configured"
    systemd_run = program("systemd-run")
    if systemd_run is None:
        return "systemd-run is not installed in a system folder"
    return _scope_problem(systemd_run)


def cap_record(memory_max: str | None, memory_total: str | None) -> dict[str, Any]:
    """The memory cap a run gets, as a receipt records it: `memory_cap` is "none" (with why) or the limits."""
    problem = cap_problem(memory_max)
    if problem:
        return {"memory_cap": "none", "memory_cap_why": problem}
    return {"memory_cap": {"memory_max": memory_max, "memory_total": memory_total, "slice": SLICE,
                           "tasks_max": TASKS_MAX}}


def job_slice(token: str | None) -> str:
    """The slice of a queue job's capped runs, its own inside vl.slice (systemd nests `vl-X.slice` there), where the
    queue reads what the job uses; vl.slice itself outside a job."""
    return f"vl-{token.replace('-', '_')}.slice" if token and re.fullmatch(r"[0-9a-f-]+", token) else SLICE


def cap_job(token: str, threads: int, memory_max: str | None) -> str | None:
    """Cap a queue job's own slice (`job_slice`) as a whole, for this login session: `threads` CPUs (CPUQuota) and,
    when given, `memory_max` (MemoryMax, no swap), so that the runs of one check that overlap (Comparator and the
    statement probes) share one budget. Returns why the slice is not capped, or None."""
    systemd_run, systemctl = program("systemd-run"), program("systemctl")
    if systemd_run is None or systemctl is None:
        return "systemd-run and systemctl are not installed in a system folder"
    if problem := _scope_problem(systemd_run):
        return problem
    limits = [f"CPUQuota={threads * 100}%", *([f"MemoryMax={memory_max}", "MemorySwapMax=0"] if memory_max else [])]
    try:
        proc = subprocess.run([systemctl, "--user", "set-property", "--runtime", job_slice(token), *limits],
                              capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return str(exc)
    return None if proc.returncode == 0 else proc.stderr.strip() or f"systemctl exited with {proc.returncode}"


def drop_slice(cgroup: Path, capped: bool = False) -> None:
    """Stop a job's slice once nothing runs in it, so that the user manager does not keep one empty unit per job;
    when `cap_job` capped it, also remove the runtime settings that capping left behind."""
    systemctl = program("systemctl")
    try:
        events: str | None = (cgroup / "cgroup.events").read_text()
    except OSError:
        events = None                            # never started
    if systemctl is None or (events is not None and "populated 0" not in events):
        return
    for action in (*(["stop"] if events is not None else []), *(["revert"] if capped else [])):
        try:
            subprocess.run([systemctl, "--user", action, cgroup.name], capture_output=True, timeout=30)
        except (OSError, subprocess.TimeoutExpired):
            pass


def remove_stopped_slices(cgroups: Sequence[Path]) -> None:
    """Remove the empty folders of job slices whose systemd unit is no longer active: a user manager can leave them
    behind, and the queue reads each one at every admission. rmdir itself refuses a folder that still holds a process
    or a child cgroup; without systemctl, or when it cannot list the units, nothing is removed."""
    systemctl = program("systemctl")
    if systemctl is None:
        return
    try:
        proc = subprocess.run([systemctl, "--user", "list-units", "--all", "--plain", "--no-legend",
                               "--state=active,activating,deactivating,reloading", "vl-*.slice"],
                              capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return
    if proc.returncode != 0:
        return
    active = {line.split()[0] for line in proc.stdout.splitlines() if line.strip()}
    for cgroup in cgroups:
        if cgroup.name not in active:
            try:
                cgroup.rmdir()
            except OSError:
                pass


def memory_capped(argv: Sequence[str], memory_max: str | None, memory_total: str | None = None) -> list[str]:
    """Wrap in a transient systemd user scope so a runaway build cannot take the machine down. Each run is
    capped at `memory_max` and TASKS_MAX tasks; all runs together at `memory_total`, the cap of the vl.slice they
    share, in the slice of the queue job they run for (`job_slice`). Without a working systemd user manager the
    command runs uncapped (`cap_record` says so)."""
    if cap_problem(memory_max):
        return list(argv)
    if memory_total:
        cap_slice(memory_total)
    return [program("systemd-run"), "--user", "--scope", "--quiet", "--collect",
            f"--slice={job_slice(os.environ.get(JOB_ENV))}",
            "-p", f"MemoryMax={memory_max}", "-p", "MemorySwapMax=0", "-p", f"TasksMax={TASKS_MAX}", "--", *argv]


# Runs in the service before bwrap: refuse to execute any checked code unless the kernel actually denies
# AF_UNIX. Use vl's own Python runtime outside the jail, so no additional interpreter has to be mounted.
_UNIX_GUARD = r"""import errno, os, socket, sys
try:
    socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
except OSError as exc:
    if exc.errno == errno.EAFNOSUPPORT:
        os.execv(sys.argv[1], sys.argv[1:])
sys.stderr.write('vl: AF_UNIX restriction is not enforced\n')
sys.exit(125)
"""


def guarded_service(argv: Sequence[str], unit: str, timeout: float, memory_max: str | None,
                    memory_total: str | None) -> list[str]:
    """Comparator's documented outer systemd mitigation, with no uncapped scope fallback.

    RuntimeMaxSec also bounds the service when the client disappears; an ordinary deadline explicitly stops
    the entire unit. The guard checks actual enforcement immediately before each exec of bwrap.
    """
    systemd_run, systemctl = program("systemd-run"), program("systemctl")
    if systemd_run is None or systemctl is None:
        raise RuntimeError("protected Lean requires systemd-run and systemctl for its AF_UNIX restriction")
    if memory_total:
        cap_slice(memory_total)
    caps = ["-p", f"MemoryMax={memory_max}", "-p", "MemorySwapMax=0"] if memory_max else []
    # Not --quiet: systemd-run then reports how the service ended (`_service_end`).
    return [systemd_run, "--user", "--wait", "--pipe", "--collect", "--service-type=exec",
            "--expand-environment=no", f"--unit={unit}", f"--slice={job_slice(os.environ.get(JOB_ENV))}",
            "-p", "RestrictAddressFamilies=~AF_UNIX", "-p", f"TasksMax={TASKS_MAX}",
            # Kill immediately, but let the manager reap children before classifying the service result.
            # A zero stop timeout can turn a successful main exit into a spurious service timeout.
            "-p", f"RuntimeMaxSec={timeout}s", "-p", "KillSignal=SIGKILL", "-p", "TimeoutStopSec=1s",
            "-p", "KillMode=control-group",
            *caps, "--", sys.executable, "-I", "-S", "-c", _UNIX_GUARD, *argv]


# Without --quiet, `systemd-run --wait` writes to its stderr, which the service shares, one line when the unit starts
# and, once the unit is gone, a report: `Finished with result: R`, `Main processes terminated with: …`, then more
# `label: value` lines. Its exit status is the main process's only for the results "success" and "exit-code"; for
# any other it exits 1 (255 for "signal"), whatever the command returned.
_RESULTS = {"oom-kill": ": the memory cap was reached",
            "timeout": (": a timeout of the unit fired (RuntimeMaxSec, or TimeoutStopSec while killing the processes "
                        "left after the main one exited)")}


def _service_end(err: bytes, unit: str, returncode: int) -> tuple[bytes, str | None]:
    """The command's stderr without systemd-run's own lines, and, when systemd's result rather than the command
    set `returncode`, why. Nothing the service prints can follow the report, so only the last one counts."""
    err = re.sub(rb"^Running as unit: " + re.escape(unit.encode()) + rb"\b.*\n", b"", err, count=1, flags=re.M)
    at = err.rfind(b"Finished with result: ")
    report = err[at:].decode("utf-8", "replace").splitlines()
    if at < 0 or not all(re.fullmatch(r" *\w[\w ]*: .*", line) for line in report):
        return err, None
    fields = dict(line.strip().split(": ", 1) for line in report)
    result, err = fields["Finished with result"], err[:at].rstrip(b" ")      # the report's first line is padded
    if result in ("success", "exit-code"):
        return err, None
    main = fields.get("Main processes terminated with", "unknown")
    return err, (f"systemd ended the service with result '{result}'{_RESULTS.get(result, '')} (main process: "
                 f"{main}); exit status {returncode} is systemd's, not the command's")


def _service_cgroup(unit: str) -> Path | None:
    """Resolve the named service once it has started; systemd-run's client PID is outside its cgroup."""
    try:
        proc = subprocess.run([program("systemctl"), "--user", "show", "--property=ControlGroup", "--value", unit],
                              capture_output=True, text=True, timeout=1)
        group = proc.stdout.strip()
        if proc.returncode == 0 and group.startswith("/"):
            return Path("/sys/fs/cgroup" + group)
    except (OSError, subprocess.TimeoutExpired):
        pass
    return None


def _stop_service(unit: str) -> None:
    """SIGKILL the complete service at the caller's deadline, including children that closed their output."""
    proc = subprocess.run([program("systemctl"), "--user", "stop", unit], capture_output=True, text=True, timeout=5)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or f"systemctl stop exited with {proc.returncode}")


class TimedOut(subprocess.TimeoutExpired):
    """`run` stopped by its deadline or by `cancel`, with the `memory_peak` and `cpu_seconds` its unit had until then."""

    def __init__(self, cmd: list[str], timeout: float, output: bytes, stderr: bytes, memory_peak: int | None,
                 cpu_seconds: float | None = None):
        super().__init__(cmd, timeout, output, stderr)
        self.memory_peak = memory_peak
        self.cpu_seconds = cpu_seconds


class Completed(subprocess.CompletedProcess):
    """A finished `run`, with the `memory_peak` and `cpu_seconds` of its unit (as `Streamed`)."""

    def __init__(self, args: list[str], returncode: int, stdout: bytes, stderr: bytes, memory_peak: int | None,
                 cpu_seconds: float | None = None):
        super().__init__(args, returncode, stdout, stderr)
        self.memory_peak = memory_peak
        self.cpu_seconds = cpu_seconds


def run(jail: Jail, command: Sequence[str], *, timeout: float, memory_max: str | None = None,
        memory_total: str | None = None, cancel: threading.Event | None = None) -> Completed:
    """`stream` without the per-line record: the same deadline and the same bounded output. Raises
    `TimedOut` (a subprocess.TimeoutExpired), with what was printed so far and the peak, when the deadline (or
    `cancel`) killed the command."""
    done = stream(jail, command, timeout=timeout, memory_max=memory_max, memory_total=memory_total, cancel=cancel)
    if done.returncode is None:
        raise TimedOut(list(command), timeout, done.stdout, done.stderr, done.memory_peak, done.cpu_seconds)
    return Completed(list(command), done.returncode, done.stdout, done.stderr, done.memory_peak, done.cpu_seconds)


@dataclass(frozen=True)
class Streamed:
    returncode: int | None                       # None: the timeout killed the command
    stdout: bytes
    stderr: bytes
    lines: tuple[tuple[float, str, str], ...]    # (seconds since start, "stdout" | "stderr", line), as they arrived
    seconds: float                               # wall time of the whole command
    memory_peak: int | None                      # bytes: the most anonymous memory its unit held (`anonymous`)
    argv: tuple[str, ...]                        # the actual outer command, including its service unit
    service: str | None = None                   # why systemd, not the command, set `returncode` (`_service_end`)
    cpu_seconds: float | None = None             # CPU time its unit used (`cpu_usage`), a lower bound; None: unread


def cpu_usage(cgroup: Path) -> float | None:
    """The CPU time a cgroup and its descendants have used, in seconds (cpu.stat `usage_usec`), None when it cannot be
    read. It only grows while the unit lives, so the last reading is the run's (a lower bound by what came after it)."""
    try:
        for line in (cgroup / "cpu.stat").read_text().splitlines():
            if line.startswith("usage_usec "):
                return int(line.split()[1]) / 1e6
    except (OSError, ValueError, IndexError):
        pass
    return None


def anonymous(cgroup: Path) -> int | None:
    """The anonymous memory of a cgroup (memory.stat `anon`), None when it cannot be read. The memory basis of the
    job queue (queue.py): page cache, which memory.peak and memory.current also count, is left out, as MemAvailable
    counts most of it as free."""
    try:
        for line in (cgroup / "memory.stat").read_text().splitlines():
            if line.startswith("anon "):
                return int(line.split()[1])
    except (OSError, ValueError, IndexError):
        pass
    return None


def _scope_cgroup(pid: int) -> Path | None:
    """The cgroup of the vl.slice scope `pid` runs in, None while it is not in one (systemd-run moves itself into
    the scope before it starts the command)."""
    try:
        own = Path("/proc/self/cgroup").read_text()
        cgroup = Path(f"/proc/{pid}/cgroup").read_text()
        rel = cgroup.strip().split("\n")[0].split("::", 1)[1]
    except (OSError, IndexError):
        return None
    if cgroup == own or f"/{SLICE}/" not in rel or not rel.endswith(".scope"):
        return None
    return Path("/sys/fs/cgroup" + rel)


class _Output:
    """One output stream, bounded: its first and last STREAM_KEEP bytes, and the lines it completes, each cut at
    LINE_MAX bytes."""

    def __init__(self) -> None:
        self.head = bytearray()
        self.tail = bytearray()
        self.dropped = 0
        self.line = bytearray()
        self.cut = False

    def add(self, chunk: bytes) -> list[str]:
        """Keep `chunk` within bounds; return the lines it completes."""
        room = max(STREAM_KEEP - len(self.head), 0)
        self.head += chunk[:room]
        if len(chunk) > room:
            self.tail += chunk[room:]
            excess = len(self.tail) - STREAM_KEEP
            if excess > 0:
                del self.tail[:excess]
                self.dropped += excess
        *complete, last = chunk.split(b"\n")
        lines = []
        for part in complete:
            self._extend(part)
            lines.append(self._take())
        self._extend(last)
        return lines

    def _extend(self, part: bytes) -> None:
        room = max(LINE_MAX - len(self.line), 0)
        self.cut = self.cut or len(part) > room
        self.line += part[:room]

    def _take(self) -> str:
        text = self.line.decode("utf-8", "replace") + (" [line cut by vl]" if self.cut else "")
        self.line, self.cut = bytearray(), False
        return text

    def rest(self) -> str | None:
        """The last line, when the stream did not end with a newline."""
        return self._take() if self.line or self.cut else None

    def data(self) -> bytes:
        if not self.dropped:
            return bytes(self.head + self.tail)
        return bytes(self.head) + f"\n[vl: {self.dropped} bytes of output dropped here]\n".encode() + bytes(self.tail)


class _Lines:
    """The first and the last STREAM_LINES lines of a run, in arrival order, and how many were dropped between."""

    def __init__(self) -> None:
        self.head: list[tuple[float, str, str]] = []
        self.tail: deque[tuple[float, str, str]] = deque(maxlen=STREAM_LINES)
        self.dropped = 0
        self.dropped_at = 0.0

    def add(self, row: tuple[float, str, str]) -> None:
        if len(self.head) < STREAM_LINES:
            self.head.append(row)
            return
        if len(self.tail) == STREAM_LINES:
            self.dropped += 1
            self.dropped_at = self.tail[0][0]
        self.tail.append(row)

    def rows(self) -> tuple[tuple[float, str, str], ...]:
        marker = [(self.dropped_at, "vl", f"[vl: {self.dropped} lines of output dropped here]")] if self.dropped else []
        return (*self.head, *marker, *self.tail)


def stream(jail: Jail, command: Sequence[str], *, timeout: float, memory_max: str | None = None,
           memory_total: str | None = None, poll: float = 0.25, cancel: threading.Event | None = None) -> Streamed:
    """Like `run`, but reads the output while the command runs: each line gets the time it arrived, and the
    unit's anonymous memory is read every `poll` seconds; the most it held is the run's `memory_peak` (a lower
    bound of the true peak, by what came and went between two polls).
    Memory stays bounded however much the command prints: the output and its lines keep their beginning and their
    end, with a marker where the middle was dropped (STREAM_KEEP, STREAM_LINES, LINE_MAX).
    A timeout kills the command and keeps what it printed so far, also when the command closed its output and
    kept running. Setting `cancel` (another thread's way to stop it early) acts as the deadline, within `poll`
    seconds, whether or not the command's output is still open."""
    inner = jail.argv(command)
    if jail.yields:      # best effort: an unprivileged process may raise its own oom_score_adj, never lower it
        inner = [program("sh") or "/bin/sh", "-c", 'echo 1000 > /proc/self/oom_score_adj 2>/dev/null; exec "$@"', "sh",
                 *inner]
    unit = f"run-vl-{secrets.token_hex(12)}.service" if jail.deny_unix else None
    argv = (guarded_service(inner, unit, timeout, memory_max, memory_total) if unit
            else memory_capped(inner, memory_max, memory_total))
    scoped = argv != inner
    cgroup: Path | None = None
    start = time.monotonic()
    out = {"stdout": _Output(), "stderr": _Output()}
    lines = _Lines()
    peak: int | None = None
    cpu: float | None = None
    timed_out = False
    aborted = False

    def stop_now() -> bool:
        return time.monotonic() >= start + timeout or (cancel is not None and cancel.is_set())

    with terminate_on_sigterm():
        proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        _register_owned(proc, unit)
        try:
            with selectors.DefaultSelector() as selector:
                selector.register(proc.stdout, selectors.EVENT_READ, "stdout")
                selector.register(proc.stderr, selectors.EVENT_READ, "stderr")
                while selector.get_map():
                    if stop_now():
                        timed_out = True
                        proc.kill()
                        break
                    for key, _ in selector.select(min(poll, max(start + timeout - time.monotonic(), 0))):
                        name, chunk = key.data, os.read(key.fd, 1 << 16)
                        at = round(time.monotonic() - start, 3)
                        if not chunk:
                            selector.unregister(key.fileobj)
                            rest = out[name].rest()
                            if rest is not None:
                                lines.add((at, name, rest))
                            continue
                        for line in out[name].add(chunk):
                            lines.add((at, name, line))
                    if scoped:
                        cgroup = cgroup or (_service_cgroup(unit) if unit else _scope_cgroup(proc.pid))
                        value = anonymous(cgroup) if cgroup else None
                        peak = peak if value is None else max(peak or 0, value)
                        used = cpu_usage(cgroup) if cgroup else None
                        cpu = cpu if used is None else max(cpu or 0, used)
            # the output is closed; the deadline and `cancel` still hold for the process itself
            while not timed_out and proc.poll() is None:
                if stop_now():
                    timed_out = True
                    proc.kill()
                else:
                    time.sleep(min(poll, max(start + timeout - time.monotonic(), 0)))
            returncode = proc.wait()
        except BaseException:
            aborted = True
            raise
        finally:
            try:
                if proc.poll() is None:
                    try:
                        proc.kill()
                    except OSError:
                        pass
                try:
                    proc.wait(timeout=5)
                except (OSError, subprocess.TimeoutExpired):
                    pass
                if unit and (timed_out or aborted):
                    try:
                        _stop_service(unit)
                    except (OSError, subprocess.TimeoutExpired, RuntimeError) as exc:
                        if timed_out and not aborted:
                            diagnostic = (f"vl: timed out; service stop failed for {unit}: {exc}; "
                                          "immediate service cleanup is unconfirmed; RuntimeMaxSec remains the fallback")
                            out["stderr"].add(("\n" + diagnostic + "\n").encode())
                            lines.add((round(time.monotonic() - start, 3), "vl", diagnostic))
            finally:
                _unregister_owned(proc, unit)
                for pipe in (proc.stdout, proc.stderr):
                    if pipe is not None:
                        try:
                            pipe.close()
                        except OSError:
                            pass
    stderr, service = out["stderr"].data(), None
    if unit and not timed_out:
        stderr, service = _service_end(stderr, unit, returncode)
        if service:
            stderr += (b"\n" if stderr[-1:] not in (b"", b"\n") else b"") + f"vl: {service}\n".encode()
            lines.add((round(time.monotonic() - start, 3), "vl", service))
    if unit and returncode == 125 and b"vl: AF_UNIX restriction is not enforced" in stderr:
        raise RuntimeError("protected Lean AF_UNIX restriction is not enforced; refusing to run checked code")
    return Streamed(None if timed_out else returncode, out["stdout"].data(), stderr, lines.rows(),
                    round(time.monotonic() - start, 3), peak, tuple(argv), service,
                    None if cpu is None else round(cpu, 3))
