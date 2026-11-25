from __future__ import annotations

import pytest

from conftest import synthetic_receipt

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
                  verdict="pass", reasons=[], inputs={"digest": "d", "files": {"x": "d" * 64}}, environment={},
                  checked={}, command=["vl"], started_at="t0", finished_at="t1", tool_version="vl 0.1.0")
    fields.update(over)
    return synthetic_receipt(None, fields)


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


@pytest.mark.parametrize("change, message", [
    (lambda r: r.pop("target"), "target"),
    (lambda r: r.update(checked={}), "checked.kernels"),
    (lambda r: r.update(environment={}), "environment"),
    (lambda r: r["inputs"].pop("trusted_files"), "trusted_files"),
    (lambda r: r["inputs"].pop("trusted_commit"), "trusted_commit"),
    (lambda r: r["inputs"].update(digest="sha256:" + "0" * 64), "inputs.digest"),
    (lambda r: r["inputs"]["files"].update({"../candidate.lean": "a" * 64}), "repository-relative"),
    (lambda r: r["inputs"]["files"].update(x="wrong"), "hex sha256"),
    (lambda r: r["target"].update(sha256="f" * 64), "target identity"),
    (lambda r: r["target"].update(commit="f" * 40), "trusted_commit"),
    (lambda r: r["target"].update(source="worktree"), "trusted_commit"),
    (lambda r: r["checked"].pop("kernels"), "checked.kernels"),
    (lambda r: r["checked"].update(kernels=[]), "checked.kernels"),
    (lambda r: r["checked"].update(theorems=["Other.main"]), "checked.theorems"),
    (lambda r: r["environment"]["tools"]["comparator"].pop("sha256"), "tools.comparator"),
    (lambda r: r.update(adapter="unknown"), "unknown adapter"),
])
def test_resealed_incomplete_or_incoherent_protected_pass_is_rejected(change, message):
    receipt = _receipt()
    assert receipt_problems(receipt) == []      # valid control
    change(receipt)
    assert any(message in p for p in receipt_problems(seal_receipt(receipt)))


@pytest.mark.parametrize("verdict, assurance", [("fail", "protected"), ("error", "protected"),
                                              ("unsupported", "protected"), ("pass", "exploratory")])
def test_partial_nonverifying_receipts_remain_readable(verdict, assurance):
    receipt = _receipt(verdict=verdict, assurance=assurance)
    receipt.pop("target")
    receipt["inputs"].pop("trusted_files")
    receipt["inputs"].pop("trusted_commit")
    receipt.update(environment={}, checked={})
    assert receipt_problems(seal_receipt(receipt)) == []


def _python_receipt():
    receipt = _receipt()
    candidate = next(iter(receipt["inputs"]["files"]))
    evaluator = "research/evaluators/e.py"
    receipt["adapter"] = "python-eval"
    receipt["target"] = {"evaluator": evaluator, "sha256": "b" * 64, "source": "trusted-commit",
                         "commit": receipt["inputs"]["trusted_commit"], "candidate": candidate, "entry": "solve",
                         "cases_sha256": "c" * 64}
    receipt["inputs"]["trusted_files"] = {evaluator: "b" * 64}
    receipt["checked"] = {"cases": 2, "passed": 2, "failed": 0, "judge_errors": 0, "cases_sha256": "c" * 64}
    receipt["environment"] = {"interpreter": "/usr/bin/python3", "python_version": "Python fixture",
                              "isolation": {"candidate": "jail", "judge": "jail"}}
    from verifylab.records import canonical_json, sha256_hex
    basis = {key: receipt["inputs"][key] for key in ("files", "trusted_files")}
    basis["target"] = receipt["target"]
    receipt["inputs"]["digest"] = "sha256:" + sha256_hex(canonical_json(basis).encode())
    return seal_receipt(receipt)


