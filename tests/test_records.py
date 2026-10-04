from __future__ import annotations

import pytest

from verifylab.records import (
    RecordError, git_blob_sha, parse_item, receipt_problems, review_problems, seal_receipt, seal_review,
)

GOOD = b"""+++
id = "t1"
kind = "result"
title = "T1"
author = "agent:opus"
created = 2026-10-01
statement = "x"
claim = "formal"
+++
body
"""


def test_parse_valid_item_and_revision_matches_git():
    item = parse_item(GOOD, "research/items/t1.md")
    assert item.id == "t1" and item.created == "2026-10-01"
    assert item.revision == git_blob_sha(GOOD)


@pytest.mark.parametrize("bad, message", [
    (GOOD.replace(b'claim = "formal"\n', b""), "needs 'claim'"),
    (GOOD.replace(b'title = "T1"', b'title = "T1"\nstatus = "verified"'), "unknown field"),
    (GOOD.replace(b'id = "t1"', b'id = "T 1"'), "must match"),
    (GOOD.replace(b'author = "agent:opus"', b'author = "opus"'), "author"),
    (GOOD.replace(b"+++\nbody", b"body"), "not closed"),
])
def test_parse_rejects(bad, message):
    with pytest.raises(RecordError, match=message):
        parse_item(bad, "research/items/t1.md")


def test_file_name_must_match_id():
    with pytest.raises(RecordError, match="file name"):
        parse_item(GOOD, "research/items/other.md")


def _receipt(**over):
    fields = dict(item="t1", item_revision="a" * 40, adapter="lean-comparator", assurance="protected",
                  verdict="pass", reasons=[], inputs={"digest": "d", "files": {"x": "y"}}, environment={},
                  checked={}, command=["vl"], started_at="t0", finished_at="t1", tool_version="vl 0.1.0")
    fields.update(over)
    return seal_receipt(fields)


def test_receipt_self_hash_detects_edit():
    receipt = _receipt()
    assert receipt_problems(receipt) == []
    receipt["verdict"] = "fail"
    assert any("forged" in p for p in receipt_problems(receipt))


def test_receipt_rejects_unknown_verdict():
    assert any("verdict" in p for p in receipt_problems(_receipt(verdict="maybe")))


def test_a_stored_vote_is_not_a_known_review_kind():
    base = dict(item="t1", item_revision="a" * 40, kind="vote", author="human:ada", text="useful", created="t")
    assert any("kind 'vote' not in" in p for p in review_problems(seal_review({**base, "score": 8})))
    assert review_problems(seal_review({**base, "kind": "understanding"})) == []


# Malformed records are rejected and shown, never a crash -----------------------------------------------------------

def _show(root, monkeypatch, capsys):
    from verifylab.cli import main
    monkeypatch.chdir(root)
    rc = main(["show", "add-zero"])
    return rc, capsys.readouterr()


def test_a_rejected_review_with_a_list_text_is_shown_not_a_crash(research_repo, monkeypatch, capsys):
    import json
    from verifylab.records import seal_review
    review = seal_review(dict(item="add-zero", item_revision="a" * 40, kind="retraction", author="agent:x",
                              text=["not", "a", "string"], created="t"))
    path = research_repo / "research" / "reviews" / "add-zero" / f"{review['review_id'][:16]}.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(review))
    rc, captured = _show(research_repo, monkeypatch, capsys)
    assert rc == 0, captured.err
    assert "MALFORMED review, ignored ('text' must be str)" in captured.out


def test_a_timing_entry_without_its_milliseconds_is_rejected_not_a_crash(research_repo, monkeypatch, capsys):
    from conftest import item_question
    from verifylab.records import seal_receipt, sha256_hex, write_new_json, receipt_problems
    lean = "Fixture/Basic.lean"
    receipt = seal_receipt(dict(
        item="add-zero", item_revision="a" * 40, question_digest=item_question(research_repo), adapter="lean-comparator",
        assurance="protected", verdict="pass", reasons=[],
        inputs={"digest": "d", "files": {lean: sha256_hex((research_repo / lean).read_bytes())}},
        environment={}, checked={}, command=["vl"], started_at="t0", finished_at="t1", tool_version="vl 0.1.0",
        extra={"phases": {"prepare": 1.0}, "modules": [{"module": "Fixture.Basic"}]}))
    assert any("extra.modules" in p for p in receipt_problems(receipt))
    write_new_json(research_repo / "research" / "evidence" / "add-zero" / f"{receipt['receipt_id'][:16]}.json", receipt)
    rc, captured = _show(research_repo, monkeypatch, capsys)
    assert rc == 0 and "REJECTED" in captured.out, captured.err


def test_a_lone_surrogate_is_a_record_problem_not_a_crash(research_repo, monkeypatch, capsys):
    path = research_repo / "research" / "reviews" / "add-zero" / "0123456789abcdef.json"
    path.parent.mkdir(parents=True)
    path.write_text('{"schema": "vl.review/1", "review_id": "x", "item": "add-zero", "item_revision": "a", '
                    '"kind": "correction", "author": "agent:x", "text": "bad \\ud800 text", "created": "t"}')
    rc, captured = _show(research_repo, monkeypatch, capsys)
    assert rc == 0 and "not valid Unicode" in captured.out, captured.err
