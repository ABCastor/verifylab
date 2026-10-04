from __future__ import annotations

import json
from pathlib import Path

import pytest

from verifylab.cli import main
from verifylab.records import seal_receipt, seal_review, sha256_hex, write_new_json

from conftest import commit_all, git, item_question


def vl(capsys, *args):
    rc = main(list(args))
    captured = capsys.readouterr()
    return rc, captured.out, captured.err


def make_item(root: Path, item_id: str, kind: str = "result", body: str = "Body.\n", **fields) -> Path:
    meta = {"id": item_id, "kind": kind, "title": f"Item {item_id}", "author": "agent:test", "created": "2026-10-01"}
    if kind == "result":
        meta.update(statement=f"Statement of {item_id}.", claim="formal")
    meta.update(fields)
    lines = []
    for key, value in meta.items():
        if isinstance(value, dict):
            value = "{ " + ", ".join(f"{k} = {json.dumps(v)}" for k, v in value.items()) + " }"
        else:
            value = json.dumps(value)
        lines.append(f"{key} = {value}")
    path = root / "research" / "items" / f"{item_id}.md"
    path.write_text("+++\n" + "\n".join(lines) + "\n+++\n" + body)
    return path


def make_receipt(root: Path, item_id: str = "add-zero", trusted: tuple[str, ...] = (),
                 assurance: str = "protected") -> Path:
    lean = "Fixture/Basic.lean"
    inputs = {"digest": "d", "files": {lean: sha256_hex((root / lean).read_bytes())}}
    if trusted:
        inputs["trusted_files"] = {p: sha256_hex((root / p).read_bytes()) for p in trusted}
    receipt = seal_receipt(dict(
        item=item_id, item_revision="a" * 40, question_digest=item_question(root, item_id),
        adapter="lean-comparator", assurance=assurance, verdict="pass",
        reasons=[], inputs=inputs,
        environment={}, checked={}, command=["vl"], started_at="t0", finished_at="t1", tool_version="vl 0.1.0",
    ))
    path = root / "research" / "evidence" / item_id / f"{receipt['receipt_id'][:16]}.json"
    write_new_json(path, receipt)
    return path


def make_review(root: Path, item_id: str = "add-zero", kind: str = "understanding", text: str = "ok") -> Path:
    review = seal_review(dict(item=item_id, item_revision="a" * 40, kind=kind, author="human:ada", text=text,
                              created="2026-10-01T00:00:00+00:00"))
    path = root / "research" / "reviews" / item_id / f"{review['review_id'][:16]}.json"
    write_new_json(path, review)
    return path


def messages(out: str, level: str) -> list[str]:
    return [line for line in out.splitlines() if line.startswith(level)]


def test_clean_repository_passes(research_repo, monkeypatch, capsys):
    monkeypatch.chdir(research_repo)
    rc, out, _ = vl(capsys, "validate")
    assert rc == 0 and "0 errors, 0 warnings" in out
    rc, out, _ = vl(capsys, "validate", "--json")
    assert json.loads(out)["ok"] is True


def test_dangling_references_are_errors(research_repo, monkeypatch, capsys):
    root = research_repo
    monkeypatch.chdir(root)
    make_item(root, "dangling", uses=["nope"], cites=["ghost-cite@abcd"], body="See [[ghost]] and [[add-zero]].\n")
    rc, out, _ = vl(capsys, "validate")
    errors = messages(out, "ERROR")
    assert rc == 1 and len(errors) == 3
    assert any("uses 'nope'" in e for e in errors)
    assert any("cites 'ghost-cite@abcd'" in e for e in errors)
    assert any("body link 'ghost'" in e for e in errors)


def test_stale_pin_and_unparseable_item(research_repo, monkeypatch, capsys):
    root = research_repo
    monkeypatch.chdir(root)
    make_item(root, "pinned", uses=["add-zero@abcd1234"])
    (root / "research" / "items" / "broken.md").write_text("no front matter\n")
    make_item(root, "uses-broken", uses=["broken"])
    rc, out, _ = vl(capsys, "validate")
    assert rc == 1
    assert any("broken.md" in e and "front matter" in e for e in messages(out, "ERROR"))
    assert not any("no item 'broken'" in e for e in messages(out, "ERROR"))  # reported once, as a parse error
    assert any("pins a revision that is not the current one" in w for w in messages(out, "WARNING"))


