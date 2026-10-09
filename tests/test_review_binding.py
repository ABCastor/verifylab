"""Meaning context is an explicit read/write precondition, not a claim about reviewer understanding."""
from __future__ import annotations

import json

import pytest

from verifylab.cli import main
from verifylab.records import seal_review, write_new_json
from verifylab.repo import Repo
from verifylab.status import fidelity, meaning, meaning_digest
from conftest import commit_all, git


def target(root):
    item = root / "research/items/add-zero.md"
    item.write_text(item.read_text().replace("+++\nBody", '[lean]\ntarget = "research/targets/t.lean"\n'
                   'theorems = ["main"]\nproofs = { main = "by rfl" }\n+++\nBody'))
    path = root / "research/targets/t.lean"
    path.parent.mkdir(parents=True)
    path.write_text("theorem main (n : Nat) : n = n := sorry\n")
    commit_all(root, "admit target")


def basis(root):
    repo = Repo.open(root)
    return meaning(repo, repo.trusted_item("add-zero"))


def review_args(digest=None):
    args = ["review", "add-zero", "--kind", "fidelity", "--verdict", "faithful", "--author", "agent:ref",
            "--text", "The target states the intended equality"]
    return args + (["--expected-meaning-digest", digest] if digest is not None else [])


def review_and_admit(root, capsys):
    assert main(review_args(meaning_digest(basis(root)))) == 0
    capsys.readouterr()
    commit_all(root, "admit review")


def state(root):
    repo = Repo.open(root)
    return fidelity(repo, repo.load_item("add-zero"))


@pytest.mark.parametrize("filename,content", [
    ("lean-toolchain", "leanprover/lean4:v4.34.0\n"),
    ("lake-manifest.json", '{"packages":[{"name":"mathlib","rev":"new"}]}'),
    ("lakefile.toml", 'leanOptions = {autoImplicit = false}\n'),
    ("lakefile.lean", '-- arbitrary build logic\n'),
])
def test_environment_addition_and_edit_stale_a_review(research_repo, monkeypatch, capsys, filename, content):
    root = research_repo
    target(root)
    monkeypatch.chdir(root)
    review_and_admit(root, capsys)
    assert state(root).label == "faithful"
    (root / filename).write_text(content)
    commit_all(root, "admit changed environment")
    assert state(root).label == "review stale: semantic environment changed since review"
    review_and_admit(root, capsys)
    assert state(root).label == "faithful"
    (root / filename).unlink()
    commit_all(root, "remove environment file")
    # The original fully bound review is valid again when its exact context is restored.
    assert state(root).label == "faithful"


def test_cache_placement_and_unpropagated_toml_fields_do_not_change_meaning(research_repo, monkeypatch, capsys):
    root = research_repo
    target(root)
    manifest = root / "lake-manifest.json"
    lakefile = root / "lakefile.toml"
    manifest.write_text('{"packagesDir":"/cache/old","packages":[{"name":"mathlib","rev":"r1"}]}')
    lakefile.write_text('packagesDir = "/cache/old"\nleanOptions = {autoImplicit = false}\n')
    commit_all(root, "stable dependencies and options")
    monkeypatch.chdir(root)
    before = basis(root)
    review_and_admit(root, capsys)
    manifest.write_text(manifest.read_text().replace("/cache/old", "/cache/new"))
    lakefile.write_text(lakefile.read_text().replace("/cache/old", "/cache/new"))
    config = root / "research/vl.toml"
    config.write_text(config.read_text() + 'cache = "/cache/new"\npackages = "/other/cache"\n')
    commit_all(root, "move caches")
    assert basis(root) == before and state(root).label == "faithful"
    manifest.write_text(manifest.read_text().replace('"r1"', '"r2"'))
    commit_all(root, "change external stable revision")
    assert state(root).label == "review stale: semantic environment changed since review"


