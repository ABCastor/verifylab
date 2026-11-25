"""Independent import-boundary probes; source evidence is a local fixture snapshot."""

from __future__ import annotations

import json

import pytest

from test_imports import (
    GCA_ITEM_ID, GCA_MODULE, _FakeLeanAdapter, commit_all, make_consumer_repo,
)
from verifylab import imports
from verifylab.commands import check
from verifylab.records import receipt_problems, seal_receipt, seal_review, write_new_json
from verifylab.repo import Repo


def _resolve(root, guards=frozenset()):
    repo = Repo.open(root)
    return imports.resolve_imports(repo, repo.load_item("consequence"), repo.trusted_commit, guards=guards)


def _repin(root, revision):
    path = root / "lake-manifest.json"
    manifest = json.loads(path.read_text())
    manifest["packages"][0]["rev"] = revision
    path.write_text(json.dumps(manifest))
    commit_all(root, "pin changed source snapshot")


@pytest.mark.parametrize("adverse", ["newer-failure", "adverse-fidelity", "changed-solution"])
def test_whole_source_snapshot_defeats_cherry_picked_pass(tmp_path, adverse):
    root, source = make_consumer_repo(tmp_path, packages_dir_mode="config")
    clean, errors = _resolve(root)
    assert not errors and len(clean) == 1
    snap = imports.SnapshotRepo.open_at(source, clean[0].manifest_rev)
    if adverse == "newer-failure":
        previous = next(r.data for r in snap.receipts(GCA_ITEM_ID)
                        if r.data.get("receipt_id") == clean[0].receipt)
        record = seal_receipt({**previous, "verdict": "fail", "reasons": ["independent negative check"],
                               "started_at": "2099-01-01T00:00:00Z",
                               "finished_at": "2099-01-01T00:00:01Z"})
        assert receipt_problems(record) == []
        path = source / "research/evidence" / GCA_ITEM_ID / f"{record['receipt_id'][:16]}.json"
        write_new_json(path, record)
    elif adverse == "adverse-fidelity":
        previous = next(r.data for r in snap.reviews(GCA_ITEM_ID)
                        if r.data.get("review_id") == clean[0].fidelity_review)
        record = seal_review({**previous, "verdict": "wrong-definition", "author": "agent:independent-referee",
                              "text": "The model does not represent the claimed interface.",
                              "created": "2099-01-01T00:00:00Z"})
        path = source / "research/reviews" / GCA_ITEM_ID / f"{record['review_id'][:16]}.json"
        write_new_json(path, record)
    else:
        path = source / "GCA/Proofs/Feedback.lean"
        path.write_text(path.read_text() + "\n-- Changed source after the pinned check.\n")
    _repin(root, commit_all(source, "admit adverse source snapshot"))
    resolved, errors = _resolve(root)
    assert resolved == [] and errors
    disabled = frozenset({"snapshot_status"})
    slipped, errors = _resolve(root, disabled)
    assert not errors and len(slipped) == 1


@pytest.mark.parametrize("location", ["target", "candidate-only"])
def test_usage_validation_detects_unlinked_target_and_candidate_modules(tmp_path, monkeypatch, location):
    """SPEC assigns missing lineage to validation; a proof check remains a separate contract."""
    root, _ = make_consumer_repo(tmp_path, with_imports=False,
                                 target_imports=[GCA_MODULE] if location == "target" else [],
                                 packages_dir_mode="config")
    # A matching declaration is the clean control: the same adapter is reachable.
    clean_root, _ = make_consumer_repo(tmp_path / "clean", packages_dir_mode="config")
    monkeypatch.setattr(check, "load_adapters", lambda: [_FakeLeanAdapter()])
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    clean_repo = Repo.open(clean_root)
    clean, receipt = check.execute(clean_repo, clean_repo.load_item("consequence"), "protected", 60)
    assert clean.verdict == "pass" and receipt is not None
    if location == "candidate-only":
        path = root / "Consumer/Proof.lean"
        path.write_text(f"import {GCA_MODULE}\n" + path.read_text())
        # Remains an uncommitted candidate edit, as protected checking permits.
    repo = Repo.open(root)
    item = repo.load_item("consequence")
    usage_errors = imports.check_consumer_lean_usage(repo, item)
    assert any("dependency without receipt" in error for error in usage_errors)
    assert imports.check_consumer_lean_usage(repo, item, guards=frozenset({"dependency_receipt"})) == []
    outcome, receipt = check.execute(repo, item, "protected", 60)
    assert outcome.verdict == "pass" and receipt is not None
    assert not receipt["inputs"].get("imports")


def test_custom_package_cannot_avoid_lineage_by_using_an_official_package_name(tmp_path):
    root, source = make_consumer_repo(tmp_path, with_imports=False, packages_dir_mode="config")
    repo = Repo.open(root)
    assert any("dependency without receipt" in error for error in
               imports.check_consumer_lean_usage(repo, repo.load_item("consequence")))
    # Same nonofficial URL, git objects and GCA module; only Lake's package name changes.
    source.rename(source.with_name("mathlib"))
    path = root / "lake-manifest.json"
    manifest = json.loads(path.read_text())
    manifest["packages"][0]["name"] = "mathlib"
    path.write_text(json.dumps(manifest))
    commit_all(root, "rename custom package to official-looking name")
    repo = Repo.open(root)
    problems = imports.check_consumer_lean_usage(repo, repo.load_item("consequence"))
    assert any("dependency without receipt" in error for error in problems), problems
