"""Isolated execution for candidate code: bubblewrap, no network, scrubbed environment, explicit mounts.

Inside a jail: new namespaces (`--unshare-all`: no network), `/usr`, `/bin`, `/lib`, `/lib64`, `/sbin` and `/etc`
read-only, a private `/tmp`, the listed folders and overlays, and only the given environment plus `HOME` (the work
folder), `TMPDIR=/tmp` and `VL_JAIL=1`. The home folder and the repository are not mounted unless listed.
The jail limits what a fallible candidate can touch; it does not defend against the host's administrator, nor
against another process of the same user outside it. bubblewrap, systemd-run and systemctl are started by absolute
path (`program`: machine.toml `[launchers]`, else a system folder), never looked up in the caller's PATH.
"""

from __future__ import annotations

import functools
import os
import re
import selectors
import subprocess
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
SLICE = "vl.slice"   # every capped vl run (lane execs, checks) shares this systemd user slice
TASKS_MAX = 4096     # processes and threads of one capped run: a fork bomb stops here, not at the user's limit
# What `stream` keeps of a command's output, whatever it prints: the first and the last STREAM_KEEP bytes of each
# stream, the first and the last STREAM_LINES lines, and LINE_MAX bytes of any one line.
STREAM_KEEP = 1 << 20
STREAM_LINES = 10_000
LINE_MAX = 1 << 14


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

    def argv(self, command: Sequence[str]) -> list[str]:
        args = [program("bwrap") or "bwrap", "--unshare-all", "--die-with-parent", "--new-session", "--clearenv",
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
        for key, value in sorted(self.env.items()):
            args += ["--setenv", key, value]
        args += ["--setenv", "HOME", str(self.workdir), "--setenv", "TMPDIR", "/tmp",
                 "--setenv", "VL_JAIL", "1", "--chdir", str(self.workdir), "--", *command]
        return args


def cap_slice(memory_total: str) -> None:
    """Cap all of vl.slice at `memory_total` for this login session (a runtime property; nothing persists)."""
    systemctl = program("systemctl")
    if systemctl is None:
        raise RuntimeError(f"cannot cap {SLICE} at {memory_total}: systemctl is not installed in a system folder")
    try:
        proc = subprocess.run([systemctl, "--user", "set-property", "--runtime", SLICE, f"MemoryMax={memory_total}"],
                              capture_output=True, text=True, timeout=30)
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


def memory_capped(argv: Sequence[str], memory_max: str | None, memory_total: str | None = None) -> list[str]:
    """Wrap in a transient systemd user scope so a runaway build cannot take the machine down. Each run is
    capped at `memory_max` and TASKS_MAX tasks; all runs together at `memory_total`, the cap of the vl.slice they
    share. Without a working systemd user manager the command runs uncapped (`cap_record` says so)."""
    if cap_problem(memory_max):
        return list(argv)
    if memory_total:
        cap_slice(memory_total)
    return [program("systemd-run"), "--user", "--scope", "--quiet", "--collect", f"--slice={SLICE}",
            "-p", f"MemoryMax={memory_max}", "-p", "MemorySwapMax=0", "-p", f"TasksMax={TASKS_MAX}", "--", *argv]


def run(jail: Jail, command: Sequence[str], *, timeout: float, memory_max: str | None = None,
        memory_total: str | None = None) -> subprocess.CompletedProcess[bytes]:
    """`stream` without the per-line record: the same deadline and the same bounded output. Raises
    subprocess.TimeoutExpired, with what was printed so far, when the deadline killed the command."""
    done = stream(jail, command, timeout=timeout, memory_max=memory_max, memory_total=memory_total)
    if done.returncode is None:
        raise subprocess.TimeoutExpired(list(command), timeout, done.stdout, done.stderr)
    return subprocess.CompletedProcess(list(command), done.returncode, done.stdout, done.stderr)


@dataclass(frozen=True)
class Streamed:
    returncode: int | None                       # None: the timeout killed the command
    stdout: bytes
    stderr: bytes
    lines: tuple[tuple[float, str, str], ...]    # (seconds since start, "stdout" | "stderr", line), as they arrived
    seconds: float                               # wall time of the whole command
    memory_peak: int | None                      # bytes: memory.peak of its vl.slice scope (systemd's MemoryPeak)


def _scope_peak(pid: int) -> int | None:
    """memory.peak of the vl.slice scope `pid` runs in, None while it is not in one (systemd-run moves itself into
    the scope before it starts the command) or when the cgroup is gone."""
    try:
        own = Path("/proc/self/cgroup").read_text()
        cgroup = Path(f"/proc/{pid}/cgroup").read_text()
        rel = cgroup.strip().split("\n")[0].split("::", 1)[1]
        if cgroup == own or f"/{SLICE}/" not in rel or not rel.endswith(".scope"):
            return None
        return int(Path("/sys/fs/cgroup" + rel, "memory.peak").read_text())
    except (OSError, IndexError, ValueError):
        return None


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
           memory_total: str | None = None, poll: float = 0.25) -> Streamed:
    """Like `run`, but reads the output while the command runs: each line gets the time it arrived, and the
    scope's memory.peak is read every `poll` seconds (a lower bound of the true peak by at most the last poll).
    Memory stays bounded however much the command prints: the output and its lines keep their beginning and their
    end, with a marker where the middle was dropped (STREAM_KEEP, STREAM_LINES, LINE_MAX).
    A timeout kills the command and keeps what it printed so far, also when the command closed its output and
    kept running."""
    if not available():
        raise RuntimeError("bubblewrap (bwrap) is not installed; protected checks are unsupported here")
    inner = jail.argv(command)
    argv = memory_capped(inner, memory_max, memory_total)
    scoped = argv != inner
    start = time.monotonic()
    proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    out = {"stdout": _Output(), "stderr": _Output()}
    lines = _Lines()
    peak: int | None = None
    timed_out = False
    with selectors.DefaultSelector() as selector:
        selector.register(proc.stdout, selectors.EVENT_READ, "stdout")
        selector.register(proc.stderr, selectors.EVENT_READ, "stderr")
        while selector.get_map():
            remaining = start + timeout - time.monotonic()
            if remaining <= 0:
                timed_out = True
                proc.kill()
                break
            for key, _ in selector.select(min(poll, remaining)):
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
                value = _scope_peak(proc.pid)
                peak = peak if value is None else max(peak or 0, value)
    try:            # the output is closed; the deadline still holds for the process itself
        returncode = proc.wait(timeout=max(start + timeout - time.monotonic(), 0) if not timed_out else None)
    except subprocess.TimeoutExpired:
        timed_out = True
        proc.kill()
        returncode = proc.wait()
    proc.stdout.close()
    proc.stderr.close()
    return Streamed(None if timed_out else returncode, out["stdout"].data(), out["stderr"].data(), lines.rows(),
                    round(time.monotonic() - start, 3), peak)