def test_project_relative_files_roots_and_per_root_options_are_bound(research_repo, monkeypatch, capsys):
    root = research_repo
    target(root)
    project = root / "lean-project"
    project.mkdir()
    config = root / "research/vl.toml"
    config.write_text(config.read_text() + 'project = "lean-project"\n')
    (project / "lakefile.toml").write_text('[[lean_lib]]\nname = "Fixture"\nleanOptions = {autoImplicit = false}\n')
    (project / "lean-toolchain").write_text("leanprover/lean4:v4.34.0\n")
    commit_all(root, "project with options")
    environment = basis(root)["semantic_environment"]
    assert "lean-project/lean-toolchain" in environment and "lean-toolchain" not in environment
    monkeypatch.chdir(root)
    review_and_admit(root, capsys)
    (project / "lakefile.toml").write_text('[[lean_lib]]\nname = "Fixture"\nleanOptions = {autoImplicit = true}\n')
    commit_all(root, "change root options")
    assert state(root).label.startswith("review stale: semantic environment")
    review_and_admit(root, capsys)
    config.write_text(config.read_text().replace('roots = ["Fixture"]', 'roots = ["Fixture", "Other"]'))
    commit_all(root, "change roots")
    assert state(root).label.startswith("review stale: semantic environment")


@pytest.mark.parametrize("filename,content", [("lake-manifest.json", "{bad"), ("lakefile.toml", "bad [")])
def test_invalid_environment_binds_raw_bytes_and_diagnostic(research_repo, filename, content):
    root = research_repo
    target(root)
    path = root / filename
    path.write_text(content)
    commit_all(root, "invalid environment")
    before = basis(root)
    assert filename in before["semantic_environment_error"]
    path.write_text(content + "different")
    commit_all(root, "different invalid environment")
    assert meaning_digest(basis(root)) != meaning_digest(before)


@pytest.mark.parametrize("narrow", ["environment", "target-only"])
def test_historical_narrow_review_is_readable_stale_and_renewable(research_repo, monkeypatch, capsys, narrow):
    root = research_repo
    target(root)
    current = basis(root)
    fields = dict(item="add-zero", item_revision=git(root, "hash-object", "research/items/add-zero.md").strip(),
                  kind="fidelity", author="agent:old", text="older scope", verdict="faithful",
                  created="2026-10-01T12:00:00+00:00", target_path="research/targets/t.lean",
                  target_sha256=current["files"]["research/targets/t.lean"])
    if narrow == "environment":
        old = {k: v for k, v in current.items() if k != "semantic_environment"}
        fields.update(meaning=old, meaning_digest=meaning_digest(old))
    record = seal_review(fields)
    write_new_json(root / "research/reviews/add-zero" / (record["review_id"][:16] + ".json"), record)
    commit_all(root, "admit historical review")
    assert Repo.open(root).reviews("add-zero")[0].problems == ()
    assert state(root).label == "review stale: semantic environment not bound; review again"
    monkeypatch.chdir(root)
    review_and_admit(root, capsys)
    assert state(root).label == "faithful"


def test_show_digest_and_dry_run_are_read_only_discovery(research_repo, monkeypatch, capsys):
    root = research_repo
    target(root)
    monkeypatch.chdir(root)
    assert main(["show", "add-zero", "--json"]) == 0
    card = json.loads(capsys.readouterr().out)["items"][0]
    assert card["meaning"] == basis(root) and card["meaning_digest"] == meaning_digest(basis(root))
    assert main(review_args() + ["--dry-run", "--json"]) == 0
    preview = json.loads(capsys.readouterr().out)["review"]
    assert preview["meaning_digest"] == card["meaning_digest"]
    assert preview["trusted_commit"] == card["trust"]["commit"]
    assert not (root / "research/reviews").exists()
    assert main(review_args()) == 2
    assert "requires --expected-meaning-digest" in capsys.readouterr().err
    assert main(review_args(card["meaning_digest"])) == 0


def test_changed_meaning_between_read_and_write_rejects_without_record(research_repo, monkeypatch, capsys):
    root = research_repo
    target(root)
    monkeypatch.chdir(root)
    digest = meaning_digest(basis(root))
    (root / "lean-toolchain").write_text("changed version\n")
    commit_all(root, "change after read")
    assert main(review_args(digest)) == 2
    assert "expected meaning digest differs" in capsys.readouterr().err
    assert not (root / "research/reviews").exists()
    # Plant the same case with the guard disabled: it incorrectly signs the newly read context.
    from verifylab.commands import review
    monkeypatch.setattr(review, "expected_meaning_problem", lambda *_: None)
    assert main(review_args(digest)) == 0
    assert list((root / "research/reviews/add-zero").glob("*.json"))


