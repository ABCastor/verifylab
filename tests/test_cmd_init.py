from __future__ import annotations

import json
import tomllib
from pathlib import Path

import pytest

from verifylab.cli import main
from verifylab.config import load_config

from conftest import git

AGENTS = "# Project rules\n\nKeep this file exactly as it is.\n"


def vl(capsys, *args):
    rc = main(list(args))
    captured = capsys.readouterr()
    return rc, captured.out, captured.err


@pytest.fixture
def fresh_repo(tmp_path: Path) -> Path:
    root = tmp_path / "fresh"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.name", "Test")
    git(root, "config", "user.email", "test@example.com")
    (root / "Proofs" / "Sub").mkdir(parents=True)
    (root / "Proofs" / "Sub" / "A.lean").write_text("theorem a : True := trivial\n")
    (root / "notes").mkdir()
    (root / "notes" / "x.md").write_text("no lean here\n")
    (root / ".lake" / "packages").mkdir(parents=True)
    (root / ".lake" / "packages" / "M.lean").write_text("-- vendored\n")
    (root / "AGENTS.md").write_text(AGENTS)
    (root / "lakefile.lean").write_text("-- lake\n")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "init")
    return root


def test_init_creates_the_layout_and_leaves_agents_alone(fresh_repo, monkeypatch, capsys):
    root = fresh_repo
    monkeypatch.chdir(root / "notes")  # works from a subdirectory: it uses the git top level
    rc, out, _ = vl(capsys, "init")
    assert rc == 0
    for folder in ("items", "targets", "evidence", "reviews", "evaluators"):
        assert (root / "research" / folder / ".gitkeep").is_file()
    config = load_config(root)
    assert config.trusted_ref == "main" and config.lean.roots == ("Proofs",)
    assert config.trusted_ref_source == "git config vl.trustedRef"
    assert git(root, "config", "--local", "--get", "vl.trustedRef").strip() == "main"
    project = tomllib.loads((root / "research" / "vl.toml").read_text())["project"]
    assert project["name"] == "fresh" and "trusted_ref" not in project
    assert ".vl-cache/" in (root / ".gitignore").read_text().splitlines()
    assert (root / "AGENTS.md").read_text() == AGENTS
    assert "AGENTS.md: not modified" in out and "WARNING" not in out
    assert "<!-- vl:begin -->" in out and "vl validate" in out and "<!-- vl:end -->" in out
    rules = [line for line in out.splitlines() if line[:2] in {f"{n}." for n in range(1, 10)}]
    assert len(rules) == 7


def test_init_refuses_twice(fresh_repo, monkeypatch, capsys):
    root = fresh_repo
    monkeypatch.chdir(root)
    rc, out, _ = vl(capsys, "init", "--trusted-ref", "trusted-branch")
    assert rc == 0 and "WARNING: trusted ref 'trusted-branch' does not resolve" in out
    before = (root / "research" / "vl.toml").read_bytes()
    gitignore = (root / ".gitignore").read_bytes()
    rc, _, err = vl(capsys, "init", "--write-agents")
    assert rc == 2 and "already exists" in err
    assert (root / "research" / "vl.toml").read_bytes() == before
    assert (root / ".gitignore").read_bytes() == gitignore
    assert (root / "AGENTS.md").read_text() == AGENTS
    assert load_config(root).trusted_ref == "trusted-branch"
    assert git(root, "config", "--local", "--get", "vl.trustedRef").strip() == "trusted-branch"


def test_init_refuses_in_an_initialised_repository(research_repo, monkeypatch, capsys):
    monkeypatch.chdir(research_repo)
    assert vl(capsys, "init")[0] == 2


def test_no_agents_file_is_created_without_the_flag(fresh_repo, monkeypatch, capsys):
    root = fresh_repo
    (root / "AGENTS.md").unlink()
    monkeypatch.chdir(root)
    assert vl(capsys, "init")[0] == 0
    assert not (root / "AGENTS.md").exists()


def test_write_agents_appends_once_between_markers(fresh_repo, monkeypatch, capsys):
    root = fresh_repo
    (root / ".gitignore").write_text("*.olean")  # no trailing newline: the cache line must go on its own line
    monkeypatch.chdir(root)
    rc, out, _ = vl(capsys, "init", "--write-agents", "--json")
    data = json.loads(out)
    assert rc == 0 and "appended" in data["agents"]
    text = (root / "AGENTS.md").read_text()
    assert text.startswith(AGENTS) and text.count("<!-- vl:begin -->") == 1 and text.count("<!-- vl:end -->") == 1
    assert (root / ".gitignore").read_text() == "*.olean\n.vl-cache/\n"


def test_write_agents_leaves_an_existing_section_alone(fresh_repo, monkeypatch, capsys):
    root = fresh_repo
    custom = AGENTS + "\n<!-- vl:begin -->\nmy own wording\n<!-- vl:end -->\n"
    (root / "AGENTS.md").write_text(custom)
    (root / ".gitignore").write_text(".vl-cache/\n")
    monkeypatch.chdir(root)
    rc, out, _ = vl(capsys, "init", "--write-agents")
    assert rc == 0 and "already has a vl section" in out and "already ignores" in out
    assert (root / "AGENTS.md").read_text() == custom
    assert (root / ".gitignore").read_text() == ".vl-cache/\n"


def test_init_outside_git_is_a_tool_failure(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))
    assert vl(capsys, "init")[0] == 2
    assert not (tmp_path / "research").exists()
