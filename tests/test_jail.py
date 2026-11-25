from __future__ import annotations

import os
import re
import socket
import time
import subprocess
from pathlib import Path

import pytest

from verifylab import jail

from conftest import system_python



def bwrap(test):
    """The test runs bubblewrap: a `jail` test, skipped where bwrap is missing; pure ones stay in the fast suite."""
    return pytest.mark.jail(pytest.mark.skipif(not jail.available(), reason="bwrap missing")(test))


DENIED_NAMES = ("OPENAI_API_KEY", "SERVICE_APIKEY", "MY_TOKEN", "DB_SECRET_X", "SECRET_SAUCE", "PASSWORD_FILE",
                "MYPASSWD", "GIT_CREDENTIALS_PATH", "AWS_REGION", "GOOGLE_MAPSKEY", "GEMINI_KEY", "OPENAI_ORG_KEY",
                "ANTHROPIC_ADMINKEY", "HF_TOKEN", "HUGGINGFACE_HUB", "SLACK_WEBHOOK")


@pytest.mark.parametrize("pattern, name", list(zip(jail.DENY_PATTERNS, DENIED_NAMES, strict=True)))
def test_secrets_never_cross(pattern, name):
    """One name for each DENY_PATTERNS entry: it is dropped even when allowlisted."""
    assert re.fullmatch(pattern, name, re.IGNORECASE)
    assert jail.filter_env({"PATH": "/usr/bin", name: "x"}, ["PATH", name]) == {"PATH": "/usr/bin"}


def test_a_jail_without_bubblewrap_refuses_instead_of_looking_it_up_in_the_path(tmp_path: Path, monkeypatch):
    fake = tmp_path / "bin"
    fake.mkdir()
    (fake / "bwrap").write_text("#!/bin/sh\nexec \"$@\"\n")
    (fake / "bwrap").chmod(0o755)
    monkeypatch.setenv("PATH", f"{fake}:/usr/bin:/bin")
    monkeypatch.setattr(jail, "program", lambda name: None)
    with pytest.raises(RuntimeError, match="bubblewrap .* not installed"):
        jail.Jail(workdir=tmp_path).argv(["true"])


def _run(tmp_path: Path, code: str, **kw):
    box = jail.Jail(workdir=tmp_path, env=jail.filter_env(os.environ, jail.DEFAULT_ENV), **kw)
    return jail.run(box, [system_python(), "-c", code], timeout=30)


@bwrap
def test_files_outside_the_jail_and_the_network_are_out_of_reach(tmp_path: Path):
    """A secret the test plants outside the jail's binds, the account's real home, and a server listening on this
    machine: the same probe sees all three outside the jail (so it can fail), none inside."""
    secret = tmp_path / "outside" / "secret.txt"
    secret.parent.mkdir()
    secret.write_text("planted-secret")
    work = tmp_path / "work"
    work.mkdir()
    # A workdir under the real home needs synthetic parent directories inside the namespace.
    home = Path.home()
    identity = home.stat()
    with socket.create_server(("127.0.0.1", 0)) as server:
        port = server.getsockname()[1]
        probe = (
            "import os, socket\n"
            f"print(open({str(secret)!r}).read() if os.path.exists({str(secret)!r}) else 'hidden')\n"
            f"home = {str(home)!r}\n"
            "st = os.stat(home) if os.path.isdir(home) else None\n"
            f"print(st is not None and (st.st_dev, st.st_ino) == {(identity.st_dev, identity.st_ino)!r})\n"
            "s = socket.socket()\n"
            "s.settimeout(2)\n"
            f"try:\n    s.connect(('127.0.0.1', {port})); print('net')\n"
            "except OSError:\n    print('nonet')\n"
            "print(os.environ.get('HOME'))\n"
        )
        host = subprocess.run([system_python(), "-c", probe], capture_output=True, text=True, timeout=30)
        assert host.stdout.split() == ["planted-secret", "True", "net", os.environ.get("HOME", "None")], host.stderr
        out = _run(work, probe)
    assert out.returncode == 0, out.stderr
    assert out.stdout.decode().split() == ["hidden", "False", "nonet", str(work)]


