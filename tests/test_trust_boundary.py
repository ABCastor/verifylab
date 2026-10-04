"""The trust boundary, planted: `vl` never derives `verified` from what a worktree edit, an environment variable or
a failed read can change. Each test plants the attack next to a clean control that shows what the guard protects.
"""

from __future__ import annotations

import json
import os
import re
import shutil
from pathlib import Path

import pytest

from verifylab.cli import main
from verifylab.gitref import GitError
from verifylab.records import seal_receipt, seal_review, sha256_hex, write_new_json
from verifylab.render import Context
from verifylab.repo import Repo

from conftest import synthetic_receipt, commit_all, git, item_question, write_item

LEAN = "Fixture/Basic.lean"


def receipt(root: Path, item_id: str = "add-zero", verdict: str = "pass", finished: str = "2026-10-01T10:00:00+00:00",
            checked: dict | None = None, **extra) -> Path:
    exists = (root / "research" / "items" / f"{item_id}.md").is_file()
    data = synthetic_receipt(root, dict(
        item=item_id, item_revision="a" * 40,
        question_digest=item_question(root, item_id) if exists else "sha256:" + "0" * 64,
        adapter="lean-comparator", assurance="protected", verdict=verdict,
        reasons=[] if verdict == "pass" else [f"{verdict} on purpose"],
        inputs={"digest": "d", "files": {LEAN: sha256_hex((root / LEAN).read_bytes())}},
        environment={}, checked=checked or {}, command=["vl"], started_at=finished, finished_at=finished,
        tool_version="vl 0.1.0", **extra))
    path = root / "research" / "evidence" / item_id / f"{data['receipt_id'][:16]}.json"
    write_new_json(path, data)
    return path


def retraction(root: Path, item_id: str = "add-zero") -> Path:
    data = seal_review(dict(item=item_id, item_revision="a" * 40, kind="retraction", author="human:integrator",
                            text="the target was wrong", created="2026-10-01T12:00:00+00:00"))
    path = root / "research" / "reviews" / item_id / f"{data['review_id'][:16]}.json"
    write_new_json(path, data)
    return path


def status(root: Path, item_id: str = "add-zero", **kwargs):
    repo = Repo.open(root, **kwargs)
    ctx = Context.load(repo)
    return ctx.status(item_id)


# A1: admitted evidence is enumerated from the trusted commit ------------------------------------------------------

def test_deleting_an_admitted_fail_in_the_worktree_does_not_restore_the_pass(research_repo):
    root = research_repo
    receipt(root, finished="2026-10-01T10:00:00+00:00")
    commit_all(root, "admit the pass")
    assert status(root).label == "verified"                       # control
    failed = receipt(root, verdict="fail", finished="2026-10-01T11:00:00+00:00")
    commit_all(root, "admit a newer fail")
    assert status(root).label == "failed"
    failed.unlink()                                                # a worktree edit, never committed
    assert status(root).label == "failed"


def test_an_admitted_retraction_counts_in_a_worktree_that_lacks_it(research_repo):
    root = research_repo
    receipt(root)
    commit_all(root, "admit the pass")
    before = git(root, "rev-parse", "HEAD").strip()
    retraction(root)
    commit_all(root, "admit a retraction")
    assert status(root).label == "retracted"
    git(root, "checkout", "-q", "-b", "lane/old", before)          # a lane opened before the retraction
    assert not (root / "research" / "reviews").exists()
    assert status(root).label == "retracted"


def test_a_worktree_copy_that_differs_from_the_admitted_record_is_rejected(research_repo):
    root = research_repo
    path = receipt(root)
    commit_all(root, "admit the pass")
    path.write_text(path.read_text().replace('"pass"', '"fail"'))
    records = Repo.open(root).receipts("add-zero")
    assert [r.admitted for r in records] == [True, False]
    assert "records are immutable" in records[1].problems[0]
    assert status(root).label == "verified"


# A12: relations change a status only once they are on the trusted ref ---------------------------------------------

def test_a_refutation_only_the_worktree_states_changes_no_status(research_repo):
    root = research_repo
    write_item(root, "counter")
    receipt(root)
    receipt(root, "counter")
    commit_all(root, "two verified results")
    assert status(root).label == "verified"
    write_item(root, "counter", refutes=["add-zero"])               # proposed in the worktree
    st = status(root)
    assert st.label == "verified" and any("worktree only" in n for n in st.notes)
    commit_all(root, "integrate the refutation")
    assert status(root).label == "refuted"                          # control: once admitted it counts


def test_an_answer_only_the_worktree_states_answers_nothing(research_repo):
    root = research_repo
    question = root / "research" / "items" / "qq.md"
    question.write_text('+++\nid = "qq"\nkind = "question"\ntitle = "Q"\nauthor = "human:x"\ncreated = "2026-10-01"\n'
                        'statement = "Is n + 0 = n?"\n+++\n')
    receipt(root)
    commit_all(root, "a question and a verified result")
    item = root / "research" / "items" / "add-zero.md"
    item.write_text(item.read_text().replace("limits =", 'answers = ["qq"]\nlimits ='))
    assert status(root, "qq").label == "open"
    commit_all(root, "integrate the answer")
    assert status(root, "qq").label == "answered"


def test_a_pinned_refutation_applies_only_to_the_pinned_revision(research_repo):
    root = research_repo
    receipt(root, "add-zero")
    write_item(root, "counter")
    receipt(root, "counter")
    commit_all(root, "two verified results")
    old = git(root, "rev-parse", "HEAD:research/items/add-zero.md").strip()
    write_item(root, "counter", refutes=[f"add-zero@{old[:12]}"])
    commit_all(root, "refute this revision")
    assert status(root).label == "refuted"
    item = root / "research" / "items" / "add-zero.md"
    item.write_text(item.read_text().replace("Body text.", "Body text, reworded."))
    commit_all(root, "a new revision of the item")
    st = status(root)
    assert st.label == "verified" and any(f"revision {old[:12]}" in n for n in st.notes)


# CHEATS R22: an answer answers the question only with the question's own target and theorems ---------------------

def _formal_question(root: Path) -> None:
    """A question with a Lean target of two theorems, both target files on disk."""
    targets = root / "research" / "targets"
    targets.mkdir(parents=True, exist_ok=True)
    (targets / "q.lean").write_text("theorem main : 1 + 1 = 2 := sorry\ntheorem side : 2 + 2 = 4 := sorry\n")
    (targets / "easy.lean").write_text("theorem main : True := sorry\n")
    (root / "research" / "items" / "qq.md").write_text(
        '+++\nid = "qq"\nkind = "question"\ntitle = "Q"\nauthor = "human:x"\ncreated = "2026-10-01"\n'
        'statement = "Is 1 + 1 = 2, and 2 + 2 = 4?"\n[lean]\ntarget = "research/targets/q.lean"\n'
        'theorems = ["main", "side"]\n+++\n')