def test_forged_and_misfiled_records_are_errors(research_repo, monkeypatch, capsys):
    root = research_repo
    monkeypatch.chdir(root)
    receipt = make_receipt(root, assurance="exploratory")
    forged = receipt.read_text().replace('"exploratory"', '"protected"')
    assert forged != receipt.read_text()
    receipt.write_text(forged)
    make_review(root, item_id="add-zero")
    other = root / "research" / "reviews" / "add-zero" / "misfiled.json"
    review = seal_review(dict(item="someone-else", item_revision="a" * 40, kind="understanding",
                              author="human:ada", text="x", created="t"))
    other.write_text(json.dumps(review))
    (root / "research" / "evidence" / "ghost-item").mkdir(parents=True)
    rc, out, _ = vl(capsys, "validate")
    errors = messages(out, "ERROR")
    assert rc == 1
    assert any(receipt.name in e and "forged" in e for e in errors)
    assert any("misfiled.json" in e and "names item 'someone-else'" in e for e in errors)
    assert any("ghost-item" in e and "not an item" in e for e in errors)


def test_explanations_must_not_cite_retracted_items(research_repo, monkeypatch, capsys):
    root = research_repo
    monkeypatch.chdir(root)
    make_item(root, "why", kind="explanation", body="Because.\n", cites=["add-zero"])
    rc, out, _ = vl(capsys, "validate")
    assert rc == 0
    assert any("cites 'add-zero', which is not verified (status: unverified)" in w for w in messages(out, "WARNING"))
    make_review(root, kind="retraction", text="wrong target")
    commit_all(root)
    rc, out, _ = vl(capsys, "validate")
    assert rc == 1
    assert any("why.md" in e and "cites 'add-zero', which is retracted" in e for e in messages(out, "ERROR"))


def test_targets_must_be_admitted_on_the_trusted_ref(research_repo, monkeypatch, capsys):
    root = research_repo
    monkeypatch.chdir(root)
    target = root / "research" / "targets" / "add-zero.lean"
    target.parent.mkdir(parents=True)
    target.write_text("theorem target : ∀ n : Nat, n + 0 = n := sorry\n")
    make_item(root, "add-zero", lean={"target": "research/targets/add-zero.lean", "theorems": ["add_zero'"]})
    rc, out, _ = vl(capsys, "validate")
    assert rc == 0
    assert any("target not admitted on the trusted ref: needs integration and a fidelity review" in w
               for w in messages(out, "WARNING"))
    commit_all(root)
    rc, out, _ = vl(capsys, "validate")
    assert rc == 0 and not messages(out, "WARNING")
    target.write_text("theorem target : True := trivial\n")
    rc, out, _ = vl(capsys, "validate")
    assert any("differs from the trusted ref" in w for w in messages(out, "WARNING"))
    make_item(root, "outside", lean={"target": "Fixture/Basic.lean"})
    rc, out, _ = vl(capsys, "validate")
    assert rc == 1 and any("must be under research/targets/" in e for e in messages(out, "ERROR"))


def test_stale_admitted_receipt_is_a_warning(research_repo, monkeypatch, capsys):
    root = research_repo
    monkeypatch.chdir(root)
    make_receipt(root)
    commit_all(root)
    assert vl(capsys, "validate")[1].count("WARNING") == 0
    (root / "Fixture" / "Basic.lean").write_text("theorem add_zero' (n : Nat) : n + 0 = n := by simp\n")
    rc, out, _ = vl(capsys, "validate")
    assert rc == 0
    assert any("stale inputs" in w and "Fixture/Basic.lean" in w for w in messages(out, "WARNING"))


def test_incoming_branch_cannot_bring_receipts_or_edit_records(research_repo, monkeypatch, capsys):
    root = research_repo
    monkeypatch.chdir(root)
    review = make_review(root)
    commit_all(root)
    git(root, "checkout", "-q", "-b", "lane/x")
    receipt = make_receipt(root)
    review.write_text(review.read_text().replace('"ok"', '"edited"'))
    make_item(root, "new-result")
    (root / "research" / "targets").mkdir(parents=True, exist_ok=True)
    (root / "research" / "targets" / "new-result.lean").write_text("theorem t : True := trivial\n")
    commit_all(root, "lane work")
    git(root, "checkout", "-q", "trusted")

    rc, out, _ = vl(capsys, "validate", "--incoming", "lane/x")
    errors, warnings = messages(out, "ERROR"), messages(out, "WARNING")
    assert rc == 1
    rel_receipt = str(receipt.relative_to(root))
    assert any(rel_receipt in e and "receipts are written only by the integrator's protected check" in e
               for e in errors)
    assert any(str(review.relative_to(root)) in e and "records are immutable" in e for e in errors)
    assert any("new-result.lean" in w and "record a fidelity review" in w for w in warnings)
    assert "items added: research/items/new-result.md" in out

    rc, out, _ = vl(capsys, "validate", "--incoming", "lane/x", "--json")
    data = json.loads(out)
    assert data["ok"] is False and data["incoming"]
    assert data["trusted_changes"] == [] and data["stale_after_merge"] == []
    assert vl(capsys, "validate", "--incoming", "no-such-branch")[0] == 2