@bwrap
def test_only_workdir_is_writable(tmp_path: Path):
    ro = tmp_path / "ro"
    ro.mkdir()
    work = tmp_path / "work"
    work.mkdir()
    code = (f"open('{work}/ok','w').write('1')\n"
            f"try:\n    open('{ro}/no','w').write('1'); print('wrote')\n"
            f"except OSError:\n    print('denied')\n")
    box = jail.Jail(workdir=work, read_only=(ro,), env=jail.filter_env(os.environ, jail.DEFAULT_ENV))
    out = jail.run(box, [system_python(), "-c", code], timeout=30)
    assert out.stdout.decode().strip() == "denied" and (work / "ok").exists()


@bwrap
def test_stream_times_each_line_as_it_arrives(tmp_path: Path):
    box = jail.Jail(workdir=tmp_path, env=jail.filter_env(os.environ, jail.DEFAULT_ENV))
    run = jail.stream(box, ["/bin/sh", "-c", "echo one; sleep 0.6; echo two >&2; sleep 0.6; printf three"], timeout=30)
    assert run.returncode == 0 and run.stdout == b"one\nthree" and run.stderr == b"two\n"
    assert [(name, line) for _, name, line in run.lines] == [("stdout", "one"), ("stderr", "two"), ("stdout", "three")]
    times = [at for at, _, _ in run.lines]
    assert times[1] - times[0] > 0.4 and times[2] - times[1] > 0.4, run.lines   # captured at the end: all equal
    assert run.seconds >= times[-1] and run.memory_peak is None                 # no systemd scope requested


@bwrap
def test_stream_timeout_kills_and_keeps_the_partial_output(tmp_path: Path):
    box = jail.Jail(workdir=tmp_path, env=jail.filter_env(os.environ, jail.DEFAULT_ENV))
    run = jail.stream(box, ["/bin/sh", "-c", "echo started; sleep 30"], timeout=1.5)
    assert run.returncode is None and run.stdout == b"started\n" and run.seconds < 10


@bwrap
@pytest.mark.skipif(jail.cap_problem("2G") is not None, reason=f"no memory cap here: {jail.cap_problem('2G')}")
def test_stream_reads_the_peak_memory_of_its_scope(tmp_path: Path):
    box = jail.Jail(workdir=tmp_path, env=jail.filter_env(os.environ, jail.DEFAULT_ENV))
    code = "import time; b = bytearray(200 * 2**20); b[::4096] = b'x' * len(b[::4096]); time.sleep(1.0)"
    run = jail.stream(box, [system_python(), "-c", code], timeout=60, memory_max="2G")
    assert run.returncode == 0, run.stderr
    assert run.memory_peak is not None and 200 * 2**20 <= run.memory_peak < 2 * 2**30, run.memory_peak


@pytest.mark.parametrize("deny_unix", [False, True])
def test_the_peak_of_a_run_is_its_units_anonymous_memory(tmp_path: Path, monkeypatch, deny_unix):
    """The basis of the job queue's usage (queue.state): page cache, which memory.peak also counts, is left out."""
    unit = tmp_path / "unit"
    unit.mkdir()
    (unit / "memory.peak").write_text(f"{8 << 30}\n")                    # anon + page cache
    (unit / "memory.stat").write_text(f"anon {1 << 30}\nfile {7 << 30}\n")
    command = ["/bin/sh", "-c", "echo up; sleep 0.3"]
    monkeypatch.setattr(jail, "available", lambda: True)
    monkeypatch.setattr(jail, "memory_capped", lambda argv, *caps: command)
    monkeypatch.setattr(jail, "guarded_service", lambda argv, *rest: command)
    monkeypatch.setattr(jail, "_scope_cgroup", lambda pid: unit, raising=False)
    monkeypatch.setattr(jail, "_service_cgroup", lambda name: unit)
    box = jail.Jail(workdir=tmp_path, deny_unix=deny_unix)
    run = jail.stream(box, ["true"], timeout=10, memory_max="2G")
    assert run.returncode == 0 and run.memory_peak == 1 << 30
    (unit / "memory.stat").unlink()                                       # control: nothing readable, no peak
    assert jail.stream(box, ["true"], timeout=10, memory_max="2G").memory_peak is None


