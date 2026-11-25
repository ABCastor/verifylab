from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path

from verifylab.cli import main
from verifylab.records import seal_review, write_new_json

from conftest import commit_all, git
from test_cmd_show import make_item, make_receipt


def vl(capsys, *args):
    rc = main(list(args))
    captured = capsys.readouterr()
    return rc, captured.out, captured.err


def _faithful_review(root: Path, item_id: str) -> None:
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        assert main(["show", item_id, "--json"]) == 0
    card = json.loads(buf.getvalue())["items"][0]
    with contextlib.redirect_stdout(io.StringIO()):
        rc = main([
            "review", item_id, "--kind", "fidelity", "--verdict", "faithful",
            "--expected-meaning-digest", card["meaning_digest"],
            "--text", "Faithful formalization.", "--author", "agent:referee",
        ])
    assert rc == 0


def test_status_clean_and_human_labels(research_repo, monkeypatch, capsys):
    root = research_repo
    monkeypatch.chdir(root)
    rc, out, err = vl(capsys, "status")
    assert rc == 0 and err == "", err
    assert "Lanes: 0/3 open" in out
    assert "By status:" in out

    rc, out, err = vl(capsys, "status", "--json")
    assert rc == 0, err
    data = json.loads(out)
    assert data["counts"]["total"] >= 1
    assert data["triage"] == {
        "needs_check": [],
        "pending_admission": [],
        "stale": [],
        "failed": [],
        "missing_fidelity": [],
        "superseded": [],
    }
    assert data["lanes"]["open"] == [] and data["lanes"]["max_parallel"] == 3
    assert data["worktree"]["edits"] == []


def test_status_triage_buckets_and_controls(research_repo, monkeypatch, capsys):
    root = research_repo
    monkeypatch.chdir(root)
    make_item(root, "add-zero", kind="result", claim="formal", statement="For every n, n + 0 = n.",
              lean={"target": "Fixture/Basic.lean", "theorems": ["add_zero'"]})
    commit_all(root, "give add-zero a formal target")

    unchecked = json.loads(vl(capsys, "status", "--json")[1])["triage"]
    assert unchecked["needs_check"] == unchecked["missing_fidelity"] == ["add-zero"]

    # 1. Pending admission (uncommitted protected pass) -> then admitted (verified, missing fidelity)
    r_path = make_receipt(root, "add-zero")
    data = json.loads(vl(capsys, "status", "--json")[1])
    assert "add-zero" in data["triage"]["pending_admission"]
    assert "add-zero" in data["triage"]["missing_fidelity"]
    assert str(r_path.relative_to(root)) in data["worktree"]["edits"]

    # Admit receipt: leaves pending_admission, still missing fidelity
    commit_all(root, "admit pass")
    data = json.loads(vl(capsys, "status", "--json")[1])
    assert data["triage"]["pending_admission"] == []
    assert "add-zero" in data["triage"]["missing_fidelity"]

    # Control: add faithful fidelity review -> leaves missing_fidelity
    _faithful_review(root, "add-zero")
    commit_all(root, "admit fidelity")
    data = json.loads(vl(capsys, "status", "--json")[1])
    assert "add-zero" not in data["triage"]["missing_fidelity"]
    assert "check passed" in vl(capsys, "status")[1]

    # 2. Failed item
    make_item(root, "bad-lem", kind="result", claim="formal", statement="Fails.",
              lean={"target": "Fixture/Basic.lean", "theorems": ["add_zero'"]})
    commit_all(root, "bad item")
    make_receipt(root, "bad-lem", verdict="fail")
    commit_all(root, "admit fail")

    # 3. Superseded item (add verified successor)
    make_item(root, "add-zero-v2", kind="result", claim="formal", statement="Successor.",
              supersedes=["add-zero"],
              lean={"target": "Fixture/Basic.lean", "theorems": ["add_zero'"]})
    commit_all(root, "successor")
    make_receipt(root, "add-zero-v2")
    commit_all(root, "admit successor")
    data = json.loads(vl(capsys, "status", "--json")[1])
    assert "add-zero" in data["triage"]["superseded"]

    # 4. Stale receipt + worktree edit
    (root / "Fixture" / "Basic.lean").write_text("-- modified in worktree\n")
    data = json.loads(vl(capsys, "status", "--json")[1])
    assert "add-zero" in data["triage"]["stale"]
    assert "bad-lem" in data["triage"]["failed"]
    assert "Fixture/Basic.lean" in data["worktree"]["edits"]


def test_status_reports_open_lanes_and_commits_ahead(research_repo, monkeypatch, capsys):
    root = research_repo
    (root / ".gitignore").write_text(".lake/\n")
    git(root, "add", ".gitignore")
    git(root, "commit", "-q", "-m", "ignore lake")
    monkeypatch.chdir(root)

    assert vl(capsys, "lane", "new", "l1")[0] == 0
    git(root, "checkout", "-q", "-b", "topic")
    (root / "README-topic.txt").write_text("hi\n")
    git(root, "add", "README-topic.txt")
    git(root, "commit", "-q", "-m", "ahead commit")

    rc, out, err = vl(capsys, "status", "--trusted-ref", "trusted")
    assert rc == 0, err
    assert "Lanes: 1/3 open (l1)" in out
    assert "1 commit(s) ahead of trusted" in out
    data = json.loads(vl(capsys, "status", "--trusted-ref", "trusted", "--json")[1])
    assert data["lanes"]["open"] == ["l1"] and data["git"]["ahead_of_trusted"] == 1
    assert vl(capsys, "lane", "done", "l1")[0] == 0
    data = json.loads(vl(capsys, "status", "--trusted-ref", "trusted", "--json")[1])
    assert data["lanes"]["open"] == []