@pytest.mark.parametrize("change, message", [
    (lambda r: r["checked"].update(cases=0), "checked.cases"),
    (lambda r: r["checked"].update(cases=True), "checked.cases"),
    (lambda r: r["checked"].update(passed=1), "checked.cases"),
    (lambda r: r["checked"].update(failed=1), "checked.cases"),
    (lambda r: r["checked"].update(judge_errors=1), "checked.cases"),
    (lambda r: r["checked"].update(cases_sha256="d" * 64), "checked.cases_sha256"),
    (lambda r: r["target"].update(candidate="missing.py"), "target.candidate"),
    (lambda r: r["target"].update(entry="not an identifier"), "target.entry"),
    (lambda r: r["environment"].pop("interpreter"), "environment.interpreter"),
])
def test_protected_python_pass_requires_coherent_case_coverage(change, message):
    receipt = _python_receipt()
    assert receipt_problems(receipt) == []
    change(receipt)
    assert any(message in p for p in receipt_problems(seal_receipt(receipt)))


def test_disabling_protected_pass_validation_lets_the_bad_digest_through(monkeypatch):
    from verifylab import records
    receipt = _receipt()
    receipt["inputs"]["digest"] = "sha256:" + "0" * 64
    receipt = seal_receipt(receipt)
    assert any("inputs.digest" in p for p in receipt_problems(receipt))
    monkeypatch.setattr(records, "_protected_pass_problems", lambda record: [])
    assert receipt_problems(receipt) == []


def test_t0_new_profile_requires_external_kernel_without_any_date_cutoff(monkeypatch):
    """T0: Newly issued protected Lean passes require an external kernel (nanoda), identified strictly by receipt
    profile (never by a timestamp/date cutoff), while historical passes keep their profile acceptance."""
    from verifylab import records
    from verifylab.config import RULES_INPUT
    from verifylab.records import canonical_json, historical_kernel_profile, sha256_hex

    def with_single_kernel(profile: str, stamp: str):
        r = _receipt(started_at=stamp, finished_at=stamp)
        r["checked"]["kernels"] = ["lean"]
        t_entry = {r["target"]["path"]: r["target"]["sha256"]}
        if profile == "historical":
            r["inputs"]["trusted_files"] = {**t_entry, "research/vl.toml": "b" * 64}
            r["environment"].pop("packages", None)
            r["checked"].pop("definitions_mode", None)
            r["checked"].pop("probe_run", None)
        elif profile == "modern-rules":
            r["inputs"]["trusted_files"] = {**t_entry, RULES_INPUT: "b" * 64}
        elif profile == "modern-identity":
            r["inputs"]["trusted_files"] = {**t_entry, "research/vl.toml": "b" * 64}
            r["environment"]["packages"] = {"dir": "/pkg", "identity": {"revisions": {}, "traces": {}}}
        elif profile == "modern-defmode":
            r["inputs"]["trusted_files"] = {**t_entry, "research/vl.toml": "b" * 64}
            r["environment"].pop("packages", None)
            r["checked"]["definitions_mode"] = "shared: target closure identical"
        basis = {"files": r["inputs"]["files"], "trusted_files": r["inputs"]["trusted_files"], "target": r["target"]}
        r["inputs"]["digest"] = "sha256:" + sha256_hex(canonical_json(basis).encode())
        return seal_receipt(r)

    # Historical profile with single kernel is still structurally valid regardless of timestamp (even 2099)
    hist_future = with_single_kernel("historical", "2099-01-01T00:00:00+00:00")
    assert historical_kernel_profile(hist_future) is True
    assert receipt_problems(hist_future) == []

    # Modern profiles with single kernel are ALWAYS rejected regardless of timestamp (even 2020)
    for prof in ("modern-rules", "modern-identity", "modern-defmode"):
        modern_old_date = with_single_kernel(prof, "2020-01-01T00:00:00+00:00")
        assert historical_kernel_profile(modern_old_date) is False, prof
        probs = receipt_problems(modern_old_date)
        assert any("external kernel" in p and "nanoda" in p for p in probs), (prof, probs)
        # Clean control: adding nanoda makes the modern receipt valid
        modern_ok = dict(modern_old_date)
        modern_ok["checked"] = {**modern_old_date["checked"], "kernels": ["lean", "nanoda"]}
        assert receipt_problems(seal_receipt(modern_ok)) == []

    # Planted guard-off check: disabling PROTECTED_EXTERNAL_KERNELS lets the modern single-kernel pass slip through
    bad_modern = with_single_kernel("modern-rules", "2020-01-01T00:00:00+00:00")
    assert receipt_problems(bad_modern)
    monkeypatch.setattr(records, "PROTECTED_EXTERNAL_KERNELS", ())
    assert receipt_problems(bad_modern) == []
