from __future__ import annotations

import json
from pathlib import Path

from verifylab.cli import main
from verifylab.records import seal_receipt, sha256_hex, write_new_json

from conftest import synthetic_receipt, commit_all, item_question

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


def write_reference_only_source(root):
    path = root / "research" / "items" / "reference-only.md"
    path.write_text(
        '''+++
id = "reference-only"
kind = "source"
title = "A bibliographic source"
author = "agent:test"
created = "2026-10-01"
ref = "DOI:10.5555/verifylab.2026.001; arXiv:2601.12345"
access = "citation-only"
+++
Metadata-only source.
'''
    )
    return path


def test_find_items_by_statement_and_limits(research_repo, monkeypatch, capsys):
    monkeypatch.chdir(research_repo)
    rc, out, _ = vl(capsys, "find", "natural numbers")
    assert rc == 0
    assert "add-zero@" in out and "[result/formal; status: unverified]" in out and "A small result" in out
    rc, out, _ = vl(capsys, "find", "every", "--json")
    data = json.loads(out)
    assert [i["id"] for i in data["items"]] == ["add-zero"] and data["items"][0]["status"] == "unverified"


def test_find_source_hits_show_reference_and_access(research_repo, monkeypatch, capsys):
    root = research_repo
    write_reference_only_source(root)
    monkeypatch.chdir(root)

    for identifier in ("10.5555/verifylab.2026.001", "arXiv:2601.12345"):
        rc, out, _ = vl(capsys, "find", identifier)
        assert rc == 0
        assert "[ref: DOI:10.5555/verifylab.2026.001; arXiv:2601.12345; access: citation-only]" in out

        rc, out, _ = vl(capsys, "find", identifier, "--json")
        data = json.loads(out)
        assert rc == 0
        assert data["items"][0]["ref"] == "DOI:10.5555/verifylab.2026.001; arXiv:2601.12345"
        assert data["items"][0]["access"] == "citation-only"

    rc, out, _ = vl(capsys, "find", "natural", "--json")
    data = json.loads(out)
    assert rc == 0 and data["items"][0]["kind"] == "result"
    assert data["items"][0]["ref"] is None and data["items"][0]["access"] is None
    rc, out, _ = vl(capsys, "find", "natural")
    assert rc == 0 and "[ref:" not in out and "access:" not in out


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
    receipt = synthetic_receipt(root, dict(
        item="add-zero", item_revision="a" * 40, question_digest=item_question(root, "add-zero"),
        adapter="lean-comparator", assurance="protected", verdict="pass",
        reasons=[], inputs={"digest": "d", "files": {lean: sha256_hex((root / lean).read_bytes())}},
        environment={}, checked={}, command=["vl"], started_at="t0", finished_at="t1", tool_version="vl 0.1.0"))
    write_new_json(root / "research" / "evidence" / "add-zero" / f"{receipt['receipt_id'][:16]}.json", receipt)
    commit_all(root)
    rc, out, _ = vl(capsys, "find", "natural")
    assert "status: check passed]" in out and "index rebuilt" not in out  # same index, fresh status


def test_find_notes_live_with_locator_and_regex(research_repo, monkeypatch, capsys):
    root = research_repo
    plan = root / "research" / "plans" / "main.md"
    plan.parent.mkdir()
    plan.write_text("# Routes\nA useful negative result: failed compactness route.\n")
    monkeypatch.chdir(root)
    rc, out, _ = vl(capsys, "find", "negative result", "--scope", "notes", "--json")
    data = json.loads(out)
    assert rc == 0 and data["notes"][0]["locator"] == "research/plans/main.md:2"
    assert data["index"] is None and not (root / ".vl-cache" / "index.sqlite").exists()
    plan.write_text("# Routes\nNew opportunity: compactness bridge.\n")
    assert vl(capsys, "find", "negative result", "--scope", "notes")[0] == 1
    assert vl(capsys, "find", "opportunity:.*bridge", "--regex", "--scope", "notes")[0] == 0
    assert vl(capsys, "find", "[", "--regex", "--scope", "notes")[0] == 2
    assert vl(capsys, "find", "bridge", "--regex")[0] == 2
    assert vl(capsys, "find", "bridge", "--scope", "notes", "--kind", "source")[0] == 2


def test_find_registered_context_and_safe_note_scope(research_repo, tmp_path, monkeypatch, capsys):
    root = research_repo
    custom = root / "notes"
    custom.mkdir()
    (custom / "goal.md").write_text("Custom objective trace.\n")
    outside = tmp_path / "private.md"
    outside.write_text("Private secret trace.\n")
    (custom / "escape.md").symlink_to(outside)
    config = root / "research" / "vl.toml"
    config.write_text(config.read_text() + '\n[context]\nproblem = "notes/goal.md"\nnotes = ["notes"]\n')
    commit_all(root)
    monkeypatch.chdir(root)
    rc, out, _ = vl(capsys, "find", "trace", "--scope", "notes", "--json")
    data = json.loads(out)
    assert rc == 0 and [row["path"] for row in data["notes"]] == ["notes/goal.md"]
    assert "Private secret" not in out and any("escapes" in warning for warning in data["warnings"])


