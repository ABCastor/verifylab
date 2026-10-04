from __future__ import annotations

import json
import re

import pytest

from verifylab import cli, machine
from verifylab.adapters.base import CheckOutcome
from verifylab.commands import check
from verifylab.records import receipt_problems, sha256_hex
from verifylab.repo import Repo
from verifylab.status import derive

from conftest import commit_all, git, write_item, write_machine

LEAN_TABLE = '\n[lean]\ntarget = "research/targets/add-zero.lean"\ntheorems = ["VL.hard", "VL.easy"]\n'


class FakeAdapter:
    name = "fake"
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
        return CheckOutcome(self.verdict, [] if self.verdict == "pass" else ["mismatch"],
                            {lean: sha256_hex((root / lean).read_bytes())}, {}, {"theorems": request.item.lean["theorems"]},
                            {}, {}, ["fake"], "log")


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
    def applies(self, item):
        return bool(item.python)

    def check(self, request):
        root = request.repo.root
        lean = "Fixture/Basic.lean"
        return CheckOutcome("pass", [], {lean: sha256_hex((root / lean).read_bytes())}, {}, {"kind": "python"},
                            {}, {}, ["fake"], "log")


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
    assert cli.main(["check", "add-zero", "--json"]) == 0
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
