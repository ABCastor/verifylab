"""scripts/gate: the commit gate refuses on any failure, error, non-zero pytest exit or skipped lean/jail test.

The suite is replaced by a fake `uv` that prints a chosen summary and exits with a chosen code, so each
refusal rule is exercised on its own (a summary that says "failed" with exit 0 must still be refused).
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

GATE = Path(__file__).resolve().parents[1] / "scripts" / "gate"

FAKE_UV = """#!/bin/sh
echo "$@" > "$FAKE_DIR/uv.argv"
echo "${PYTEST_ADDOPTS-unset}" > "$FAKE_DIR/uv.addopts"
printf '%s\\n' "$FAKE_OUT"
exit "$FAKE_CODE"
"""

FAKE_SYSTEMD_RUN = """#!/bin/sh
echo "$@" > "$FAKE_DIR/systemd-run.argv"
while [ "$1" != "--" ]; do shift; done
shift
exec "$@"
"""


def _script(path: Path, text: str) -> None:
    path.write_text(text)
    path.chmod(0o755)


@pytest.fixture
def fake_bin(tmp_path: Path) -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _script(bin_dir / "uv", FAKE_UV)
    _script(bin_dir / "systemd-run", FAKE_SYSTEMD_RUN)
    _script(bin_dir / "systemctl", "#!/bin/sh\nexit 0\n")
    return bin_dir


NO_SKIPS = "vl-gate: 0 lean/jail tests skipped\n"


def gate(bin_dir: Path, out: str, code: int = 0, *args: str, path: str | None = None, allow_skip: str | None = None):
    env = {**os.environ, "FAKE_DIR": str(bin_dir), "FAKE_OUT": out, "FAKE_CODE": str(code),
           "PATH": f"{bin_dir}:{path if path is not None else os.environ['PATH']}"}
    env.pop("VL_GATE_ALLOW_SKIP", None)
    if allow_skip is not None:
        env["VL_GATE_ALLOW_SKIP"] = allow_skip
    return subprocess.run([str(GATE), *args], env=env, capture_output=True, text=True, timeout=60)


def test_green_suite_passes_and_prints_the_summary_under_a_memory_cap(fake_bin):
    proc = gate(fake_bin, "....\n" + NO_SKIPS + "245 passed in 154.16s (0:02:34)")
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "gate: ok: 245 passed in 154.16s (0:02:34)"
    assert (fake_bin / "uv.argv").read_text().split() == ["run", "pytest", "-q", "-rs"]
    capped = (fake_bin / "systemd-run.argv").read_text().split()
    assert capped[:3] == ["--user", "--scope", "--quiet"] and "MemoryMax=8G" in capped


@pytest.mark.parametrize("out, code", [
    ("1 failed, 244 passed in 3.00s", 1),
    ("244 passed, 1 error in 3.00s", 1),
    ("2 errors in 0.10s", 2),
    ("1 failed, 2 passed in 1.00s", 0),       # a failure count refuses even when the exit code says 0
    ("3 passed in 0.50s", 1),                 # a non-zero exit refuses even when the summary looks green
    ("no summary here", 0),                   # no summary line: refused, never assumed green
    ("no tests ran in 0.01s", 5),
])
def test_any_failure_error_or_nonzero_exit_is_refused(fake_bin, out, code):
    proc = gate(fake_bin, NO_SKIPS + out, code)
    assert proc.returncode == 1
    assert "gate: REFUSED" in proc.stderr and "gate: ok" not in proc.stdout


def test_arguments_are_refused_so_the_gate_always_runs_everything(fake_bin):
    proc = gate(fake_bin, "1 passed in 0.10s", 0, "-m", "not lean")
    assert proc.returncode == 2 and not (fake_bin / "uv.argv").exists()


def test_an_inherited_selection_never_reaches_the_suite_and_a_deselection_is_refused(fake_bin, monkeypatch):
    """PYTEST_ADDOPTS="-m 'not lean and not jail'" in the caller's environment once let the gate pass the fast
    suite: the gate drops it, and refuses any summary that reports deselected tests."""
    monkeypatch.setenv("PYTEST_ADDOPTS", "-m 'not lean and not jail'")
    proc = gate(fake_bin, NO_SKIPS + "373 passed in 10.00s")
    assert proc.returncode == 0, proc.stderr
    assert (fake_bin / "uv.addopts").read_text().strip() == "unset"
    proc = gate(fake_bin, NO_SKIPS + "373 passed, 160 deselected in 10.47s")
    assert proc.returncode == 1 and "gate: REFUSED: tests were deselected" in proc.stderr


def test_without_systemd_run_the_suite_runs_uncapped(fake_bin, tmp_path):
    (fake_bin / "systemd-run").unlink()
    tools = tmp_path / "tools"
    tools.mkdir()
    for name in ("bash", "dirname", "mktemp", "rm", "grep", "tail", "cat", "env"):
        (tools / name).symlink_to(shutil.which(name))
    proc = gate(fake_bin, NO_SKIPS + "7 passed in 0.10s", 0, path=str(tools))
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "gate: ok: 7 passed in 0.10s"


def test_installed_systemd_without_a_user_manager_runs_the_suite_uncapped(fake_bin, tmp_path, monkeypatch):
    _script(fake_bin / "systemctl", "#!/bin/sh\nexit 1\n")
    # Plant the headless-runner failure: invoking the scope would fail before pytest starts.
    _script(fake_bin / "systemd-run", "#!/bin/sh\nexit 1\n")
    proc = gate(fake_bin, NO_SKIPS + "7 passed in 0.10s")
    assert proc.returncode == 0, proc.stderr
    assert (fake_bin / "uv.argv").exists()
    assert proc.stdout.strip() == "gate: ok: 7 passed in 0.10s"
    guard_disabled = tmp_path / "gate-disabled"
    text = GATE.read_text().replace(
        ' && command -v systemctl >/dev/null 2>&1 \\\n    && systemctl --user show-environment >/dev/null 2>&1', '')
    assert text != GATE.read_text()
    _script(guard_disabled, text)
    monkeypatch.setitem(globals(), "GATE", guard_disabled)
    (fake_bin / "uv.argv").unlink()
    assert gate(fake_bin, NO_SKIPS + "7 passed in 0.10s").returncode == 1
    assert not (fake_bin / "uv.argv").exists()


SKIPPED = "vl-gate: 72 lean/jail tests skipped\nSKIPPED [72] tests/test_lean_adapter.py:40: toolchain missing\n"


def test_skipped_lean_or_jail_tests_refuse_the_gate(fake_bin):
    proc = gate(fake_bin, SKIPPED + "300 passed, 72 skipped in 1.00s")
    assert proc.returncode == 1 and "gate: ok" not in proc.stdout
    assert "gate: REFUSED: 72 lean/jail tests were skipped and did not run" in proc.stderr
    assert "toolchain missing" in proc.stderr                       # the reasons are shown
    assert gate(fake_bin, SKIPPED + "300 passed, 72 skipped in 1.00s", allow_skip="yes").returncode == 1


def test_allowed_skips_pass_loudly_naming_the_count(fake_bin):
    proc = gate(fake_bin, SKIPPED + "300 passed, 72 skipped in 1.00s", allow_skip="1")
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.splitlines() == ["gate: WARNING: 72 LEAN/JAIL TESTS WERE SKIPPED AND DID NOT RUN "
                                        "(VL_GATE_ALLOW_SKIP=1)", "gate: ok: 300 passed, 72 skipped in 1.00s"]


def test_other_skips_do_not_refuse_but_a_missing_skip_count_does(fake_bin):
    assert gate(fake_bin, NO_SKIPS + "300 passed, 2 skipped in 1.00s").returncode == 0
    proc = gate(fake_bin, "300 passed in 1.00s")
    assert proc.returncode == 1 and "no 'vl-gate: N lean/jail tests skipped' line" in proc.stderr


def test_the_skip_count_comes_from_the_lean_and_jail_markers():
    """The conftest hook counts a skipped test only when it carries a gated marker, and always prints its line."""
    from types import SimpleNamespace

    import conftest
    reports = [SimpleNamespace(keywords={"lean": 1, "test_x": 1}), SimpleNamespace(keywords={"jail": 1}),
               SimpleNamespace(keywords={"test_y": 1})]
    lines = []
    reporter = SimpleNamespace(stats={"skipped": reports}, write_line=lines.append)
    conftest.pytest_terminal_summary(reporter)
    assert lines == ["vl-gate: 2 lean/jail tests skipped"]
    conftest.pytest_terminal_summary(SimpleNamespace(stats={}, write_line=lines.append))
    assert lines[-1] == "vl-gate: 0 lean/jail tests skipped"
