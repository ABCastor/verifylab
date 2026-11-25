"""Statement probes (triviality, vacuity) run beside Comparator on a copy of the challenge built before any candidate
code and are recorded only after a pass: what they record, and that a crash or a timeout of the probe run never changes the verdict."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest

from conftest import lean_elan_home
from lean_helpers import TOOLCHAIN, explain, git, lean_unavailable, make_repo, request, run_case, tool_env
from verifylab.adapters import lean_probes
from verifylab.adapters.lean_comparator import LeanComparatorAdapter, toolchain_dir
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
    named = "#[" + ", ".join(f'"{c}"' for c in lean_probes.TOTALIZED_CONSTANTS) + "]"
    assert runs == [*(f'run_elab VLProbe.probe "n0nce" "{name}" #["intros; trivial", "simp"] #["propext"] {named} #[] '
                      f'1234 {lean_probes.TOTALIZED_CAP} true false true' for name in THEOREMS),
                    *(f'run_elab VLProbe.boundary "n0nce" "{name}" #["intros; trivial", "simp"] #["propext"] #[0, 1, 2] '
                      f'1234 {lean_probes.BOUNDARY_BINDERS}' for name in THEOREMS)]
    assert lean_probes.SOURCE.read_text() in text
    off = lean_probes.probe_file("VLChallenge", THEOREMS, ["simp"], [], [], 1, "n", project=["Fixture", "VLChallenge"],
                                 scan=False, boundary_binders=0)
    runs = [line for line in off.splitlines() if line.startswith("run_elab")]
    assert len(runs) == 2 and all(r.endswith(' #["Fixture", "VLChallenge"] 1 40 true true false') for r in runs), runs
    with pytest.raises(ValueError):
        lean_probes.probe_file("VLChallenge", ['A" ++ x'], [], [], [], 1, "n")


def test_every_kind_the_metaprogram_reports_is_explained():
    """The totalized kinds `structural?` returns are exactly those vl explains; a constant is explained by its name."""
    kinds = set(re.findall(r'return some "([a-z_]+)"', lean_probes.SOURCE.read_text()))
    assert kinds == set(lean_probes.TOTALIZED_KINDS)
    assert lean_probes.why("Real.sqrt") == lean_probes.TOTALIZED_CONSTANTS["Real.sqrt"]
    assert lean_probes.why("nat_sub").startswith("subtraction in ℕ truncates")


def test_every_foundations_kind_the_metaprogram_reports_is_explained():
    """The kinds `foundation?` returns are exactly those vl explains (the other `some "…"` of the source are not in a
    match arm)."""
    kinds = set(re.findall(r'=> some "([a-z_]+)"|then some "([a-z_]+)"', lean_probes.SOURCE.read_text()))
    assert {k for pair in kinds for k in pair if k} == set(lean_probes.FOUNDATIONS_KINDS)


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


def test_parse_output_reads_definitions_totalized_operations_and_boundary_values():
    main = {"trivial_by": None, "vacuous_by": None, "prop_hypotheses": 1, "vacuity_skipped": None,
            "limit_reached": {"trivial": [], "vacuous": [], "totalized": ["scan"]}, "unavailable": [],
            "definitions": ["Fixture.gap"], "totalized": [{"op": "nat_sub", "term": "a - b" * 100, "in": "Fixture.gap"}]}
    bound = {"phase": "boundary", "limit_reached": ["n = 1: simp"],
             "boundary": [{"binder": "n", "value": 0, "trivial_by": None, "vacuous_by": "intros; omega"}]}
    stdout = "\n".join([_line("n", theorem="VL.A.main", **main), _line("n", theorem="VL.A.main", **bound),
                        _line("n", theorem="VL.A.witness", **{**main, "definitions": [], "totalized": []})])
    results, problems = lean_probes.parse_output(stdout, "n", THEOREMS)
    assert problems == []
    entry = results["VL.A.main"]
    assert entry["definitions"] == ["Fixture.gap"] and entry["boundary"] == [
        {"binder": "n", "value": 0, "vacuous_by": "intros; omega"}]
    assert entry["totalized"] == [{"op": "nat_sub", "term": ("a - b" * 100)[:lean_probes.TERM_CHARS], "in": "Fixture.gap",
                                   "why": lean_probes.TOTALIZED_KINDS["nat_sub"]}]
    assert entry["limit_reached"] == {"totalized": ["scan"], "boundary": ["n = 1: simp"]}
    # No boundary line (the run ended first): no `boundary`, and no problem; the main probes stand.
    assert "boundary" not in results["VL.A.witness"] and results["VL.A.witness"]["totalized"] == []


def test_parse_output_reads_foundations_and_explains_each_kind():
    row = {"trivial_by": None, "vacuous_by": None, "prop_hypotheses": 0, "limit_reached": {}, "definitions": [],
           "totalized": [], "foundations": [{"kind": "cardinal", "const": "Cardinal.mk", "in": "statement"}]}
    results, problems = lean_probes.parse_output(_line("n", theorem="VL.A.main", **row), "n", ("VL.A.main",))
    assert problems == []
    assert results["VL.A.main"]["foundations"] == [{"kind": "cardinal", "const": "Cardinal.mk", "in": "statement",
                                                    "why": lean_probes.FOUNDATIONS_KINDS["cardinal"]}]
    # A row from a scan that listed none, or without the key at all (a receipt from before), reports none.
    results, _ = lean_probes.parse_output(_line("n", theorem="VL.A.main", **{**row, "foundations": []}), "n",
                                          ("VL.A.main",))
    assert results["VL.A.main"]["foundations"] == []
    row.pop("foundations")
    results, _ = lean_probes.parse_output(_line("n", theorem="VL.A.main", **row), "n", ("VL.A.main",))
    assert "foundations" not in results["VL.A.main"]


@pytest.mark.parametrize("found", [
    "Cardinal.mk",
    [{"kind": "nonsense", "const": "Cardinal.mk", "in": "statement"}],
    [{"kind": "cardinal", "const": 3, "in": "statement"}],
])
def test_malformed_foundations_are_a_problem_never_a_finding(found):
    line = _line("n", theorem="VL.A.main", trivial_by=None, vacuous_by=None, prop_hypotheses=0, limit_reached={},
                 definitions=[], totalized=[], foundations=found)
    results, problems = lean_probes.parse_output(line, "n", ("VL.A.main",))
    assert any("malformed probe line" in p for p in problems) and "foundations" not in results["VL.A.main"]


@pytest.mark.parametrize("row", [
    {"definitions": "Fixture.gap", "totalized": []},
    {"definitions": [], "totalized": [{"op": "nat_sub", "term": 3, "in": "statement"}]},
    {"definitions": []},
])
def test_a_malformed_scan_is_a_problem_never_a_finding(row):
    line = _line("n", theorem="VL.A.main", trivial_by=None, vacuous_by=None, prop_hypotheses=0, limit_reached={}, **row)
    results, problems = lean_probes.parse_output(line, "n", ("VL.A.main",))
    assert any("malformed probe line" in p for p in problems) and "totalized" not in results["VL.A.main"]


@pytest.mark.parametrize("hit", [{"binder": "n", "value": "0", "vacuous_by": "simp"},
                                 {"binder": "n", "value": 0, "trivial_by": None, "vacuous_by": None}])
def test_a_malformed_boundary_line_is_a_problem(hit):
    lines = [_line("n", theorem="VL.A.main", trivial_by=None, vacuous_by=None, prop_hypotheses=0, limit_reached={}),
             _line("n", theorem="VL.A.main", phase="boundary", boundary=[hit], limit_reached=[])]
    results, problems = lean_probes.parse_output("\n".join(lines), "n", ("VL.A.main",))
    assert any("malformed boundary probe line" in p for p in problems) and "boundary" not in results["VL.A.main"]


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


@pytest.mark.parametrize("main_lines, problem", [(True, None), (False, "the probes timed out after 30 s")])
def test_a_timeout_among_the_boundary_values_is_not_an_incomplete_probe_run(tmp_path, monkeypatch, main_lines, problem):
    """The boundary values run after every theorem's own probes: a run that times out there leaves them unprobed
    (`boundary_unfinished`), never "probes incomplete". The control: a timeout before a theorem's line is a problem."""
    from types import SimpleNamespace
    from verifylab import jail
    from verifylab.adapters import lean_comparator
    row = {"trivial_by": None, "vacuous_by": None, "prop_hypotheses": 0, "limit_reached": {}, "definitions": [],
           "totalized": []}

    def stream(box, command, timeout, **kw):
        nonce = re.search(r'VLProbe.probe "([0-9a-f]+)"', (tmp_path / "VLProbe.lean").read_text()).group(1)
        out = _line(nonce, theorem="main", **row) if main_lines else ""
        return SimpleNamespace(returncode=None, stdout=out.encode(), stderr=b"", seconds=30.0, service=None,
                               memory_peak=None)
    monkeypatch.setattr(jail, "stream", stream)
    check = lean_comparator._Check(SimpleNamespace(repo=SimpleNamespace(root=tmp_path), assurance="protected",
                                                   guards=(), timeout=60.0, memory_max=None, memory_total=None,
                                                   threads=None))
    config = SimpleNamespace(probe_timeout=30.0, probe_heartbeats=1000,
                             lean=SimpleNamespace(permitted_axioms=[], roots=["Fixture"]))
    ran = check.run_probes(None, tmp_path, ("main",), config, None, [], None)
    assert ran["probe_run"]["boundary_unfinished"] == ["main"]
    assert '#["Fixture", "VLChallenge", "VLTrusted"]' in (tmp_path / "VLProbe.lean").read_text()
    problems = ran["probe_run"]["problems"]
    notes = probe_notes({"checked": {"probe_run": ran["probe_run"], "probes": ran["probes"]}})
    if problem is None:
        assert problems == [] and notes == [] and "boundary" not in ran["probes"]["main"]
    else:
        assert problems[0].startswith(problem) and notes
    assert ran["unfinished"] is (problem is not None)          # only a run that left a theorem unprobed is redone


