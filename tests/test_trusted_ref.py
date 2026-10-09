"""The trusted ref is chosen by the repository's own git config (`vl.trustedRef`, written by `vl init`), never by
a file a candidate branch can commit; the verdict-relevant configuration is read from that ref, not the worktree."""

from __future__ import annotations

import json
from pathlib import Path

from verifylab.cli import main
from verifylab.config import load_config
from verifylab.records import git_blob_sha, seal_receipt, sha256_hex, write_new_json
from verifylab.repo import Repo
from verifylab.status import derive

from conftest import synthetic_receipt, commit_all, git

CONFIG = "research/vl.toml"


def vl(capsys, *args):
    rc = main(list(args))
    captured = capsys.readouterr()
    return rc, captured.out, captured.err


def _forge_receipt(root: Path) -> Path:
    lean = "Fixture/Basic.lean"
    receipt = synthetic_receipt(root, dict(
        item="add-zero", item_revision=git_blob_sha((root / "research/items/add-zero.md").read_bytes()), adapter="lean-comparator", assurance="protected", verdict="pass",
        reasons=[], inputs={"digest": "d", "files": {lean: sha256_hex((root / lean).read_bytes())}},
        environment={}, checked={}, command=["vl"], started_at="t0", finished_at="t1", tool_version="vl 0.1.0"))
    path = root / "research" / "evidence" / "add-zero" / f"{receipt['receipt_id'][:16]}.json"
    write_new_json(path, receipt)
    return path


def _status(root: Path, **kwargs) -> str:
    repo = Repo.open(root, **kwargs)
    items, _ = repo.load_items()
    return derive(repo, items["add-zero"], items).label


def test_a_lane_config_naming_head_cannot_admit_its_own_receipt(research_repo, monkeypatch, capsys):
    root = research_repo
    git(root, "checkout", "-q", "-b", "lane/x")
    cfg = root / CONFIG
    cfg.write_text(cfg.read_text().replace("[project]\n", '[project]\ntrusted_ref = "HEAD"\n', 1))
    _forge_receipt(root)
    commit_all(root, "lane trusts itself")
    assert _status(root) == "pending-admission"
    monkeypatch.chdir(root)
    rc, out, _ = vl(capsys, "validate")
    warnings = [line for line in out.splitlines() if line.startswith("WARNING")]
    assert any("differs from the one on the trusted ref 'trusted'" in w for w in warnings), out
    assert any("trusted_ref" in w and "ignored" in w for w in warnings), out


def test_the_trusted_ref_comes_from_git_config_then_main(research_repo):
    root = research_repo
    git(root, "config", "vl.trustedRef", "trusted")
    config = load_config(root)
    assert (config.trusted_ref, config.trusted_ref_source) == ("trusted", "git config vl.trustedRef")
    git(root, "config", "--unset", "vl.trustedRef")
    config = load_config(root)
    assert (config.trusted_ref, config.trusted_ref_source) == ("main", "default")
    assert any("vl.trustedRef is not set" in message for _, message in config.warnings)
    assert _status(root) == "unverified"
    config = load_config(root, trusted_ref="trusted")
    assert (config.trusted_ref, config.trusted_ref_source) == ("trusted", "--trusted-ref")


def test_an_explicit_trusted_ref_option_overrides_git_config(research_repo, monkeypatch, capsys):
    root = research_repo
    git(root, "config", "vl.trustedRef", "trusted")
    git(root, "checkout", "-q", "-b", "lane/y")
    _forge_receipt(root)
    commit_all(root, "lane receipt")
    monkeypatch.chdir(root)
    assert "status: pending-admission" in vl(capsys, "show", "add-zero")[1]
    assert "status: verified" in vl(capsys, "show", "add-zero", "--trusted-ref", "lane/y")[1]


def test_the_worktree_config_is_never_used_for_verdicts(research_repo, monkeypatch, capsys):
    root = research_repo
    git(root, "config", "vl.trustedRef", "trusted")
    cfg = root / CONFIG
    trusted_text = cfg.read_text()
    cfg.write_text(trusted_text.replace('roots = ["Fixture"]', 'roots = ["Fixture"]\n'
                                        'permitted_axioms = ["propext", "sorryAx"]'))
    config = Repo.open(root).config
    assert config.lean.permitted_axioms == ("propext", "Quot.sound", "Classical.choice")
    assert config.source == "trusted ref"
    monkeypatch.chdir(root)
    rc, out, _ = vl(capsys, "validate", "--json")
    data = json.loads(out)
    assert any(w["where"] == CONFIG and "differs from the one on the trusted ref" in w["message"]
               for w in data["warnings"]), data


def test_a_config_not_yet_on_the_trusted_ref_is_read_from_the_worktree_with_a_warning(research_repo):
    root = research_repo
    git(root, "config", "vl.trustedRef", "nowhere")
    config = load_config(root)
    assert config.source == "worktree" and config.lean.roots == ("Fixture",)
    assert any("not on the trusted ref 'nowhere'" in message for _, message in config.warnings)