@bwrap
def test_a_chatty_command_cannot_exhaust_the_parent(tmp_path: Path):
    """17 MB of short lines: the parent keeps the beginning and the end of the output and of its lines, and says
    where it dropped the middle, instead of holding everything until the command ends."""
    box = jail.Jail(workdir=tmp_path, env=jail.filter_env(os.environ, jail.DEFAULT_ENV))
    run = jail.stream(box, ["/bin/sh", "-c", "yes 0123456789abcdef | head -c 17000000; echo done >&2"], timeout=120)
    assert run.returncode == 0 and run.stderr == b"done\n"
    assert len(run.stdout) < 3 * 2**20, len(run.stdout)
    assert run.stdout.startswith(b"0123456789abcdef\n") and run.stdout.endswith(b"0123456789abcdef\n")
    assert b"bytes of output dropped here]" in run.stdout
    assert len(run.lines) < 25_000, len(run.lines)
    assert run.lines[0][1:] == ("stdout", "0123456789abcdef") and run.lines[-1][1:] == ("stderr", "done")
    assert any(where == "vl" and "lines of output dropped here" in line for _, where, line in run.lines)


@bwrap
def test_a_line_without_an_end_is_cut(tmp_path: Path):
    box = jail.Jail(workdir=tmp_path, env=jail.filter_env(os.environ, jail.DEFAULT_ENV))
    run = jail.stream(box, ["/bin/sh", "-c", "head -c 3000000 /dev/zero | tr '\\0' x"], timeout=120)
    assert run.returncode == 0
    [(_, where, line)] = run.lines
    assert where == "stdout" and len(line) < 64 * 2**10 and line.endswith("[line cut by vl]"), len(line)


@bwrap
def test_comparator_service_denies_unix_sockets_in_the_same_child(tmp_path: Path):
    """The benign operation succeeds in bwrap alone; the inherited upstream guard denies it."""
    from dataclasses import replace
    code = ("import errno, socket\ntry:\n    socket.socket(socket.AF_UNIX); print('allowed')\n"
            "except OSError as exc:\n    print('denied', exc.errno)\n")
    box = jail.Jail(workdir=tmp_path)
    control = jail.run(box, [system_python(), "-c", code], timeout=30)
    assert control.returncode == 0 and control.stdout == b"allowed\n", control.stderr
    guarded = jail.stream(replace(box, deny_unix=True), [system_python(), "-c", code], timeout=30,
                          memory_max="2G")
    assert guarded.returncode == 0 and guarded.stdout == b"denied 97\n", guarded.stderr
    assert "RestrictAddressFamilies=~AF_UNIX" in guarded.argv and "--scope" not in guarded.argv
    assert "MemoryMax=2G" in guarded.argv and "MemorySwapMax=0" in guarded.argv
    assert f"TasksMax={jail.TASKS_MAX}" in guarded.argv and f"--slice={jail.SLICE}" in guarded.argv


def test_comparator_service_refuses_missing_manager_without_executing_code(tmp_path, monkeypatch):
    box = jail.Jail(workdir=tmp_path, deny_unix=True)
    launcher = jail.program
    monkeypatch.setattr(jail, "program", lambda name: None if name == "systemd-run" else launcher(name))
    with pytest.raises(RuntimeError, match="requires systemd-run"):
        jail.run(box, ["/bin/sh", "-c", "touch should-not-exist"], timeout=5)
    assert not (tmp_path / "should-not-exist").exists()


@bwrap
def test_comparator_service_refuses_unenforced_restriction(tmp_path, monkeypatch):
    """Dropping exactly the upstream property prevents the trusted helper from executing bwrap."""
    service = jail.guarded_service
    def unguarded(*args):
        argv = service(*args)
        index = argv.index("RestrictAddressFamilies=~AF_UNIX")
        del argv[index - 1:index + 1]
        return argv
    monkeypatch.setattr(jail, "guarded_service", unguarded)
    with pytest.raises(RuntimeError, match="AF_UNIX restriction is not enforced"):
        jail.run(jail.Jail(workdir=tmp_path, deny_unix=True),
                 ["/bin/sh", "-c", "touch should-not-exist"], timeout=5)
    assert not (tmp_path / "should-not-exist").exists()