def test_probes_that_did_not_finish_beside_comparator_run_again_alone_after_a_pass(tmp_path):
    """The probes share the check's CPUs and memory with Comparator and give way there: a run that did not finish
    (here killed by the memory cap the two share) is done again alone, and the receipt records that run and why. A run
    that finished is kept (the control), and one that failed alone is not repeated."""
    from concurrent.futures import Future
    from types import SimpleNamespace
    from verifylab.adapters import lean_comparator

    def done(value) -> Future:
        future = Future()
        future.set_result(value)
        return future

    killed = {"probe_run": {"problems": ["the probe run did not end normally: systemd ended the service with result "
                                         "'oom-kill'"]}, "seconds": 3.0, "log": "first", "unfinished": True}
    whole = {"probe_run": {"problems": []}, "probes": {"main": {"vacuous_by": "simp"}}, "seconds": 5.0, "log": "alone",
             "unfinished": False}
    def copy(ran: dict) -> dict:
        return {**ran, "probe_run": dict(ran["probe_run"])}

    for first, can_rerun, recorded in ((killed, True, whole), (whole, True, whole), (killed, False, killed)):
        check = lean_comparator._Check(SimpleNamespace(repo=SimpleNamespace(root=tmp_path), assurance="protected",
                                                       guards=(), timeout=60.0, memory_max=None, memory_total=None,
                                                       threads=None))
        check.extra["phases"] = {}
        calls = []
        check.record_probes(done(copy(first)), (lambda: calls.append(1) or copy(whole)) if can_rerun else None)
        assert check.checked["probe_run"]["problems"] == recorded["probe_run"]["problems"]
        assert check.checked.get("probes") == recorded.get("probes")
        rerun = first is killed and can_rerun
        assert len(calls) == rerun
        assert ("rerun_alone" in check.checked["probe_run"]) is rerun
        if rerun:
            assert "oom-kill" in check.checked["probe_run"]["rerun_alone"]
            assert check.extra["phases"] == {"probes": 3.0, "probes_alone": 5.0} and check.log == ["first", "alone"]


