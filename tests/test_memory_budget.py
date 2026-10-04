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
                       ["/usr/bin/systemctl", "--user", "set-property", "--runtime", "vl.slice", "MemoryMax=20G"]]


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
