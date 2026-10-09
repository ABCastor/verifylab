"""python-eval adapter: candidate_exec regressions, planted defects, and guard mutations.

Part 1 holds the regression tests of `candidate_exec` (the 2026-07-11 incident, where a candidate rebound
an in-process scorer and scored 35/35 with wrong answers, must stay impossible).
Part 2 runs the adapter on `fixtures/python-planted/`: the fixture repo is committed on branch `trusted`,
then each planted defect is applied in the worktree and checked in protected mode.
Part 3 disables each guard in turn and shows that a planted defect then passes (the instrument can fail).
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import shutil
import socketserver
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

import pytest

from verifylab import candidate_exec, gitref, jail
from verifylab.adapters.base import CheckOutcome, CheckRequest
from verifylab.adapters.python_eval import GUARDS, PythonEvalAdapter, SpecError, parse_spec
from verifylab.candidate_exec import run_candidate_calls
from verifylab.repo import Repo

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "python-planted"
SECRET = "sk-planted-secret-4f1c9e27"
PRIMES = {1: 2, 2: 3, 3: 5, 6: 13, 25: 97, 100: 541}   # the trusted evaluator's cases and answers
needs_jail = pytest.mark.skipif(not jail.available(), reason="bwrap missing")


# ---------------------------------------------------------------------------------------------------
# Part 1 — candidate_exec regressions (plain subprocess, no jail)
# ---------------------------------------------------------------------------------------------------

_INPUTS = [("kitten", "sitting"), ("", "abc"), ("abc", "abc"), ("flaw", "lawn")]
_EXPECTED = [3, 3, 0, 2]   # live only here, as a real judge keeps its expected answers

_HONEST = """
def edit_distance(s, t):
    n, m = len(s), len(t)
    prev = list(range(m + 1))
    for i in range(1, n + 1):
        curr = [i] + [0] * m
        for j in range(1, m + 1):
            curr[j] = prev[j - 1] if s[i - 1] == t[j - 1] else 1 + min(prev[j], curr[j - 1], prev[j - 1])
        prev = curr
    return prev[m]