def test_trusted_ref_move_during_write_keeps_one_snapshot(research_repo, monkeypatch, capsys):
    from verifylab.commands import review
    root = research_repo
    target(root)
    monkeypatch.chdir(root)
    old_commit = git(root, "rev-parse", "HEAD").strip()
    old_basis = basis(root)
    original = review.meaning

    def concurrent_move(repo, item):
        (root / "lean-toolchain").write_text("changed concurrently\n")
        commit_all(root, "move trusted ref inside command")
        return original(repo, item)

    monkeypatch.setattr(review, "meaning", concurrent_move)
    assert main(review_args(meaning_digest(old_basis)) + ["--json"]) == 0
    record = json.loads(capsys.readouterr().out)["review"]
    assert record["meaning"] == old_basis and record["trusted_commit"] == old_commit
    commit_all(root, "admit snapshot review after move")
    assert state(root).label == "review stale: semantic environment changed since review"


def test_show_ref_move_during_read_keeps_digest_and_trust_commit_coherent(research_repo, monkeypatch, capsys):
    from verifylab import render
    root = research_repo
    target(root)
    monkeypatch.chdir(root)
    old_commit = git(root, "rev-parse", "HEAD").strip()
    old_basis = basis(root)
    original = render.meaning

    def concurrent_move(repo, item):
        (root / "lean-toolchain").write_text("changed concurrently\n")
        commit_all(root, "move trusted ref inside show")
        return original(repo, item)

    monkeypatch.setattr(render, "meaning", concurrent_move)
    assert main(["show", "add-zero", "--json"]) == 0
    card = json.loads(capsys.readouterr().out)["items"][0]
    assert card["meaning"] == old_basis and card["trust"]["commit"] == old_commit
    assert card["meaning_digest"] == meaning_digest(old_basis)


def test_removing_a_file_without_an_older_matching_review_stales_it(research_repo, monkeypatch, capsys):
    root = research_repo
    target(root)
    (root / "lean-toolchain").write_text("leanprover/lean4:v4.34.0\n")
    commit_all(root, "admit toolchain")
    monkeypatch.chdir(root)
    review_and_admit(root, capsys)
    (root / "lean-toolchain").unlink()
    commit_all(root, "remove toolchain")
    assert state(root).label == "review stale: semantic environment changed since review"


def test_with_environment_guard_disabled_a_changed_toolchain_keeps_review(research_repo, monkeypatch, capsys):
    from verifylab import status
    root = research_repo
    target(root)
    monkeypatch.chdir(root)
    original = status.semantic_environment(Repo.open(root))
    monkeypatch.setattr(status, "semantic_environment", lambda _: original)
    review_and_admit(root, capsys)
    (root / "lean-toolchain").write_text("different toolchain\n")
    commit_all(root, "change environment with guard disabled")
    assert state(root).label == "faithful"


def test_expected_digest_only_applies_to_fidelity(research_repo, monkeypatch, capsys):
    monkeypatch.chdir(research_repo)
    assert main(["review", "add-zero", "--kind", "understanding", "--author", "agent:ref", "--text", "x",
                 "--expected-meaning-digest", "sha256:" + "0" * 64]) == 2
    assert "only applies to --kind fidelity" in capsys.readouterr().err


def test_trusted_ref_override_checks_the_explicit_snapshot(research_repo, monkeypatch, capsys):
    root = research_repo
    target(root)
    monkeypatch.chdir(root)
    trusted_commit = git(root, "rev-parse", "HEAD").strip()
    expected = meaning_digest(basis(root))
    git(root, "checkout", "-q", "-b", "alternate")
    (root / "lean-toolchain").write_text("different branch toolchain\n")
    commit_all(root, "different semantic environment on alternate")
    assert main(review_args(expected) + ["--trusted-ref", "alternate"]) == 2
    assert "expected meaning digest differs" in capsys.readouterr().err
    assert not (root / "research/reviews").exists()
    assert main(review_args(expected) + ["--json"]) == 0
    record = json.loads(capsys.readouterr().out)["review"]
    assert record["trusted_ref"] == "trusted" and record["trusted_commit"] == trusted_commit


def test_path_dependency_identity_remains_bound(research_repo, monkeypatch, capsys):
    root = research_repo
    target(root)
    manifest = root / "lake-manifest.json"
    manifest.write_text('{"packages":[{"name":"local","type":"path","dir":"../defs-one"}]}')
    commit_all(root, "local dependency identity")
    monkeypatch.chdir(root)
    review_and_admit(root, capsys)
    manifest.write_text(manifest.read_text().replace("defs-one", "defs-two"))
    commit_all(root, "change path dependency identity")
    assert state(root).label == "review stale: semantic environment changed since review"