def _forge_explore_receipt(root: Path) -> Path:
    """A sealed protected pass hand-written into the exploratory store and force-added past .gitignore."""
    receipt = make_receipt(root)
    forged = root / ".vl-cache" / "explore" / "add-zero" / receipt.name
    forged.parent.mkdir(parents=True)
    receipt.rename(forged)
    git(root, "add", "-f", str(forged))
    return forged


def test_a_tracked_file_under_the_cache_is_an_error(research_repo, monkeypatch, capsys):
    root = research_repo
    monkeypatch.chdir(root)
    forged = _forge_explore_receipt(root)
    commit_all(root, "forged explore receipt")
    rc, out, _ = vl(capsys, "validate")
    rel = str(forged.relative_to(root))
    assert rc == 1 and any(rel in e and "never versioned" in e for e in messages(out, "ERROR")), out


def test_incoming_branch_adding_a_cache_receipt_is_an_error(research_repo, monkeypatch, capsys):
    root = research_repo
    monkeypatch.chdir(root)
    holder = {}
    on_lane(root, "lane/cache", lambda r: holder.setdefault("path", _forge_explore_receipt(r)))
    rc, out, _ = vl(capsys, "validate", "--incoming", "lane/cache")
    rel = str(holder["path"].relative_to(root))
    assert rc == 1 and any(rel in e and "on lane/cache" in e for e in messages(out, "ERROR")), out
    assert "code and other files changed" not in out


def test_refutation_cycle_is_reported_not_a_crash(research_repo, monkeypatch, capsys):
    root = research_repo
    monkeypatch.chdir(root)
    make_item(root, "claim-a", refutes=["claim-b"])
    make_item(root, "claim-b", refutes=["claim-a"])
    rc, out, _ = vl(capsys, "validate")             # a cycle the worktree proposes
    assert rc == 1
    assert any("claim-a.md" in e and "form a cycle" in e for e in messages(out, "ERROR"))
    rc, out, _ = vl(capsys, "show", "claim-a")      # proposals change no status
    assert rc == 0 and "status: unverified" in out and "in the worktree only" in out
    commit_all(root, "integrate the cycle")
    rc, out, _ = vl(capsys, "validate")
    assert rc == 1 and any("claim-a.md" in e and "form a cycle" in e for e in messages(out, "ERROR"))
    rc, out, _ = vl(capsys, "show", "claim-a")
    assert rc == 0 and "status: undetermined" in out and "form a cycle" in out


def test_verified_without_fidelity_review_warns_and_review_clears_it(research_repo, monkeypatch, capsys):
    from verifylab.records import seal_receipt, sha256_hex, write_new_json
    root = research_repo
    monkeypatch.chdir(root)
    item = root / "research" / "items" / "add-zero.md"
    item.write_text(item.read_text().replace("+++\nBody", '[lean]\ntarget = "research/targets/add-zero.lean"\n+++\nBody'))
    target = root / "research" / "targets" / "add-zero.lean"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("theorem main (n : Nat) : n + 0 = n := sorry\n")
    lean = "Fixture/Basic.lean"
    receipt = seal_receipt(dict(
        item="add-zero", item_revision="a" * 40, question_digest=item_question(root),
        adapter="lean-comparator", assurance="protected", verdict="pass",
        reasons=[], inputs={"digest": "d", "files": {lean: sha256_hex((root / lean).read_bytes())}},
        environment={}, checked={}, command=["vl"], started_at="t0", finished_at="t1", tool_version="vl 0.1.0"))
    write_new_json(root / "research" / "evidence" / "add-zero" / f"{receipt['receipt_id'][:16]}.json", receipt)
    commit_all(root, "admit")
    rc, out, _ = vl(capsys, "validate")
    assert "fidelity is 'not reviewed'" in out
    vl(capsys, "review", "add-zero", "--kind", "fidelity", "--verdict", "faithful", "--author", "agent:test",
       "--text", "states n + 0 = n over Nat")
    commit_all(root, "review")
    rc, out, _ = vl(capsys, "validate")
    assert "not reviewed" not in out and "is by the item's author" in out
    rc, out, _ = vl(capsys, "show", "add-zero")
    assert "fidelity: faithful (self-review)" in out.splitlines()[0]
    target.write_text("theorem main (n : Nat) : n + 0 = n ∨ True := sorry\n")
    commit_all(root, "change target")
    rc, out, _ = vl(capsys, "validate")
    assert "review stale: target changed since review" in out


