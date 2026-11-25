"""What a receipt says of the run and the machine, and where the queue's limits come from: CPU seconds (S1), the host
class that makes receipts of different machines comparable (S6), `vl queue status --explain` (S1). None of it decides a
verdict; each reading has a planted source next to a control where the source is missing."""

from __future__ import annotations

import json
import os
import platform
from pathlib import Path
from types import SimpleNamespace

import pytest

from verifylab import cli, jail, machine, queue
from verifylab.adapters import lean_comparator
from verifylab.adapters.base import CheckOutcome
from verifylab.commands.check import build_receipt
from verifylab.records import receipt_problems, seal_receipt
from verifylab.repo import Repo

from conftest import system_python, write_machine


def bwrap(test):
    """The test runs bubblewrap: a `jail` test, skipped where bwrap is missing."""
    return pytest.mark.jail(pytest.mark.skipif(not jail.available(), reason="bwrap missing")(test))


# CPU seconds ---------------------------------------------------------------------------------------------------------

def test_cpu_usage_is_the_usage_usec_of_the_cgroup(tmp_path: Path):
    (tmp_path / "cpu.stat").write_text("usage_usec 2500000\nuser_usec 2000000\nsystem_usec 500000\nnr_periods 0\n")
    assert jail.cpu_usage(tmp_path) == 2.5
    (tmp_path / "cpu.stat").write_text("user_usec 1\n")                       # control: no such line, nothing read
    assert jail.cpu_usage(tmp_path) is None
    assert jail.cpu_usage(tmp_path / "gone") is None


@pytest.mark.parametrize("deny_unix", [False, True])
def test_a_run_reports_the_cpu_time_of_its_unit(tmp_path: Path, monkeypatch, deny_unix):
    unit = tmp_path / "unit"
    unit.mkdir()
    (unit / "memory.stat").write_text("anon 1024\n")
    (unit / "cpu.stat").write_text("usage_usec 3250000\n")
    command = ["/bin/sh", "-c", "echo up; sleep 0.3"]
    monkeypatch.setattr(jail, "available", lambda: True)
    monkeypatch.setattr(jail, "memory_capped", lambda argv, *caps: command)
    monkeypatch.setattr(jail, "guarded_service", lambda argv, *rest: command)
    monkeypatch.setattr(jail, "_scope_cgroup", lambda pid: unit, raising=False)
    monkeypatch.setattr(jail, "_service_cgroup", lambda name: unit)
    box = jail.Jail(workdir=tmp_path, deny_unix=deny_unix)
    assert jail.stream(box, ["true"], timeout=10, memory_max="2G").cpu_seconds == 3.25
    assert jail.run(box, ["true"], timeout=10, memory_max="2G").cpu_seconds == 3.25
    (unit / "cpu.stat").unlink()                                              # control: unreadable, not zero
    assert jail.stream(box, ["true"], timeout=10, memory_max="2G").cpu_seconds is None
    assert jail.stream(box, ["true"], timeout=10).cpu_seconds is None         # no unit at all


@bwrap
@pytest.mark.skipif(jail.cap_problem("2G") is not None, reason=f"no memory cap here: {jail.cap_problem('2G')}")
def test_stream_reads_the_cpu_time_of_its_scope(tmp_path: Path):
    box = jail.Jail(workdir=tmp_path, env=jail.filter_env(os.environ, jail.DEFAULT_ENV))
    busy = "import time; t = time.process_time()\nwhile time.process_time() - t < 1.0: pass"
    run = jail.stream(box, [system_python(), "-c", busy], timeout=60, memory_max="2G")
    assert run.returncode == 0, run.stderr
    assert run.cpu_seconds is not None and 0.5 <= run.cpu_seconds < 30, run.cpu_seconds
    idle = jail.stream(box, [system_python(), "-c", "import time; time.sleep(1.0)"], timeout=60, memory_max="2G")
    assert idle.cpu_seconds is not None and idle.cpu_seconds < 0.5, idle.cpu_seconds       # control: waiting costs none


def lean_check(tmp_path: Path) -> lean_comparator._Check:
    return lean_comparator._Check(SimpleNamespace(repo=SimpleNamespace(root=tmp_path), assurance="protected",
                                                  guards=(), timeout=60.0, memory_max=None, memory_total=None,
                                                  threads=None))


