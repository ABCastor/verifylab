"""One memory budget: every capped run joins vl.slice, whose aggregate cap is the machine's [check] memory_total."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from verifylab import jail, machine
from verifylab.config import load_config, parse_config
from verifylab.machine import default_memory_total

from conftest import commit_all, write_machine

BASE = '[project]\nname = "budget"\n'


def test_default_total_is_seventy_percent_of_physical_ram(tmp_path: Path):
    meminfo = tmp_path / "meminfo"
    meminfo.write_text("MemTotal:        1000000 kB\nMemFree:          5 kB\n")
    assert default_memory_total(meminfo) == "700000K"
    assert default_memory_total(tmp_path / "missing") is None


def test_memory_total_comes_from_the_machine_or_the_default(tmp_path: Path, monkeypatch):
    write_machine(check={"memory_total": "20G"})
    assert machine.load().total_memory_cap() == "20G"
    meminfo = tmp_path / "meminfo"
    meminfo.write_text("MemTotal:        2000000 kB\n")
    monkeypatch.setattr("verifylab.machine.default_memory_total", lambda: default_memory_total(meminfo))
    write_machine()
    assert machine.load().total_memory_cap() == "1400000K"
    for bad in ('memory_total = ""', "memory_total = 20", 'memory_totl = "20G"'):
        machine.config_path().write_text(f"[check]\n{bad}\n")
        machine.forget()
        with pytest.raises(machine.MachineError, match=r"\[check\]"):
            machine.load()


def test_a_project_memory_total_is_deprecated_and_ignored(research_repo, monkeypatch, capsys):
    """Two projects that each set the cap of the shared slice would overwrite each other's: only the machine sets it."""
    write_machine(check={"memory_total": "20G"})
    cfg = research_repo / "research" / "vl.toml"
    cfg.write_text(cfg.read_text() + '\n[check]\nmemory_total = "1G"\n')
    commit_all(research_repo, "project cap")
    config = load_config(research_repo)
    assert config.legacy_memory_total == "1G" and not hasattr(config, "memory_total")
    assert any("[check] memory_total = '1G' is deprecated and ignored" in m for _, m in config.warnings)
    assert machine.load().total_memory_cap() == "20G"
    with pytest.raises(Exception, match="memory_total must be a string"):
        parse_config(research_repo, BASE + "[check]\nmemory_total = 1\n")