"""


def _write(dir_: Path, body: str) -> Path:
    dir_.mkdir(parents=True, exist_ok=True)
    (dir_ / "main.py").write_text(body, encoding="utf-8")
    return dir_


def _score(run, expected) -> int:
    return sum(1 for outcome, exp in zip(run.outcomes, expected) if outcome.ok and outcome.value == exp)


def test_honest_candidate_scores_full(tmp_path: Path) -> None:
    run = run_candidate_calls(code_dir=_write(tmp_path / "honest", _HONEST), symbol="edit_distance", inputs=_INPUTS)
    assert run.status == "ok"
    assert _score(run, _EXPECTED) == len(_EXPECTED)


def test_rebind_main_scorer_cannot_force_pass(tmp_path: Path) -> None:
    """The headline exploit: rebinding __main__._run plus a wrong function. The rebind lands in the
    disposable child; the parent sees the real (wrong) outputs."""
    body = (
        "import __main__\n"
        "__main__._run = lambda fn, cases: {'pass_rate': 1.0, 'n_passed': len(cases)}\n"
        "__main__._score = __main__._run\n"
        "def edit_distance(s, t):\n"
        "    return 999999\n"
    )
    run = run_candidate_calls(code_dir=_write(tmp_path / "rebind", body), symbol="edit_distance", inputs=_INPUTS)
    assert run.status == "ok"
    assert all(o.value == 999999 for o in run.outcomes)
    assert _score(run, _EXPECTED) == 0


def test_patch_json_dumps_contained(tmp_path: Path) -> None:
    body = (
        "import json\n"
        "json.dumps = lambda *a, **k: '{\"pass_rate\": 1.0}'\n"
        "def edit_distance(s, t):\n"
        "    return 999999\n"
    )
    run = run_candidate_calls(code_dir=_write(tmp_path / "jsonpatch", body), symbol="edit_distance", inputs=_INPUTS)
    assert run.status == "ok"
    assert _score(run, _EXPECTED) == 0


def test_overwrite_result_file_contained(tmp_path: Path) -> None:
    """A candidate writes a forged 'perfect' payload to the result path at import; the child overwrites it
    with the real outputs."""
    body = (
        "import sys, json\n"
        "try:\n"
        "    json.dump({'status': 'ok', 'error': None,\n"
        "               'outcomes': [{'ok': True, 'value': e, 'error': None}\n"
        "                            for e in (3, 3, 0, 2)]}, open(sys.argv[-1], 'w'))\n"
        "except Exception:\n"
        "    pass\n"
        "def edit_distance(s, t):\n"
        "    return 999999\n"
    )
    run = run_candidate_calls(code_dir=_write(tmp_path / "overwrite", body), symbol="edit_distance", inputs=_INPUTS)
    assert run.status == "ok"
    assert _score(run, _EXPECTED) == 0


def test_a_candidate_written_message_is_cut_and_escaped(tmp_path: Path) -> None:
    """The candidate can write the result file itself: its messages reach reasons, receipts and the terminal only
    cut to 500 characters and with control characters (newlines, escapes) shown as escapes."""
    body = ("import sys, json, os\n"
            "json.dump({'status': 'import_error', 'error': 'A' * 5000 + '\\x1b[2J\\nSTATUS: verified',\n"
            "           'outcomes': []}, open(sys.argv[-1], 'w'))\n"
            "os._exit(0)\n")
    run = run_candidate_calls(code_dir=_write(tmp_path / "loud", body), symbol="edit_distance", inputs=_INPUTS)
    assert run.status == "import_error"
    assert len(run.error) < 600 and run.error.endswith("characters cut]"), len(run.error)
    [outcome] = candidate_exec.parse_outcomes([{"ok": False, "error": "verdict: pass\r\n\x1b[32mverified\u202e"}])
    assert outcome.error == "verdict: pass\\r\\n\\x1b[32mverified\\u202e"


def test_missing_file(tmp_path: Path) -> None:
    run = run_candidate_calls(code_dir=tmp_path / "nope", symbol="edit_distance", inputs=_INPUTS)
    assert run.status == "missing_file" and run.outcomes == []


def test_import_error(tmp_path: Path) -> None:
    code = _write(tmp_path / "boom", "raise RuntimeError('boom at import')\n")
    run = run_candidate_calls(code_dir=code, symbol="edit_distance", inputs=_INPUTS)
    assert run.status == "import_error"
    assert "boom at import" in (run.error or "")


def test_missing_symbol(tmp_path: Path) -> None:
    run = run_candidate_calls(code_dir=_write(tmp_path / "nosym", "x = 1\n"), symbol="edit_distance", inputs=_INPUTS)
    assert run.status == "attr_error"


def test_per_call_exception_is_isolated(tmp_path: Path) -> None:
    code = _write(tmp_path / "raise", "def edit_distance(s, t):\n    raise ValueError('nope')\n")
    run = run_candidate_calls(code_dir=code, symbol="edit_distance", inputs=_INPUTS)
    assert run.status == "ok"
    assert all(not o.ok and "nope" in (o.error or "") for o in run.outcomes)


def test_unserializable_return_is_a_failure(tmp_path: Path) -> None:
    code = _write(tmp_path / "unser", "def edit_distance(s, t):\n    return object()\n")
    run = run_candidate_calls(code_dir=code, symbol="edit_distance", inputs=[("a", "b")])
    assert run.status == "ok"
    assert not run.outcomes[0].ok


def test_timeout_is_fail_closed(tmp_path: Path) -> None:
    code = _write(tmp_path / "slow", "import time\ndef edit_distance(s, t):\n    time.sleep(30)\n    return 0\n")
    run = run_candidate_calls(code_dir=code, symbol="edit_distance", inputs=[("a", "b")], timeout=1.0)
    assert run.status == "timeout" and run.outcomes == []


# Child-side limits and safe result reading ----------------------------------------------------------

def test_case_timeout_fires_inside_the_child(tmp_path: Path) -> None:
    body = "def edit_distance(s, t):\n    while s == 'loop':\n        pass\n    return 0\n"
    run = run_candidate_calls(code_dir=_write(tmp_path / "loop", body), symbol="edit_distance",
                              inputs=[("loop", "x"), ("a", "b")], timeout=20, case_timeout=0.3)
    assert run.status == "ok"
    assert run.outcomes[0].timeout and not run.outcomes[0].ok
    assert run.outcomes[1].ok and run.outcomes[1].value == 0


def test_hard_exit_is_a_started_runner_error(tmp_path: Path) -> None:
    body = "import os\ndef edit_distance(s, t):\n    os._exit(0)\n"
    run = run_candidate_calls(code_dir=_write(tmp_path / "exit", body), symbol="edit_distance", inputs=_INPUTS)
    assert run.status == "runner_error" and run.started


def test_runner_that_cannot_launch_is_not_started(tmp_path: Path) -> None:
    run = run_candidate_calls(code_dir=_write(tmp_path / "ok", _HONEST), symbol="edit_distance", inputs=_INPUTS,
                              interpreter="/nonexistent/python3")
    assert run.status == "runner_error" and not run.started


def test_result_file_swapped_for_a_symlink_is_not_followed(tmp_path: Path) -> None:
    secret = tmp_path / "host-secret.json"
    # A well-formed result: if the parent followed the symlink it would accept these as outcomes.
    forged = [{"ok": True, "value": e, "error": None} for e in _EXPECTED]
    secret.write_text(json.dumps({"status": "ok", "outcomes": forged, "secret": SECRET}))
    body = (
        "import atexit, os, sys\n"
        "def _swap():\n"
        "    os.remove(sys.argv[-1]); os.symlink(%r, sys.argv[-1])\n"
        "atexit.register(_swap)\n"
        "def edit_distance(s, t):\n"
        "    return 0\n" % str(secret)
    )
    run = run_candidate_calls(code_dir=_write(tmp_path / "swap", body), symbol="edit_distance", inputs=_INPUTS)
    assert run.status == "runner_error" and run.started
    assert SECRET not in (run.error or "")


@needs_jail
@pytest.mark.jail
def test_rebind_main_scorer_cannot_force_pass_in_the_jail(tmp_path: Path) -> None:
    body = "import __main__\n__main__._run = lambda *a: 1.0\ndef edit_distance(s, t):\n    return 999999\n"
    code = _write(tmp_path / "rebind", body)

    def sandbox(cmd, layout):
        box = jail.Jail(workdir=layout.work_dir, read_only=(layout.code_dir, layout.runner_dir),
                        env=jail.filter_env(os.environ, jail.DEFAULT_ENV))
        return box.argv(cmd)

    run = run_candidate_calls(code_dir=code, symbol="edit_distance", inputs=_INPUTS, run_dir=tmp_path / "run",
                              interpreter="/usr/bin/python3", sandbox=sandbox)
    assert run.status == "ok" and run.argv[0] == jail.program("bwrap")
    assert _score(run, _EXPECTED) == 0


# ---------------------------------------------------------------------------------------------------
# Part 2 — the adapter on the planted-defect fixture
# ---------------------------------------------------------------------------------------------------

def _git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, text=True).stdout


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@pytest.fixture
def planted(tmp_path: Path) -> Path:
    """The fixture repo, committed on branch `trusted`; defects are applied afterwards in the worktree."""
    root = tmp_path / "repo"
    shutil.copytree(FIXTURE / "repo", root)
    _git(root, "init", "-q", "-b", "trusted")
    _git(root, "config", "vl.trustedRef", "trusted")
    _git(root, "config", "user.name", "Test")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "trusted fixture")
    return root


class _Oracle(socketserver.BaseRequestHandler):
    def handle(self):
        self.request.sendall(json.dumps({str(k): v for k, v in PRIMES.items()}).encode())


@pytest.fixture
def oracle():
    """An answer oracle on 127.0.0.1 (local only) for the network defect."""
    server = socketserver.TCPServer(("127.0.0.1", 0), _Oracle)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server.server_address[1]
    server.shutdown()
    server.server_close()


def apply_defect(root: Path, name: str, port: int | None = None) -> None:
    source = FIXTURE / "defects" / name
    assert source.is_dir(), name
    for file in source.rglob("*"):
        if file.is_file():
            text = file.read_text(encoding="utf-8")
            if port is not None:
                text = text.replace("__ORACLE_PORT__", str(port))
            dest = root / file.relative_to(source)
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(text, encoding="utf-8")


def check(root: Path, item: str = "nth-prime", *, assurance: str = "protected", guards=(), timeout: float = 60.0,
          memory_max: str | None = None, scratch: Path | None = None, item_override=None) -> CheckOutcome:
    repo = Repo.open(root)
    loaded = repo.load_item(item)
    if item_override is not None:
        loaded = item_override(loaded)
    scratch = scratch or Path(tempfile.mkdtemp(dir=root.parent, prefix="scratch-"))
    request = CheckRequest(repo=repo, item=loaded, assurance=assurance, trusted_commit=gitref.rev_parse(root, "trusted"),
                           scratch=scratch, timeout=timeout, memory_max=memory_max, guards=frozenset(guards))
    return PythonEvalAdapter().check(request)


def _text(outcome: CheckOutcome) -> str:
    return "\n".join(outcome.reasons)


# Genuine controls --------------------------------------------------------------------------------------

@needs_jail
@pytest.mark.jail
def test_genuine_candidate_passes_with_full_provenance(planted: Path) -> None:
    outcome = check(planted)
    assert outcome.verdict == "pass", outcome.reasons
    assert outcome.reasons[0] == "all 6 cases passed"
    assert outcome.files == {
        "experiments/primes/solve.py": _sha((planted / "experiments/primes/solve.py").read_bytes()),
        "experiments/primes/helpers.py": _sha((planted / "experiments/primes/helpers.py").read_bytes()),
    }
    trusted = gitref.show(planted, "trusted", "research/evaluators/primes.py")
    assert outcome.trusted_files == {"research/evaluators/primes.py": _sha(trusted)}
    assert outcome.target["source"] == "trusted-commit" and outcome.target["entry"] == "solve"
    assert outcome.checked["cases"] == 6 and outcome.checked["passed"] == 6
    assert [r["status"] for r in outcome.checked["results"]] == ["pass"] * 6
    assert outcome.environment["interpreter"] == "/usr/bin/python3"
    assert outcome.environment["python_version"]
    assert "evaluator not mounted" in outcome.environment["isolation"]["candidate"]
    assert outcome.environment["guards_disabled"] == []
    from verifylab.commands.check import build_receipt
    from verifylab.records import receipt_problems
    repo = Repo.open(planted)
    item = repo.load_item("nth-prime")
    receipt = build_receipt(repo, item, item, "python-eval", "protected", repo.trusted_commit, outcome, "t0", "t1")
    assert receipt_problems(json.loads(json.dumps(receipt))) == []


@needs_jail
@pytest.mark.jail
def test_second_genuine_control_passes(planted: Path) -> None:
    outcome = check(planted, "sorted-list")
    assert outcome.verdict == "pass", outcome.reasons


@needs_jail
@pytest.mark.jail
@pytest.mark.skipif(shutil.which("systemd-run") is None, reason="systemd-run missing")
def test_genuine_candidate_passes_under_the_memory_cap(planted: Path) -> None:
    outcome = check(planted, memory_max="512M")
    assert outcome.verdict == "pass", outcome.reasons
    assert f"== candidate argv ==\n{jail.program('systemd-run')} --user --scope" in outcome.log
    assert "MemoryMax=512M" in outcome.log and "--slice=vl.slice" in outcome.log


@needs_jail
@pytest.mark.jail
def test_wrong_candidate_fails_naming_the_cases(planted: Path) -> None:
    apply_defect(planted, "wrong")
    outcome = check(planted)
    assert outcome.verdict == "fail"
    assert outcome.checked["passed"] == 2
    assert outcome.reasons[0] == "4 of 6 case(s) did not pass"
    assert "case 2 (input 3): solve(3) returned 4, expected 5" in outcome.reasons


# Planted defects in protected mode -----------------------------------------------------------------------

@needs_jail
@pytest.mark.jail
def test_a_judge_takeover_from_the_candidate_has_no_effect(planted: Path) -> None:
    apply_defect(planted, "a_rebind_judge")
    outcome = check(planted)
    assert outcome.verdict == "fail"
    assert outcome.checked["passed"] == 0
    assert "case 0 (input 1): solve(1) returned 4, expected 2" in outcome.reasons


@needs_jail
@pytest.mark.jail
def test_b_forged_verdict_and_result_files_are_ignored(planted: Path, tmp_path: Path) -> None:
    apply_defect(planted, "b_fake_verdict")
    scratch = tmp_path / "scratch-b"
    scratch.mkdir()
    outcome = check(planted, scratch=scratch)
    assert outcome.verdict == "fail"
    assert "solve(1) returned 4, expected 2" in _text(outcome)
    assert "defect-b: forged" in outcome.log                               # the attempt really happened
    assert (scratch / "candidate" / "work" / "verdict.json").is_file()    # inside its own work dir: ignored
    assert not (scratch / "judge" / "work" / "verdict.json").exists()     # the judge's dir is out of reach


@needs_jail
@pytest.mark.jail
def test_c_evaluator_is_not_visible_inside_the_candidate_jail(planted: Path) -> None:
    apply_defect(planted, "c_read_evaluator")
    outcome = check(planted)
    assert outcome.verdict == "fail"
    assert "defect-c: evaluator not visible" in outcome.log
    assert "solve(1) returned 4, expected 2" in _text(outcome)


def test_c_candidate_cannot_mount_the_evaluator_as_one_of_its_files(planted: Path) -> None:
    def add_evaluator(item):
        return dataclasses.replace(item, python={**item.python, "files": ["research/evaluators/primes.py"]})

    outcome = check(planted, item_override=add_evaluator)
    assert outcome.verdict == "error"
    assert "lies under research/evaluators/" in _text(outcome)


@needs_jail
@pytest.mark.jail
def test_d_edited_worktree_evaluator_is_ignored_in_protected_mode(planted: Path) -> None:
    apply_defect(planted, "d_edit_evaluator")
    outcome = check(planted)
    assert outcome.verdict == "fail"
    assert "solve(1) returned 4, expected 2" in _text(outcome)
    trusted = gitref.show(planted, "trusted", "research/evaluators/primes.py")
    assert outcome.trusted_files == {"research/evaluators/primes.py": _sha(trusted)}
    assert "research/evaluators/primes.py" not in outcome.files


@needs_jail
@pytest.mark.jail
def test_d_edited_evaluator_passes_in_exploratory_mode_which_is_why_exploratory_receipts_never_verify(
        planted: Path) -> None:
    apply_defect(planted, "d_edit_evaluator")
    outcome = check(planted, assurance="exploratory")
    assert outcome.verdict == "pass"
    assert outcome.trusted_files == {}
    edited = (planted / "research/evaluators/primes.py").read_bytes()
    assert outcome.files["research/evaluators/primes.py"] == _sha(edited)   # recorded as candidate-side
    assert outcome.target["source"] == "worktree"
    assert "can never make the item verified" in _text(outcome)


@needs_jail
@pytest.mark.jail
def test_e_infinite_loop_times_out_per_case_and_fails(planted: Path) -> None:
    apply_defect(planted, "e_infinite_loop")
    outcome = check(planted)
    assert outcome.verdict == "fail"
    assert [r["status"] for r in outcome.checked["results"]] == ["timeout"] * 6
    assert "case 0 (input 1): timed out after 0.5s" in outcome.reasons


@needs_jail
@pytest.mark.jail
def test_e_candidate_that_disarms_its_timer_fails_at_the_evaluators_own_bound(planted: Path) -> None:
    """6 cases x TIMEOUT_S 0.5 s + 5 s: the evaluator's bound, inside the check's 60 s, kills it: its failure."""
    apply_defect(planted, "e_defeat_timer")
    outcome = check(planted, timeout=60.0)
    assert outcome.verdict == "fail", outcome.reasons
    assert "exceeded its time budget of 8.0s" in _text(outcome)
    assert outcome.checked["candidate_status"] == "timeout"


@needs_jail
@pytest.mark.jail
def test_e_a_check_stopped_by_its_overall_timeout_is_a_tool_error_never_a_fail(planted: Path) -> None:
    """The check's --timeout (3 s) is shorter than the evaluator's bound (8 s): an honest slow candidate would be cut
    off too, so the outcome is inconclusive (exit 3), never the candidate's failure."""
    apply_defect(planted, "e_defeat_timer")
    outcome = check(planted, timeout=3.0)
    assert outcome.verdict == "error", outcome.reasons
    assert "the check's overall timeout (3s) stopped the candidate" in _text(outcome)
    assert "before the evaluator's own bound of 8.0s" in _text(outcome)


