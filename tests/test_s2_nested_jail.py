"""Nested bwrap capability probes, not qualification of the absent Lake backend."""

from __future__ import annotations

from dataclasses import replace

import pytest

from conftest import system_python
from verifylab import jail


@pytest.mark.jail
@pytest.mark.skipif(not jail.available(), reason="bwrap missing")
def test_nested_bwrap_keeps_outer_readonly_and_unix_socket_denial(tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    package = tmp_path / "package"
    package.mkdir()
    marker = package / "source"
    marker.write_text("pinned source\n")
    code = (
        "import errno, socket\n"
        f"open({str(work / 'positive')!r}, 'w').write('allowed')\n"
        "print('write-positive')\n"
        f"try:\n    open({str(marker)!r}, 'w').write('escaped'); print('write-escaped')\n"
        "except OSError:\n    print('write-denied')\n"
        "try:\n    socket.socket(socket.AF_UNIX); print('unix-allowed')\n"
        "except OSError as exc:\n    print('unix-denied', exc.errno)\n"
    )
    outer = jail.Jail(workdir=work, read_only=(package,))
    inner = jail.Jail(workdir=work, read_only=(package,))
    command = inner.argv([system_python(), "-c", code])
    control = jail.run(outer, command, timeout=30)
    assert control.returncode == 0, control.stderr
    assert control.stdout.splitlines() == [b"write-positive", b"write-denied", b"unix-allowed"]
    guarded = jail.run(replace(outer, deny_unix=True), command, timeout=30, memory_max="2G")
    assert guarded.returncode == 0, guarded.stderr
    assert guarded.stdout.splitlines() == [b"write-positive", b"write-denied", b"unix-denied 97"]
    assert marker.read_text() == "pinned source\n"
    assert (work / "positive").read_text() == "allowed"


@pytest.mark.jail
@pytest.mark.skipif(not jail.available(), reason="bwrap missing")
def test_inner_bind_cannot_upgrade_outer_readonly_package(tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    package = tmp_path / "package"
    package.mkdir()
    marker = package / "source"
    marker.write_text("pinned source\n")
    code = (f"try:\n    open({str(marker)!r}, 'w').write('escaped'); print('escaped')\n"
            "except OSError:\n    print('denied')\n")
    outer = jail.Jail(workdir=work, read_only=(package,), deny_unix=True)
    inner = jail.Jail(workdir=work, read_write=(package,))
    result = jail.run(outer, inner.argv([system_python(), "-c", code]), timeout=30, memory_max="2G")
    assert marker.read_text() == "pinned source\n", (result.returncode, result.stdout, result.stderr)
    assert result.returncode != 0 or result.stdout == b"denied\n", result