@pytest.fixture
def systemd(monkeypatch):
    calls = []

    def fake_run(argv, **kwargs):
        calls.append(list(argv))
        return subprocess.CompletedProcess(argv, 0, "", "")
    monkeypatch.setattr(jail, "program", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(jail.subprocess, "run", fake_run)
    return calls


def test_capped_run_joins_the_slice_with_its_own_cap_and_caps_the_slice(systemd):
    argv = jail.memory_capped(["bwrap", "true"], "16G", "20G")
    assert argv[:6] == ["/usr/bin/systemd-run", "--user", "--scope", "--quiet", "--collect", "--slice=vl.slice"]
    assert argv[argv.index("-p") + 1] == "MemoryMax=16G" and argv[-3:] == ["--", "bwrap", "true"]
    assert systemd == [["/usr/bin/systemd-run", "--user", "--scope", "--quiet", "--collect", "true"],   # the probe
                       ["/usr/bin/systemctl", "--user", "set-property", "--runtime", "vl.slice", "MemoryMax=20G",
                        "CPUWeight=20"]]                     # the owner's own programs come first for the CPU


def test_without_a_total_the_run_still_joins_the_slice(systemd):
    assert "--slice=vl.slice" in jail.memory_capped(["true"], "16G", None)
    assert not any(call[0].endswith("systemctl") for call in systemd)


def test_a_slice_that_cannot_be_capped_is_a_tool_failure(monkeypatch):
    """A user manager that runs scopes but refuses the slice's cap: a tool failure, never a silent uncapped run."""
    monkeypatch.setattr(jail, "program", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(jail.subprocess, "run", lambda argv, **kw: subprocess.CompletedProcess(
        argv, 1 if argv[0].endswith("systemctl") else 0, "", "Access denied"))
    with pytest.raises(RuntimeError, match="cannot cap vl.slice at 20G: Access denied"):
        jail.memory_capped(["true"], "16G", "20G")


def test_without_systemd_run_nothing_is_wrapped_or_capped(monkeypatch):
    monkeypatch.setattr(jail, "program", lambda name: None)
    monkeypatch.setattr(jail.subprocess, "run", lambda *a, **k: pytest.fail("systemctl must not run"))
    assert jail.memory_capped(["true"], "16G", "20G") == ["true"]


@pytest.fixture(autouse=True)
def fresh_scope_probe():
    """Each test asks systemd-run anew: the answer is cached per process."""
    jail._scope_problem.cache_clear()
    yield
    jail._scope_problem.cache_clear()


def test_a_run_without_a_systemd_user_manager_runs_uncapped_and_says_so(monkeypatch):
    calls = []

    def no_bus(argv, **kwargs):
        calls.append(list(argv))
        return subprocess.CompletedProcess(argv, 1, "", "Failed to connect to bus: No medium found")
    monkeypatch.setattr(jail, "program", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(jail.subprocess, "run", no_bus)
    assert jail.memory_capped(["bwrap", "true"], "16G", "20G") == ["bwrap", "true"]
    assert jail.memory_capped(["bwrap", "true"], "16G", "20G") == ["bwrap", "true"]
    record = jail.cap_record("16G", "20G")
    assert record["memory_cap"] == "none" and "Failed to connect to bus" in record["memory_cap_why"]
    assert len(calls) == 1 and calls[0][:3] == ["/usr/bin/systemd-run", "--user", "--scope"]   # asked once


def test_a_capped_run_also_caps_its_tasks(systemd):
    argv = jail.memory_capped(["bwrap", "true"], "16G", None)
    assert argv[argv.index(f"TasksMax={jail.TASKS_MAX}") - 1] == "-p" and jail.TASKS_MAX == 4096
    assert jail.cap_record("16G", None)["memory_cap"]["tasks_max"] == 4096


def test_a_lean_check_records_the_peak_of_the_runs_that_overlap(tmp_path: Path, monkeypatch):
    """The preflight runs beside the challenge build, the statement probes beside Comparator: the peak a check
    records, which the job queue reserves for the item's next check, is that of the larger pair together."""
    import threading
    from types import SimpleNamespace
    from verifylab.adapters import lean_comparator
    G = 1 << 30

    def unit(command):                             # the preflight, the challenge build (it fails), the probes
        peak, code = ((G, 0) if "--no-build" in command else (3 * G, 1) if lean_comparator.CHALLENGE in command
                      else (2 * G, 0))
        return SimpleNamespace(returncode=code, stdout=b"", stderr=b"", lines=(), seconds=0.1, service=None,
                               memory_peak=peak)
    monkeypatch.setattr(jail, "run", lambda box, command, timeout, **kw: unit(command))
    monkeypatch.setattr(jail, "stream", lambda box, command, timeout, **kw: unit(command))
    check = lean_comparator._Check(SimpleNamespace(repo=SimpleNamespace(root=tmp_path), assurance="protected",
                                                   guards=(), timeout=60.0, memory_max=None, memory_total=None,
                                                   threads=None))
    check.note_peak("comparator", 4 * G)
    assert check.outcome("fail", []).extra["memory_peak_bytes"] == 4 * G     # control: Comparator's unit alone
    cancel = threading.Event()
    check.preflight(None, tmp_path / "pre", "leanprover/lean4:v4.0.0", "", {}, tmp_path, ["Mathlib"], cancel)
    (tmp_path / "project").mkdir()
    assert check.prebuild_challenge(None, tmp_path / "project", tmp_path, tmp_path / "landrun", tmp_path / "probe",
                                    None, cancel) is None
    config = SimpleNamespace(probe_timeout=30.0, probe_heartbeats=1000,
                             lean=SimpleNamespace(permitted_axioms=[], roots=[]))
    (tmp_path / "probe").mkdir()
    check.run_probes(None, tmp_path / "probe", ("main",), config, None, [], cancel)
    assert check.peaks == {"preflight": G, "prebuild_challenge": 3 * G, "comparator": 4 * G, "probes": 2 * G}
    assert check.outcome("fail", []).extra["memory_peak_bytes"] == 6 * G     # Comparator and the probes at once
    check.note_peak("probes_alone", 5 * G)                                    # probes run again, alone: not added
    assert check.outcome("fail", []).extra["memory_peak_bytes"] == 6 * G
    check.note_peak("probes_alone", 7 * G)
    assert check.outcome("fail", []).extra["memory_peak_bytes"] == 7 * G


def test_a_lean_check_passes_its_thread_limit_to_lean_and_lake(tmp_path: Path, monkeypatch):
    """The CPUs one check may use reach Lean and Lake as LEAN_NUM_THREADS: in the jail's environment, and through
    landrun, which passes on only the variables it names, to the challenge build."""
    from types import SimpleNamespace
    from verifylab.adapters import lean_comparator
    tools = {"landrun": tmp_path / "landrun", "lean4export": tmp_path / "lean4export"}
    assert lean_comparator.comparator_jail(tmp_path, tmp_path, tools, None, 3).env["LEAN_NUM_THREADS"] == "3"
    assert "LEAN_NUM_THREADS" not in lean_comparator.comparator_jail(tmp_path, tmp_path, tools, None).env  # control
    seen = []
    monkeypatch.setattr(jail, "run", lambda box, command, timeout, **kw: seen.append(command) or SimpleNamespace(
        returncode=1, stdout=b"", stderr=b"", memory_peak=None, cpu_seconds=None))
    (tmp_path / "project").mkdir()
    for threads in (3, None):
        check = lean_comparator._Check(SimpleNamespace(repo=SimpleNamespace(root=tmp_path), assurance="protected",
                                                       guards=(), timeout=60.0, memory_max=None, memory_total=None,
                                                       threads=threads))
        check.prebuild_challenge(None, tmp_path / "project", tmp_path, tmp_path / "landrun", tmp_path / "probe",
                                 None, None)
    passed = [any(command[i:i + 2] == ["--env", "LEAN_NUM_THREADS"] for i in range(len(command))) for command in seen]
    assert passed == [True, False]


def test_a_check_caps_its_job_slice_at_its_threads(monkeypatch, tmp_path: Path):
    """A check's job slice gets a CPU quota of its threads, so the checks the queue admits together use at most the
    CPUs there are; the runtime setting is removed when the job ends."""
    from verifylab import queue
    calls = []
    monkeypatch.setattr(jail, "program", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(jail.subprocess, "run", lambda argv, **kw: subprocess.CompletedProcess(argv, 0, "", "") if (
        "systemctl" not in argv[0]) else calls.append(argv) or subprocess.CompletedProcess(argv, 0, "", ""))
    ticket = queue.Ticket(tmp_path / "123-abc.json", None, None)
    with queue.inside(ticket, 4, "8G"):
        pass
    slice_ = jail.job_slice("123-abc")
    assert calls[0] == ["/usr/bin/systemctl", "--user", "set-property", "--runtime", slice_, "CPUQuota=400%",
                        "MemoryMax=8G", "MemorySwapMax=0"]           # the check's runs share one memory cap too
    assert calls[-1] == ["/usr/bin/systemctl", "--user", "revert", slice_]
    calls.clear()
    with queue.inside(ticket):                                     # control: a job without threads is not capped
        pass
    assert calls == []


def test_a_run_that_is_stopped_still_reports_the_peak_it_reached(tmp_path: Path, monkeypatch):
    """A challenge build cut off (by the deadline, or by a failed preflight) held memory until then: the check's
    peak, which the next check reserves, counts it; and it says which of the two stopped it."""
    import threading
    from types import SimpleNamespace
    from verifylab.adapters import lean_comparator
    G = 1 << 30
    stopped = SimpleNamespace(returncode=None, stdout=b"", stderr=b"", lines=(), seconds=1.0, service=None,
                              memory_peak=3 * G, cpu_seconds=None)
    monkeypatch.setattr(jail, "stream", lambda box, command, timeout, **kw: stopped)
    with pytest.raises(subprocess.TimeoutExpired) as caught:
        jail.run(None, ["true"], timeout=1)
    assert caught.value.memory_peak == 3 * G
    check = lean_comparator._Check(SimpleNamespace(repo=SimpleNamespace(root=tmp_path), assurance="protected",
                                                   guards=(), timeout=60.0, memory_max=None, memory_total=None,
                                                   threads=None))
    (tmp_path / "project").mkdir()
    with pytest.raises(lean_comparator._Stop):
        check.prebuild_challenge(None, tmp_path / "project", tmp_path, tmp_path / "landrun", tmp_path / "probe",
                                 None, None)
    assert check.peaks == {"prebuild_challenge": 3 * G}
    stopped.memory_peak = None                                       # control: nothing readable, nothing recorded
    with pytest.raises(lean_comparator._Stop) as deadline:
        check.prebuild_challenge(None, tmp_path / "project", tmp_path, tmp_path / "landrun", tmp_path / "probe",
                                 None, None)
    assert check.peaks == {"prebuild_challenge": 3 * G}
    assert "timed out" in deadline.value.reasons[0]
    cancelled = threading.Event()
    cancelled.set()                                                   # a failed preflight stopped it: no timeout
    with pytest.raises(lean_comparator._Stop) as stop:
        check.prebuild_challenge(None, tmp_path / "project", tmp_path, tmp_path / "landrun", tmp_path / "probe",
                                 None, cancelled)
    assert "dependency preflight failed" in stop.value.reasons[0]