@bwrap
def test_cancel_stops_a_run_that_closed_its_output(tmp_path, monkeypatch):
    """A command that closes its output and keeps working is still stopped by `cancel` (a failed preflight, the end
    of the check), not only by the deadline; the control, nothing cancels, runs to its end."""
    import threading
    monkeypatch.setattr(jail.Jail, "argv", lambda self, command: list(command))
    monkeypatch.setattr(jail, "memory_capped", lambda argv, *args, **kwargs: list(argv))
    quiet = ["/bin/sh", "-c", "exec >/dev/null 2>&1; sleep 30"]
    cancel = threading.Event()
    threading.Timer(0.3, cancel.set).start()
    start = time.monotonic()
    done = jail.stream(jail.Jail(workdir=tmp_path), quiet, timeout=60, cancel=cancel, poll=0.05)
    assert done.returncode is None and time.monotonic() - start < 10
    done = jail.stream(jail.Jail(workdir=tmp_path), ["/bin/sh", "-c", "exec >/dev/null 2>&1; sleep 0.3; exit 4"],
                       timeout=60, cancel=threading.Event(), poll=0.05)
    assert done.returncode == 4                                                    # control


@bwrap
@pytest.mark.parametrize("close_output", [False, True])
def test_comparator_service_deadline_kills_children_and_unloads_unit(tmp_path, close_output):
    code = ("import os, time\n"
            "print('started', flush=True)\n"
            + ("os.close(1); os.close(2)\n" if close_output else "")
            + "if os.fork() == 0:\n    time.sleep(3); open('survived', 'w').write('bad'); os._exit(0)\n"
            + "time.sleep(30)\n")
    run = jail.stream(jail.Jail(workdir=tmp_path, deny_unix=True), [system_python(), "-c", code], timeout=1)
    assert run.returncode is None and run.stdout == b"started\n" and run.seconds < 5, run
    unit = next(arg.split('=', 1)[1] for arg in run.argv if arg.startswith('--unit='))
    state = subprocess.run([jail.program("systemctl"), "--user", "is-active", unit], capture_output=True)
    assert state.returncode != 0, state.stdout
    time.sleep(3.2)
    assert not (tmp_path / "survived").exists()


@bwrap
def test_comparator_service_reports_its_peak_memory(tmp_path):
    code = "import time; b = bytearray(100 * 2**20); time.sleep(1)"
    run = jail.stream(jail.Jail(workdir=tmp_path, deny_unix=True), [system_python(), "-c", code],
                      timeout=10, memory_max="2G")
    assert run.returncode == 0, run.stderr
    assert run.memory_peak is not None and 100 * 2**20 <= run.memory_peak < 2 * 2**30


@bwrap
@pytest.mark.parametrize("exit_code", [0, 7])
def test_comparator_service_preserves_main_exit_while_reaping_children(tmp_path, exit_code):
    """A surviving child must be killed without turning a completed command into a stop timeout."""
    import uuid
    marker = tmp_path / "survived"
    code = ("import os, signal, sys, time\n"
            "read, write = os.pipe()\n"
            "if os.fork() == 0:\n"
            "    os.close(read); signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
            "    os.write(write, b'ready'); os.close(write)\n"
            "    time.sleep(2); open(sys.argv[1], 'w').write('bad'); os._exit(0)\n"
            "os.close(write); os.read(read, 5); os.close(read)\n"
            "print('completed', flush=True); sys.exit(int(sys.argv[2]))\n")
    # Exercise the outer service directly: this makes a child remain at main-process exit
    # deterministically, without depending on bubblewrap's own teardown timing.
    argv = jail.guarded_service([system_python(), "-c", code, str(marker), str(exit_code)],
                                f"run-vl-reap-{uuid.uuid4().hex}.service", 5, "2G", None)
    result = subprocess.run(argv, capture_output=True, timeout=10)
    assert result.returncode == exit_code and result.stdout == b"completed\n", result
    time.sleep(2.2)
    assert not marker.exists()


