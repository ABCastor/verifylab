from __future__ import annotations

import json
from pathlib import Path

from verifylab.cli import main

from conftest import commit_all
from test_cmd_show import make_item, make_receipt

ROOT = Path(__file__).resolve().parents[1]


def vl(capsys, *args):
    rc = main(list(args))
    captured = capsys.readouterr()
    return rc, captured.out, captured.err


def test_e2_status_vocabulary_consistent_across_text_json_docs_and_skills(research_repo, monkeypatch, capsys):
    root = research_repo
    monkeypatch.chdir(root)
    make_receipt(root, "add-zero")
    commit_all(root, "admit add-zero")
    make_item(root, "succ", kind="result", claim="formal", statement="Successor.", supersedes=["add-zero"],
              lean={"target": "Fixture/Basic.lean", "theorems": ["add_zero'"]})
    commit_all(root, "propose succ")

    # Human text surfaces consistently show `check passed` and never leak `until verified` or `status: verified`
    card_out = vl(capsys, "show", "add-zero")[1]
    find_out = vl(capsys, "find", "every")[1]
    status_out = vl(capsys, "status")[1]
    assert "status: check passed;" in card_out and "not a replacement until check passed" in card_out
    assert "until verified" not in card_out and "status: verified" not in card_out
    assert "status: check passed]" in find_out and "status: verified" not in find_out
    assert "check passed" in status_out and "status: verified" not in status_out

    # JSON field names and machine labels stay stable (`verified`)
    card_json = json.loads(vl(capsys, "show", "add-zero", "--json")[1])
    find_json = json.loads(vl(capsys, "find", "every", "--json")[1])
    status_json = json.loads(vl(capsys, "status", "--json")[1])
    assert card_json["items"][0]["status"]["label"] == "verified"
    assert find_json["items"][0]["status"] == "verified"
    assert status_json["counts"]["by_status"]["verified"] == 1

    # The guide owns the mapping and the setup snippet points to the skill.
    guide = ROOT.joinpath("docs", "GUIDE.md").read_text()
    assert "verified" in guide and "check passed" in guide
    assert "verified-stale" in guide and "check passed, stale" in guide