def _answer(root: Path, item_id: str, target: str, theorems: list[str]) -> None:
    write_item(root, item_id)
    item = root / "research" / "items" / f"{item_id}.md"
    proofs = ", ".join(f'{t} = "rfl"' for t in theorems)
    item.write_text(item.read_text().replace("limits =", 'answers = ["qq"]\nlimits =').replace(
        "+++\nBody", f'[lean]\ntarget = "{target}"\ntheorems = {theorems}\nproofs = {{ {proofs} }}\n+++\nBody'
        .replace("'", '"')))
    receipt(root, item_id)


@pytest.mark.parametrize("target, theorems", [("research/targets/easy.lean", ["main"]),
                                              ("research/targets/q.lean", ["main"])],
                         ids=["an easier target", "a subset of the theorems"])
def test_an_answer_with_another_target_or_theorems_does_not_answer(research_repo, monkeypatch, capsys, target,
                                                                    theorems):
    root = research_repo
    _formal_question(root)
    _answer(root, "elsewhere", target, theorems)
    commit_all(root, "a verified result that answers the question with another question")
    assert status(root, "elsewhere").label == "verified"
    st = status(root, "qq")
    assert st.label == "open" and any("elsewhere" in n and "same target and theorems" in n for n in st.notes), st.notes
    monkeypatch.chdir(root)
    assert main(["validate"]) == 1
    assert "answers 'qq'" in capsys.readouterr().out
    from verifylab import status as status_module
    monkeypatch.setattr(status_module, "answer_mismatch", lambda question, answer: None)   # the guard, disabled
    assert status(root, "qq").label == "answered"


def test_an_answer_with_the_same_target_and_theorems_answers(research_repo, monkeypatch, capsys):
    root = research_repo
    _formal_question(root)
    _answer(root, "proved", "research/targets/q.lean", ["side", "main"])      # the same theorems, in another order
    commit_all(root, "a verified answer to the question asked")
    assert status(root, "qq").label == "answered"
    monkeypatch.chdir(root)
    main(["validate"])
    assert "answers 'qq'" not in capsys.readouterr().out


def test_incoming_validation_flags_an_answer_to_another_question(research_repo, monkeypatch, capsys):
    root = research_repo
    _formal_question(root)
    commit_all(root, "the question")
    git(root, "checkout", "-q", "-b", "lane/easy")
    _answer(root, "elsewhere", "research/targets/easy.lean", ["main"])
    shutil.rmtree(root / "research" / "evidence" / "elsewhere")                    # lanes bring no receipts
    commit_all(root, "an answer with an easier target")
    git(root, "checkout", "-q", "trusted")
    monkeypatch.chdir(root)
    assert main(["validate", "--incoming", "lane/easy"]) == 1
    out = capsys.readouterr().out
    assert "on lane/easy: answers 'qq'" in out and "same target and theorems" in out


def _python_question(root: Path) -> None:
    """A question asking for a candidate the evaluator `primes.py` judges through the entry `solve`."""
    evaluators = root / "research" / "evaluators"
    evaluators.mkdir(parents=True, exist_ok=True)
    (evaluators / "primes.py").write_text("CASES = [1, 2]\ndef judge(case, output):\n    return output == [2, 3][case - 1]\n")
    (evaluators / "lenient.py").write_text("CASES = [1]\ndef judge(case, output):\n    return True\n")
    (root / "research" / "items" / "qq.md").write_text(
        '+++\nid = "qq"\nkind = "question"\ntitle = "Q"\nauthor = "human:x"\ncreated = "2026-10-01"\n'
        'statement = "Which function returns the n-th prime?"\n[python]\nevaluator = "research/evaluators/primes.py"\n'
        'entry = "solve"\n+++\n')


def _python_answer(root: Path, item_id: str, evaluator: str, entry: str) -> None:
    write_item(root, item_id)
    item = root / "research" / "items" / f"{item_id}.md"
    item.write_text(item.read_text().replace("limits =", 'answers = ["qq"]\nlimits =').replace(
        "+++\nBody", f'[python]\nevaluator = "{evaluator}"\ncandidate = "experiments/{item_id}.py"\n'
                     f'entry = "{entry}"\n+++\nBody'))
    receipt(root, item_id)


@pytest.mark.parametrize("evaluator, entry", [("research/evaluators/lenient.py", "solve"),
                                              ("research/evaluators/primes.py", "solve_small")],
                         ids=["a lenient evaluator", "another entry"])
def test_an_answer_with_another_evaluator_or_entry_does_not_answer(research_repo, monkeypatch, capsys, evaluator,
                                                                    entry):
    root = research_repo
    _python_question(root)
    _python_answer(root, "elsewhere", evaluator, entry)
    commit_all(root, "a verified result that answers the question under another evaluator or entry")
    assert status(root, "elsewhere").label == "verified"
    st = status(root, "qq")
    assert st.label == "open" and any("elsewhere" in n and "same evaluator and entry" in n for n in st.notes), st.notes
    monkeypatch.chdir(root)
    assert main(["validate"]) == 1
    assert "answers 'qq'" in capsys.readouterr().out
    from verifylab import status as status_module
    monkeypatch.setattr(status_module, "answer_mismatch", lambda question, answer: None)   # the guard, disabled
    assert status(root, "qq").label == "answered"


def test_an_answer_with_the_same_evaluator_and_entry_answers(research_repo, monkeypatch, capsys):
    root = research_repo
    _python_question(root)
    _python_answer(root, "solved", "research/evaluators/primes.py", "solve")      # its own candidate file
    commit_all(root, "a verified answer to the question asked")
    assert status(root, "qq").label == "answered"
    monkeypatch.chdir(root)
    main(["validate"])
    assert "answers 'qq'" not in capsys.readouterr().out


def test_incoming_validation_flags_an_answer_under_another_evaluator(research_repo, monkeypatch, capsys):
    root = research_repo
    _python_question(root)
    commit_all(root, "the question")
    git(root, "checkout", "-q", "-b", "lane/lenient")
    _python_answer(root, "elsewhere", "research/evaluators/lenient.py", "solve")
    shutil.rmtree(root / "research" / "evidence" / "elsewhere")                    # lanes bring no receipts
    commit_all(root, "an answer under a lenient evaluator")
    git(root, "checkout", "-q", "trusted")
    monkeypatch.chdir(root)
    assert main(["validate", "--incoming", "lane/lenient"]) == 1
    out = capsys.readouterr().out
    assert "on lane/lenient: answers 'qq'" in out and "same evaluator and entry" in out