def on_lane(root: Path, branch: str, change) -> None:
    """Commit `change(root)` on a new branch, then return to the trusted branch."""
    git(root, "checkout", "-q", "-b", branch)
    change(root)
    commit_all(root, "lane work")
    git(root, "checkout", "-q", "trusted")


def incoming(capsys, branch: str) -> tuple[int, list[str], dict]:
    rc, out, _ = vl(capsys, "validate", "--incoming", branch)
    rc_json, data_out, _ = vl(capsys, "validate", "--incoming", branch, "--json")
    assert rc == rc_json
    return rc, messages(out, "WARNING"), json.loads(data_out)


def test_incoming_lane_that_widens_permitted_axioms_is_a_trusted_change(research_repo, monkeypatch, capsys):
    root = research_repo
    monkeypatch.chdir(root)

    def widen(r: Path) -> None:
        cfg = r / "research" / "vl.toml"
        cfg.write_text(cfg.read_text() + 'permitted_axioms = ["propext", "Quot.sound", "Classical.choice", "sorryAx"]\n')
    on_lane(root, "lane/axiom", widen)
    rc, warnings, data = incoming(capsys, "lane/axiom")
    assert rc == 0
    assert any("research/vl.toml" in w and "trusted input modified on lane/axiom" in w for w in warnings)
    assert [(c["path"], c["change"]) for c in data["trusted_changes"]] == [("research/vl.toml", "modified")]


def test_incoming_evaluator_and_lean_build_file_edits_are_trusted_changes(research_repo, monkeypatch, capsys):
    root = research_repo
    monkeypatch.chdir(root)
    evaluator = root / "research" / "evaluators" / "square.py"
    evaluator.parent.mkdir(parents=True)
    evaluator.write_text("CASES = [1, 2]\ndef judge(case, output):\n    return output == case * case\n")
    (root / "lean-toolchain").write_text("leanprover/lean4:v4.0.0\n")
    commit_all(root, "evaluator and toolchain")

    def edit(r: Path) -> None:
        (r / "research" / "evaluators" / "square.py").write_text("CASES = [1]\ndef judge(case, output):\n    return True\n")
        (r / "lean-toolchain").write_text("leanprover/lean4:v4.1.0\n")
        (r / "lakefile.toml").write_text('name = "fixture"\n')
        (r / "Fixture" / "More.lean").write_text("theorem more : True := trivial\n")
    on_lane(root, "lane/eval", edit)
    rc, warnings, data = incoming(capsys, "lane/eval")
    assert rc == 0
    assert any("square.py" in w and "an evaluator" in w for w in warnings)
    assert [(c["path"], c["change"]) for c in data["trusted_changes"]] == [
        ("lakefile.toml", "added"), ("lean-toolchain", "modified"), ("research/evaluators/square.py", "modified")]
    assert any("code and other files changed: Fixture/More.lean" in i["message"] for i in data["incoming"])


def test_incoming_deleted_code_file_is_listed_with_the_receipts_it_stales(research_repo, monkeypatch, capsys):
    root = research_repo
    monkeypatch.chdir(root)
    receipt = make_receipt(root, trusted=("Fixture/Basic.lean",))
    (root / "Fixture" / "Unused.lean").write_text("theorem unused : True := trivial\n")
    commit_all(root, "admit")
    on_lane(root, "lane/del", lambda r: (git(r, "rm", "-q", "Fixture/Basic.lean"),
                                         git(r, "rm", "-q", "Fixture/Unused.lean")))
    rc, warnings, data = incoming(capsys, "lane/del")
    assert rc == 0
    assert any("Fixture/Unused.lean: deleted on lane/del" in w for w in warnings)
    rel = str(receipt.relative_to(root))
    assert any(rel in w and "goes stale if lane/del is merged" in w for w in warnings)
    assert data["stale_after_merge"] == [{"receipt": rel, "item": "add-zero",
                                          "inputs": ["Fixture/Basic.lean (trusted)", "Fixture/Basic.lean"]}]
    assert [(c["path"], c["change"]) for c in data["trusted_changes"]] == [("Fixture/Basic.lean", "deleted")]


