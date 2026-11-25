from __future__ import annotations

from verifylab.records import seal_receipt, seal_review, sha256_hex, write_new_json
from verifylab.repo import Repo
from verifylab.status import derive

from conftest import synthetic_receipt, commit_all, item_question, write_item


def _write_receipt(root, verdict="pass", assurance="protected"):
    lean = "Fixture/Basic.lean"
    receipt = synthetic_receipt(root, dict(
        item="add-zero", item_revision="a" * 40, question_digest=item_question(root, "add-zero"),
        adapter="lean-comparator", assurance=assurance,
        verdict=verdict, reasons=[] if verdict == "pass" else ["mismatch"],
        inputs={"digest": "d", "files": {lean: sha256_hex((root / lean).read_bytes())}},
        environment={}, checked={}, command=["vl"], started_at="t0", finished_at="t1", tool_version="vl 0.1.0",
    ))
    write_new_json(root / "research" / "evidence" / "add-zero" / f"{receipt['receipt_id'][:16]}.json", receipt)


def _status(root):
    repo = Repo.open(root)
    items, problems = repo.load_items()
    assert problems == []
    return derive(repo, items["add-zero"], items)


def test_lifecycle(research_repo):
    root = research_repo
    assert _status(root).label == "unverified"
    _write_receipt(root, assurance="exploratory")
    assert _status(root).label == "explored"
    _write_receipt(root)
    assert _status(root).label == "pending-admission"
    commit_all(root)
    assert _status(root).label == "verified"
    (root / "Fixture" / "Basic.lean").write_text("-- changed\n")
    assert _status(root).label == "verified-stale"


def test_an_edited_receipt_is_rejected_by_its_seal(research_repo):
    """An exploratory pass edited to claim `protected` and committed: its self-hash no longer matches. The seal only
    shows an edit; anyone can recompute it (next test)."""
    root = research_repo
    _write_receipt(root, assurance="exploratory")
    file = next((root / "research" / "evidence" / "add-zero").glob("*.json"))
    forged = file.read_text().replace('"exploratory"', '"protected"')
    assert forged != file.read_text()
    file.write_text(forged)
    commit_all(root)
    status = _status(root)
    assert status.label == "unverified" and any("rejected receipt" in n for n in status.notes)


def test_a_self_sealed_counterfeit_counts_only_once_the_integrator_admits_it(research_repo):
    """A protected pass the caller wrote and sealed itself, with the public hash: it is well formed, so the seal is no
    guard. Admission is: it stays `pending-admission` until committed on the trusted ref. Once there it counts, which
    is the documented limit of a same-user process (docs/adr/0002 §8): replaying the receipt from a clean clone, the
    planned deep check, is what exposes it."""
    root = research_repo
    git_lane = __import__("conftest").git
    git_lane(root, "checkout", "-q", "-b", "lane/forger")
    _write_receipt(root)                  # sealed with the public receipt_id; no check ever ran
    commit_all(root, "a lane commits its own counterfeit")
    assert _status(root).label == "pending-admission"
    git_lane(root, "checkout", "-q", "trusted")
    git_lane(root, "merge", "-q", "--ff-only", "lane/forger")
    assert _status(root).label == "verified"


def test_a_protected_receipt_in_the_explore_store_is_never_admitted(research_repo):
    """A hand-sealed protected pass force-committed under .vl-cache/explore/ on the trusted ref: the explore store
    is never admitted and never protected, whatever the receipt says and wherever it is committed."""
    root = research_repo
    lean = "Fixture/Basic.lean"
    receipt = synthetic_receipt(root, dict(
        item="add-zero", item_revision="a" * 40, question_digest=item_question(root, "add-zero"),
        adapter="lean-comparator", assurance="protected", verdict="pass",
        reasons=[], inputs={"digest": "d", "files": {lean: sha256_hex((root / lean).read_bytes())}},
        environment={}, checked={}, command=["vl"], started_at="t0", finished_at="t1", tool_version="vl 0.1.0"))
    path = root / ".vl-cache" / "explore" / "add-zero" / f"{receipt['receipt_id'][:16]}.json"
    write_new_json(path, receipt)
    from conftest import git
    git(root, "add", "-f", str(path))
    commit_all(root, "forged explore receipt")
    repo = Repo.open(root)
    [record] = repo.receipts("add-zero")
    assert not record.admitted and record.problems
    status = _status(root)
    assert status.label == "unverified" and any("explore" in n for n in status.notes)


def test_receipt_on_candidate_branch_is_not_admitted(research_repo):
    root = research_repo
    from conftest import git
    git(root, "checkout", "-q", "-b", "lane/x")
    _write_receipt(root)
    commit_all(root)
    assert _status(root).label == "pending-admission"