def test_the_worktree_cannot_change_the_kind_that_decides_the_status(research_repo):
    root = research_repo
    receipt(root)
    commit_all(root, "admit the pass")
    item = root / "research" / "items" / "add-zero.md"
    item.write_text(item.read_text().replace('kind = "result"', 'kind = "conjecture"'))
    st = status(root)
    assert st.label == "verified" and any("trusted ref's result/formal decides" in n for n in st.notes)


# A15: a failed git read is an error, and git reads only this repository's real objects --------------------------

def _admitted_retraction(root: Path) -> str:
    """Admit a pass, then a retraction; return the commit before the retraction."""
    receipt(root)
    commit_all(root, "admit the pass")
    before = git(root, "rev-parse", "HEAD").strip()
    retraction(root)
    commit_all(root, "admit a retraction")
    assert status(root).label == "retracted"
    return before


def test_an_unreadable_admitted_record_is_an_error_not_an_absence(research_repo, monkeypatch, capsys):
    root = research_repo
    _admitted_retraction(root)
    [rel] = git(root, "ls-tree", "-r", "--name-only", "HEAD", "research/reviews").split()
    sha = git(root, "rev-parse", f"HEAD:{rel}").strip()
    loose = root / ".git" / "objects" / sha[:2] / sha[2:]
    loose.chmod(0o644)
    loose.unlink()                                                  # the retraction's bytes cannot be read
    with pytest.raises(GitError):
        status(root)
    monkeypatch.chdir(root)
    assert main(["show", "add-zero"]) == 2
    assert "status: verified" not in capsys.readouterr().out


def test_a_replaced_trusted_commit_is_ignored(research_repo):
    root = research_repo
    before = _admitted_retraction(root)
    git(root, "replace", git(root, "rev-parse", "HEAD").strip(), before)   # git show would now see `before`
    assert "retraction" not in git(root, "log", "-1", "--format=%s", "trusted")
    assert status(root).label == "retracted"


def test_git_environment_variables_do_not_redirect_the_trusted_reads(research_repo, tmp_path, monkeypatch):
    root = research_repo
    receipt(root)
    commit_all(root, "admit the pass")
    old = tmp_path / "old.git"
    git(tmp_path, "clone", "-q", "--bare", str(root), str(old))    # a copy without the retraction
    retraction(root)
    commit_all(root, "admit a retraction")
    monkeypatch.setenv("GIT_DIR", str(old))
    assert status(root).label == "retracted"


# Validation sees admitted records that the worktree lacks ---------------------------------------------------------

def test_validate_checks_admitted_records_missing_from_the_worktree(research_repo, monkeypatch, capsys):
    root = research_repo
    path = receipt(root, "ghost")                                    # filed under an id that is no item
    commit_all(root, "admit a stray receipt")
    shutil.rmtree(path.parent)
    monkeypatch.chdir(root)
    assert main(["validate"]) == 1
    assert "records filed under 'ghost', which is not an item" in capsys.readouterr().out


# A2: an admitted receipt answers for the item as committed on the trusted ref ------------------------------------

LEAN_TABLE = '[lean]\ntarget = "research/targets/t.lean"\ntheorems = ["main"]\nproofs = {{ main = "{proof}" }}\n'


def _with_proof(root: Path, proof: str) -> Path:
    item = root / "research" / "items" / "add-zero.md"
    text = item.read_text()
    head, _, rest = text.partition("+++\nBody")
    head = head.split("[lean]")[0]
    item.write_text(head + LEAN_TABLE.format(proof=proof) + "+++\nBody" + rest)
    return item


def test_an_admitted_receipt_does_not_verify_an_answer_only_the_worktree_has(research_repo):
    root = research_repo
    _with_proof(root, "sorry")
    commit_all(root, "a question with a placeholder answer")
    _with_proof(root, "rfl")                                         # the answer, never committed
    path = receipt(root)                                             # checked against the worktree's answer
    git(root, "add", str(path))
    git(root, "commit", "-q", "-m", "admit only the receipt")
    st = status(root)
    assert st.label == "verified-stale"
    assert any("not the item on the trusted ref" in n for n in st.notes), st.notes
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "integrate the answer too")
    assert status(root).label == "verified"                          # control


def test_an_admitted_receipt_of_an_item_not_on_the_trusted_ref_never_verifies(research_repo):
    root = research_repo
    write_item(root, "fresh")
    path = receipt(root, "fresh")
    git(root, "add", str(path))
    git(root, "commit", "-q", "-m", "admit a receipt of an item that is not integrated")
    st = status(root, "fresh")
    assert st.label == "verified-stale" and any("not on the trusted ref" in n for n in st.notes)


# A5: the programs vl starts come from system folders or machine.toml, never from the caller's PATH ---------------

FAKE = "#!/bin/sh\necho 'fake {name}' >&2\nexit 1\n"


def _fake_path(tmp_path: Path, monkeypatch) -> Path:
    from verifylab import machine
    folder = tmp_path / "fake-bin"
    folder.mkdir()
    for name in machine.LAUNCHER_NAMES:
        (folder / name).write_text(FAKE.format(name=name))
        (folder / name).chmod(0o755)
    monkeypatch.setenv("PATH", f"{folder}:{os.environ['PATH']}")
    return folder


def test_launchers_and_git_never_come_from_the_callers_path(research_repo, tmp_path, monkeypatch, capsys):
    from verifylab import gitref, jail
    from verifylab.adapters.lean_comparator import comparator_command
    receipt(research_repo)
    commit_all(research_repo, "admit")
    fake = _fake_path(tmp_path, monkeypatch)
    assert shutil.which("git") == str(fake / "git")                 # the planted wrapper is first on PATH
    assert status(research_repo).label == "verified"                # git reads go to the real git
    assert not gitref.program().startswith(str(fake))
    argv = jail.Jail(workdir=tmp_path).argv(["true"])
    assert argv[0] == jail.program("bwrap") and argv[0].startswith("/") and not argv[0].startswith(str(fake))
    command = comparator_command(Path("/opt/comparator"), tmp_path, tmp_path / "c.json")
    assert command[0].startswith("/") and not any(part.startswith(str(fake)) for part in command)


def test_machine_toml_can_name_a_launcher(tmp_path, monkeypatch):
    """Control: the machine owner, not the caller, may point vl at another bubblewrap."""
    from verifylab import jail, machine
    other = tmp_path / "bwrap"
    other.write_text("#!/bin/sh\n")
    other.chmod(0o755)
    path = machine.config_path()
    path.write_text(path.read_text() + f'\n[launchers]\nbwrap = "{other}"\n')
    machine.forget()
    assert jail.program("bwrap") == str(other)
    path.write_text(path.read_text().replace('bwrap = "', 'bwrap = "relative/'))
    machine.forget()
    with pytest.raises(machine.MachineError, match="relative"):
        machine.load()


# A6: a machine policy the caller's environment chose is recorded and shown ---------------------------------------