def test_the_probes_give_way_to_the_check_they_share_memory_with(tmp_path, monkeypatch):
    """A run that yields raises its own oom_score_adj, so the kernel kills it first when the memory of the check's
    slice runs out; Comparator, which decides the verdict, keeps running."""
    from verifylab import jail
    monkeypatch.setattr(jail.Jail, "argv", lambda self, command: list(command))
    monkeypatch.setattr(jail, "memory_capped", lambda argv, *args, **kwargs: list(argv))
    read = ["/bin/sh", "-c", "cat /proc/self/oom_score_adj"]
    assert jail.stream(jail.Jail(workdir=tmp_path, yields=True), read, timeout=30).stdout == b"1000\n"
    assert jail.stream(jail.Jail(workdir=tmp_path), read, timeout=30).stdout != b"1000\n"      # control


def test_probe_budget_is_configurable_with_floors(tmp_path):
    base = '[project]\nname = "t"\n'
    config = parse_config(tmp_path, base)
    assert (config.probe_heartbeats, config.probe_timeout) == (PROBE_HEARTBEATS, PROBE_TIMEOUT)
    config = parse_config(tmp_path, base + "[check]\nprobe_heartbeats = 5000\nprobe_timeout = 60\n")
    assert (config.probe_heartbeats, config.probe_timeout) == (5000, 60.0)
    for bad in ("probe_heartbeats = 1", "probe_heartbeats = 2.5", "probe_timeout = 1", 'probe_timeout = "x"'):
        with pytest.raises(ConfigError):
            parse_config(tmp_path, base + f"[check]\n{bad}\n")