def test_incoming_change_outside_receipt_inputs_stales_nothing(research_repo, monkeypatch, capsys):
    root = research_repo
    monkeypatch.chdir(root)
    make_receipt(root, trusted=("Fixture/Basic.lean",))
    commit_all(root, "admit")
    on_lane(root, "lane/other", lambda r: (r / "Fixture" / "Other.lean").write_text("theorem o : True := trivial\n"))
    rc, warnings, data = incoming(capsys, "lane/other")
    assert rc == 0 and warnings == []
    assert data["trusted_changes"] == [] and data["stale_after_merge"] == []
    assert any("code and other files changed: Fixture/Other.lean" in i["message"] for i in data["incoming"])


def test_incoming_receipt_with_a_non_ascii_or_spaced_name_is_still_an_error(research_repo, monkeypatch, capsys):
    """git C-quotes such names unless asked for NUL-separated output; a quoted path once slipped past the
    evidence prefix test and was reported as an ordinary code change."""
    root = research_repo
    monkeypatch.chdir(root)
    names = ("é.json", "a b.json", "e.json")

    def add(r: Path) -> None:
        for name in names:
            path = r / "research" / "evidence" / "foo" / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("{}")
    on_lane(root, "lane/uni", add)
    rc, out, _ = vl(capsys, "validate", "--incoming", "lane/uni")
    errors = messages(out, "ERROR")
    assert rc == 1
    for name in names:
        assert any(e.startswith(f"ERROR research/evidence/foo/{name}: added on lane/uni: receipts") for e in errors), out
    assert "code and other files changed" not in out and "\\303" not in out


def test_incoming_lane_that_changes_the_question_of_an_existing_item_warns(research_repo, monkeypatch, capsys):
    root = research_repo
    monkeypatch.chdir(root)
    (root / "research" / "targets").mkdir(parents=True)
    for name in ("add-zero", "other", "easier"):
        (root / "research" / "targets" / f"{name}.lean").write_text("theorem main : True := sorry\n")
    make_item(root, "add-zero", lean={"target": "research/targets/add-zero.lean", "theorems": ["main"]})
    make_item(root, "other", lean={"target": "research/targets/other.lean", "theorems": ["main"]})
    commit_all(root, "items")

    def retarget(r: Path) -> None:
        make_item(r, "add-zero", lean={"target": "research/targets/easier.lean", "theorems": ["main"]})
        make_item(r, "other", title="Renamed only", lean={"target": "research/targets/other.lean", "theorems": ["main"]})
    on_lane(root, "lane/q", retarget)
    rc, warnings, data = incoming(capsys, "lane/q")
    assert rc == 0, warnings
    assert any("add-zero.md" in w and "changes the question" in w and "[lean] target" in w for w in warnings), warnings
    assert not any("other.md" in w for w in warnings), warnings
    assert [c["path"] for c in data["trusted_changes"]] == ["research/items/add-zero.md"]


def _sealed_receipt(root: Path, **over) -> bytes:
    lean = "Fixture/Basic.lean"
    fields = dict(item="add-zero", item_revision="a" * 40, question_digest=item_question(root, "add-zero"),
                  adapter="lean-comparator", assurance="protected", verdict="pass", reasons=[],
                  inputs={"digest": "d", "files": {lean: sha256_hex((root / lean).read_bytes())}},
                  environment={}, checked={}, command=["vl"], started_at="t0", finished_at="t1",
                  tool_version="vl 0.1.0")
    for key, value in over.items():
        target, _, sub = key.partition("__")
        if sub:
            fields[target] = {**fields[target], sub: value}
        else:
            fields[target] = value
    return json.dumps(seal_receipt(fields)).encode()


