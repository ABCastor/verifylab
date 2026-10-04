from __future__ import annotations

import json

import pytest

from verifylab.cli import main
from verifylab.records import sha256_hex, review_problems, write_new_json

from conftest import commit_all, git


def vl(capsys, *args):
    rc = main(list(args))
    captured = capsys.readouterr()
    return rc, captured.out, captured.err


def review_files(root, item_id="add-zero"):
    folder = root / "research" / "reviews" / item_id
    return sorted(folder.glob("*.json")) if folder.is_dir() else []


FIDELITY = ("review", "add-zero", "--kind", "fidelity", "--verdict", "faithful", "--author", "human:ada",
            "--human-approved", "--text", "The target states exactly n + 0 = n over Nat.")


def test_review_writes_one_immutable_record_bound_to_the_full_revision(research_repo, monkeypatch, capsys):
    root = research_repo
    monkeypatch.chdir(root)
    rc, _, err = vl(capsys, *FIDELITY)
    assert rc == 2 and "needs an item with a [lean] target" in err
    item = root / "research" / "items" / "add-zero.md"
    item.write_text(item.read_text().replace("+++\nBody", '[lean]\ntarget = "research/targets/add-zero.lean"\n+++\nBody'))
    target = root / "research" / "targets" / "add-zero.lean"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("theorem main (n : Nat) : n + 0 = n := sorry\n")
    rc, _, err = vl(capsys, *FIDELITY)
    assert rc == 2 and "merge the lane first" in err
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "admit target")
    rc, out, _ = vl(capsys, *FIDELITY, "--json")
    assert rc == 0
    data = json.loads(out)
    [file] = review_files(root)
    review = json.loads(file.read_text())
    assert data["path"] == str(file.relative_to(root)) and file.name == review["review_id"][:16] + ".json"
    assert review["item_revision"] == git(root, "hash-object", "research/items/add-zero.md").strip()
    assert len(review["item_revision"]) == 40
    assert review["kind"] == "fidelity" and review["verdict"] == "faithful" and review_problems(review) == []
    assert data["admitted"] is False
    assert review["target_path"] == "research/targets/add-zero.lean"
    assert review["target_sha256"] == sha256_hex(target.read_bytes())

    rc, _, err = vl(capsys, *FIDELITY)
    assert rc == 2 and "identical review already exists" in err
    assert review_files(root) == [file]
    with pytest.raises(FileExistsError):
        write_new_json(file, review)

    file.write_text(file.read_text().replace("faithful", "vacuous"))
    assert any("forged" in p for p in review_problems(json.loads(file.read_text())))


@pytest.mark.parametrize("extra, message", [
    (("--kind", "fidelity", "--verdict", "great"), "fidelity review needs --verdict"),
    (("--kind", "fidelity"), "fidelity review needs --verdict"),
    (("--kind", "compare"), "needs --compare-with"),
    (("--kind", "understanding", "--compare-with", "add-zero"), "--compare-with only applies"),
])
def test_review_rejects_bad_usage_and_writes_nothing(research_repo, monkeypatch, capsys, extra, message):
    monkeypatch.chdir(research_repo)
    rc, _, err = vl(capsys, "review", "add-zero", "--author", "agent:x", "--text", "reason", *extra)
    assert rc == 2 and message in err
    assert review_files(research_repo) == []


def test_review_rejects_bad_author_and_empty_reason(research_repo, monkeypatch, capsys):
    monkeypatch.chdir(research_repo)
    assert vl(capsys, "review", "add-zero", "--kind", "understanding", "--author", "ada", "--text", "x")[0] == 2
    assert vl(capsys, "review", "add-zero", "--kind", "understanding", "--author", "agent:x", "--text", "  ")[0] == 2
    assert review_files(research_repo) == []


@pytest.mark.parametrize("extra", [("--kind", "vote"), ("--kind", "understanding", "--score", "8")])
def test_votes_and_scores_are_not_part_of_review(research_repo, monkeypatch, capsys, extra):
    """Scores are a non-goal: a review is a judgement with its reason, never a number to add up."""
    monkeypatch.chdir(research_repo)
    with pytest.raises(SystemExit) as exit_info:
        main(["review", "add-zero", "--author", "agent:x", "--text", "reason", *extra])
    assert exit_info.value.code == 2 and review_files(research_repo) == []


