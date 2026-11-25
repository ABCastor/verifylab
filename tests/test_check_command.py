from __future__ import annotations

import json
import os
import re

import pytest

from verifylab import cli, jobs, machine
from verifylab.adapters.base import CheckOutcome
from verifylab.commands import check
from verifylab.records import receipt_problems, sha256_hex
from verifylab.repo import Repo
from verifylab.status import derive

from conftest import synthetic_receipt, commit_all, git, write_item, write_machine

LEAN_TABLE = '\n[lean]\ntarget = "research/targets/add-zero.lean"\ntheorems = ["VL.hard", "VL.easy"]\n'


class FakeAdapter:
    name = "lean-comparator"
    seen = []

    def __init__(self, verdict="pass", crash=False):
        self.verdict, self.crash = verdict, crash

    def applies(self, item):
        return bool(item.lean)

    def check(self, request):
        FakeAdapter.seen.append(request)
        if self.crash:
            raise RuntimeError("boom")
        root = request.repo.root
        lean = "Fixture/Basic.lean"
        data = synthetic_receipt(root, {"inputs": {"files": {lean: sha256_hex((root / lean).read_bytes())}}})
        data["target"]["theorems"] = request.item.lean["theorems"]
        data["checked"]["theorems"] = request.item.lean["theorems"]
        if request.assurance == "exploratory":
            data["target"]["source"] = "worktree"
        return CheckOutcome(self.verdict, [] if self.verdict == "pass" else ["mismatch"],
                            data["inputs"]["files"], data["inputs"]["trusted_files"], data["target"],
                            data["environment"], data["checked"], ["fake"], "log")


@pytest.fixture
def repo_with_lean(research_repo, monkeypatch):
    item = research_repo / "research" / "items" / "add-zero.md"
    text = item.read_text()
    head, _, body = text.partition("+++\nBody")
    item.write_text(head + LEAN_TABLE + "+++\nBody" + body)
    commit_all(research_repo)
    monkeypatch.chdir(research_repo)
    FakeAdapter.seen.clear()
    return research_repo


def _use(monkeypatch, adapter):
    monkeypatch.setattr(check, "load_adapters", lambda: [adapter])