@needs_jail
@pytest.mark.jail
def test_a_judge_process_that_exits_non_zero_does_not_count(planted: Path) -> None:
    """The judge wrote passing verdicts, then its process failed (here an atexit hook of the evaluator): its
    result is not trusted, the check is an error."""
    evaluator = planted / "research" / "evaluators" / "primes.py"
    evaluator.write_text(evaluator.read_text() + "\nimport atexit, os\natexit.register(lambda: os._exit(3))\n")
    _git(planted, "commit", "-qam", "an evaluator whose process fails after writing its verdicts")
    outcome = check(planted)
    assert outcome.verdict == "error", outcome.reasons
    assert "exited with 3 after writing its result, which therefore does not count" in _text(outcome)


@needs_jail
@pytest.mark.jail
@pytest.mark.parametrize("defect, reason", [
    ("f_import_crash", "failed to import: RuntimeError: planted crash at import"),
    ("f_unserializable", "unserializable return: set"),
    ("f_hard_exit", "candidate process crashed or corrupted its result channel"),
])
def test_f_crash_or_non_json_output_fails(planted: Path, defect: str, reason: str) -> None:
    apply_defect(planted, defect)
    outcome = check(planted)
    assert outcome.verdict == "fail"
    assert reason in _text(outcome)


@needs_jail
@pytest.mark.jail
def test_g_network_is_blocked_in_the_jail(planted: Path, oracle: int) -> None:
    apply_defect(planted, "g_network", port=oracle)
    outcome = check(planted)
    assert outcome.verdict == "fail"
    assert "defect-g: network blocked" in outcome.log
    assert "solve(1) returned 4, expected 2" in _text(outcome)