def test_a_protected_tool_inside_the_repository_is_refused(research_repo):
    from verifylab import machine
    from verifylab.adapters.lean_comparator import protected_tool
    tools = research_repo / "tools"
    tools.mkdir()
    (tools / "comparator").write_text("#!/bin/sh\necho 'Your solution is okay!'\n")
    (tools / "comparator").chmod(0o755)
    machine.write_revisions(tools, {"comparator": machine.file_sha256(tools / "comparator")})
    loaded = machine.Machine(machine.config_path(), "0" * 64, tools={"comparator": str(tools / "comparator")})
    tool, problem = protected_tool("comparator", loaded, "leanprover/lean4:v4.0.0", {}, (research_repo,))
    assert tool is None and "inside the repository" in problem
    tool, problem = protected_tool("comparator", loaded, "leanprover/lean4:v4.0.0", {}, ())   # control: pinned
    assert tool is not None and problem is None


def test_an_xdg_override_of_the_machine_policy_is_recorded_and_shown(tmp_path, monkeypatch):
    from verifylab import machine
    monkeypatch.setattr(machine, "home", lambda: tmp_path / "home")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "home" / ".config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "home" / ".local" / "share"))
    assert machine.overrides() == {}                                  # control: the account's own locations
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "elsewhere"))
    assert machine.overrides() == {"XDG_CONFIG_HOME": str(tmp_path / "elsewhere")}
    record = machine.policy_record()
    assert record["overridden_by"] == {"XDG_CONFIG_HOME": str(tmp_path / "elsewhere")}
    assert record["config"] == str(tmp_path / "elsewhere" / "verifylab" / "machine.toml")


def test_a_pass_checked_under_an_overridden_machine_policy_says_so(research_repo):
    root = research_repo
    policy = {"config": "/x/verifylab/machine.toml", "overridden_by": {"XDG_CONFIG_HOME": "/x"}}
    path = receipt(root)
    commit_all(root, "admit a pass under the default policy")
    st = status(root)
    assert st.label == "verified" and not any("machine policy" in n for n in st.notes)   # control
    git(root, "rm", "-q", str(path))
    data = synthetic_receipt(root, dict(
        item="add-zero", item_revision="a" * 40, question_digest=item_question(root, "add-zero"),
        adapter="lean-comparator", assurance="protected", verdict="pass", reasons=[],
        inputs={"digest": "d", "files": {LEAN: sha256_hex((root / LEAN).read_bytes())}},
        environment={"machine_policy": policy}, checked={}, command=["vl"], started_at="2026-10-01T10:00:00+00:00",
        finished_at="2026-10-01T10:00:00+00:00", tool_version="vl 0.1.0"))
    write_new_json(root / "research" / "evidence" / "add-zero" / f"{data['receipt_id'][:16]}.json", data)
    commit_all(root, "admit a pass under a caller's policy")
    st = status(root)
    assert st.label == "verified" and any("XDG_CONFIG_HOME=/x" in n for n in st.notes)


# A3: a trust anchor the caller chose is named wherever a status depends on it ------------------------------------

def test_a_trusted_ref_given_on_the_command_line_is_named_on_every_status(research_repo, monkeypatch, capsys):
    root = research_repo
    git(root, "checkout", "-q", "-b", "lane/y")
    receipt(root)
    commit_all(root, "a lane admits its own receipt")
    monkeypatch.chdir(root)
    assert main(["show", "add-zero"]) == 0
    out = capsys.readouterr().out
    assert "status: pending-admission]" in out and "trusted ref lane/y" not in out      # control
    for argv in (["show", "add-zero"], ["show", "add-zero", "--brief"], ["show", "add-zero", "--impact"],
                 ["find", "small"], ["validate"]):
        main([*argv, "--trusted-ref", "lane/y"])
        out = capsys.readouterr().out
        assert "lane/y" in out and "--trusted-ref" in out, (argv, out)
    main(["show", "add-zero", "--trusted-ref", "lane/y"])
    assert "status: verified under trusted ref lane/y (--trusted-ref)" in capsys.readouterr().out


# A9, A10: an admitted failure or a demonstrated vacuity is never lost to a tie or an inconclusive rerun -----------

def test_a_fail_finished_in_the_same_second_as_a_pass_overrides_it(research_repo):
    root = research_repo
    receipt(root, finished="2026-10-01T10:00:00+00:00")
    receipt(root, verdict="fail", finished="2026-10-01T10:00:00+00:00")
    commit_all(root, "a pass and a fail of the same instant")
    assert status(root).label == "failed"


def test_receipt_times_compare_at_full_precision(research_repo):
    root = research_repo
    receipt(root, verdict="fail", finished="2026-10-01T10:00:00.250000+00:00")
    receipt(root, finished="2026-10-01T10:00:00.500000+00:00")
    commit_all(root, "a fail, then a pass half a second later")
    assert status(root).label == "verified"                          # control: the later pass decides
    receipt(root, verdict="fail", finished="2026-10-01T10:00:00.750000+00:00")
    commit_all(root, "a fail a quarter second after the pass")
    assert status(root).label == "failed"


def _probed(vacuous: str | None, problems: list[str]) -> dict:
    entry = {"trivial_by": None, "vacuous_by": vacuous, "prop_hypotheses": 1 if problems == [] else None}
    return {"probes": {"main": entry}, "probe_run": {"battery": ["simp"], "problems": problems}}


def test_an_incomplete_probe_rerun_does_not_erase_a_demonstrated_vacuity(research_repo):
    root = research_repo
    receipt(root, finished="2026-10-01T10:00:00+00:00", checked=_probed("simp_all", []))
    commit_all(root, "a pass whose probes derive False")
    assert status(root).label == "vacuous"
    receipt(root, finished="2026-10-01T11:00:00+00:00", checked=_probed(None, ["the probes timed out after 300 s"]))
    commit_all(root, "the same inputs again, probes timed out")
    assert status(root).label == "vacuous"


# A13, A14: a tool or process failure is never turned into a verdict ----------------------------------------------

BUILT = ("Building VLChallenge\nExporting #[Nat, main] from VLChallenge\nBuilding VLSolution\n")


def test_a_rejection_line_from_a_killed_comparator_or_from_the_candidate_is_not_a_fail():
    from verifylab.adapters.lean_comparator import classify
    printed = BUILT + "nanoda kernel rejected the solution\nLean default kernel rejects the solution\n"
    assert classify(printed, "", -9, ("main",)).verdict == "error"            # killed while the candidate built
    assert classify(printed, "", 137, ("main",)).verdict == "error"           # the same, as bubblewrap reports it
    planted = classify(printed, "uncaught exception: Child exited with 1\n", 1, ("main",))
    assert planted.verdict != "fail" or "kernel rejected" not in planted.reason
    own = BUILT + "Exporting #[Nat] from VLSolution\nRunning nanoda kernel on solution\nnanoda kernel rejected the solution\n"
    control = classify(own, "uncaught exception: nanoda exited with 1\n", 1, ("main",))
    assert control.verdict == "fail" and "nanoda kernel rejected" in control.reason