MALFORMED_RECORDS = {
    "trusted files as a list": ("evidence", lambda root: _sealed_receipt(root, inputs__trusted_files=[])),
    "reasons that are not text": ("evidence", lambda root: _sealed_receipt(root, verdict="fail", reasons=[1])),
    "probe run as a list": ("evidence", lambda root: _sealed_receipt(root, checked={"probe_run": [1]})),
    "kernels as a number": ("evidence", lambda root: _sealed_receipt(root, checked={"kernels": 5})),
    "phases as text": ("evidence", lambda root: _sealed_receipt(root, extra={"phases": {"build": "slow"}})),
    "deep nesting": ("evidence", lambda root: b"[" * 100_000 + b"]" * 100_000),
    "invalid UTF-8": ("evidence", lambda root: b'{"item": "add-zero", "x": "\xff\xfe"}'),
    "review in invalid UTF-8": ("reviews", lambda root: b'{"item": "\xff"}'),
    "review nested too deeply": ("reviews", lambda root: b'{"a":' * 50_000 + b"1" + b"}" * 50_000),
}


@pytest.mark.parametrize("case", sorted(MALFORMED_RECORDS))
def test_a_malformed_sealed_record_is_a_record_problem_never_a_crash(research_repo, monkeypatch, capsys, case):
    """Admitted on the trusted ref, so that status, show and validate all read it."""
    root = research_repo
    monkeypatch.chdir(root)
    folder, make = MALFORMED_RECORDS[case]
    path = root / "research" / folder / "add-zero" / "0123456789abcdef.json"
    path.parent.mkdir(parents=True)
    path.write_bytes(make(root))
    commit_all(root, f"malformed: {case}")
    rc, out, err = vl(capsys, "validate")
    assert rc == 1 and "internal error" not in err, err
    assert any(str(path.relative_to(root)) in e for e in messages(out, "ERROR")), out
    rc, out, err = vl(capsys, "show", "add-zero")
    assert rc == 0 and "internal error" not in err, err
    assert ("REJECTED" if folder == "evidence" else "MALFORMED") in out, out


def test_incoming_reviews_are_named_with_kind_verdict_and_author(research_repo, monkeypatch, capsys):
    """A branch can add a retraction of any item, or a fidelity review signed by anyone: merging admits it, so the
    integrator sees each one as a warning, not only as an info line."""
    root = research_repo
    monkeypatch.chdir(root)

    def reviews(r: Path) -> None:
        make_review(r, kind="retraction", text="the target is wrong")
        review = seal_review(dict(item="add-zero", item_revision="a" * 40, kind="fidelity", author="human:rival",
                                  verdict="faithful", text="looks right", created="2026-10-01T00:00:00+00:00"))
        write_new_json(r / "research" / "reviews" / "add-zero" / f"{review['review_id'][:16]}.json", review)
    on_lane(root, "lane/rev", reviews)
    rc, warnings, data = incoming(capsys, "lane/rev")
    assert rc == 0, warnings
    assert any("a retraction review by human:ada is added on lane/rev: merging it retracts the item" in w
               for w in warnings), warnings
    assert any("a fidelity review with verdict 'faithful' by human:rival" in w for w in warnings), warnings


# B5: --incoming validates what the branch brings, as it is on the branch ---------------------------------------------

def test_incoming_malformed_or_dangling_items_and_malformed_reviews_are_errors(research_repo, monkeypatch, capsys):
    root = research_repo
    monkeypatch.chdir(root)

    def bring(r: Path) -> None:
        (r / "research" / "items" / "broken.md").write_text("+++\nid = 'broken'\n+++\n")
        make_item(r, "dangling", refutes=["nowhere"])
        make_item(r, "fine", uses=["add-zero"])
        review = r / "research" / "reviews" / "add-zero" / "0123456789abcdef.json"
        review.parent.mkdir(parents=True)
        review.write_text('{"kind": "retraction"}')
    on_lane(root, "lane/bad", bring)
    rc, out, _ = vl(capsys, "validate", "--incoming", "lane/bad")
    errors = messages(out, "ERROR")
    assert rc == 1
    assert any("broken.md: on lane/bad" in e for e in errors), out
    assert any("dangling.md: on lane/bad: refutes 'nowhere': no item 'nowhere'" in e for e in errors), out
    assert any("0123456789abcdef.json" in e and "malformed review" in e for e in errors), out
    assert not any("fine.md" in e for e in errors), out                                     # control