def test_review_at_an_old_revision_and_compare(research_repo, monkeypatch, capsys):
    root = research_repo
    monkeypatch.chdir(root)
    old = git(root, "hash-object", "research/items/add-zero.md").strip()
    path = root / "research" / "items" / "add-zero.md"
    path.write_text(path.read_text().replace("Body text.", "Body text, revised."))
    commit_all(root)
    new = git(root, "hash-object", "research/items/add-zero.md").strip()

    rc, out, _ = vl(capsys, "review", f"add-zero@{old[:7]}", "--kind", "understanding", "--author", "human:ada",
                    "--human-approved", "--text", "I followed the old proof.", "--json")
    assert rc == 0
    review = json.loads(out)["review"]
    assert review["item_revision"] == old

    rc, out, _ = vl(capsys, "review", "add-zero", "--kind", "compare", "--author", "agent:cmp", "--verdict", "same",
                    "--compare-with", f"add-zero@{old[:7]}", "--text", "Only the prose changed.", "--json")
    review = json.loads(out)["review"]
    assert review["item_revision"] == new and review["compare_with"] == f"add-zero@{old}"

    rc, _, err = vl(capsys, "review", "add-zero@deadbeef", "--kind", "understanding", "--author", "agent:x",
                    "--text", "x")
    assert rc == 2 and "not in the git object store" in err
    assert vl(capsys, "review", "nope", "--kind", "understanding", "--author", "agent:x", "--text", "x")[0] == 2


def _admit_fidelity_target(root):
    item = root / "research/items/add-zero.md"
    item.write_text(item.read_text().replace("+++\nBody", '[lean]\ntarget = "research/targets/add-zero.lean"\n'
                                             'theorems = ["main"]\n+++\nBody'))
    target = root / "research/targets/add-zero.lean"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("theorem main (n : Nat) : n + 0 = n := sorry\n")
    commit_all(root, "admit target")
    return item


@pytest.mark.parametrize("field", ["statement", "limits", "body"])
def test_historical_fidelity_cannot_review_the_current_trusted_revision(research_repo, monkeypatch, capsys, field):
    root = research_repo
    monkeypatch.chdir(root)
    item = _admit_fidelity_target(root)
    old = git(root, "hash-object", "research/items/add-zero.md").strip()
    changes = {"statement": ("For every n, n + 0 = n.", "This also holds over arbitrary types."),
               "limits": ('limits = ["Only natural numbers."]', "limits = []"),
               "body": ("Body text.", "New commentary.")}
    before, after = changes[field]
    item.write_text(item.read_text().replace(before, after))
    commit_all(root, "revise claim")
    args = list(FIDELITY)
    args[1] = f"add-zero@{old[:12]}"
    rc, _, err = vl(capsys, *args)
    assert rc == 2 and "fidelity reviews require the current trusted item revision" in err
    assert review_files(root) == []


def test_fidelity_records_trusted_revision_and_self_review_despite_worktree_author_edit(
        research_repo, monkeypatch, capsys):
    from verifylab.repo import Repo
    from verifylab.status import fidelity
    root = research_repo
    monkeypatch.chdir(root)
    item = _admit_fidelity_target(root)
    trusted_revision = git(root, "hash-object", "research/items/add-zero.md").strip()
    item.write_text(item.read_text().replace('author = "agent:test"', 'author = "agent:someone-else"'))
    args = ("review", "add-zero", "--kind", "fidelity", "--verdict", "faithful", "--author", "agent:test",
            "--text", "I authored and read the trusted target.", "--json")
    rc, out, _ = vl(capsys, *args, "--dry-run")
    assert rc == 0 and json.loads(out)["self_review"] is True
    rc, out, _ = vl(capsys, *args)
    assert rc == 0
    data = json.loads(out)
    assert data["review"]["item_revision"] == trusted_revision
    git(root, "add", "research/reviews")
    git(root, "commit", "-q", "-m", "admit self-review only")
    repo = Repo.open(root)
    assert fidelity(repo, repo.load_item("add-zero")).self_review is True
    assert json.loads(vl(capsys, "show", "add-zero", "--json")[1])["items"][0]["fidelity"]["self_review"] is True


def test_disabling_the_revision_guard_accepts_a_request_for_the_wrong_meaning(
        research_repo, monkeypatch, capsys):
    from verifylab.commands import review
    from verifylab.repo import Repo
    from verifylab.status import fidelity
    root = research_repo
    monkeypatch.chdir(root)
    item = _admit_fidelity_target(root)
    old = git(root, "hash-object", "research/items/add-zero.md").strip()
    item.write_text(item.read_text().replace('limits = ["Only natural numbers."]', "limits = []"))
    commit_all(root, "widen claim")
    monkeypatch.setattr(review, "fidelity_revision_problem", lambda *_: None)
    args = list(FIDELITY)
    args[1] = f"add-zero@{old[:12]}"
    assert vl(capsys, *args)[0] == 0
    commit_all(root, "admit misattributed review")
    repo = Repo.open(root)
    assert fidelity(repo, repo.load_item("add-zero")).label == "faithful"
    assert repo.reviews("add-zero")[0].data["item_revision"] != old