def test_service_report_is_split_off_and_names_a_result_that_is_not_the_commands():
    unit = "run-vl-0123.service"
    report = (b"          Finished with result: timeout\n"
              b"Main processes terminated with: code=exited, status=0/SUCCESS\n"
              b"               Service runtime: 79ms\n                   Memory peak: 3.1M (swap: 0B)\n")
    err, why = jail._service_end(b"Running as unit: " + unit.encode() + b"; invocation ID: 9f\nown\n" + report, unit, 1)
    assert err == b"own\n" and "result 'timeout'" in why and "status=0/SUCCESS" in why and "exit status 1" in why
    for result in (b"success", b"exit-code"):                                     # the exit status is the command's
        assert jail._service_end(b"own\n" + report.replace(b"timeout", result), unit, 0) == (b"own\n", None)
    killed = report.replace(b"timeout", b"signal").replace(b"exited, status=0/SUCCESS", b"killed, status=9/KILL")
    assert "result 'signal'" in jail._service_end(killed, unit, 255)[1]           # 255 is systemd's, not 128 + 9
    forged = report + b"printed by the command after a forged report\n"         # only a report at the very end
    assert jail._service_end(forged, unit, 1) == (forged, None)


@bwrap
@pytest.mark.parametrize("force_stop_timeout", [True, False])
def test_a_stop_timeout_after_a_clean_exit_is_named_as_systemds(tmp_path, monkeypatch, force_stop_timeout):
    """A clean main exit can still end in a service timeout when a child ignores the stop signal.
    A finite stop deadline plants that result. The normal SIGKILL cleanup is the zero-exit control."""
    service = jail.guarded_service
    code = ("import os, signal\nread, write = os.pipe()\nif os.fork() == 0:\n"
            "    signal.signal(signal.SIGTERM, signal.SIG_IGN); os.close(read); os.close(write); signal.pause()\n"
            "    os._exit(0)\nos.close(write); os.read(read, 1)\nprint('All targets up-to-date', flush=True)\n")
    def leftover(argv, unit, *rest):                      # no bwrap: its namespace would take the child down with it
        out = service([system_python(), "-c", code], unit, *rest)
        if force_stop_timeout:
            out[out.index("KillSignal=SIGKILL")] = "KillSignal=SIGTERM"
            out[out.index("TimeoutStopSec=1s")] = "TimeoutStopSec=100ms"
        return out
    monkeypatch.setattr(jail, "guarded_service", leftover)
    run = jail.stream(jail.Jail(workdir=tmp_path, deny_unix=True), ["true"], timeout=20)
    assert run.stdout == b"All targets up-to-date\n", run
    if force_stop_timeout:
        clean = re.search(r"status=0(/SUCCESS)?\)", run.service)     # systemd 255 writes `code=exited/status=0`
        assert run.returncode == 1 and "result 'timeout'" in run.service and clean, run
        assert run.stderr == f"vl: {run.service}\n".encode()
    else:
        assert (run.returncode, run.service, run.stderr) == (0, None, b""), run


@bwrap
def test_the_memory_cap_of_a_guarded_service_is_named(tmp_path):
    """systemd stops the whole service when the OOM killer fires in it and systemd-run exits 1, not 128 + 9: without
    its report a memory kill looked like a normal exit 1."""
    code = "import time; b = bytearray(512 * 2**20); time.sleep(1); print('survived')"
    run = jail.stream(jail.Jail(workdir=tmp_path, deny_unix=True), [system_python(), "-c", code], timeout=30,
                      memory_max="128M")
    assert run.returncode == 1 and b"survived" not in run.stdout, run
    assert "result 'oom-kill': the memory cap was reached" in run.service, run


