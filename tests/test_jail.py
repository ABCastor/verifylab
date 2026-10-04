from __future__ import annotations

import os
import re
import socket
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
    with socket.create_server(("127.0.0.1", 0)) as server:
        port = server.getsockname()[1]
        probe = (
            "import os, socket\n"
            f"print(open({str(secret)!r}).read() if os.path.exists({str(secret)!r}) else 'hidden')\n"
            f"print(os.path.isdir({str(Path.home())!r}))\n"
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