def test_retraction_only_counts_when_admitted(research_repo):
    root = research_repo
    review = seal_review(dict(item="add-zero", item_revision="a" * 40, kind="retraction", author="agent:x",
                              text="wrong target", created="t"))
    write_new_json(root / "research" / "reviews" / "add-zero" / f"{review['review_id'][:16]}.json", review)
    assert _status(root).label == "unverified"
    commit_all(root)
    assert _status(root).label == "retracted"


def test_refutation_needs_verified_refuter(research_repo):
    root = research_repo
    write_item(root, "counter", refutes=["add-zero"])
    commit_all(root)
    status = _status(root)
    assert status.label == "unverified" and any("refutation proposed by counter" in n for n in status.notes)


def test_refutation_cycle_is_undetermined_not_a_crash(research_repo):
    root = research_repo
    write_item(root, "add-zero", refutes=["counter"])
    write_item(root, "counter", refutes=["add-zero"])
    commit_all(root)
    assert _status(root).label == "undetermined"


def test_a_receipt_without_a_question_digest_is_bound_through_its_item_revision(research_repo):
    """Receipts written before `question_digest` existed: the question is read back from the item blob their
    `item_revision` names, so an unchanged question stays fresh and a changed or unknown one is stale."""
    from verifylab.records import git_blob_sha
    root = research_repo
    item = root / "research" / "items" / "add-zero.md"
    item.write_text(item.read_text().replace("+++\nBody", '[lean]\ntarget = "research/targets/t.lean"\n'
                                             'theorems = ["VL.main"]\nsolution = "Fixture.Basic"\n+++\nBody'))
    commit_all(root, "lean table")
    lean = "Fixture/Basic.lean"

    def legacy(revision: str, finished: str) -> None:
        receipt = synthetic_receipt(root, dict(
            item="add-zero", item_revision=revision, adapter="lean-comparator", assurance="protected",
            verdict="pass", reasons=[], inputs={"digest": "d", "files": {lean: sha256_hex((root / lean).read_bytes())}},
            environment={}, checked={}, command=["vl"], started_at="t0", finished_at=finished, tool_version="vl 0.1.0"))
        write_new_json(root / "research" / "evidence" / "add-zero" / f"{receipt['receipt_id'][:16]}.json", receipt)

    legacy(git_blob_sha(item.read_bytes()), "t1")
    commit_all(root, "admit")
    assert _status(root).label == "verified"
    item.write_text(item.read_text().replace('["VL.main"]', '["VL.other"]'))
    commit_all(root, "change the question")
    status = _status(root)
    assert status.label == "verified-stale" and any("add-zero.md#question" in n for n in status.notes)
    item.write_text(item.read_text().replace('["VL.other"]', '["VL.main"]'))
    commit_all(root, "change it back")
    assert _status(root).label == "verified"
    for path in (root / "research" / "evidence" / "add-zero").glob("*.json"):
        path.unlink()
    legacy("b" * 40, "t2")                         # an item revision git does not know
    commit_all(root, "unknown revision")
    status = _status(root)
    assert status.label == "verified-stale" and any("not in the git object store" in n for n in status.notes)


def _receipt(root, files: dict[str, str], verdict="pass", finished="t1", started="t0"):
    receipt = synthetic_receipt(root, dict(
        item="add-zero", item_revision="a" * 40, question_digest=item_question(root, "add-zero"),
        adapter="lean-comparator", assurance="protected", verdict=verdict,
        reasons=[] if verdict == "pass" else ["the solution's statement differs"],
        inputs={"digest": "d", "files": files}, environment={}, checked={}, command=["vl"], started_at=started,
        finished_at=finished, tool_version="vl 0.1.0"))
    path = root / "research" / "evidence" / "add-zero" / f"{receipt['receipt_id'][:16]}.json"
    write_new_json(path, receipt)
    return path


def _digests(root, *paths):
    return {p: sha256_hex((root / p).read_bytes()) for p in paths}


def test_verified_needs_the_checked_candidate_files_on_the_trusted_ref(research_repo):
    """A file only this checkout has (gitignored, or edited and not committed) cannot keep a result verified that a
    fresh clone of the trusted ref would show stale."""
    root = research_repo
    (root / ".gitignore").write_text("Fixture/Secret.lean\n")
    commit_all(root, "ignore a file")
    (root / "Fixture" / "Secret.lean").write_text("theorem s : True := trivial\n")
    _receipt(root, _digests(root, "Fixture/Basic.lean", "Fixture/Secret.lean"))
    commit_all(root, "admit")
    status = _status(root)
    assert status.label == "verified-stale", status
    assert any("Fixture/Secret.lean (as checked, it is not on the trusted ref" in n for n in status.notes)