def _layout(tmp_path: Path, child: str):
    """A fake runner child that behaves as `child` says, in place of the real one."""
    from verifylab import candidate_exec
    code = tmp_path / "code"
    code.mkdir()
    (code / "main.py").write_text("def solve(x):\n    return x\n")
    runner = tmp_path / "runner.py"
    runner.write_text("import json, os, sys\nres = sys.argv[2]\n" + child)
    return code, runner, candidate_exec


def test_an_ok_result_followed_by_a_non_zero_exit_does_not_count(tmp_path, monkeypatch):
    import sys
    code, runner, candidate_exec = _layout(tmp_path, (
        "json.dump({'status': 'ok', 'error': None, 'outcomes': [{'ok': True, 'value': 1, 'error': None}]}, "
        "open(res, 'w'))\nsys.stdout.flush()\nos._exit(137)\n"))
    monkeypatch.setattr(candidate_exec, "CHILD_SCRIPT", runner)
    run = candidate_exec.run_candidate_calls(code_dir=code, symbol="solve", inputs=[[1]], run_dir=tmp_path / "run",
                                             interpreter=sys.executable)
    assert run.status == candidate_exec.STATUS_RUNNER_ERROR and run.returncode == 137 and run.started


def test_a_signal_after_the_candidate_started_is_inconclusive_and_a_crash_is_a_fail(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from verifylab import candidate_exec
    from verifylab.adapters import python_eval
    check = python_eval._Check.__new__(python_eval._Check)
    check.request = SimpleNamespace(timeout=10)
    check.notes, check.log, check.checked, check.files, check.trusted_files = [], [], {}, {}, {}
    check.target, check.environment, check.command, check.disabled = {}, {}, [], frozenset()
    check.guard = lambda name: True
    check.remaining = lambda: 10.0
    check.interpreter = "/usr/bin/python3"
    check.jail_argv = lambda command, work, read_only: command
    spec = python_eval.PythonSpec("research/evaluators/e.py", "main.py", "solve", ())

    def verdict(returncode):
        run = candidate_exec.CandidateRun(status=candidate_exec.STATUS_RUNNER_ERROR, error="died", started=True,
                                          returncode=returncode)
        monkeypatch.setattr(candidate_exec, "run_candidate_calls", lambda **kw: run)
        return check.run_separated(spec, [1], "d", 1.0, tmp_path, tmp_path).verdict

    assert verdict(137) == "error" and verdict(-9) == "error"       # the memory cap, the OOM killer, a signal
    assert verdict(3) == "fail"                                      # control: the candidate's own exit


# B1: vl check never writes a receipt vl validate rejects -----------------------------------------------------------

def test_a_check_that_stops_before_reading_the_candidate_writes_no_receipt(research_repo, monkeypatch, capsys):
    """The real Lean adapter on an item whose trusted target is missing: it stops after reading only trusted inputs
    (the configuration), so there is no candidate input to bind, and no receipt is written."""
    root = research_repo
    item = root / "research" / "items" / "add-zero.md"
    item.write_text(item.read_text().replace("+++\nBody", '[lean]\ntarget = "research/targets/missing.lean"\n'
                                             'theorems = ["main"]\nproofs = { main = "rfl" }\n+++\nBody'))
    commit_all(root, "an item whose target was never committed")
    monkeypatch.chdir(root)
    assert main(["check", "add-zero"]) == 3
    out = capsys.readouterr().out
    assert "no receipt" in out and "missing.lean" in out
    assert not (root / "research" / "evidence").exists()
    main(["validate"])
    errors = [line for line in capsys.readouterr().out.splitlines() if line.startswith("ERROR")]
    assert errors and not any("evidence" in e for e in errors), errors        # only the missing target


# B2, B3: deadlines and output bounds hold for every run of a check -------------------------------------------------

@pytest.fixture
def bare_jail(monkeypatch):
    """The jail's process handling without bubblewrap: the command runs as given."""
    from verifylab import jail
    monkeypatch.setattr(jail, "available", lambda: True)
    monkeypatch.setattr(jail.Jail, "argv", lambda self, command: list(command))
    monkeypatch.setattr(jail, "memory_capped", lambda argv, *args, **kwargs: list(argv))
    return jail


def test_a_command_that_closes_its_output_still_meets_its_deadline(bare_jail, tmp_path):
    import time
    start = time.monotonic()
    done = bare_jail.stream(bare_jail.Jail(workdir=tmp_path), ["/bin/sh", "-c", "exec >&- 2>&-; exec sleep 30"],
                            timeout=1)
    assert done.returncode is None and time.monotonic() - start < 10
    done = bare_jail.stream(bare_jail.Jail(workdir=tmp_path), ["/bin/sh", "-c", "exec >&- 2>&-; exit 4"], timeout=10)
    assert done.returncode == 4                                       # control: a command that ends in time


def test_run_keeps_the_output_bounded_and_raises_on_its_deadline(bare_jail, tmp_path):
    import subprocess
    box = bare_jail.Jail(workdir=tmp_path)
    proc = bare_jail.run(box, ["/bin/sh", "-c", "head -c 5000000 /dev/zero | tr '\\0' x"], timeout=60)
    assert proc.returncode == 0 and len(proc.stdout) < 2 * bare_jail.STREAM_KEEP + 200
    with pytest.raises(subprocess.TimeoutExpired):
        bare_jail.run(box, ["/bin/sh", "-c", "exec sleep 30"], timeout=0.5)


def test_every_step_of_a_lean_check_runs_within_the_one_remaining_budget(tmp_path, monkeypatch):
    import time
    from types import SimpleNamespace
    from verifylab.adapters import lean_comparator
    from verifylab import jail
    seen = []
    monkeypatch.setattr(jail, "run", lambda box, command, timeout, **kw: seen.append(timeout) or
                        __import__("subprocess").CompletedProcess(command, 0, b"", b""))
    check = lean_comparator._Check.__new__(lean_comparator._Check)
    check.req = SimpleNamespace(timeout=30.0, memory_max=None, memory_total=None, scratch=tmp_path)
    check.started, check.log = time.monotonic() - 25.0, []
    check.preflight(None, tmp_path / "pre", "leanprover/lean4:v4.0.0", "", {}, tmp_path, ["Mathlib"])
    assert seen and seen[-1] <= 5.0                                  # not the 60 s floor it had
    check.started = time.monotonic() - 31.0
    with pytest.raises(lean_comparator._Stop, match="timed out"):
        check.preflight(None, tmp_path / "pre2", "leanprover/lean4:v4.0.0", "", {}, tmp_path, ["Mathlib"])


@pytest.mark.parametrize("value", ["nan", "inf", "-inf"])
def test_non_finite_limits_are_refused(research_repo, value, monkeypatch, capsys):
    from verifylab.config import ConfigError, parse_config
    with pytest.raises(ConfigError, match="finite"):
        parse_config(research_repo, f"[check]\nprobe_timeout = {value}\n")
    monkeypatch.chdir(research_repo)
    with pytest.raises(SystemExit):
        main(["check", "add-zero", f"--timeout={value}"])
    assert "finite, positive number of seconds" in capsys.readouterr().err


# A11: a fidelity review vouches for the meaning it read, and goes stale when any of it changes --------------------

DEFS = "def double (n : Nat) : Nat := n + n\n"
TARGET = "import Fixture.Defs\n\ntheorem main (n : Nat) : double n = 2 * n := sorry\n"


def _with_target(root: Path) -> None:
    (root / "Fixture" / "Defs.lean").write_text(DEFS)
    target = root / "research" / "targets" / "t.lean"
    target.parent.mkdir(parents=True)
    target.write_text(TARGET)
    item = root / "research" / "items" / "add-zero.md"
    item.write_text(item.read_text().replace("+++\nBody", '[lean]\ntarget = "research/targets/t.lean"\n'
                                             'theorems = ["main"]\nproofs = { main = "by omega" }\n+++\nBody'))
    commit_all(root, "a target that imports a definition")


def _reviewed(root: Path, monkeypatch, capsys) -> None:
    _with_target(root)
    monkeypatch.chdir(root)
    assert main(["review", "add-zero", "--kind", "fidelity", "--verdict", "faithful", "--author", "human:ref",
                 "--human-approved", "--text", "double is doubling; main says so"]) == 0
    capsys.readouterr()
    commit_all(root, "admit the review")
    assert _fidelity(root).label == "faithful"


def _fidelity(root: Path):
    from verifylab.status import fidelity
    repo = Repo.open(root)
    return fidelity(repo, repo.load_item("add-zero"))


CHANGES = [
    (lambda root: (root / "Fixture" / "Defs.lean").write_text("def double (n : Nat) : Nat := n * n\n"), "definitions"),
    (lambda root: _replace(root / "research/items/add-zero.md", 'theorems = ["main"]', 'theorems = ["main", "aux"]'),
     "theorems"),
    (lambda root: _replace(root / "research/items/add-zero.md", "For every n, n + 0 = n.", "Something else."), "claim"),
    (lambda root: _replace(root / "research/targets/t.lean", "2 * n", "n + n"), "target"),
    (lambda root: _replace(root / "research/items/add-zero.md", 'limits = ["Only natural numbers."]', "limits = []"),
     "limits"),
    (lambda root: _replace(root / "research/items/add-zero.md", "limits =", 'assumptions = ["n is finite"]\nlimits ='),
     "assumptions"),
]


@pytest.mark.parametrize("change, what", CHANGES)
def test_a_fidelity_review_goes_stale_when_what_it_read_changes(research_repo, monkeypatch, capsys, change, what):
    root = research_repo
    _reviewed(root, monkeypatch, capsys)
    change(root)
    commit_all(root, f"change the {what}")
    state = _fidelity(root)
    assert state.label == f"review stale: {what} changed since review", state


def test_a_fidelity_review_survives_what_it_did_not_read(research_repo, monkeypatch, capsys):
    root = research_repo
    _reviewed(root, monkeypatch, capsys)
    _replace(root / "research/items/add-zero.md", "Body text.", "Body text, reworded.")
    _replace(root / "research/items/add-zero.md", 'title = "A small result"', 'title = "A small result, renamed"')
    (root / "Fixture" / "Basic.lean").write_text("-- not imported by the target\n")
    commit_all(root, "change the title, the body and an unrelated module")
    assert _fidelity(root).label == "faithful"


def test_with_limits_unbound_a_removed_limit_keeps_the_review(research_repo, monkeypatch, capsys):
    """The guard disabled: a meaning without limits and assumptions lets a removed limit widen the claim under a
    faithful review."""
    from verifylab import status as status_module
    monkeypatch.setattr(status_module, "BOUND_LISTS", ())
    root = research_repo
    _reviewed(root, monkeypatch, capsys)
    _replace(root / "research/items/add-zero.md", 'limits = ["Only natural numbers."]', "limits = []")
    commit_all(root, "remove the limit")
    assert _fidelity(root).label == "faithful"


def _pre_binding_review(root: Path) -> None:
    """A fidelity review recorded before reviews bound limits and assumptions: its meaning lacks their digests."""
    from verifylab.status import meaning, meaning_digest
    repo = Repo.open(root)
    old = {k: v for k, v in meaning(repo, repo.load_item("add-zero")).items()
           if k not in ("limits_sha256", "assumptions_sha256")}
    review = seal_review(dict(item="add-zero", item_revision="a" * 40, kind="fidelity", author="human:old",
                              text="read before reviews bound limits", created="2026-10-01T00:00:00+00:00",
                              verdict="faithful", target_path="research/targets/t.lean",
                              target_sha256=old["files"]["research/targets/t.lean"], meaning=old,
                              meaning_digest=meaning_digest(old)))
    write_new_json(root / "research" / "reviews" / "add-zero" / f"{review['review_id'][:16]}.json", review)


def test_a_review_recorded_before_limits_were_bound_counts_marked_and_validate_asks_again(research_repo, monkeypatch,
                                                                                          capsys):
    root = research_repo
    (root / "Fixture" / "Defs.lean").write_text(DEFS)
    target = root / "research" / "targets" / "t.lean"
    target.parent.mkdir(parents=True)
    target.write_text(TARGET)
    item = root / "research" / "items" / "add-zero.md"
    item.write_text(item.read_text().replace("+++\nBody", '[lean]\ntarget = "research/targets/t.lean"\n'
                                             'theorems = ["main"]\nproofs = { main = "by omega" }\n+++\nBody'))
    commit_all(root, "a target")
    _pre_binding_review(root)
    receipt(root)
    commit_all(root, "an older review and a verified pass")
    state = _fidelity(root)
    assert state.label == "faithful" and state.unbound == ("limits", "assumptions") and not state.legacy, state
    monkeypatch.chdir(root)
    assert main(["show", "add-zero"]) == 0
    assert "it does not bind the limits or the assumptions" in capsys.readouterr().out
    assert main(["validate"]) == 0
    assert "predates the binding of limits and assumptions" in capsys.readouterr().out
    _replace(item, 'limits = ["Only natural numbers."]', "limits = []")       # what it never bound
    commit_all(root, "remove the limit")
    assert _fidelity(root).label == "faithful"
    _replace(item, "For every n, n + 0 = n.", "Something else.")             # what it bound
    commit_all(root, "change the claim")
    assert _fidelity(root).label == "review stale: claim changed since review"
    _replace(item, "Something else.", "For every n, n + 0 = n.")
    commit_all(root, "restore the claim")
    assert main(["review", "add-zero", "--kind", "fidelity", "--verdict", "too-weak", "--author", "agent:ref",
                 "--text", "without the limit the record claims more than main says"]) == 0
    commit_all(root, "a review that binds the limits")
    state = _fidelity(root)
    assert state.label == "disputed: too-weak" and state.unbound == () and state.by == "agent:ref"
    _replace(item, "limits = []", 'limits = ["Only small n."]')
    commit_all(root, "edit the limits under the dispute")
    # The newer review is stale; the older one, which never bound the limits, does not come back as faithful.
    assert _fidelity(root).label == "review stale: limits changed since review"
    assert main(["review", "add-zero", "--kind", "fidelity", "--verdict", "faithful", "--author", "agent:ref",
                 "--text", "main says n + 0 = n; the limit is honest"]) == 0
    commit_all(root, "review the new limits")
    state = _fidelity(root)
    assert state.label == "faithful" and state.unbound == () and state.by == "agent:ref"
    assert main(["validate"]) == 0
    assert "predates the binding" not in capsys.readouterr().out


# CHEATS R21: an adverse fidelity review is never lost to a tie --------------------------------------------------------

def _fidelity_review(capsys, verdict: str, text: str, dry_run: bool = False) -> dict:
    assert main(["review", "add-zero", "--kind", "fidelity", "--verdict", verdict, "--author", "agent:ref",
                 "--text", text, "--json", *(["--dry-run"] if dry_run else [])]) == 0
    return json.loads(capsys.readouterr().out)["review"]


def test_review_times_are_written_to_the_microsecond(research_repo, monkeypatch, capsys):
    _with_target(research_repo)
    monkeypatch.chdir(research_repo)
    created = _fidelity_review(capsys, "faithful", "main says double n = 2 n")["created"]
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{6}\+00:00", created), created


@pytest.mark.parametrize("faithful_path_sorts_last", [True, False])
def test_conflicting_fidelity_reviews_of_the_same_instant_resolve_to_the_adverse_one(research_repo, monkeypatch, capsys,
                                                                                    faithful_path_sorts_last):
    """Two real `vl review` calls at one instant (the clock is held still): a faithful review, then a too-weak one.
    Whichever way their content-hash file names sort, the adverse verdict decides."""
    from datetime import datetime, timezone
    from verifylab.commands import review as review_module

    class Still(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 10, 2, 17, 15, 43, tzinfo=timezone.utc)
    root = research_repo
    _with_target(root)
    monkeypatch.chdir(root)
    monkeypatch.setattr(review_module, "datetime", Still)
    weak = _fidelity_review(capsys, "too-weak", "main misses the integers", dry_run=True)["review_id"][:16]
    text = next(t for t in (f"main says double n = 2 n ({k})" for k in range(200))
                if (_fidelity_review(capsys, "faithful", t, dry_run=True)["review_id"][:16] > weak)
                == faithful_path_sorts_last)
    first = _fidelity_review(capsys, "faithful", text)
    second = _fidelity_review(capsys, "too-weak", "main misses the integers")
    assert first["created"] == second["created"]                                      # a real tie
    commit_all(root, "two reviews of the same instant")
    state = _fidelity(root)
    assert state.label == "disputed: too-weak" and state.review.endswith(f"{second['review_id'][:16]}.json"), state


def test_a_later_review_still_decides_and_equal_verdicts_tie_harmlessly(research_repo, monkeypatch, capsys):
    """Controls: chronology at full precision (a faithful review a microsecond after a too-weak one decides; a review
    recorded to the second, as earlier versions wrote them, is older than one later in that second), and two
    faithful reviews of the same instant stay faithful."""
    from verifylab.status import meaning, meaning_digest
    root = research_repo
    _with_target(root)
    repo = Repo.open(root)
    recorded = meaning(repo, repo.load_item("add-zero"))

    def review(verdict: str, created: str, text: str) -> None:
        data = seal_review(dict(item="add-zero", item_revision="a" * 40, kind="fidelity", author="agent:ref",
                                text=text, created=created, verdict=verdict, target_path="research/targets/t.lean",
                                target_sha256=recorded["files"]["research/targets/t.lean"], meaning=recorded,
                                meaning_digest=meaning_digest(recorded)))
        write_new_json(root / "research" / "reviews" / "add-zero" / f"{data['review_id'][:16]}.json", data)
    review("faithful", "2026-10-02T17:15:43+00:00", "read to the second")
    review("faithful", "2026-10-02T17:15:43+00:00", "read to the second, again")
    commit_all(root, "two faithful reviews of one instant")
    assert _fidelity(root).label == "faithful"
    review("too-weak", "2026-10-02T17:15:43.000001+00:00", "a microsecond later: too weak")
    commit_all(root, "a later adverse review")
    assert _fidelity(root).label == "disputed: too-weak"
    review("faithful", "2026-10-02T19:15:43.000002+02:00", "a microsecond later again, in another zone")
    commit_all(root, "a later faithful review")
    assert _fidelity(root).label == "faithful"


# CHEATS R25: the card shows the prose its status and fidelity hold for; a worktree edit is an unreviewed proposal ---

RIEMANN = "This algorithm proves the Riemann hypothesis for all inputs."


def _card(capsys, *args) -> dict:
    assert main(["show", "add-zero", "--json", *args]) == 0
    return json.loads(capsys.readouterr().out)


def _verified_and_reviewed(root: Path, monkeypatch, capsys) -> Path:
    _reviewed(root, monkeypatch, capsys)
    receipt(root)
    commit_all(root, "a verified pass")
    return root / "research" / "items" / "add-zero.md"


def test_an_uncommitted_stronger_claim_is_shown_as_an_unreviewed_proposal(research_repo, monkeypatch, capsys):
    from verifylab.render import PROPOSAL
    root = research_repo
    item = _verified_and_reviewed(root, monkeypatch, capsys)
    _replace(item, "For every n, n + 0 = n.", RIEMANN)                     # not committed
    _replace(item, 'limits = ["Only natural numbers."]', "limits = []")
    card = _card(capsys)["items"][0]
    assert (card["status"]["label"], card["fidelity"]["label"]) == ("verified", "faithful")
    assert card["statement"] == "For every n, n + 0 = n." and card["limits"] == ["Only natural numbers."]
    assert card["proposed"] == {"label": PROPOSAL, "revision": card["revision"], "fields": ["statement", "limits"],
                                "statement": RIEMANN, "limits": []}
    assert main(["show", "add-zero"]) == 0
    out = capsys.readouterr().out
    assert f"{PROPOSAL}: statement, limits" in out.splitlines()[0]
    assert out.index("For every n, n + 0 = n.") < out.index("STATUS")
    assert out.index("PROPOSED") < out.index(RIEMANN) < out.index("STATUS")
    assert out.count(RIEMANN) == 1 and "limits: none stated" in out
    brief = _card(capsys, "--brief")["brief"]
    assert f"{PROPOSAL}: statement, limits" in brief.splitlines()[0]
    assert brief.index("statement:\n  For every n, n + 0 = n.") < brief.index("proposed:") < brief.index(RIEMANN)
    assert main(["validate", "--json"]) == 0
    warnings = [w["message"] for w in json.loads(capsys.readouterr().out)["warnings"]
                if w["where"] == "research/items/add-zero.md"]
    assert any(m.startswith(f"{PROPOSAL}: its statement, limits differ") for m in warnings), warnings


def test_an_unbound_or_integrated_edit_is_no_proposal(research_repo, monkeypatch, capsys):
    """Controls: the title and body bind nothing, so editing them proposes nothing; an edit committed on the trusted
    ref is the trusted text, and its fidelity review goes stale instead."""
    root = research_repo
    item = _verified_and_reviewed(root, monkeypatch, capsys)
    _replace(item, "Body text.", "Body text, reworded.")
    _replace(item, 'title = "A small result"', 'title = "A small result, renamed"')
    card = _card(capsys)["items"][0]
    assert card.get("proposed") is None and card["title"] == "A small result, renamed"
    assert main(["validate", "--json"]) == 0
    assert not any("not reviewed" in w["message"] for w in json.loads(capsys.readouterr().out)["warnings"])
    _replace(item, "For every n, n + 0 = n.", RIEMANN)
    commit_all(root, "integrate the stronger claim")
    card = _card(capsys)["items"][0]
    assert card.get("proposed") is None and card["statement"] == RIEMANN
    assert card["fidelity"]["label"] == "review stale: claim changed since review"


def test_a_renewed_review_of_a_changed_target_is_not_a_duplicate(research_repo, monkeypatch, capsys):
    root = research_repo
    _reviewed(root, monkeypatch, capsys)
    _replace(root / "research/targets/t.lean", "2 * n", "n + n")
    commit_all(root, "change the target")
    assert main(["review", "add-zero", "--kind", "fidelity", "--verdict", "faithful", "--author", "human:ref",
                 "--human-approved", "--text", "double is doubling; main says so"]) == 0, capsys.readouterr().err


def _replace(path: Path, old: str, new: str) -> None:
    text = path.read_text()
    assert old in text
    path.write_text(text.replace(old, new))


def _review_without_meaning(root: Path) -> None:
    """A fidelity review written before reviews recorded a meaning: it binds the target file's sha256 only."""
    target = root / "research" / "targets" / "t.lean"
    older = seal_review(dict(item="add-zero", item_revision="a" * 40, kind="fidelity", author="human:old",
                             text="read before reviews bound the meaning", created="2026-09-01T00:00:00+00:00",
                             verdict="faithful", target_path="research/targets/t.lean",
                             target_sha256=sha256_hex(target.read_bytes())))
    write_new_json(root / "research" / "reviews" / "add-zero" / f"{older['review_id'][:16]}.json", older)


def test_an_older_review_without_a_meaning_binds_the_target_only_and_says_so(research_repo, monkeypatch, capsys):
    root = research_repo
    _with_target(root)
    _review_without_meaning(root)
    (root / "Fixture" / "Defs.lean").write_text("def double (n : Nat) : Nat := n * n\n")
    commit_all(root, "an older review, and a changed definition")
    state = _fidelity(root)
    assert state.label == "faithful" and state.legacy and state.by == "human:old"
    assert state.unbound == ("definitions", "theorems", "claim", "limits", "assumptions")
    monkeypatch.chdir(root)
    assert main(["show", "add-zero"]) == 0
    assert "binds the target file only" in capsys.readouterr().out


@pytest.mark.parametrize("change, what", [(change, what) for change, what in CHANGES if what != "target"])
def test_an_older_review_without_a_meaning_never_comes_back_once_a_newer_review_exists(research_repo, monkeypatch,
                                                                                         capsys, change, what):
    """Whatever makes the newer review stale, the target-only review it followed stays superseded: it never bound
    the definitions, theorems, claim, limits or assumptions that changed."""
    root = research_repo
    _with_target(root)
    _review_without_meaning(root)
    commit_all(root, "an older review")
    monkeypatch.chdir(root)
    assert main(["review", "add-zero", "--kind", "fidelity", "--verdict", "faithful", "--author", "agent:ref",
                 "--text", "double is doubling; main says so"]) == 0
    commit_all(root, "a newer review that binds the meaning")
    assert _fidelity(root).label == "faithful" and not _fidelity(root).legacy
    change(root)
    commit_all(root, f"change the {what}")
    assert _fidelity(root).label == f"review stale: {what} changed since review"


# A8: one kernel where the rules ask for two does not count -------------------------------------------------------

def test_a_pass_replayed_by_the_lean_kernel_only_does_not_count_while_external_kernels_is_on(research_repo,
                                                                                            monkeypatch):
    root = research_repo
    receipt(root, checked={"kernels": ["lean"]}, finished="2026-10-01T10:00:00+00:00")
    commit_all(root, "a pass on a machine without nanoda")
    st = status(root)
    assert st.label == "check-unsupported" and any("Lean kernel only" in n and "does not count" in n
                                                   for n in st.notes), st.notes
    receipt(root, checked={"kernels": ["lean", "nanoda"]}, finished="2026-10-01T11:00:00+00:00")
    commit_all(root, "a pass replayed by both kernels")
    st = status(root)
    assert st.label == "verified" and st.receipt.endswith(".json")                         # control
    assert "nanoda" in __import__("json").loads((root / st.receipt).read_text())["checked"]["kernels"]
    cfg = root / "research" / "vl.toml"
    cfg.write_text(cfg.read_text() + "external_kernels = false\n")
    commit_all(root, "the project asks for the Lean kernel only")
    from verifylab import status as status_module
    assert status_module.single_kernel(Repo.open(root), {"adapter": "lean-comparator", "assurance": "protected",
                                                         "verdict": "pass", "checked": {"kernels": ["lean"]}}) is False


def test_with_the_kernel_guard_disabled_a_single_kernel_pass_verifies(research_repo, monkeypatch):
    root = research_repo
    receipt(root, checked={"kernels": ["lean"]})
    commit_all(root, "a pass on a machine without nanoda")
    from verifylab import status as status_module
    monkeypatch.setattr(status_module, "single_kernel", lambda repo, data: False)
    assert status(root).label == "verified"
