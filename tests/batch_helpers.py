"""Records and items for the batch and admit tests: a repository of several Lean-table items on the trusted branch."""

from __future__ import annotations

from verifylab.records import seal_review, sha256_hex, write_new_json

from conftest import commit_all, item_question, synthetic_receipt


LEAN_TABLE = '\n[lean]\ntarget = "research/targets/add-zero.lean"\ntheorems = ["VL.hard", "VL.easy"]\n'


def add_lean_items(root, *ids: str) -> None:
    """Give the item `add-zero` a Lean table, and add copies of it under `ids`; all committed on the trusted branch."""
    item = root / "research" / "items" / "add-zero.md"
    head, _, body = item.read_text().partition("+++\nBody")
    if "[lean]" not in head:
        item.write_text(head + LEAN_TABLE + "+++\nBody" + body)
    (root / "research" / "targets").mkdir(exist_ok=True)
    (root / "research" / "targets" / "add-zero.lean").write_text("theorem VL.easy : True := sorry\n")
    source = (root / "research" / "items" / "add-zero.md").read_text()
    for item_id in ids:
        (root / "research" / "items" / f"{item_id}.md").write_text(source.replace('id = "add-zero"', f'id = "{item_id}"'))
    commit_all(root, "items")


def write_receipt(root, item_id: str, verdict: str = "pass", assurance: str = "protected", stale: bool = False):
    """A receipt of the item, bound to Fixture/Basic.lean as it is (or, `stale`, as it never was); its path."""
    lean = "Fixture/Basic.lean"
    digest = "0" * 64 if stale else sha256_hex((root / lean).read_bytes())
    receipt = synthetic_receipt(root, dict(
        item=item_id, item_revision="a" * 40, question_digest=item_question(root, item_id),
        adapter="lean-comparator", assurance=assurance, verdict=verdict,
        reasons=[] if verdict == "pass" else ["mismatch"], inputs={"digest": "d", "files": {lean: digest}},
        environment={}, checked={}, command=["vl"], started_at="t0", finished_at="t1", tool_version="vl 0.1.0"))
    path = root / "research" / "evidence" / item_id / f"{receipt['receipt_id'][:16]}.json"
    write_new_json(path, receipt)
    return path


def write_review(root, item_id: str, text: str = "a note"):
    review = seal_review(dict(item=item_id, item_revision="a" * 40, kind="correction", author="agent:x", text=text,
                              created="2026-10-11T10:00:00+00:00"))
    path = root / "research" / "reviews" / item_id / f"{review['review_id'][:16]}.json"
    write_new_json(path, review)
    return path