def test_an_uncommitted_edit_of_a_checked_file_does_not_verify(research_repo):
    root = research_repo
    (root / "Fixture" / "Basic.lean").write_text("theorem add_zero' (n : Nat) : n + 0 = n := by simp\n")
    _receipt(root, _digests(root, "Fixture/Basic.lean"))
    from conftest import git
    git(root, "add", "research/evidence")
    git(root, "commit", "-q", "-m", "admit the receipt, not the edit")
    status = _status(root)
    assert status.label == "verified-stale" and any("Fixture/Basic.lean (as checked" in n for n in status.notes)


def test_a_newer_admitted_fail_of_the_same_inputs_overrides_the_pass(research_repo):
    root = research_repo
    files = _digests(root, "Fixture/Basic.lean")
    _receipt(root, files, finished="2026-10-01T10:00:00+00:00")
    commit_all(root, "pass")
    assert _status(root).label == "verified"
    failed = _receipt(root, files, verdict="fail", finished="2026-10-01T12:00:00+00:00")
    commit_all(root, "a newer check of the same inputs fails")
    status = _status(root)
    assert status.label == "failed" and status.receipt.endswith(failed.name), status
    assert any("newer admitted protected check of the same inputs failed" in n for n in status.notes)


def test_an_older_fail_or_a_fail_of_other_inputs_leaves_the_pass_verified(research_repo):
    root = research_repo
    files = _digests(root, "Fixture/Basic.lean")
    _receipt(root, files, verdict="fail", finished="2026-10-01T09:00:00+00:00")
    _receipt(root, {"Fixture/Basic.lean": "0" * 64}, verdict="fail", finished="2026-10-01T13:00:00+00:00")
    _receipt(root, files, finished="2026-10-01T10:00:00+00:00")
    commit_all(root)
    assert _status(root).label == "verified"


def _named_in_order(make, older_key: str, newer_key: str):
    """Write two records so that the OLDER one's file name sorts first (file order is not age order)."""
    for k in range(200):
        older, newer = make(older_key, k), make(newer_key, k)
        if older.name < newer.name:
            return older, newer
        older.unlink()
        newer.unlink()
    raise AssertionError("no ordering found")


def test_a_stale_result_names_its_newest_receipt(research_repo):
    root = research_repo
    files = _digests(root, "Fixture/Basic.lean")
    older, newer = _named_in_order(
        lambda finished, k: _receipt(root, files, finished=finished, started=f"s{k}"),
        "2026-10-01T10:00:00+00:00", "2026-10-01T11:00:00+00:00")
    commit_all(root)
    (root / "Fixture" / "Basic.lean").write_text("-- changed\n")
    status = _status(root)
    assert status.label == "verified-stale" and status.receipt.endswith(newer.name), (status, older.name)


def test_a_retraction_names_the_newest_admitted_one(research_repo):
    root = research_repo

    def retraction(created, k):
        review = seal_review(dict(item="add-zero", item_revision="a" * 40, kind="retraction", author="agent:x",
                                  text=f"wrong target {k}", created=created))
        path = root / "research" / "reviews" / "add-zero" / f"{review['review_id'][:16]}.json"
        write_new_json(path, review)
        return path
    older, newer = _named_in_order(retraction, "2026-10-01T10:00:00+00:00", "2026-10-01T11:00:00+00:00")
    commit_all(root)
    status = _status(root)
    assert status.label == "retracted" and status.receipt.endswith(newer.name), (status, older.name)


def test_labels_for_questions_records_and_inconclusive_checks(research_repo):
    """`answered` needs a verified answer; a result whose claim no checker verifies is `recorded-<claim>`; a check
    that only errored is `check-error`, never `failed`."""
    root = research_repo
    question = root / "research" / "items" / "qq.md"
    question.write_text('+++\nid = "qq"\nkind = "question"\ntitle = "Q"\nauthor = "agent:test"\n'
                        'created = "2026-10-01"\nstatement = "Is n + 0 = n?"\n+++\n')
    item = root / "research" / "items" / "add-zero.md"
    item.write_text(item.read_text().replace("refutes = []", 'refutes = []\nanswers = ["qq"]'))
    prose = root / "research" / "items" / "prose.md"
    prose.write_text(item.read_text().replace('id = "add-zero"', 'id = "prose"').replace('claim = "formal"',
                                                                                         'claim = "prose"'))
    commit_all(root, "a question, its answer, a prose result")

    def labels():
        repo = Repo.open(root)
        items, problems = repo.load_items()
        assert problems == []
        return {i: derive(repo, items[i], items).label for i in ("qq", "add-zero", "prose")}
    assert labels() == {"qq": "open", "add-zero": "unverified", "prose": "recorded-prose"}
    _receipt(root, _digests(root, "Fixture/Basic.lean"), verdict="error")
    commit_all(root)
    assert labels()["add-zero"] == "check-error"
    _receipt(root, _digests(root, "Fixture/Basic.lean"), finished="t2")
    commit_all(root)
    assert labels() == {"qq": "answered", "add-zero": "verified", "prose": "recorded-prose"}