# The metaprogram alone, on fixtures/lean-probes: planted cases and clean controls ----------------------------

PROBED = Path(__file__).resolve().parents[1] / "fixtures" / "lean-probes"
PROBED_THEOREMS = tuple(f"T.{n}" for n in re.findall(r"^theorem (\w+)", (PROBED / "VLChallenge.lean").read_text(), re.M))
TOTALIZED = {
    "T.natSub": [("nat_sub", "n - 1", "statement")],
    "T.natDiv": [("nat_div", "n / 2", "statement")],
    "T.natMod": [("nat_mod", "a % b", "statement")],
    "T.intDiv": [("int_div", "a / b", "statement"), ("int_mod", "a % b", "statement")],
    "T.floatDiv": [("div_zero", "x / y", "statement")],
    "T.cast": [("cast_truncated", "↑(a - b)", "statement"), ("nat_sub", "a - b", "statement")],
    "T.toNat": [("Int.toNat", "z.toNat", "statement")],
    "T.viaDef": [("nat_sub", "a - b", "Probed.gap")],
    "T.viaField": [("nat_sub", "n - 1", "Probed.Pos")],       # `T.struct`, over `Probed.Box`, is the control
    "T.fndPick": [("Classical.choice", "Classical.choice Probed.pick._proof_1", "Probed.pick")],   # a tool, not foundations (see FOUNDATIONS)
}
FOUNDATIONS = {                  # (kind, constant, where); the controls, with names that only contain the words, are clean
    "T.fndZFSet": [("zfset", "ZFSet", "statement")],
    "T.fndPSet": [("zfset", "PSet", "statement")],
    "T.fndCardinal": [("cardinal", "Cardinal", "statement")],
    "T.fndOrdinal": [("ordinal", "Ordinal", "statement")],
    "T.fndChoice": [("choice", "Probed.AC", "statement")],
    "T.fndTheory": [("theory", "Probed.ZFC", "statement")],
    "T.fndConsistent": [("independence", "Probed.Consistent", "statement")],
    "T.fndViaDef": [("choice", "Probed.AC", "Probed.needsChoice")],
    "T.fndViaField": [("cardinal", "Cardinal", "Probed.Counted")],
}
DEFINITIONS = {"T.viaDef": ["Probed.gap"], "T.viaClean": ["Probed.total"], "T.struct": ["Probed.Box", "Probed.area"],
               "T.viaShared": ["Probed.shared"], "T.viaField": ["Probed.Pos"],
               "T.fndZFSet": ["ZFSet"], "T.fndPSet": ["PSet"], "T.fndCardinal": ["Cardinal"],
               "T.fndOrdinal": ["Ordinal"], "T.fndChoice": ["Probed.AC"], "T.fndTheory": ["Probed.ZFC"],
               "T.fndConsistent": ["Probed.Consistent"], "T.fndViaDef": ["Probed.needsChoice", "Probed.AC"], "T.fndPick": ["Probed.pick"],
               "T.fndViaField": ["Probed.Counted", "Cardinal"],
               "T.fndLookalike": ["Probed.Choiceless", "Probed.Cardinality"]}