def test_existing_misattributed_fidelity_is_rejected_without_editing_its_record(
        research_repo, monkeypatch, capsys):
    from verifylab.records import seal_review
    from verifylab.repo import Repo
    from verifylab.status import fidelity
    root = research_repo
    monkeypatch.chdir(root)
    item = _admit_fidelity_target(root)
    old = git(root, "hash-object", "research/items/add-zero.md").strip()
    item.write_text(item.read_text().replace('limits = ["Only natural numbers."]', "limits = []"))
    commit_all(root, "widen claim")
    assert vl(capsys, *FIDELITY)[0] == 0
    [file] = review_files(root)
    record = json.loads(file.read_text())
    record["item_revision"] = old      # reproduce a record written by the earlier buggy review command
    record = seal_review(record)
    file.unlink()
    file = file.with_name(record["review_id"][:16] + ".json")
    write_new_json(file, record)
    original = file.read_bytes()
    commit_all(root, "admit earlier inconsistent review")
    repo = Repo.open(root)
    assert fidelity(repo, repo.load_item("add-zero")).label == "not reviewed"
    rc, out, _ = vl(capsys, "validate")
    assert rc == 1 and "fidelity meaning disagrees with item_revision" in out
    assert file.read_bytes() == original


def test_fidelity_accepts_the_explicit_current_trusted_revision(research_repo, monkeypatch, capsys):
    root = research_repo
    monkeypatch.chdir(root)
    _admit_fidelity_target(root)
    revision = git(root, "hash-object", "research/items/add-zero.md").strip()
    args = list(FIDELITY)
    args[1] = f"add-zero@{revision[:12]}"
    assert vl(capsys, *args)[0] == 0 and len(review_files(root)) == 1


@pytest.mark.parametrize("change_meaning", [True, False])
def test_valid_older_fidelity_remains_readable_and_only_meaning_changes_stale_it(
        research_repo, monkeypatch, capsys, change_meaning):
    from verifylab.repo import Repo
    from verifylab.status import fidelity
    root = research_repo
    monkeypatch.chdir(root)
    item = _admit_fidelity_target(root)
    assert vl(capsys, *FIDELITY)[0] == 0
    commit_all(root, "admit valid review")
    before, after = (('limits = ["Only natural numbers."]', "limits = []") if change_meaning
                     else ("Body text.", "New commentary."))
    item.write_text(item.read_text().replace(before, after))
    commit_all(root, "revise item")
    repo = Repo.open(root)
    assert repo.reviews("add-zero")[0].problems == ()
    state = fidelity(repo, repo.load_item("add-zero"))
    assert state.label.startswith("review stale:") if change_meaning else state.label == "faithful"


def test_retraction_counts_only_once_committed(research_repo, monkeypatch, capsys):
    root = research_repo
    monkeypatch.chdir(root)
    rc, out, _ = vl(capsys, "review", "add-zero", "--kind", "retraction", "--author", "human:ada", "--human-approved",
                    "--text", "The target was wrong.")
    assert rc == 0 and "not admitted until committed" in out
    assert "status: unverified" in vl(capsys, "show", "add-zero")[1]
    commit_all(root)
    out = vl(capsys, "show", "add-zero")[1]
    assert "status: retracted" in out and "READ FIRST: 1 retraction" in out


# CHEATS R23: a review signed by a human that the human did not write or approve -----------------------------------

UNDERSTOOD = ("review", "add-zero", "--kind", "understanding", "--author", "human:ada",
              "--text", "I can redo the induction on n myself.")


def test_a_human_author_needs_the_humans_approval(research_repo, monkeypatch, capsys):
    from verifylab.commands import review
    root = research_repo
    monkeypatch.chdir(root)
    rc, _, err = vl(capsys, *UNDERSTOOD)                      # an agent signing for the human, no terminal to ask
    assert rc == 2 and "--human-approved" in err and review_files(root) == []
    assert vl(capsys, *UNDERSTOOD, "--dry-run")[0] == 0 and review_files(root) == []
    monkeypatch.setattr(review, "_interactive", lambda: True)
    monkeypatch.setattr("sys.stdin", __import__("io").StringIO("n\n"))
    rc, _, err = vl(capsys, *UNDERSTOOD)
    assert rc == 2 and "Did ada write or approve it?" in err and review_files(root) == []
    monkeypatch.setattr("sys.stdin", __import__("io").StringIO("y\n"))
    assert vl(capsys, *UNDERSTOOD)[0] == 0 and len(review_files(root)) == 1


def test_an_approved_human_review_and_an_agent_review_need_no_question(research_repo, monkeypatch, capsys):
    root = research_repo
    monkeypatch.chdir(root)
    assert vl(capsys, *UNDERSTOOD, "--human-approved")[0] == 0
    agent = [a if a != "human:ada" else "agent:helper" for a in UNDERSTOOD]
    assert vl(capsys, *agent)[0] == 0 and len(review_files(root)) == 2


def test_with_the_approval_guard_disabled_a_forged_human_review_is_written(research_repo, monkeypatch, capsys):
    from verifylab.commands import review
    root = research_repo
    monkeypatch.chdir(root)
    monkeypatch.setattr(review, "human_approval_problem", lambda args: None)
    assert vl(capsys, *UNDERSTOOD)[0] == 0 and len(review_files(root)) == 1