def test_missing_or_empty_kernel_coverage_never_bypasses_external_requirement(research_repo):
    from verifylab.status import single_kernel
    from dataclasses import replace
    repo = Repo.open(research_repo)
    repo.config = replace(repo.config, lean=replace(repo.config.lean, external_kernels=True))
    base = {"adapter": "lean-comparator", "assurance": "protected", "verdict": "pass"}
    assert not single_kernel(repo, {**base, "checked": {"kernels": ["lean", "nanoda"]}})
    for checked in ({}, {"kernels": []}, {"kernels": ["lean"]}, {"kernels": None}):
        assert single_kernel(repo, {**base, "checked": checked})


def test_admitted_resealed_pass_without_coverage_is_rejected(research_repo):
    import json
    root = research_repo
    _write_receipt(root)
    path = next((root / "research/evidence/add-zero").glob("*.json"))
    data = json.loads(path.read_text())
    data["checked"] = {}
    path.unlink()
    data = seal_receipt(data)
    write_new_json(path.parent / f"{data['receipt_id'][:16]}.json", data)
    commit_all(root)
    status = _status(root)
    assert status.label == "unverified"
    assert any("rejected receipt" in n and "checked.kernels" in n for n in status.notes)


def test_t0_single_kernel_new_profile_never_passes_even_when_external_kernels_is_false(research_repo):
    """T0: Even if [lean] external_kernels = false is set in config, newly issued profiles without nanoda still count
    as single_kernel (never as pass), while a historical-profile single-kernel pass keeps its verified status."""
    from verifylab.config import RULES_INPUT
    from verifylab.records import canonical_json, sha256_hex
    from verifylab.status import single_kernel

    root = research_repo
    cfg_path = root / "research" / "vl.toml"
    cfg_path.write_text(cfg_path.read_text().replace('roots = ["Fixture"]',
                                                   'roots = ["Fixture"]\nexternal_kernels = false'))
    commit_all(root, "disable external_kernels in historical style")
    repo = Repo.open(root)
    assert repo.config.lean.external_kernels is False

    base = {"adapter": "lean-comparator", "assurance": "protected", "verdict": "pass",
            "environment": {}, "inputs": {"files": {"Fixture/Basic.lean": "a" * 64}, "trusted_files": {}}}
    historical = {**base, "inputs": {"trusted_files": {"research/vl.toml": "b" * 64}},
                  "checked": {"kernels": ["lean"]}}
    modern_rules = {**base, "inputs": {"trusted_files": {RULES_INPUT: "b" * 64}},
                    "checked": {"kernels": ["lean"]}}
    modern_ident = {**base, "environment": {"packages": {"identity": {"revisions": {}, "traces": {}}}},
                    "checked": {"kernels": ["lean"]}}

    assert single_kernel(repo, historical) is False
    assert single_kernel(repo, modern_rules) is True
    assert single_kernel(repo, modern_ident) is True
    assert single_kernel(repo, {**modern_rules, "checked": {"kernels": ["lean", "nanoda"]}}) is False

    # End-to-end status derivation: historical single-kernel receipt committed on trusted keeps verified + coverage label
    _write_receipt(root)
    path = next((root / "research/evidence/add-zero").glob("*.json"))
    import json
    data = json.loads(path.read_text())
    trusted = {k: v for k, v in data["inputs"]["trusted_files"].items() if k != RULES_INPUT}
    trusted["research/vl.toml"] = sha256_hex((root / "research/vl.toml").read_bytes())
    data["inputs"]["trusted_files"] = trusted
    basis = {"files": data["inputs"]["files"], "trusted_files": trusted, "target": data["target"]}
    data["inputs"]["digest"] = "sha256:" + sha256_hex(canonical_json(basis).encode())
    data["environment"].pop("packages", None)
    data["checked"] = {**{k: v for k, v in data["checked"].items() if k not in ("definitions_mode", "probe_run")},
                       "kernels": ["lean"]}
    path.unlink()
    data = seal_receipt(data)
    write_new_json(path.parent / f"{data['receipt_id'][:16]}.json", data)
    commit_all(root, "historical single-kernel pass")
    st = _status(root)
    assert st.label == "verified" and "single-kernel" not in " ".join(st.notes)
    from verifylab.catalog import build
    from verifylab.repo import TrustedRepo
    entry = next(e for e in build(TrustedRepo.open(root))["items"] if e["id"] == "add-zero")
    ext_row = next(r for r in entry["ladder"] if r["row"] == "second_kernel")
    assert ext_row["state"] == "not assessed"