BOUNDARY = {
    "T.natSub": [{"binder": "n", "value": 0, "vacuous_by": "intros; trivial"}],
    "T.natMod": [{"binder": "a", "value": 0, "trivial_by": "simp"}],
    "T.viaClean": [{"binder": "a", "value": 0, "trivial_by": "simp"}],
    "T.bndTriv": [{"binder": "n", "value": 0, "trivial_by": "simp"}, {"binder": "m", "value": 0, "trivial_by": "simp"}],
    "T.bndVac": [{"binder": "n", "value": 0, "vacuous_by": "intros; trivial"},
                 {"binder": "n", "value": 1, "vacuous_by": "intros; trivial"}],
}


@pytest.fixture(scope="module")
def probed(tmp_path_factory):
    """Run the probe file on fixtures/lean-probes with the fixture's toolchain, the new probes on and off: (results
    on, results off, seconds on, seconds off). Lean alone, no Comparator: seconds."""
    lean = toolchain_dir(TOOLCHAIN, elan_home=Path(lean_elan_home())) / "bin" / "lean"
    if not lean.is_file():
        pytest.skip(f"toolchain {TOOLCHAIN} not installed")
    folder = tmp_path_factory.mktemp("probed")
    import shutil
    import time
    shutil.copytree(PROBED, folder, dirs_exist_ok=True)
    env = {"LEAN_PATH": str(folder), "PATH": "/usr/bin:/bin", "HOME": str(folder)}
    for module in ("Probed/Defs", "VLChallenge"):
        subprocess.run([str(lean), "-o", f"{module}.olean", f"{module}.lean"], cwd=folder, env=env, check=True,
                       capture_output=True)
    out = []
    for flags in ({}, {"scan": False, "boundary_binders": 0}):
        (folder / "VLProbe.lean").write_text(lean_probes.probe_file(
            "VLChallenge", PROBED_THEOREMS, list(lean_probes.CORE_BATTERY), [], ["propext", "Quot.sound"],
            PROBE_HEARTBEATS, "n", project=["Probed", "VLChallenge"], **flags))
        start = time.monotonic()
        run = subprocess.run([str(lean), "VLProbe.lean"], cwd=folder, env=env, capture_output=True, text=True,
                             timeout=300)
        results, problems = lean_probes.parse_output(run.stdout, "n", PROBED_THEOREMS)
        assert run.returncode == 0 and problems == [], (problems, run.stdout[-2000:], run.stderr[-2000:])
        out += [results, time.monotonic() - start]
    return out[0], out[2], out[1], out[3]


@pytest.mark.lean
def test_totalized_operations_are_found_in_the_statement_and_its_definitions(probed):
    found = {name: [(x["op"], x["term"], x["in"]) for x in entry["totalized"]] for name, entry in probed[0].items()}
    assert found == {name: TOTALIZED.get(name, []) for name in PROBED_THEOREMS}      # every other theorem is clean
    assert all(x["why"] == lean_probes.why(x["op"]) for e in probed[0].values() for x in e["totalized"])