def test_protected_pass_writes_valid_receipt_then_admission_verifies(repo_with_lean, monkeypatch, capsys):
    _use(monkeypatch, FakeAdapter())
    assert cli.main(["check", "add-zero", "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    receipt = json.loads((repo_with_lean / out["receipt"]).read_text())
    assert receipt_problems(receipt) == [] and receipt["assurance"] == "protected"
    assert (receipt["inputs"]["trusted_ref"], receipt["inputs"]["trusted_ref_source"]) == (
        "trusted", "git config vl.trustedRef")
    # full-precision times, so that two checks of the same second still have an order
    assert all(re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{6}\+00:00", receipt[k])
               for k in ("started_at", "finished_at"))
    commit_all(repo_with_lean)
    repo = Repo.open(repo_with_lean)
    items, _ = repo.load_items()
    assert derive(repo, items["add-zero"], items).label == "verified"


def test_check_passes_both_memory_caps_from_the_trusted_config(repo_with_lean, monkeypatch, capsys):
    cfg = repo_with_lean / "research" / "vl.toml"
    trusted = cfg.read_text() + '\n[check]\nmemory_max = "6G"\n'
    cfg.write_text(trusted)
    commit_all(repo_with_lean, "caps")
    write_machine(check={"memory_total": "20G"})          # the cap of all runs together is the machine's
    _use(monkeypatch, FakeAdapter())
    assert cli.main(["check", "add-zero"]) == 0
    assert (FakeAdapter.seen[-1].memory_max, FakeAdapter.seen[-1].memory_total) == ("6G", "20G")
    cfg.write_text(trusted.replace('memory_max = "6G"', 'memory_max = ""'))     # a worktree edit changes nothing
    assert cli.main(["check", "add-zero", "--explore"]) == 0
    assert FakeAdapter.seen[-1].memory_max == "6G"
    assert "differs from the one on the trusted ref" in capsys.readouterr().err


def test_candidate_cannot_drop_a_theorem_from_the_question(repo_with_lean, monkeypatch, capsys):
    _use(monkeypatch, FakeAdapter())
    git(repo_with_lean, "checkout", "-q", "-b", "lane/x")
    item = repo_with_lean / "research" / "items" / "add-zero.md"
    item.write_text(item.read_text().replace('["VL.hard", "VL.easy"]', '["VL.easy"]'))
    assert cli.main(["check", "add-zero"]) == 0
    assert FakeAdapter.seen[-1].item.lean["theorems"] == ["VL.hard", "VL.easy"]
    assert cli.main(["check", "add-zero", "--explore"]) == 0
    assert FakeAdapter.seen[-1].item.lean["theorems"] == ["VL.easy"]


def test_item_not_on_trusted_ref_is_unsupported(repo_with_lean, monkeypatch, capsys):
    _use(monkeypatch, FakeAdapter())
    git(repo_with_lean, "checkout", "-q", "-b", "lane/y")
    new = repo_with_lean / "research" / "items" / "fresh.md"
    new.write_text((repo_with_lean / "research" / "items" / "add-zero.md").read_text().replace('"add-zero"', '"fresh"'))
    assert cli.main(["check", "fresh"]) == check.EXIT_TOOL
    assert "not on the trusted ref" in capsys.readouterr().out
    assert not (repo_with_lean / "research" / "evidence" / "fresh").exists()


def test_adapter_crash_is_a_tool_failure_not_a_verdict(repo_with_lean, monkeypatch, capsys):
    _use(monkeypatch, FakeAdapter(crash=True))
    assert cli.main(["check", "add-zero"]) == check.EXIT_TOOL
    assert "adapter crashed" in capsys.readouterr().out


def test_fail_verdict_exit_code(repo_with_lean, monkeypatch, capsys):
    _use(monkeypatch, FakeAdapter(verdict="fail"))
    assert cli.main(["check", "add-zero"]) == check.EXIT_FAIL


def test_disabled_guards_never_leave_a_receipt(repo_with_lean, monkeypatch, capsys):
    _use(monkeypatch, FakeAdapter())
    args = cli.build_parser().parse_args(["check", "add-zero"])
    assert check.run(args, guards=frozenset({"trusted_target"})) == check.EXIT_TOOL
    assert not (repo_with_lean / "research" / "evidence").exists()


def test_exploratory_receipt_stays_out_of_the_records(repo_with_lean, monkeypatch, capsys):
    _use(monkeypatch, FakeAdapter())
    assert cli.main(["check", "add-zero", "--explore", "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["receipt"].startswith(".vl-cache/explore/add-zero/")
    assert not (repo_with_lean / "research" / "evidence").exists()
    repo = Repo.open(repo_with_lean)
    items, _ = repo.load_items()
    assert derive(repo, items["add-zero"], items).label == "explored"


def _label(root):
    repo = Repo.open(root)
    items, _ = repo.load_items()
    return derive(repo, items["add-zero"], items)


QUESTION_EDITS = {
    "target": ('target = "research/targets/add-zero.lean"', 'target = "research/targets/easier.lean"'),
    "theorems": ('["VL.hard", "VL.easy"]', '["VL.easy"]'),
    "witnesses": ('theorems = ["VL.hard", "VL.easy"]', 'theorems = ["VL.hard", "VL.easy"]\nwitnesses = ["VL.easy"]'),
    "proofs": ('theorems = ["VL.hard", "VL.easy"]',
               'theorems = ["VL.hard", "VL.easy"]\nproofs = { "VL.hard" = "trivial", "VL.easy" = "trivial" }'),
    "solution": ('theorems = ["VL.hard", "VL.easy"]', 'theorems = ["VL.hard", "VL.easy"]\nsolution = "Fixture.Basic"'),
}


@pytest.mark.parametrize("field", sorted(QUESTION_EDITS))
def test_changing_the_question_after_a_check_stales_its_receipt(repo_with_lean, monkeypatch, capsys, field):
    _use(monkeypatch, FakeAdapter())
    assert cli.main(["check", "add-zero", "--json"]) == 0
    receipt = json.loads((repo_with_lean / json.loads(capsys.readouterr().out)["receipt"]).read_text())
    assert receipt["question_digest"].startswith("sha256:")
    commit_all(repo_with_lean, "admit")
    assert _label(repo_with_lean).label == "verified"
    item = repo_with_lean / "research" / "items" / "add-zero.md"
    item.write_text(item.read_text().replace('limits = ["Only natural numbers."]', 'limits = ["Only Nat."]'))
    commit_all(repo_with_lean, "prose only")
    assert _label(repo_with_lean).label == "verified"           # prose, limits and relations are not the question
    old, new = QUESTION_EDITS[field]
    item.write_text(item.read_text().replace(old, new))
    commit_all(repo_with_lean, f"change {field}")
    status = _label(repo_with_lean)
    assert status.label == "verified-stale", status
    assert any("research/items/add-zero.md#question" in n for n in status.notes), status.notes


class FakePythonAdapter(FakeAdapter):
    name = "python-eval"

    def applies(self, item):
        return bool(item.python)

    def check(self, request):
        root = request.repo.root
        lean = "Fixture/Basic.lean"
        evaluator = "Fixture/Evaluator.py"
        digest = sha256_hex((root / evaluator).read_bytes())
        return CheckOutcome("pass", [], {lean: sha256_hex((root / lean).read_bytes())}, {evaluator: digest},
                            {"evaluator": evaluator, "sha256": digest, "candidate": lean, "entry": "solve",
                             "source": "trusted-commit", "commit": request.trusted_commit, "cases_sha256": "e" * 64},
                            {"interpreter": "/usr/bin/python3", "python_version": "Python fixture",
                             "isolation": {"candidate": "jail", "judge": "jail"}},
                            {"cases": 1, "passed": 1, "failed": 0, "judge_errors": 0, "cases_sha256": "e" * 64},
                            ["fake"], "log")


PYTHON_TABLE = ('\n[python]\nevaluator = "research/evaluators/e.py"\ncandidate = "experiments/solve.py"\n'
                'entry = "solve"\nfiles = ["experiments/helpers.py"]\n')


@pytest.mark.parametrize("old, new", [
    ('evaluator = "research/evaluators/e.py"', 'evaluator = "research/evaluators/lenient.py"'),
    ('candidate = "experiments/solve.py"', 'candidate = "experiments/other.py"'),
    ('entry = "solve"', 'entry = "solve2"'),
    ('files = ["experiments/helpers.py"]', 'files = []'),
])
def test_changing_a_python_binding_after_a_check_stales_its_receipt(research_repo, monkeypatch, capsys, old, new):
    item = research_repo / "research" / "items" / "add-zero.md"
    head, _, body = item.read_text().partition("+++\nBody")
    item.write_text(head + PYTHON_TABLE + "+++\nBody" + body)
    commit_all(research_repo, "python item")
    monkeypatch.chdir(research_repo)
    _use(monkeypatch, FakePythonAdapter())
    assert cli.main(["check", "add-zero"]) == 0
    commit_all(research_repo, "admit")
    assert _label(research_repo).label == "verified"
    item.write_text(item.read_text().replace(old, new))
    commit_all(research_repo, "rebind")
    assert _label(research_repo).label == "verified-stale"


def test_without_a_systemd_user_manager_a_check_runs_uncapped_warns_and_records_it(repo_with_lean, monkeypatch, capsys):
    from verifylab import jail
    _use(monkeypatch, FakeAdapter())
    monkeypatch.setattr(jail, "_scope_problem", lambda systemd_run: "no systemd user manager: Failed to connect to bus")
    monkeypatch.setattr(jail, "program", lambda name: f"/usr/bin/{name}")
    assert cli.main(["check", "add-zero", "--json"]) == 0
    captured = capsys.readouterr()
    assert "warning: this check runs without a memory cap (no systemd user manager" in captured.err
    receipt = json.loads((repo_with_lean / json.loads(captured.out)["receipt"]).read_text())
    assert receipt["environment"]["memory_cap"] == "none"
    assert "Failed to connect to bus" in receipt["environment"]["memory_cap_why"]


def test_a_capped_check_records_its_limits(repo_with_lean, monkeypatch, capsys):
    from verifylab import jail
    _use(monkeypatch, FakeAdapter())
    monkeypatch.setattr(jail, "_scope_problem", lambda systemd_run: None)
    monkeypatch.setattr(jail, "program", lambda name: f"/usr/bin/{name}")
    capped = []
    monkeypatch.setattr(jail, "cap_job", lambda token, threads, memory_max: capped.append((threads, memory_max)))
    assert cli.main(["check", "add-zero", "--json"]) == 0
    assert capped == [(machine.load().threads(), "16G")]       # the check's runs together: its CPUs and memory_max
    captured = capsys.readouterr()
    assert "memory cap" not in captured.err
    receipt = json.loads((repo_with_lean / json.loads(captured.out)["receipt"]).read_text())
    assert receipt["environment"]["memory_cap"] == {"memory_max": "16G", "memory_total": machine.load().total_memory_cap(),
                                                    "slice": "vl.slice", "tasks_max": 4096}


class RecordingPythonAdapter(FakePythonAdapter):
    def check(self, request):
        FakeAdapter.seen.append(request)
        return super().check(request)


def test_a_python_item_is_checked_with_the_trusted_evaluator_binding(research_repo, monkeypatch, capsys):
    """Through the command line: a worktree item that repoints its evaluator is checked, in protected mode, with the
    evaluator the trusted item names; a [python] table the trusted item lacks brings no evaluator at all."""
    item = research_repo / "research" / "items" / "add-zero.md"
    head, _, body = item.read_text().partition("+++\nBody")
    item.write_text(head + PYTHON_TABLE + "+++\nBody" + body)
    commit_all(research_repo, "python item")
    monkeypatch.chdir(research_repo)
    FakeAdapter.seen.clear()
    _use(monkeypatch, RecordingPythonAdapter())
    item.write_text(item.read_text().replace("research/evaluators/e.py", "research/evaluators/lenient.py"))
    assert cli.main(["check", "add-zero"]) == 0
    assert FakeAdapter.seen[-1].item.python["evaluator"] == "research/evaluators/e.py"
    assert cli.main(["check", "add-zero", "--explore"]) == 0
    assert FakeAdapter.seen[-1].item.python["evaluator"] == "research/evaluators/lenient.py"

    git(research_repo, "checkout", "-q", "-b", "lane/new")
    write_item(research_repo, "fresh-python")
    fresh = research_repo / "research" / "items" / "fresh-python.md"
    commit_all(research_repo, "a python-less item")
    git(research_repo, "checkout", "-q", "trusted")
    git(research_repo, "merge", "-q", "--ff-only", "lane/new")
    head, _, body = fresh.read_text().partition("+++\nBody")
    fresh.write_text(head + PYTHON_TABLE + "+++\nBody" + body)          # the candidate adds the binding itself
    assert cli.main(["check", "fresh-python"]) == 0
    assert "evaluator" not in FakeAdapter.seen[-1].item.python


def test_the_checkers_are_imported_not_looked_up_by_name():
    """A checker that fails to import is an error of vl itself, never a silently missing checker ("no checker")."""
    assert all(isinstance(adapter, type) for adapter in check.ADAPTERS)
    assert [a.name for a in check.load_adapters()] == ["lean-comparator", "python-eval"]


# The machine's job queue ----------------------------------------------------------------------------------------

LEAN = FakeAdapter.name


class PeakAdapter(FakeAdapter):
    def __init__(self, peak):
        super().__init__()
        self.peak = peak

    def check(self, request):
        outcome = super().check(request)
        outcome.extra["memory_peak_bytes"] = self.peak
        return outcome


def test_a_check_waits_for_the_queue_and_its_receipt_records_the_wait(repo_with_lean, monkeypatch, capsys, tmp_path):
    from types import SimpleNamespace

    from verifylab import queue

    write_machine(queue={"slots": 1, "default_reservation": "1"})
    monkeypatch.setattr(queue, "slice_cgroup", lambda: tmp_path / "cgroup")
    _use(monkeypatch, FakeAdapter())
    clock = 0.0

    def poll(seconds):
        nonlocal clock
        state = queue.state(queue.Queue.from_machine())
        assert state.waiting == 1
        assert [job.label for job in state.running] == ["another job"]
        busy.release()
        clock += seconds

    monkeypatch.setattr(queue, "time", SimpleNamespace(monotonic=lambda: clock, time=lambda: clock, sleep=poll))
    with queue.admit("another job") as busy:
        assert cli.main(["check", "add-zero", "--json"]) == 0
    captured = capsys.readouterr()
    assert "vl: queue: vl check add-zero waits: all 1 slots are busy" in captured.err
    receipt = json.loads((repo_with_lean / json.loads(captured.out)["receipt"]).read_text())
    assert receipt_problems(receipt) == []
    assert clock > 0 and receipt["extra"]["queue_wait_seconds"] == clock
    assert cli.main(["check", "add-zero", "--json"]) == 0
    captured = capsys.readouterr()
    assert " waits:" not in captured.err
    receipt = json.loads((repo_with_lean / json.loads(captured.out)["receipt"]).read_text())
    assert receipt["extra"]["queue_wait_seconds"] == 0


def test_a_check_that_waits_past_max_wait_never_runs(repo_with_lean, monkeypatch, capsys):
    from verifylab import queue
    write_machine(queue={"slots": 1})
    _use(monkeypatch, FakeAdapter())
    with queue.admit("another job"):
        assert cli.main(["check", "add-zero", "--max-wait", "0.2"]) == 2
    assert "did not start: all 1 slots are busy" in capsys.readouterr().err
    assert FakeAdapter.seen == [] and not (repo_with_lean / "research" / "evidence").exists()


def test_a_check_reserves_the_peak_of_the_last_admitted_or_exploratory_receipt(repo_with_lean, monkeypatch):
    from verifylab import queue
    reserved = []
    admit = queue.admit
    monkeypatch.setattr(queue, "admit", lambda label, reservation=None, max_wait=None: reserved.append(reservation)
                        or admit(label, reservation, max_wait))
    repo = lambda: Repo.open(repo_with_lean)                                       # noqa: E731
    assert check.last_peak(repo(), "add-zero", LEAN) is None
    _use(monkeypatch, PeakAdapter(3 << 30))
    assert cli.main(["check", "add-zero", "--explore"]) == 0
    assert check.last_peak(repo(), "add-zero", LEAN) == 3 << 30
    _use(monkeypatch, PeakAdapter(5 << 30))
    assert cli.main(["check", "add-zero"]) == 0
    assert check.last_peak(repo(), "add-zero", LEAN) == 3 << 30          # a protected receipt counts once admitted
    commit_all(repo_with_lean, "admit")
    assert check.last_peak(repo(), "add-zero", LEAN) == 5 << 30
    assert cli.main(["check", "add-zero", "--reserve", "1G"]) == 0
    assert reserved == [None, 3 << 30, 1 << 30]


def test_a_check_reserves_the_largest_recent_peak_else_its_checkers_largest(repo_with_lean, monkeypatch):
    repo = lambda: Repo.open(repo_with_lean)                                       # noqa: E731
    for peak in (5 << 30, 3 << 30):
        _use(monkeypatch, PeakAdapter(peak))
        assert cli.main(["check", "add-zero", "--explore"]) == 0
    assert check.last_peak(repo(), "add-zero", LEAN) == 5 << 30     # not only the newest receipt's 3G
    assert check.last_peak(repo(), "fresh", LEAN) == 5 << 30        # a first check: the largest of its checker
    assert check.last_peak(repo(), "fresh", "python-eval") is None  # control: another checker's peaks do not count
    for _ in range(jobs.RECENT):
        assert cli.main(["check", "add-zero", "--explore"]) == 0
    assert check.last_peak(repo(), "add-zero", LEAN) == 3 << 30     # control: an old peak drops out


class SliceAdapter(FakeAdapter):
    """Records the queue job its check runs in (VL_QUEUE_JOB) and whether that job's place is held."""
    runs_in: list = []

    def check(self, request):
        from verifylab import queue
        token = os.environ.get(queue.ENV)
        SliceAdapter.runs_in.append((token, bool(token) and (queue.state_root() / "jobs" / f"{token}.json").is_file()))
        return super().check(request)


def test_a_check_runs_in_the_slice_of_its_own_queue_job(repo_with_lean, monkeypatch):
    from verifylab import jail, queue
    monkeypatch.delenv(queue.ENV, raising=False)
    SliceAdapter.runs_in = []
    _use(monkeypatch, SliceAdapter())
    assert cli.main(["check", "add-zero", "--explore"]) == 0
    (token, held), = SliceAdapter.runs_in
    assert held and jail.job_slice(token) != jail.SLICE          # its capped runs go in its own slice
    assert queue.ENV not in os.environ                           # control: nothing is left behind


def test_a_check_that_runs_nothing_does_not_wait_for_the_queue(repo_with_lean, monkeypatch, capsys):
    from verifylab import queue
    write_machine(queue={"slots": 1})
    _use(monkeypatch, FakeAdapter())
    git(repo_with_lean, "checkout", "-q", "-b", "lane/y")
    new = repo_with_lean / "research" / "items" / "fresh.md"
    new.write_text((repo_with_lean / "research" / "items" / "add-zero.md").read_text().replace('"add-zero"', '"fresh"'))
    with queue.admit("another job"):
        assert cli.main(["check", "fresh", "--max-wait", "0.2"]) == check.EXIT_TOOL
        assert "not on the trusted ref" in capsys.readouterr().out
        assert cli.main(["check", "add-zero", "--max-wait", "0.2"]) == 2     # control: a check that would run waits
    assert FakeAdapter.seen == []


def test_a_peak_that_is_not_a_positive_whole_number_is_not_reserved(repo_with_lean, monkeypatch):
    for planted in (float("nan"), -(5 << 30), 0.5):
        _use(monkeypatch, PeakAdapter(planted))
        assert cli.main(["check", "add-zero", "--explore"]) == 0
        assert check.last_peak(Repo.open(repo_with_lean), "add-zero", LEAN) is None
    _use(monkeypatch, PeakAdapter(3 << 30))                       # control: a recorded peak is reserved
    assert cli.main(["check", "add-zero", "--explore"]) == 0
    assert check.last_peak(Repo.open(repo_with_lean), "add-zero", LEAN) == 3 << 30


# Selftest (`vl check --selftest`) ---------------------------------------------------------------------------------


class SelftestFakeAdapter(FakeAdapter):
    """Inspects the generated SelftestDefs/SelftestSol files and returns realistic Comparator outcomes."""

    def __init__(self, overrides: dict[str, tuple[str, list[str]]] | None = None):
        super().__init__()
        self.overrides = overrides or {}
        self.runs: list[tuple[str, str]] = []

    def check(self, request):
        root = request.repo.root
        sol = (root / "Fixture" / "SelftestSol.lean").read_text(encoding="utf-8")
        defs = (root / "Fixture" / "SelftestDefs.lean").read_text(encoding="utf-8")
        if "def selftestOk (_ : Nat) : Prop := True" in defs:
            case, outcome = "altered-definition", (
                "fail", ["declaration 'selftestOk', used by the target statement, differs between the trusted definitions and the candidate's"])
        elif "by sorry" in sol:
            case, outcome = "sorry", ("fail", ["the solution contains `sorry` (axiom 'sorryAx'), which is not a permitted axiom"])
        elif "axiom selftestCheat" in sol:
            case, outcome = "extra-axiom", ("fail", ["the solution uses axiom 'selftestCheat', which is not in permitted_axioms"])
        elif "theorem selftest_claim : True" in sol:
            case, outcome = "weakened-target", ("fail", ["the solution's statement of 'selftest_claim' differs from the target's"])
        else:
            case, outcome = "control", ("pass", ["Comparator accepted the solution: 1 theorem(s)"])
        self.runs.append((case, request.assurance))
        verdict, reasons = self.overrides.get(case, outcome)
        files = {"Fixture/SelftestSol.lean": sha256_hex(sol.encode())}
        return CheckOutcome(verdict, reasons, files, {}, {"path": "research/targets/selftest.lean"},
                            {}, {"theorems": ["selftest_claim"]}, ["fake"], "log")


def test_selftest_passes_on_real_config_and_writes_no_receipts(repo_with_lean, monkeypatch, capsys):
    cfg = repo_with_lean / "research" / "vl.toml"
    # Worktree edits to research/vl.toml are ignored: selftest reads the trusted commit.
    cfg.write_text('[project]\nname = "Fixture"\n')
    adapter = SelftestFakeAdapter()
    _use(monkeypatch, adapter)
    evidence_before = sorted((repo_with_lean / "research" / "evidence").rglob("*"))
    assert cli.main(["check", "--selftest", "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is True
    assert [c["case"] for c in out["cases"]] == ["control", "sorry", "extra-axiom", "weakened-target", "altered-definition"]
    assert all(c["ok"] for c in out["cases"])
    assert all(assurance == "protected" for _, assurance in adapter.runs)
    assert sorted((repo_with_lean / "research" / "evidence").rglob("*")) == evidence_before


def test_selftest_exit_codes_for_slipped_plant_wrong_reason_and_tool_error(repo_with_lean, monkeypatch, capsys):
    _use(monkeypatch, SelftestFakeAdapter({"sorry": ("pass", ["slipped through"])}))
    assert cli.main(["check", "--selftest", "--json"]) == check.EXIT_FAIL
    assert json.loads(capsys.readouterr().out)["ok"] is False

    _use(monkeypatch, SelftestFakeAdapter({"sorry": ("fail", ["unexpected syntax failure"])}))
    assert cli.main(["check", "--selftest", "--json"]) == check.EXIT_FAIL
    assert json.loads(capsys.readouterr().out)["ok"] is False

    _use(monkeypatch, SelftestFakeAdapter({"control": ("unsupported", ["nanoda missing"])}))
    assert cli.main(["check", "--selftest", "--json"]) == check.EXIT_TOOL
    assert json.loads(capsys.readouterr().out)["ok"] is False


def test_selftest_refuses_explore_and_missing_lean_roots(repo_with_lean, monkeypatch, capsys):
    _use(monkeypatch, SelftestFakeAdapter())
    assert cli.main(["check", "--selftest", "--explore"]) == 2
    assert "--selftest" in capsys.readouterr().err
    cfg = repo_with_lean / "research" / "vl.toml"
    cfg.write_text('[project]\nname = "Fixture"\n')
    commit_all(repo_with_lean, "drop lean table")
    assert cli.main(["check", "--selftest"]) == 2
    assert "roots" in capsys.readouterr().err


@pytest.mark.lean
def test_selftest_runs_real_lean_comparator_against_project_config(tmp_path, monkeypatch, capsys):
    import lean_helpers
    skip = lean_helpers.lean_unavailable()
    if skip:
        pytest.skip(skip)
    for k, v in lean_helpers.tool_env().items():
        monkeypatch.setenv(k, v)
    root = lean_helpers.make_repo(tmp_path, lean_helpers.load_case("control-original"))
    monkeypatch.chdir(root)
    assert cli.main(["check", "--selftest", "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is True
    assert {c["case"]: c["verdict"] for c in out["cases"]} == {
        "control": "pass",
        "sorry": "fail",
        "extra-axiom": "fail",
        "weakened-target": "fail",
        "altered-definition": "fail",
    }