@bwrap
def test_comparator_service_cannot_run_without_a_user_manager(tmp_path, monkeypatch):
    monkeypatch.setenv("DBUS_SESSION_BUS_ADDRESS", "unix:path=/nonexistent-vl-test-user-bus")
    result = jail.run(jail.Jail(workdir=tmp_path, deny_unix=True),
                      ["/bin/sh", "-c", "touch should-not-exist"], timeout=5)
    assert result.returncode != 0 and not (tmp_path / "should-not-exist").exists()


@bwrap
@pytest.mark.parametrize("close_output", [False, True])
@pytest.mark.parametrize("stop_error", [OSError("injected stop failure"),
                                         subprocess.TimeoutExpired(["systemctl"], 5),
                                         RuntimeError("injected nonzero stop status")])
def test_comparator_stop_failure_keeps_timeout_and_reaps_client(tmp_path, monkeypatch, close_output, stop_error):
    """A failed service stop leaves cleanup unconfirmed; the client and its pipes are still released."""
    popen, service, stop = subprocess.Popen, jail.guarded_service, jail._stop_service
    clients = []
    def tracked_popen(argv, *args, **kwargs):
        proc = popen(argv, *args, **kwargs)
        if argv[0] == jail.program("systemd-run") and "--wait" in argv:
            clients.append(proc)
        return proc
    def longer_service(*args):
        argv = service(*args)
        # Keep the service alive long enough to demonstrate that client cleanup does not establish child cleanup.
        index = next(i for i, arg in enumerate(argv) if arg.startswith("RuntimeMaxSec="))
        argv[index] = "RuntimeMaxSec=5s"
        return argv
    def failed_stop(unit):
        raise stop_error
    monkeypatch.setattr(subprocess, "Popen", tracked_popen)
    monkeypatch.setattr(jail, "guarded_service", longer_service)
    monkeypatch.setattr(jail, "_stop_service", failed_stop)
    code = ("import os, time; print('started', flush=True); "
            + ("os.close(1); os.close(2); " if close_output else "") + "time.sleep(30)")
    unit = None
    try:
        run = jail.stream(jail.Jail(workdir=tmp_path, deny_unix=True), [system_python(), "-c", code], timeout=1)
        unit = next(arg.split("=", 1)[1] for arg in run.argv if arg.startswith("--unit="))
        assert run.returncode is None and run.stdout == b"started\n", run
        assert len(clients) == 1 and clients[0].poll() is not None
        assert clients[0].stdout.closed and clients[0].stderr.closed
        assert b"service stop failed" in run.stderr and b"immediate service cleanup is unconfirmed" in run.stderr
        assert b"RuntimeMaxSec remains the fallback" in run.stderr and unit.encode() in run.stderr
        assert any(name == "vl" and "service stop failed" in line for _, name, line in run.lines)
        state = subprocess.run([jail.program("systemctl"), "--user", "is-active", unit], capture_output=True)
        assert state.returncode == 0, state.stdout
    finally:
        if unit:
            stop(unit)



def test_service_stop_nonzero_status_is_an_error(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs:
                        subprocess.CompletedProcess(args[0], 1, "", "injected manager refusal"))
    with pytest.raises(RuntimeError, match="injected manager refusal"):
        jail._stop_service("run-vl-test.service")