def test_a_check_adds_up_the_cpu_time_of_all_its_units_overlapping_or_not(tmp_path: Path):
    check = lean_check(tmp_path)
    assert "cpu_seconds" not in check.outcome("fail", []).extra               # control: nothing read, nothing recorded
    for step, cpu in (("preflight", 1.5), ("prebuild_challenge", 20.0), ("comparator", 30.25), ("probes", 4.0),
                      ("comparator", 0.25)):                                  # a second Comparator run counts too
        check.note_run(step, SimpleNamespace(memory_peak=1, cpu_seconds=cpu))
    check.note_run("probes", SimpleNamespace(memory_peak=None, cpu_seconds=None))      # an unreadable unit adds nothing
    check.note_run("preflight", RuntimeError("the unit never started"))               # nor does a run with no figures
    extra = check.outcome("fail", []).extra
    assert extra["cpu_seconds"] == 56.0 and extra["memory_peak_bytes"] == lean_comparator.check_peak(check.peaks)


def test_a_receipt_with_cpu_seconds_is_well_formed_and_a_text_is_not(research_repo: Path):
    repo = Repo.open(research_repo)
    item = repo.load_item("add-zero")
    outcome = CheckOutcome("unsupported", ["x"], {"a.lean": "0" * 64}, {}, {}, {}, {}, [], "",
                           {"cpu_seconds": 56.0, "memory_peak_bytes": 1})
    receipt = build_receipt(repo, item, item, "lean-comparator", "exploratory", repo.trusted_commit, outcome, "t0", "t1")
    assert receipt["extra"]["cpu_seconds"] == 56.0 and receipt_problems(json.loads(json.dumps(receipt))) == []
    body = {k: v for k, v in receipt.items() if k != "receipt_id"}
    body["extra"] = {**body["extra"], "cpu_seconds": "56"}
    assert any("extra.cpu_seconds" in p for p in receipt_problems(seal_receipt(body)))


# The host class ------------------------------------------------------------------------------------------------------

def test_the_cpu_model_is_the_first_model_name_of_cpuinfo(tmp_path: Path):
    cpuinfo = tmp_path / "cpuinfo"
    cpuinfo.write_text("processor\t: 0\nmodel name\t: Example CPU @ 3.00GHz\nprocessor\t: 1\nmodel name\t: Other\n")
    assert machine.cpu_model(cpuinfo) == "Example CPU @ 3.00GHz"
    cpuinfo.write_text("processor\t: 0\n")                                       # control: none says it
    assert machine.cpu_model(cpuinfo) in (None, platform.processor())


def test_the_host_class_names_the_sandbox_the_kernel_the_cpu_the_cores_and_the_ram():
    host = machine.host_class()
    assert host["sandbox_class"] == "workstation" and host["kernel"] == os.uname().release
    assert host["arch"] == os.uname().machine and host["cores"] == os.cpu_count() and host["ram_bytes"] > 0
    write_machine(sandbox="disposable")
    assert machine.host_class()["sandbox_class"] == "disposable"


def test_every_receipt_records_the_host_class(research_repo: Path):
    repo = Repo.open(research_repo)
    item = repo.load_item("add-zero")
    outcome = CheckOutcome("unsupported", ["x"], {"a.lean": "0" * 64}, {}, {}, {"third_party_packages": ["PrimeCert"]},
                           {}, [], "")
    for sandbox in ("workstation", "disposable"):
        write_machine(sandbox=sandbox)
        receipt = build_receipt(repo, item, item, "lean-comparator", "exploratory", repo.trusted_commit, outcome,
                                "t0", "t1")
        assert receipt["environment"]["host"] == machine.host_class() and receipt["environment"]["host"][
            "sandbox_class"] == sandbox
        assert receipt["environment"]["third_party_packages"] == ["PrimeCert"]
        assert receipt_problems(json.loads(json.dumps(receipt))) == []
    body = {k: v for k, v in receipt.items() if k != "receipt_id"}
    body["environment"] = {**body["environment"], "host": {"sandbox_class": "disposable", "cores": "many"}}
    assert any("environment.host.cores" in p for p in receipt_problems(seal_receipt(body)))


