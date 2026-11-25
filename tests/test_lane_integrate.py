from __future__ import annotations

import json
from pathlib import Path

from verifylab.cli import main

from conftest import commit_all, git
from test_cmd_show import make_item, make_receipt


def vl(capsys, *args):
    rc = main(list(args))
    captured = capsys.readouterr()
    return rc, captured.out, captured.err


def _lane_home(root: Path) -> Path:
    from verifylab.commands import lane
    from verifylab.config import load_config
    return lane.lanes_dir(load_config(root))


def test_lane_integrate_merges_and_lists_check_and_fidelity(research_repo, monkeypatch, capsys):
    root = research_repo
    (root / ".gitignore").write_text(".lake/\n")
    git(root, "add", ".gitignore")
    git(root, "commit", "-q", "-m", "ignore lake")
    monkeypatch.chdir(root)

    assert vl(capsys, "lane", "new", "feat")[0] == 0
    lane_wt = _lane_home(root) / "feat"
    (lane_wt / "research" / "targets").mkdir(parents=True, exist_ok=True)
    (lane_wt / "research" / "targets" / "new-thm.lean").write_text("theorem t : True := trivial\n")
    make_item(lane_wt, "new-thm", kind="result", claim="formal", statement="True holds.",
              lean={"target": "research/targets/new-thm.lean", "theorems": ["t"]})
    commit_all(lane_wt, "add new-thm in lane")

    # --no-merge dry run leaves HEAD untouched
    before = git(root, "rev-parse", "HEAD").strip()
    rc, out, err = vl(capsys, "lane", "integrate", "feat", "--no-merge", "--json")
    assert rc == 0 and err == "", err
    data = json.loads(out)
    assert data["merged"] is False and "new-thm" in data["needs_check"] and "new-thm" in data["needs_fidelity"]
    assert git(root, "rev-parse", "HEAD").strip() == before

    # Real merge integrates --no-ff and reports items needing check & fidelity
    rc, out, err = vl(capsys, "lane", "integrate", "feat")
    assert rc == 0, err
    assert "Integrated lane/feat into trusted." in out
    assert "Needs check: new-thm" in out
    assert "Needs fidelity: new-thm" in out
    assert (root / "research" / "items" / "new-thm.md").is_file()


def test_lane_integrate_f15_refuses_trusted_changes_unless_accepted(research_repo, monkeypatch, capsys):
    root = research_repo
    (root / ".gitignore").write_text(".lake/\n")
    git(root, "add", ".gitignore")
    git(root, "commit", "-q", "-m", "ignore lake")
    monkeypatch.chdir(root)

    assert vl(capsys, "lane", "new", "cfg")[0] == 0
    lane_wt = _lane_home(root) / "cfg"
    cfg = lane_wt / "research" / "vl.toml"
    cfg.write_text(cfg.read_text() + 'permitted_axioms = ["propext", "Quot.sound", "Classical.choice", "sorryAx"]\n')
    commit_all(lane_wt, "widen axioms")

    before = git(root, "rev-parse", "HEAD").strip()
    rc, _, err = vl(capsys, "lane", "integrate", "cfg")
    assert rc == 2 and "--accept-trusted-change" in err
    assert git(root, "rev-parse", "HEAD").strip() == before

    # Control: --accept-trusted-change proceeds and merges
    rc, out, err = vl(capsys, "lane", "integrate", "cfg", "--accept-trusted-change", "--json")
    assert rc == 0, err
    data = json.loads(out)
    assert data["merged"] is True and data["accepted_trusted_change"] is True
    assert data["trusted_changes"][0]["path"] == "research/vl.toml"


def test_lane_integrate_refuses_validation_errors_and_dirty_checkout(research_repo, monkeypatch, capsys):
    root = research_repo
    (root / ".gitignore").write_text(".lake/\n")
    git(root, "add", ".gitignore")
    git(root, "commit", "-q", "-m", "ignore lake")
    monkeypatch.chdir(root)

    assert vl(capsys, "lane", "new", "bad")[0] == 0
    lane_wt = _lane_home(root) / "bad"
    make_receipt(lane_wt, "add-zero")
    commit_all(lane_wt, "forged receipt")

    before = git(root, "rev-parse", "HEAD").strip()
    rc, _, err = vl(capsys, "lane", "integrate", "bad")
    assert rc == 2 and "validation errors" in err
    assert git(root, "rev-parse", "HEAD").strip() == before

    # Dirty checkout refuses before validating/merging
    assert vl(capsys, "lane", "new", "clean")[0] == 0
    lane_clean = _lane_home(root) / "clean"
    make_item(lane_clean, "clean-note", kind="explanation", body="Clean note.\n")
    commit_all(lane_clean, "clean item")
    (root / "Fixture" / "Basic.lean").write_text("-- dirty\n")
    rc, _, err = vl(capsys, "lane", "integrate", "clean")
    assert rc == 2 and "uncommitted changes" in err


def test_preview_does_not_assign_a_proof_to_a_note_on_diverged_branches(research_repo, monkeypatch, capsys):
    root = research_repo
    monkeypatch.chdir(root)
    git(root, "checkout", "-qb", "lane/note")
    make_item(root, "reader-note", kind="explanation", body="Source reading.")
    commit_all(root, "add a note")
    git(root, "checkout", "-q", "trusted")
    (root / "unrelated.txt").write_text("main branch change")
    commit_all(root, "independent change")
    before = git(root, "rev-parse", "HEAD")
    rc, out, err = vl(capsys, "lane", "integrate", "note", "--no-merge", "--json")
    assert rc == 0, err
    preview = json.loads(out)
    assert git(root, "rev-parse", "HEAD") == before
    rc, out, err = vl(capsys, "lane", "integrate", "note", "--json")
    assert rc == 0, err
    actual = json.loads(out)
    for key in ("needs_check", "needs_fidelity"):
        assert preview[key] == actual[key] == []


def test_integrate_uses_full_branch_name_and_aborts_a_linked_worktree_conflict(
        research_repo, monkeypatch, capsys):
    root = research_repo
    (root / "shared.txt").write_text("base\n")
    commit_all(root, "common content")
    git(root, "switch", "-qc", "parking")
    integrator = root.parent / "integrator"
    git(root, "worktree", "add", "-q", str(integrator), "trusted")
    git(root, "config", "vl.trustedRef", "refs/heads/trusted")
    monkeypatch.chdir(integrator)
    assert vl(capsys, "lane", "new", "conflict")[0] == 0
    incoming = _lane_home(integrator) / "conflict"
    (incoming / "shared.txt").write_text("from lane\n")
    commit_all(incoming, "change in lane")
    (integrator / "shared.txt").write_text("from trusted\n")
    commit_all(integrator, "change in trusted")
    original = git(integrator, "rev-parse", "HEAD")
    code, _, err = vl(capsys, "lane", "integrate", "conflict")
    assert code == 2 and "git merge failed" in err
    assert git(integrator, "rev-parse", "HEAD") == original
    assert not git(integrator, "status", "--porcelain")
    assert (integrator / "shared.txt").read_text() == "from trusted\n"

    assert vl(capsys, "lane", "new", "clean")[0] == 0
    other = _lane_home(integrator) / "clean"
    (other / "new.txt").write_text("from lane\n")
    commit_all(other, "a disjoint change")
    assert vl(capsys, "lane", "integrate", "clean")[0] == 0
    assert (integrator / "new.txt").read_text() == "from lane\n"
