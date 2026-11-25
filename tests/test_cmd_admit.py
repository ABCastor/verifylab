"""`vl admit`: commit only new receipts and reviews, only when `vl validate` passes, on the trusted branch, without
pushing. Each refusal has a planted case next to a clean control that is admitted."""

from __future__ import annotations

import json

import pytest

from verifylab import cli

from batch_helpers import add_lean_items, write_receipt, write_review
from conftest import git


@pytest.fixture
def repo(research_repo, monkeypatch):
    add_lean_items(research_repo, "t1", "t2")
    monkeypatch.chdir(research_repo)
    return research_repo


def head(root) -> str:
    return git(root, "rev-parse", "HEAD").strip()


def refused(root, capsys, *argv) -> str:
    """Run `vl admit`: it must exit 2, commit nothing and stage nothing; its message."""
    before = head(root)
    assert cli.main(["admit", *argv]) == 2
    assert head(root) == before and git(root, "diff", "--cached", "--name-only") == ""
    return capsys.readouterr().err


def test_admit_commits_the_new_receipts_and_reviews_with_their_counts(repo, capsys):
    receipts = [write_receipt(repo, "t1"), write_receipt(repo, "t2", "fail")]
    review = write_review(repo, "t1")
    before = head(repo)
    assert cli.main(["admit", "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["title"] == "Admit 2 receipts and 1 review" and out["commit"] == head(repo) != before
    assert git(repo, "log", "-1", "--format=%s%n%b").strip() == "Admit 2 receipts and 1 review"
    assert sorted(git(repo, "show", "--name-only", "--format=", "HEAD").split()) == sorted(
        str(p.relative_to(repo)) for p in [*receipts, review])
    assert git(repo, "status", "--porcelain") == ""


def test_the_title_names_only_what_is_there(repo, capsys):
    write_receipt(repo, "t1")
    assert cli.main(["admit"]) == 0
    assert git(repo, "log", "-1", "--format=%s").strip() == "Admit 1 receipt"
    write_review(repo, "t1")
    assert cli.main(["admit"]) == 0
    assert git(repo, "log", "-1", "--format=%s").strip() == "Admit 1 review"


def test_nothing_new_commits_nothing(repo, capsys):
    before = head(repo)
    assert cli.main(["admit"]) == 0 and head(repo) == before
    assert "nothing to admit" in capsys.readouterr().out


def test_admit_refuses_when_another_file_changed_and_admits_once_it_is_clean(repo, capsys):
    write_receipt(repo, "t1")
    (repo / "Fixture" / "Basic.lean").write_text("-- edited\n")                  # tracked file modified
    assert "Fixture/Basic.lean (changed)" in refused(repo, capsys)
    git(repo, "checkout", "--", "Fixture/Basic.lean")
    (repo / "notes.txt").write_text("stray\n")                                    # untracked file elsewhere
    assert "notes.txt (new)" in refused(repo, capsys)
    (repo / "notes.txt").unlink()
    (repo / "research" / "evidence" / "t1" / "readme.md").write_text("x\n")       # not a .json record
    assert "readme.md (new)" in refused(repo, capsys)
    (repo / "research" / "evidence" / "t1" / "readme.md").unlink()
    assert cli.main(["admit"]) == 0                                               # control: same receipt, clean tree


def test_admit_refuses_a_new_item_or_a_changed_target(repo, capsys):
    write_receipt(repo, "t1")
    (repo / "research" / "items" / "t3.md").write_text(
        (repo / "research" / "items" / "t1.md").read_text().replace('id = "t1"', 'id = "t3"'))
    assert "research/items/t3.md (new)" in refused(repo, capsys)


def test_admit_refuses_a_modified_or_deleted_existing_record(repo, capsys):
    old = write_receipt(repo, "t1")
    assert cli.main(["admit"]) == 0
    write_receipt(repo, "t2")
    text = old.read_text()
    old.write_text(text.replace('"pass"', '"fail"', 1))                           # an admitted receipt rewritten
    assert f"{old.relative_to(repo)} (changed)" in refused(repo, capsys)
    old.write_text(text)
    old.unlink()                                                                  # or deleted
    assert f"{old.relative_to(repo)} (changed)" in refused(repo, capsys)
    git(repo, "checkout", "--", str(old.relative_to(repo)))
    assert cli.main(["admit"]) == 0                                               # control: only the new one is left
    assert git(repo, "log", "-1", "--format=%s").strip() == "Admit 1 receipt"


def test_admit_refuses_when_validate_fails_and_stages_nothing(repo, capsys):
    path = write_receipt(repo, "t1")
    write_receipt(repo, "t2")
    path.write_text(path.read_text().replace('"verdict": "pass"', '"verdict": "fail"'))        # the seal no longer fits
    err = refused(repo, capsys)
    assert "vl validate reports errors" in err and "ERROR" in err
    path.unlink()
    assert cli.main(["admit"]) == 0                                               # control: validate passes


def test_admit_refuses_off_the_trusted_branch(repo, capsys):
    git(repo, "checkout", "-q", "-b", "lane/x")
    write_receipt(repo, "t1")
    assert "HEAD is not the trusted branch" in refused(repo, capsys)
    git(repo, "checkout", "-q", "--detach", "trusted")
    assert "HEAD is not the trusted branch" in refused(repo, capsys)                # detached at the trusted commit
    git(repo, "checkout", "-q", "trusted")
    assert cli.main(["admit"]) == 0                                               # control


def test_admit_takes_the_trusted_ref_flag_but_not_a_different_head(repo, capsys):
    write_receipt(repo, "t1")
    assert "HEAD is not the trusted branch" in refused(repo, capsys, "--trusted-ref", "HEAD~1")


def test_admit_does_not_push(repo, tmp_path, capsys):
    remote = tmp_path / "remote.git"
    git(tmp_path, "init", "-q", "--bare", str(remote))
    git(repo, "remote", "add", "origin", str(remote))
    write_receipt(repo, "t1")
    assert cli.main(["admit"]) == 0
    assert git(remote, "branch", "--list") == ""


def test_a_failing_commit_leaves_the_records_in_the_worktree_unstaged(repo, capsys, monkeypatch):
    from verifylab import gitref
    path = write_receipt(repo, "t1")
    real = gitref.git

    def refuse_commit(root, *args, **kwargs):
        if args and args[0] == "commit":
            raise gitref.GitError("controlled commit failure")
        return real(root, *args, **kwargs)

    monkeypatch.setattr(gitref, "git", refuse_commit)
    before = head(repo)
    assert cli.main(["admit"]) == 2
    assert head(repo) == before and git(repo, "diff", "--cached", "--name-only") == "" and path.exists()