def test_incoming_question_change_of_a_verified_item_is_in_stale_after_merge(research_repo, monkeypatch, capsys):
    root = research_repo
    monkeypatch.chdir(root)
    receipt = make_receipt(root)
    commit_all(root, "admit")
    on_lane(root, "lane/rq", lambda r: make_item(r, "add-zero", lean={"target": "research/targets/x.lean",
                                                                     "theorems": ["main"]}))
    rc, warnings, data = incoming(capsys, "lane/rq")
    rel = str(receipt.relative_to(root))
    row = {"receipt": rel, "item": "add-zero", "inputs": ["research/items/add-zero.md#question"]}
    assert row in data["stale_after_merge"], data
    on_lane(root, "lane/prose", lambda r: make_item(r, "add-zero", title="Only the title"))    # control
    rc, warnings, data = incoming(capsys, "lane/prose")
    assert data["stale_after_merge"] == [], data


# Process state in a record's prose: status is derived, never written -----------------------------------------------

# One planted phrase per pattern of validate.PROCESS_STATE, as the referees found them in real records.
PROCESS_PHRASES = [
    ("statement", "Exploratory check only, run by the prover.", "Exploratory check"),
    ("limits", "Checked only in explore mode.", "explore mode"),
    ("assumptions", "There is no protected receipt yet.", "no protected receipt"),
    ("body", "The bound is not verified yet.\n", "not verified yet"),
    ("limits", "The target is not yet admitted.", "not yet admitted"),
    ("body", "This is a proposal until the coordinator admits the target.\n", "proposal until"),
    ("body", "Supersedes is not set: the old row stays.\n", "Supersedes is not set"),
    ("statement", "Main holds; no fidelity review has been recorded.", "fidelity review"),
    ("body", "The coordinator's next step is a protected check.\n", "coordinator's next step"),
    ("body", "Notes from the import (to be reviewed)\n", "Notes from the import (to be reviewed)"),
]


def _plant(root: Path, item_id: str, field: str, text: str) -> None:
    if field == "body":
        make_item(root, item_id, body=text)
    elif field == "statement":
        make_item(root, item_id, statement=text)
    else:
        make_item(root, item_id, **{field: ["Only finite alphabets.", text]})


@pytest.mark.parametrize("field, text, phrase", PROCESS_PHRASES, ids=[p[2] for p in PROCESS_PHRASES])
def test_process_state_in_the_prose_of_a_record_warns_with_the_phrase(research_repo, monkeypatch, capsys, field,
                                                                     text, phrase):
    root = research_repo
    monkeypatch.chdir(root)
    _plant(root, "stale-prose", field, text)
    rc, out, _ = vl(capsys, "validate")
    found = [w for w in messages(out, "WARNING") if "stale-prose.md" in w and "process state" in w]
    assert rc == 0 and len(found) == 1 and f"{field}: " in found[0] and f"'{phrase}'" in found[0], out
    from verifylab.commands import validate
    monkeypatch.setattr(validate, "PROCESS_STATE", ())                       # the guard, disabled
    rc, out, _ = vl(capsys, "validate")
    assert "process state" not in out


def test_every_process_state_pattern_has_a_planted_phrase():
    from verifylab.commands.validate import PROCESS_STATE
    for pattern in PROCESS_STATE:
        assert any(pattern.search(text) for _, text, _ in PROCESS_PHRASES), pattern.pattern
    for _, text, phrase in PROCESS_PHRASES:
        assert [m.group(0) for p in PROCESS_STATE for m in p.finditer(text)] == [phrase], text


def test_prose_without_process_state_and_quotes_in_fenced_code_are_clean(research_repo, monkeypatch, capsys):
    root = research_repo
    monkeypatch.chdir(root)
    make_item(root, "clean-prose", statement="For every finite alphabet, the verified bound of [[add-zero]] holds.",
              assumptions=["The distribution is strictly positive.", "The code is prefix-free."],
              limits=["Not a claim about all corpora.", "We explore no infinite alphabet."],
              body="A record that quotes a stale note on purpose:\n\n```text\nexploratory check only; no protected "
                   "receipt; supersedes is not set\n```\n\n~~~~\nnot yet admitted\n~~~~\n\nThe proof is short.\n")
    rc, out, _ = vl(capsys, "validate")
    assert rc == 0 and "process state" not in out, out
    from verifylab.commands.validate import outside_fences
    assert outside_fences("a\n````\nb\n```\nc\n`````\nd") == "a\nd"         # a shorter fence does not close; a longer does
    assert outside_fences("a\n  ~~~ x\nb\n") == "a"                           # an unclosed fence runs to the end