@needs_jail
@pytest.mark.jail
def test_h_secret_env_var_is_absent_in_the_jail(planted: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", SECRET)
    monkeypatch.setenv("PLANTED_SERVICE_TOKEN", SECRET)
    apply_defect(planted, "h_secret_env")
    outcome = check(planted)
    assert outcome.verdict == "fail"
    assert "solve(1) returned 'absent', expected 2" in _text(outcome)
    assert "defect-h: secrets seen: []" in outcome.log
    assert SECRET not in outcome.log and SECRET not in json.dumps(dataclasses.asdict(outcome))


@needs_jail
@pytest.mark.jail
def test_i_worktree_item_repointing_its_evaluator_is_refused(planted: Path) -> None:
    apply_defect(planted, "i_repoint_evaluator")
    outcome = check(planted)
    assert outcome.verdict == "error"
    assert "evaluator binding changed" in _text(outcome)


# ---------------------------------------------------------------------------------------------------
# Part 3 — guard mutations: each guard disabled lets a planted defect pass
# ---------------------------------------------------------------------------------------------------

GUARD_DEFECTS = {
    "trusted_evaluator": "d_edit_evaluator",
    "separate_judge": "a_rebind_judge",
    "candidate_jail": "g_network",
    "evaluator_binding": "i_repoint_evaluator",
}


def test_every_guard_has_a_mutation_test() -> None:
    assert set(GUARD_DEFECTS) == set(GUARDS)


@needs_jail
@pytest.mark.jail
@pytest.mark.parametrize("guard", sorted(GUARD_DEFECTS))
def test_disabling_the_guard_lets_its_defect_pass(planted: Path, oracle: int, guard: str) -> None:
    apply_defect(planted, GUARD_DEFECTS[guard], port=oracle)
    guarded = check(planted)
    assert guarded.verdict in ("fail", "error"), guarded.reasons
    mutated = check(planted, guards=[guard])
    assert mutated.verdict == "pass", mutated.reasons
    assert mutated.environment["guards_disabled"] == [guard]
    assert any("TEST ONLY" in r for r in mutated.reasons)


@needs_jail
@pytest.mark.jail
def test_without_a_separate_judge_the_evaluator_is_also_readable(planted: Path) -> None:
    apply_defect(planted, "c_read_evaluator")
    assert check(planted).verdict == "fail"
    outcome = check(planted, guards=["separate_judge"])
    assert outcome.verdict == "pass"


@needs_jail
@pytest.mark.jail
def test_without_the_candidate_jail_secrets_and_the_evaluator_leak(planted: Path,
                                                                   monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", SECRET)
    apply_defect(planted, "h_secret_env")
    assert SECRET in check(planted, guards=["candidate_jail"]).log
    apply_defect(planted, "c_read_evaluator")
    assert check(planted, guards=["candidate_jail"]).verdict == "pass"


# ---------------------------------------------------------------------------------------------------
# Part 4 — bad items and tool problems are error/unsupported, never fail and never pass
# ---------------------------------------------------------------------------------------------------

def test_applies_only_to_items_with_a_python_table(planted: Path) -> None:
    item = Repo.open(planted).load_item("nth-prime")
    adapter = PythonEvalAdapter()
    assert adapter.name == "python-eval"
    assert adapter.applies(item)
    assert not adapter.applies(dataclasses.replace(item, python={}))


def test_item_without_python_table_is_unsupported(planted: Path) -> None:
    outcome = check(planted, item_override=lambda item: dataclasses.replace(item, python={}))
    assert outcome.verdict == "unsupported"


@pytest.mark.parametrize("change, message", [
    ({"evaluator": None}, "needs 'evaluator'"),
    ({"entry": "not an identifier"}, "entry must be a Python identifier"),
    ({"candidate": "../outside.py"}, "plain repo-relative path"),
    ({"candidate": "/etc/passwd"}, "plain repo-relative path"),
    ({"evaluator": "experiments/primes/solve.py"}, "evaluator must be a .py file under research/evaluators/"),
    ({"candidate": "research/evaluators/primes.py"}, "lies under research/evaluators/"),
    ({"files": "experiments/primes/helpers.py"}, "files must be a list"),
    ({"extra": 1}, "unknown key"),
])
def test_bad_python_table_is_an_error(planted: Path, change: dict, message: str) -> None:
    def edit(item):
        table = {**item.python, **change}
        return dataclasses.replace(item, python={k: v for k, v in table.items() if v is not None})

    outcome = check(planted, item_override=edit)
    assert outcome.verdict == "error"
    assert message in _text(outcome)


def test_parse_spec_dedupes_files() -> None:
    spec = parse_spec({"evaluator": "research/evaluators/e.py", "candidate": "x/a.py", "entry": "f",
                       "files": ["x/b.py", "x/a.py", "x/b.py"]}, "research/evaluators")
    assert spec.candidate_files == ("x/a.py", "x/b.py")
    with pytest.raises(SpecError):
        parse_spec({"evaluator": "research/evaluators/e.py", "candidate": "x//a.py", "entry": "f"},
                   "research/evaluators")


@pytest.mark.parametrize("timeout", [0, -1.0, None, "60"])
def test_bad_timeout_is_an_error(planted: Path, timeout) -> None:
    outcome = check(planted, timeout=timeout)
    assert outcome.verdict == "error" and "timeout must be a positive number" in _text(outcome)


def test_unknown_guard_is_an_error(planted: Path) -> None:
    outcome = check(planted, guards=["trusted_evaluatr"])
    assert outcome.verdict == "error" and "unknown guard" in _text(outcome)


def test_missing_bwrap_is_unsupported(planted: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(jail, "available", lambda: False)
    outcome = check(planted)
    assert outcome.verdict == "unsupported" and "bwrap" in _text(outcome)


@needs_jail
@pytest.mark.jail
def test_missing_candidate_file_is_an_error(planted: Path) -> None:
    (planted / "experiments/primes/helpers.py").unlink()
    outcome = check(planted)
    assert outcome.verdict == "error" and "missing in the worktree" in _text(outcome)


@needs_jail
@pytest.mark.jail
def test_symlinked_candidate_file_is_refused(planted: Path, tmp_path: Path) -> None:
    secret = tmp_path / "host-secret.txt"
    secret.write_text(SECRET)
    helpers = planted / "experiments/primes/helpers.py"
    helpers.unlink()
    helpers.symlink_to(secret)
    outcome = check(planted)
    assert outcome.verdict == "error" and "symlink" in _text(outcome)


def _add_item(root: Path, item_id: str, evaluator_source: str | None, commit: bool) -> None:
    text = (FIXTURE / "repo" / "research/items/nth-prime.md").read_text()
    text = text.replace('id = "nth-prime"', f'id = "{item_id}"')
    text = text.replace("research/evaluators/primes.py", f"research/evaluators/{item_id.replace('-', '_')}.py")
    (root / "research/items" / f"{item_id}.md").write_text(text)
    if evaluator_source is not None:
        (root / "research/evaluators" / f"{item_id.replace('-', '_')}.py").write_text(evaluator_source)
    if commit:
        _git(root, "add", "-A")
        _git(root, "commit", "-q", "-m", f"add {item_id}")


@needs_jail
@pytest.mark.jail
def test_evaluator_absent_from_the_trusted_commit_is_an_error(planted: Path) -> None:
    source = (FIXTURE / "repo/research/evaluators/primes.py").read_text()
    _add_item(planted, "fresh", source, commit=False)
    outcome = check(planted, "fresh")
    assert outcome.verdict == "error"
    assert "does not exist at the trusted commit" in _text(outcome)
    assert "not on the trusted commit yet" in _text(outcome)


_JUDGE_OK = "def judge(case, output):\n    return output == {1: 2, 2: 3}.get(case)\n"


@needs_jail
@pytest.mark.jail
@pytest.mark.parametrize("source, message", [
    ("raise RuntimeError('evaluator bug')\n", "the evaluator failed to import"),
    ("CASES = 3\n" + _JUDGE_OK, "the evaluator must define CASES as a list"),
    ("CASES = []\n" + _JUDGE_OK, "defines no CASES"),
    ("CASES = [1, 2]\n", "must define judge(case, output)"),
    ("CASES = [1, 2]\nTIMEOUT_S = -1\n" + _JUDGE_OK, "TIMEOUT_S must be a positive number"),
    ("CASES = [1, 2]\ndef judge(case, output):\n    raise KeyError('judge bug')\n", "the evaluator raised: KeyError"),
    ("CASES = [1, 2]\ndef judge(case, output):\n    return 1\n", "judge must return bool or (bool, str)"),
    ("import os\nCASES = [1, 2, os.urandom(8).hex()]\n" + _JUDGE_OK, "not deterministic"),
])
def test_broken_evaluator_is_an_error_not_a_fail(planted: Path, source: str, message: str) -> None:
    _add_item(planted, "broken", source, commit=True)
    outcome = check(planted, "broken")
    assert outcome.verdict == "error", outcome.reasons
    assert message in _text(outcome)


# Strict numbers and symlinked directories ----------------------------------------------------------------------

_TOLERANCE_EVALUATOR = """CASES = [1.0, 2.0, 3.0]
TIMEOUT_S = 2


def judge(case, output):
    return not (abs(output - 2 * case) > 1e-9)     # a common tolerance idiom: NaN compares False with everything
"""

_FORGE_AT_EXIT = """import atexit
import sys


def _forge():
    outcome = '{{"ok": true, "value": {value}, "error": null}}'
    with open(sys.argv[2], "w") as handle:
        handle.write('{{"status": "ok", "error": null, "outcomes": [' + ", ".join([outcome] * 3) + ']}}')


atexit.register(_forge)


def solve(x):
    return 0.0
"""


@needs_jail
@pytest.mark.jail
@pytest.mark.parametrize("value", ["NaN", "-Infinity"])
def test_candidate_nan_and_infinity_never_reach_the_judge(planted: Path, value: str) -> None:
    """The child rejects non-finite returns, but the candidate can rewrite the result file at exit: the judge runner
    parses the outputs strictly, so NaN never meets an evaluator whose tolerance test it would slip through."""
    _add_item(planted, "tolerance", _TOLERANCE_EVALUATOR, commit=True)
    (planted / "experiments/primes/solve.py").write_text(_FORGE_AT_EXIT.format(value=value))
    outcome = check(planted, "tolerance")
    assert outcome.verdict == "fail", outcome.reasons
    assert [r["status"] for r in outcome.checked["results"]] == ["fail"] * 3
    assert f"{value}, which strict JSON forbids" in _text(outcome)


@needs_jail
@pytest.mark.jail
def test_c_candidate_cannot_reach_the_evaluators_through_a_symlinked_directory(planted: Path) -> None:
    (planted / "experiments/primes/ev").symlink_to("../../research/evaluators")

    def through_link(item):
        files = [*item.python["files"], "experiments/primes/ev/primes.py"]
        return dataclasses.replace(item, python={**item.python, "files": files})

    outcome = check(planted, item_override=through_link)
    assert outcome.verdict == "error", outcome.reasons
    assert "experiments/primes/ev is a symlink" in _text(outcome)
