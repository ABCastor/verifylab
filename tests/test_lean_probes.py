"""Statement probes (triviality, vacuity) run after a pass on a copy of the challenge built before any candidate
code: what they record, and that a crash or a timeout of the probe run never changes the verdict."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lean_helpers import explain, git, lean_unavailable, make_repo, request, run_case, tool_env
from verifylab.adapters import lean_probes
from verifylab.adapters.lean_comparator import LeanComparatorAdapter
from verifylab.config import ConfigError, PROBE_HEARTBEATS, PROBE_TIMEOUT, parse_config
from verifylab.status import probe_notes

LEAN_SKIP = lean_unavailable()
THEOREMS = ("VL.A.main", "VL.A.witness")


@pytest.fixture
def lean_env(monkeypatch):
    if LEAN_SKIP:
        pytest.skip(LEAN_SKIP)
    for key, value in tool_env().items():
        monkeypatch.setenv(key, value)


# The probe file and its output --------------------------------------------------------------------------

def test_probe_file_names_theorems_as_strings_and_never_contains_statement_text():
    text = lean_probes.probe_file("VLChallenge", THEOREMS, ["intros; trivial", "simp"], ["Aesop"],
                                  ["propext"], 1234, "n0nce", vacuity=False)
    head = text.split("\n\n", 1)[0].splitlines()
    assert head == ["import VLChallenge", "import Lean", "import Aesop"]
    assert "set_option maxHeartbeats 0" in text and "set_option Elab.async false" in text
    runs = [line for line in text.splitlines() if line.startswith("run_elab")]
    assert runs == [f'run_elab VLProbe.probe "n0nce" "{name}" #["intros; trivial", "simp"] #["propext"] 1234 true false'
                    for name in THEOREMS]
    assert lean_probes.SOURCE.read_text() in text
    with pytest.raises(ValueError):
        lean_probes.probe_file("VLChallenge", ['A" ++ x'], [], [], [], 1, "n")


def test_battery_adds_tauto_and_aesop_only_when_built_in_the_packages(tmp_path):
    assert lean_probes.battery(None, ["mathlib", "aesop"]) == (list(lean_probes.CORE_BATTERY), [])
    lib = tmp_path / "aesop" / ".lake" / "build" / "lib" / "lean"
    lib.mkdir(parents=True)
    (lib / "Aesop.olean").write_bytes(b"")
    tactics, imports = lean_probes.battery(tmp_path, ["mathlib", "aesop"])
    assert imports == ["Aesop"] and tactics == [*lean_probes.CORE_BATTERY, "intros; aesop"]
    assert lean_probes.battery(tmp_path, ["mathlib"]) == (list(lean_probes.CORE_BATTERY), [])   # not a dependency


def _line(nonce: str, **row) -> str:
    return f"VLPROBE-{nonce} {json.dumps(row)}"


def test_parse_output_reads_tagged_lines_and_turns_anything_missing_into_nulls():
    ok = {"trivial_by": "simp", "vacuous_by": None, "prop_hypotheses": 0, "vacuity_skipped": "no Prop hypothesis",
          "limit_reached": {"trivial": ["decide"], "vacuous": []}, "unavailable": []}
    stdout = "\n".join([
        "noise", _line("good", theorem="VL.A.main", **ok),
        _line("forged", theorem="VL.A.witness", trivial_by="simp", vacuous_by="simp", prop_hypotheses=1,
              limit_reached={}),                                   # wrong nonce: ignored
    ])
    results, problems = lean_probes.parse_output(stdout, "good", THEOREMS)
    assert results["VL.A.main"] == {"trivial_by": "simp", "vacuous_by": None, "prop_hypotheses": 0,
                                    "limit_reached": {"trivial": ["decide"]}, "vacuity_skipped": "no Prop hypothesis"}
    assert results["VL.A.witness"] == {"trivial_by": None, "vacuous_by": None, "prop_hypotheses": None,
                                       "error": "no probe result"}
    assert problems == ["VL.A.witness: no probe result"]


@pytest.mark.parametrize("lines, problem", [
    ([_line("n", theorem="VL.A.main", error="not found in the challenge environment")], "not found"),
    ([_line("n", theorem="VL.A.main", trivial_by=3, vacuous_by=None, prop_hypotheses=0, limit_reached={})],
     "malformed probe line"),
    ([_line("n", theorem="VL.A.main", trivial_by=None, vacuous_by=None, prop_hypotheses=0, limit_reached={})] * 2,
     "two probe lines"),
    (["VLPROBE-n {not json}"], "unreadable probe line"),
])
def test_parse_output_problems_never_flag_a_theorem(lines, problem):
    results, problems = lean_probes.parse_output("\n".join(lines), "n", ("VL.A.main",))
    assert any(problem in p for p in problems), problems
    assert results["VL.A.main"]["trivial_by"] is None and results["VL.A.main"]["vacuous_by"] is None


def test_probe_budget_is_configurable_with_floors(tmp_path):
    base = '[project]\nname = "t"\n'
    config = parse_config(tmp_path, base)
    assert (config.probe_heartbeats, config.probe_timeout) == (PROBE_HEARTBEATS, PROBE_TIMEOUT)
    config = parse_config(tmp_path, base + "[check]\nprobe_heartbeats = 5000\nprobe_timeout = 60\n")
    assert (config.probe_heartbeats, config.probe_timeout) == (5000, 60.0)
    for bad in ("probe_heartbeats = 1", "probe_heartbeats = 2.5", "probe_timeout = 1", 'probe_timeout = "x"'):
        with pytest.raises(ConfigError):
            parse_config(tmp_path, base + f"[check]\n{bad}\n")


# On the planted fixture -------------------------------------------------------------------------------------

@pytest.mark.lean
def test_probes_are_recorded_after_a_pass(lean_env, shared_cases):
    outcome = shared_cases("control-original")
    assert outcome.verdict == "pass", explain(outcome)
    assert outcome.checked["probes"] == {"VL.DoubleEven.main": {
        "trivial_by": None, "vacuous_by": None, "prop_hypotheses": 0, "vacuity_skipped": "no Prop hypothesis"}}
    run = outcome.checked["probe_run"]
    assert run["battery"] == list(lean_probes.CORE_BATTERY) and run["imports"] == [] and run["problems"] == []
    assert run["heartbeats_per_attempt"] == PROBE_HEARTBEATS
    # The challenge was built once before Comparator (which then found it built), the probes ran last.
    phases = list(outcome.extra["phases"])
    assert phases.index("prebuild_challenge") < phases.index("comparator_start") and phases[-1] == "probes"
    assert f"lake build VLChallenge\n--- exit 0" in outcome.log


@pytest.mark.lean
def test_a_closed_decidable_target_is_trivial(lean_env, shared_cases):
    outcome = shared_cases("control-value")
    assert outcome.verdict == "pass", explain(outcome)
    assert outcome.checked["probes"]["VL.DoubleValue.main"]["trivial_by"] == "intros; trivial"


@pytest.mark.lean
def test_no_probes_after_a_failure(lean_env, shared_cases):
    outcome = shared_cases("d4-sorry")
    assert outcome.verdict == "fail", explain(outcome)
    assert "probes" not in outcome.checked


@pytest.mark.lean
def test_a_probe_crash_is_a_recorded_tool_problem_never_a_verdict(tmp_path, lean_env, monkeypatch):
    broken = tmp_path / "Broken.lean"
    broken.write_text("this is not Lean\n")
    monkeypatch.setattr(lean_probes, "SOURCE", broken)
    outcome = run_case(tmp_path, "control-original")
    assert outcome.verdict == "pass", explain(outcome)
    problems = outcome.checked["probe_run"]["problems"]
    assert problems[0].startswith("the probe run exited with 1") and "VL.DoubleEven.main: no probe result" in problems
    assert outcome.checked["probes"]["VL.DoubleEven.main"]["trivial_by"] is None
    assert probe_notes({"checked": outcome.checked})[0].startswith("probes incomplete (the probe run exited with 1")


@pytest.mark.lean
def test_a_probe_timeout_records_nulls_never_a_failure(tmp_path, lean_env, monkeypatch):
    from verifylab import config as vl_config
    monkeypatch.setattr(vl_config, "PROBE_TIMEOUT_MIN", 1.0)      # the floor would make this test wait 10 s
    original = lean_probes.probe_file

    def slow(*args, **kwargs):
        head, sep, runs = original(*args, **kwargs).partition("\nrun_elab VLProbe.probe")
        return head + "\nrun_elab IO.sleep 120000" + sep + runs
    monkeypatch.setattr(lean_probes, "probe_file", slow)
    root = make_repo(tmp_path)
    git(root, "checkout", "-q", "trusted")
    config = root / "research" / "vl.toml"
    config.write_text(config.read_text() + "\n[check]\nprobe_timeout = 2\n")
    git(root, "commit", "-qam", "short probe timeout")
    outcome = LeanComparatorAdapter().check(request(root, "double-even"))
    assert outcome.verdict == "pass", explain(outcome)
    run = outcome.checked["probe_run"]
    assert run["timeout_seconds"] == 2 and run["problems"][0].startswith("the probes timed out after 2 s")
    assert outcome.checked["probes"]["VL.DoubleEven.main"] == {
        "trivial_by": None, "vacuous_by": None, "prop_hypotheses": None, "error": "no probe result"}
    assert probe_notes({"checked": outcome.checked})[0].startswith("probes incomplete (the probes timed out")


# End to end through the command line: check, admit, show, validate ---------------------------------------------

def _vl(capsys, *args):
    from verifylab.cli import main
    rc = main(list(args))
    out = capsys.readouterr().out
    return rc, out


def _admitted_check(tmp_path, monkeypatch, capsys, name: str):
    from lean_helpers import load_case
    root = make_repo(tmp_path, load_case(name))
    git(root, "checkout", "-q", "trusted")
    monkeypatch.chdir(root)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    rc, out = _vl(capsys, "check", "double-even")
    assert rc == 0, out
    git(root, "add", "-A")
    git(root, "commit", "-qm", "admit the receipt")
    return root


@pytest.mark.lean
def test_contradictory_hypotheses_end_as_vacuous_never_verified(tmp_path, lean_env, monkeypatch, capsys):
    _admitted_check(tmp_path, monkeypatch, capsys, "s6-contradictory-hypotheses")
    rc, out = _vl(capsys, "show", "double-even", "--json")
    status = json.loads(out)["items"][0]["status"]
    assert status["label"] == "vacuous", status
    assert any("VL.DoubleEven.main: `" in n and "derives False from its hypotheses" in n for n in status["notes"])
    rc, out = _vl(capsys, "show", "double-even")
    assert "status: vacuous" in out.splitlines()[0] and "VACUOUS: `" in out
    rc, out = _vl(capsys, "validate")
    assert any(line.startswith("WARNING") and "vacuous: VL.DoubleEven.main" in line for line in out.splitlines()), out


@pytest.mark.lean
def test_a_genuine_result_with_a_witness_is_verified_without_probe_warnings(tmp_path, lean_env, monkeypatch, capsys):
    _admitted_check(tmp_path, monkeypatch, capsys, "control-hypothesis-witness")
    rc, out = _vl(capsys, "show", "double-even", "--json")
    card = json.loads(out)["items"][0]
    assert card["status"]["label"] == "verified"
    assert card["proof"]["probes"]["VL.DoublePos.main"]["prop_hypotheses"] == 1
    rc, out = _vl(capsys, "validate")
    warnings = [line for line in out.splitlines() if line.startswith("WARNING")]
    assert not any("witness" in w or "automation alone" in w or "vacuous" in w for w in warnings), warnings
    assert any("fidelity is 'not reviewed'" in w for w in warnings)       # meaning still needs a reader
