from __future__ import annotations

import json

from verifylab.cli import main
from verifylab.records import parse_item


def vl(capsys, *args):
    rc = main(list(args))
    captured = capsys.readouterr()
    return rc, captured.out, captured.err


def test_new_scaffolds_item_and_lean_target_that_pass_validate(research_repo, monkeypatch, capsys):
    root = research_repo
    monkeypatch.chdir(root)
    rc, out, err = vl(capsys, "new", "my-lemma", "--model", "gpt-5", "--tags", "area:Algebra")
    assert rc == 0 and err == ""
    assert "research/items/my-lemma.md" in out and "research/targets/my-lemma.lean" in out

    item_path = root / "research" / "items" / "my-lemma.md"
    target_path = root / "research" / "targets" / "my-lemma.lean"
    assert item_path.is_file() and target_path.is_file()

    item = parse_item(item_path.read_bytes(), item_path)
    assert item.kind == "conjecture"
    assert item.lean == {"target": "research/targets/my-lemma.lean", "theorems": ["VL.MyLemma.main"]}
    assert item.origin["agent"] == "agent:vl" and item.origin["model"] == "gpt-5"
    assert item.provenance == {"kind": "own"}
    assert ":= by\n  sorry" in target_path.read_text()

    v_rc, v_out, _ = vl(capsys, "validate")
    assert v_rc == 0 and "0 errors" in v_out, v_out


def test_new_result_and_explanation_json(research_repo, monkeypatch, capsys):
    root = research_repo
    monkeypatch.chdir(root)
    rc, out, _ = vl(capsys, "new", "sum-even", "--kind", "result", "--claim", "formal",
                      "--statement", "Sum of two evens is even.", "--uses", "add-zero", "--json")
    assert rc == 0
    data = json.loads(out)
    assert data["id"] == "sum-even" and data["theorems"] == ["VL.SumEven.main"]

    rc, _, _ = vl(capsys, "new", "overview-note", "--kind", "explanation", "--no-lean")
    assert rc == 0
    assert not (root / "research" / "targets" / "overview-note.lean").exists()

    v_rc, v_out, _ = vl(capsys, "validate")
    assert v_rc == 0 and "0 errors" in v_out, v_out


def test_new_refuses_existing_item_and_bad_inputs(research_repo, monkeypatch, capsys):
    root = research_repo
    monkeypatch.chdir(root)

    # Control: fresh id succeeds; planted existing id refuses without overwriting
    rc, _, err = vl(capsys, "new", "add-zero")
    assert rc == 2 and "already exists" in err

    (root / "research" / "targets").mkdir(parents=True, exist_ok=True)
    existing_target = root / "research" / "targets" / "taken.lean"
    existing_target.write_text("-- existing\n")
    rc, _, err = vl(capsys, "new", "taken")
    assert rc == 2 and "already exists" in err
    assert existing_target.read_text() == "-- existing\n"

    assert vl(capsys, "new", "Bad_Id")[0] == 2
    assert vl(capsys, "new", "ok-id", "--author", "badauthor")[0] == 2
    assert vl(capsys, "new", "ok-id", "--provenance", "upstream-reviewed")[0] == 2


def test_new_only_emits_supported_lean_names(research_repo, monkeypatch, capsys):
    monkeypatch.chdir(research_repo)
    assert vl(capsys, "new", "42", "--theorem", "good_name'")[0] == 0
    assert "namespace VL.Item42" in (research_repo / "research/targets/42.lean").read_text()
    for flag, value in (("--theorem", "123"), ("--theorem", "by"), ("--namespace", "Bad-Name")):
        assert vl(capsys, "new", "unused", flag, value)[0] == 2
        assert not (research_repo / "research/items/unused.md").exists()