@pytest.mark.lean
def test_set_theoretic_foundations_are_found_by_name_in_the_statement_and_its_definitions(probed):
    found = {name: [(x["kind"], x["const"], x["in"]) for x in entry["foundations"]] for name, entry in probed[0].items()}
    assert found == {name: FOUNDATIONS.get(name, []) for name in PROBED_THEOREMS}     # every other theorem is clean
    assert all(x["why"] == lean_probes.FOUNDATIONS_KINDS[x["kind"]] for e in probed[0].values() for x in e["foundations"])


@pytest.mark.lean
def test_the_scan_stops_at_its_heartbeat_limit_on_a_definition_with_shared_subterms(probed):
    """`Probed.shared` has 2^60 paths: the walk of its value must end at the heartbeat limit (an unbounded one never
    finishes, and the probe run with it), keep the other probes of the theorem, and say it stopped. The control: the
    scan of every other theorem completes."""
    entry = probed[0]["T.viaShared"]
    assert entry["limit_reached"] == {"totalized": ["scan"]} and entry["totalized"] == []
    assert entry["definitions"] == ["Probed.shared"] and entry["trivial_by"] is not None
    assert not any("totalized" in e.get("limit_reached", {}) for n, e in probed[0].items() if n != "T.viaShared")


@pytest.mark.lean
def test_in_project_definitions_a_statement_depends_on_are_listed(probed):
    assert {name: entry["definitions"] for name, entry in probed[0].items()} == {
        name: DEFINITIONS.get(name, []) for name in PROBED_THEOREMS}


@pytest.mark.lean
def test_boundary_values_name_where_a_statement_degenerates(probed):
    assert {name: entry["boundary"] for name, entry in probed[0].items()} == {
        name: BOUNDARY.get(name, []) for name in PROBED_THEOREMS}


@pytest.mark.lean
def test_switched_off_the_planted_cases_go_unreported_and_the_other_probes_are_unchanged(probed):
    on, off = probed[0], probed[1]
    assert not any({"totalized", "definitions", "boundary", "foundations"} & set(entry) for entry in off.values())
    keys = ("trivial_by", "vacuous_by", "prop_hypotheses")
    assert {n: [e.get(k) for k in keys] for n, e in on.items()} == {n: [e.get(k) for k in keys] for n, e in off.items()}


@pytest.mark.lean
def test_the_cost_of_the_new_probes_is_bounded(probed):
    """Measured, not tuned: the boundary values try at most BOUNDARY_BINDERS × len(BOUNDARY_VALUES) instances of
    each theorem under the same per-attempt heartbeats; on this fixture they stay within a few seconds."""
    seconds_on, seconds_off = probed[2], probed[3]
    assert seconds_on < seconds_off + 60, (seconds_on, seconds_off)


# On the planted fixture -------------------------------------------------------------------------------------

@pytest.mark.lean
def test_probes_are_recorded_after_a_pass(lean_env, shared_cases):
    outcome = shared_cases("control-original")
    assert outcome.verdict == "pass", explain(outcome)
    assert outcome.checked["probes"] == {"VL.DoubleEven.main": {
        "trivial_by": None, "vacuous_by": None, "prop_hypotheses": 0, "vacuity_skipped": "no Prop hypothesis",
        "definitions": ["Fixture.double"], "totalized": [], "foundations": [], "boundary": []}}
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
    # What the reader must answer: the definition the statement rests on, and n = 0, where it says nothing.
    assert any("definition adequacy" in w and "Fixture.double" in w for w in warnings), warnings
    assert any("boundary values: VL.DoublePos.main: at n = 0" in w and "refutes its hypotheses" in w
               for w in warnings), warnings
    rc, out = _vl(capsys, "show", "double-even")
    assert "definition adequacy (Comparator checks" in out and "- not named by any fidelity review" in out