@bwrap
def test_stream_selector_error_unwinds_and_stops_only_owned_unit(tmp_path: Path, monkeypatch):
    """Planted: an exception inside the selector loop unwinds stream(), kills its client and stops only its own
    systemd unit; a concurrent unrelated service unit stays active (control)."""
    import selectors
    import secrets
    unrelated = f"run-vl-unrelated-{secrets.token_hex(6)}.service"
    systemctl = jail.program("systemctl")
    systemd_run = jail.program("systemd-run")
    other = subprocess.Popen([systemd_run, "--user", "--wait", "--collect", f"--unit={unrelated}", "--",
                              system_python(), "-c", "import time; time.sleep(30)"],
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    units: list[str] = []
    real_service = jail.guarded_service
    def capture_service(argv, unit, *rest):
        units.append(unit)
        return real_service(argv, unit, *rest)
    monkeypatch.setattr(jail, "guarded_service", capture_service)
    real_select = selectors.DefaultSelector.select
    seen_data = False
    def exploding_select(self, timeout=None):
        nonlocal seen_data
        events = real_select(self, timeout)
        if events and seen_data:
            raise RuntimeError("injected selector failure")
        if events:
            seen_data = True
        return events
    monkeypatch.setattr(selectors.DefaultSelector, "select", exploding_select)
    try:
        code = "import time; print('line1', flush=True); time.sleep(0.2); print('line2', flush=True); time.sleep(30)"
        with pytest.raises(RuntimeError, match="injected selector failure"):
            jail.stream(jail.Jail(workdir=tmp_path, deny_unix=True), [system_python(), "-c", code], timeout=30)
        assert len(units) == 1
        assert subprocess.run([systemctl, "--user", "is-active", units[0]], capture_output=True).returncode != 0
        assert subprocess.run([systemctl, "--user", "is-active", unrelated], capture_output=True).returncode == 0
    finally:
        for unit in units:
            subprocess.run([systemctl, "--user", "stop", unit], capture_output=True)
        subprocess.run([systemctl, "--user", "stop", unrelated], capture_output=True)
        other.wait(timeout=5)
        other.stdout.close()
        other.stderr.close()


@bwrap
def test_stream_sigterm_stops_only_owned_service_unit(tmp_path: Path):
    """SIGTERM in a child running stream() stops its owned unit and leaves unrelated units alive."""
    import secrets
    import signal
    import sys
    unrelated = f"run-vl-unrelated-{secrets.token_hex(6)}.service"
    systemctl = jail.program("systemctl")
    systemd_run = jail.program("systemd-run")
    other = subprocess.Popen([systemd_run, "--user", "--wait", "--collect", f"--unit={unrelated}", "--",
                              system_python(), "-c", "import time; time.sleep(30)"],
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    unit_file = tmp_path / "owned.unit"
    script = (f"from pathlib import Path\n"
              f"from verifylab import jail\n"
              f"real = jail.guarded_service\n"
              f"def cap(argv, unit, *rest):\n"
              f"    Path({str(unit_file)!r}).write_text(unit)\n"
              f"    return real(argv, unit, *rest)\n"
              f"jail.guarded_service = cap\n"
              f"jail.stream(jail.Jail(workdir=Path({str(tmp_path)!r}), deny_unix=True),\n"
              f"            [{system_python()!r}, '-c', \"import time; print('up', flush=True); time.sleep(30)\"],\n"
              f"            timeout=60)\n")
    child = subprocess.Popen([sys.executable, "-c", script], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    owned = None
    try:
        deadline = time.monotonic() + 20
        while not unit_file.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert unit_file.exists()
        owned = unit_file.read_text().strip()
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if subprocess.run([systemctl, "--user", "is-active", owned], capture_output=True).returncode == 0:
                break
            time.sleep(0.05)
        os.kill(child.pid, signal.SIGTERM)
        child.wait(timeout=15)
        assert child.returncode != 0
        assert subprocess.run([systemctl, "--user", "is-active", owned], capture_output=True).returncode != 0
        assert subprocess.run([systemctl, "--user", "is-active", unrelated], capture_output=True).returncode == 0
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=5)
        child.stdout.close()
        child.stderr.close()
        if owned:
            subprocess.run([systemctl, "--user", "stop", owned], capture_output=True)
        subprocess.run([systemctl, "--user", "stop", unrelated], capture_output=True)
        other.wait(timeout=5)
        other.stdout.close()
        other.stderr.close()


def test_stop_owned_leaves_unregistered_units_alone(monkeypatch):
    """stop_owned() only calls _stop_service on units registered as owned by this process."""
    stopped = []
    monkeypatch.setattr(jail, "_stop_service", lambda u: stopped.append(u))
    with jail._OWNED_LOCK:
        jail._OWNED_UNITS.add("run-vl-mine.service")
    try:
        jail.stop_owned()
        assert stopped == ["run-vl-mine.service"]
    finally:
        with jail._OWNED_LOCK:
            jail._OWNED_UNITS.discard("run-vl-mine.service")
