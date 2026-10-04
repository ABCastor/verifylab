from __future__ import annotations

import json

from verifylab.cli import main
from verifylab.records import seal_receipt, sha256_hex, write_new_json

from conftest import commit_all, item_question

NS_LEAN = """/- A block comment that mentions
   theorem decoy_in_comment : True := trivial -/
namespace Fixture
-- theorem decoy_in_line_comment : True := trivial
@[simp] theorem double_add (n : Nat) : n + n = 2 * n := by omega
namespace Inner
noncomputable def double (n : Nat) : Nat := 2 * n
private lemma double_helper : True := trivial
end Inner
section Local
theorem double_comm (a b : Nat) : a + b = b + a := Nat.add_comm a b
end Local
end Fixture
"""


def vl(capsys, *args):
    rc = main(list(args))
    captured = capsys.readouterr()
    return rc, captured.out, captured.err


def test_find_items_by_statement_and_limits(research_repo, monkeypatch, capsys):
    monkeypatch.chdir(research_repo)
    rc, out, _ = vl(capsys, "find", "natural numbers")
    assert rc == 0
    assert "add-zero@" in out and "[result/formal; status: unverified]" in out and "A small result" in out
    rc, out, _ = vl(capsys, "find", "every", "--json")
    data = json.loads(out)
    assert [i["id"] for i in data["items"]] == ["add-zero"] and data["items"][0]["status"] == "unverified"


def test_find_lean_declarations_with_namespaces(research_repo, monkeypatch, capsys):
    root = research_repo
    (root / "Fixture" / "Ns.lean").write_text(NS_LEAN)
    monkeypatch.chdir(root)
    rc, out, _ = vl(capsys, "find", "double", "--kind", "lean")
    assert rc == 0
    assert "Fixture.Inner.double  def  Fixture/Ns.lean:7" in out
    assert "Fixture.double_add  theorem  Fixture/Ns.lean:5" in out
    assert "Fixture.Inner.double_helper  lemma  Fixture/Ns.lean:8" in out
    assert "Fixture.double_comm  theorem  Fixture/Ns.lean:11" in out
    assert "decoy" not in out and "items (" not in out
    rc, out, _ = vl(capsys, "find", "add_zero'", "--json")
    decls = json.loads(out)["declarations"]
    assert decls[0] == {"name": "add_zero'", "kind": "theorem", "path": "Fixture/Basic.lean", "line": 1}
    assert vl(capsys, "find", "decoy")[0] == 1


def test_find_ranks_exact_names_first_and_says_when_it_cut(research_repo, monkeypatch, capsys):
    root = research_repo
    (root / "Fixture" / "Ns.lean").write_text(NS_LEAN)
    monkeypatch.chdir(root)
    rc, out, _ = vl(capsys, "find", "Fixture.Inner.double", "--kind", "lean", "--limit", "1")
    lines = out.splitlines()
    assert lines[0] == "lean declarations (1 of 2):" and lines[1].startswith("  Fixture.Inner.double  def")
    assert "[MORE: 1 more declarations match; raise --limit to see them]" in out


def test_find_kind_filter_and_no_match(research_repo, monkeypatch, capsys):
    monkeypatch.chdir(research_repo)
    rc, out, _ = vl(capsys, "find", "natural", "--kind", "question")
    assert rc == 1 and "no match" in out
    rc, out, _ = vl(capsys, "find", "zzzz-not-there")
    assert rc == 1 and out.startswith("no match for 'zzzz-not-there'")
    assert vl(capsys, "find", "  ")[0] == 2


def test_find_status_comes_from_records_not_the_index(research_repo, monkeypatch, capsys):
    root = research_repo
    monkeypatch.chdir(root)
    assert "status: unverified" in vl(capsys, "find", "natural")[1]
    lean = "Fixture/Basic.lean"
    receipt = seal_receipt(dict(
        item="add-zero", item_revision="a" * 40, question_digest=item_question(root, "add-zero"),
        adapter="lean-comparator", assurance="protected", verdict="pass",
        reasons=[], inputs={"digest": "d", "files": {lean: sha256_hex((root / lean).read_bytes())}},
        environment={}, checked={}, command=["vl"], started_at="t0", finished_at="t1", tool_version="vl 0.1.0"))
    write_new_json(root / "research" / "evidence" / "add-zero" / f"{receipt['receipt_id'][:16]}.json", receipt)
    commit_all(root)
    rc, out, _ = vl(capsys, "find", "natural")
    assert "status: verified]" in out and "index rebuilt" not in out  # same index, fresh status