def test_receipt_records_deterministic_environment_class(research_repo: Path, tmp_path):
    repo = Repo.open(research_repo)
    item = repo.load_item("add-zero")
    env_a = {"toolchain": "leanprover/lean:v4.29.0", "lean_version": "4.29.0",
             "tools": {"comparator": {"path": "/tmp/a/comparator", "sha256": "1" * 64}},
             "packages": {"dir": "/tmp/a/.lake/packages", "identity": "pkg-v1"}}
    env_b = {"toolchain": "leanprover/lean:v4.29.0", "lean_version": "4.29.0",
             "tools": {"comparator": {"path": "/srv/other/comparator", "sha256": "1" * 64}},
             "packages": {"dir": "/srv/other/.lake/packages", "identity": "pkg-v1"}}
    write_machine(sandbox="workstation")
    r_a = build_receipt(repo, item, item, "lean-comparator", "exploratory", repo.trusted_commit,
                        CheckOutcome("unsupported", ["x"], {"a.lean": "0" * 64}, {}, {}, env_a, {}, [], ""),
                        "2026-11-14T00:00:00+00:00", "2026-11-14T00:00:05+00:00", {"memory_cap": "16G"},
                        {"machine": {"name": "box-alpha"}})
    write_machine(sandbox="workstation")
    r_b = build_receipt(repo, item, item, "lean-comparator", "exploratory", repo.trusted_commit,
                        CheckOutcome("unsupported", ["x"], {"a.lean": "0" * 64}, {}, {}, env_b, {}, [], ""),
                        "2026-11-15T12:00:00+00:00", "2026-11-15T12:00:09+00:00", {"memory_cap": "16G"},
                        {"machine": {"name": "box-beta"}})
    ec_a = r_a["environment"]["environment_class"]
    ec_b = r_b["environment"]["environment_class"]
    assert ec_a == ec_b and ec_a["digest"].startswith("sha256:")
    assert receipt_problems(json.loads(json.dumps(r_a))) == []
    # Changing a tool sha256 or sandbox class changes the environment_class digest.
    env_c = {**env_a, "tools": {"comparator": {"path": "/tmp/a/comparator", "sha256": "2" * 64}}}
    r_c = build_receipt(repo, item, item, "lean-comparator", "exploratory", repo.trusted_commit,
                        CheckOutcome("unsupported", ["x"], {"a.lean": "0" * 64}, {}, {}, env_c, {}, [], ""),
                        "t0", "t1", {"memory_cap": "16G"})
    assert r_c["environment"]["environment_class"]["digest"] != ec_a["digest"]
    bad = {k: v for k, v in r_a.items() if k != "receipt_id"}
    bad["environment"] = {**bad["environment"], "environment_class": {"digest": 42}}
    assert any("environment.environment_class.digest" in p for p in receipt_problems(seal_receipt(bad)))


# Where the queue's limits come from ----------------------------------------------------------------------------------

def sources() -> dict[str, tuple[str, str]]:
    return {row["name"]: (row["source"], row["where"]) for row in queue.limits()}


def test_each_limit_names_its_source():
    write_machine(queue={"slots": 4, "default_reservation": "1G"}, check={"memory_total": "20G"})
    found = sources()
    assert found["slots"] == ("machine.toml", "[queue] slots")
    assert found["memory_total"] == ("machine.toml", "[check] memory_total")
    assert found["default_reservation"] == ("machine.toml", "[queue] default_reservation")
    assert found["keep_free"] == ("machine.toml", "[queue] keep_free")         # the test machine keeps no floor
    assert found["max_wait"] == ("default", "no limit")


def test_a_limit_nobody_set_is_the_default():
    found = sources()                                                           # control: the test machine's file
    assert found["slots"] == ("default", "the number of CPUs divided by threads")
    assert found["threads"] == ("default", "4, at most the number of CPUs")
    assert found["memory_total"] == ("default", "70% of RAM")
    assert found["default_reservation"] == ("default", "2G")


def test_a_flag_beats_machine_toml_and_the_default():
    write_machine(queue={"max_wait": 60})
    assert {r["name"]: r for r in queue.limits()}["max_wait"]["value"] == 60
    flagged = {r["name"]: r for r in queue.limits(mem=1 << 30, max_wait=5.0)}
    assert (flagged["max_wait"]["value"], flagged["max_wait"]["source"], flagged["max_wait"]["where"]) == (
        5.0, "flag", "--max-wait")
    assert (flagged["reservation"]["value"], flagged["reservation"]["source"]) == (1 << 30, "flag")
    assert "reservation" not in {r["name"] for r in queue.limits()}


def test_vl_queue_status_explain_prints_each_limit_and_its_source(capsys):
    write_machine(queue={"slots": 3})
    assert cli.main(["queue", "status", "--explain", "--mem", "1G", "--max-wait", "9"]) == 0
    out = capsys.readouterr().out
    assert "limits (flag > machine.toml > default):" in out
    assert "slots" in out and "machine.toml: [queue] slots" in out
    assert "flag: --mem" in out and "flag: --max-wait" in out and "default: 70% of RAM" in out
    assert cli.main(["queue", "status", "--explain", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert {r["name"]: r["source"] for r in data["limits"]}["slots"] == "machine.toml"
    assert cli.main(["queue", "status", "--json"]) == 0
    assert "limits" not in json.loads(capsys.readouterr().out)                  # control: only on request
    assert cli.main(["queue", "status", "--mem", "1G"]) == 2