def test_find_reviews_searches_admitted_and_proposed_without_cache(research_repo, monkeypatch, capsys):
    root = research_repo
    monkeypatch.chdir(root)
    assert vl(capsys, "review", "add-zero", "--kind", "understanding", "--author", "agent:reader",
              "--text", "Unresolved objection about reuse.")[0] == 0
    rc, out, _ = vl(capsys, "find", "objection reuse", "--scope", "reviews", "--json")
    data = json.loads(out)
    assert rc == 0 and data["reviews"][0]["admitted"] is False
    path = data["reviews"][0]["path"]
    assert data["reviews"][0]["author"] == "agent:reader"
    commit_all(root)
    (root / path).unlink()
    rc, out, _ = vl(capsys, "find", "objection", "--scope", "reviews", "--json")
    assert rc == 0 and json.loads(out)["reviews"][0]["admitted"] is True


def test_find_classifier_output_retains_score_and_appraisal_basis(research_repo, monkeypatch, capsys):
    root = research_repo
    monkeypatch.chdir(root)
    note = root / "research" / "map.md"
    note.write_text("Selected source route.\n")
    payload = {"schema": "vl.appraisal.v1", "subject": "connection",
               "ratings": {"novelty": {"score": 1.4}}, "input_context": "Selected source route.",
               "context_files": ["research/map.md"], "raw_output": {"native_marker": "classifier-choice"}}
    assessment = root / "assessment.json"
    assessment.write_text(json.dumps(payload))
    assert vl(capsys, "review", "add-zero", "--kind", "appraisal", "--author", "agent:classifier",
              "--assessment", str(assessment))[0] == 0
    rc, out, _ = vl(capsys, "find", "classifier-choice", "--scope", "reviews", "--json")
    row = json.loads(out)["reviews"][0]
    assert rc == 0 and row["assessment"]["ratings"]["novelty"] == {"score": 1.4}
    assert row["text"] == "" and row["applicability"]["state"] == "current"
    assert "novelty: 1.4; basis current" in vl(capsys, "find", "classifier-choice", "--scope", "reviews")[1]
    note.write_text("Changed source route.\n")
    assert "basis stale" in vl(capsys, "find", "classifier-choice", "--scope", "reviews")[1]


def test_find_scope_warns_large_and_invalid_notes_and_reviews(research_repo, monkeypatch, capsys):
    root = research_repo
    monkeypatch.chdir(root)
    (root / "research" / "map.md").write_bytes(b"trace" + b"x" * (2 * 1024 * 1024))
    (root / "research" / "ideas.md").write_bytes(b"\xfftrace")
    bad = root / "research" / "reviews" / "add-zero"
    bad.mkdir(parents=True)
    (bad / "bad.json").write_text('{"text": "trace"}')
    rc, out, _ = vl(capsys, "find", "trace", "--json")
    data = json.loads(out)
    assert rc == 1 and data["notes"] == [] and data["reviews"] == []
    assert any("larger" in message for message in data["warnings"])
    assert any("not searched" in message for message in data["warnings"])
    assert any("invalid review" in message for message in data["warnings"])


def test_find_note_scope_reports_unreadable_subdirectories(research_repo, monkeypatch, capsys):
    import errno
    from verifylab import context

    root = research_repo
    blocked = root / "research" / "inaccessible"
    blocked.mkdir()
    (blocked / "lost.md").write_text("A missing bridge.\n")
    (root / "research" / "map.md").write_text("A readable bridge.\n")
    scandir = context.os.scandir

    def denied(path):
        if Path(path) == blocked:
            raise PermissionError(errno.EACCES, "Permission denied", str(blocked))
        return scandir(path)

    monkeypatch.setattr(context.os, "scandir", denied)
    monkeypatch.chdir(root)
    rc, out, _ = vl(capsys, "find", "bridge", "--scope", "notes", "--json")
    data = json.loads(out)
    assert rc == 0 and [row["path"] for row in data["notes"]] == ["research/map.md"]
    assert any("inaccessible" in warning and "not searched" in warning for warning in data["warnings"])


def test_find_note_count_includes_only_files_actually_searched(research_repo, monkeypatch, capsys):
    root = research_repo
    (root / "research" / "map.md").write_text("A readable bridge.\n")
    (root / "research" / "ideas.md").write_bytes(b"\xffunreadable bridge")
    monkeypatch.chdir(root)
    rc, out, _ = vl(capsys, "find", "bridge", "--scope", "notes", "--json")
    data = json.loads(out)
    assert rc == 0 and data["note_files"] == 1
    assert any("ideas.md" in warning and "not searched" in warning for warning in data["warnings"])
