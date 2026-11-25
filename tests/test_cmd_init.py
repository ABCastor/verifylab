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
    assert "<!-- vl:begin -->" in out and "load the `verifylab` skill" in out and "<!-- vl:end -->" in out


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


def test_optional_context_keeps_existing_problem_and_links_agents(fresh_repo, monkeypatch, capsys):
    root = fresh_repo
    (root / "research").mkdir()
    (root / "research" / "problem.md").write_text("Owner's existing problem.\n")
    monkeypatch.chdir(root)
    rc, out, _ = vl(capsys, "init", "--context", "--write-agents", "--json")
    data = json.loads(out)
    assert rc == 0
    assert (root / "research" / "problem.md").read_text() == "Owner's existing problem.\n"
    assert data["context_kept"] == ["research/problem.md"]
    for role in ("problem", "map", "ideas"):
        assert (root / "research" / f"{role}.md").is_file()
        assert f"{role}: `research/{role}.md`" in (root / "AGENTS.md").read_text()


def test_context_refuses_escape_before_initializing(fresh_repo, tmp_path, monkeypatch, capsys):
    root = fresh_repo
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "research").symlink_to(outside, target_is_directory=True)
    monkeypatch.chdir(root)
    rc, _, err = vl(capsys, "init", "--context")
    assert rc == 2 and "escapes the repository" in err
    assert list(outside.iterdir()) == []


def test_init_skills_symlinks_and_copies_idempotently(research_repo, tmp_path, monkeypatch, capsys):
    home = tmp_path / "fake-home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.chdir(research_repo)

    # Installs into ~/.claude/skills when research/ already exists
    rc, out, err = vl(capsys, "init", "--skills", "claude")
    assert rc == 0 and err == ""
    assert "1 installed" in out
    claude_dir = home / ".claude" / "skills"
    installed = sorted(p.name for p in claude_dir.iterdir())
    assert installed == ["verifylab"]                               # one skill, its references inside it
    assert all((claude_dir / name).is_symlink() and (claude_dir / name / "SKILL.md").is_file() for name in installed)
    assert (claude_dir / "verifylab/references/study.md").is_file()

    # Idempotent rerun
    rc, out, _ = vl(capsys, "init", "--skills", "claude", "--json")
    assert rc == 0
    data = json.loads(out)
    assert data["installed"] == [] and data["kept"] == ["verifylab"] and data["skills"] == ["verifylab"]

    # Copy mode to explicit path + idempotent rerun
    custom = tmp_path / "copied-skills"
    rc, out, _ = vl(capsys, "init", "--skills", str(custom), "--copy")
    assert rc == 0 and "1 installed" in out
    assert all((custom / name).is_dir() and not (custom / name).is_symlink() for name in installed)
    assert (custom / "verifylab" / "references" / "referee.md").is_file()
    rc, out, _ = vl(capsys, "init", "--skills", str(custom), "--copy", "--json")
    assert rc == 0 and json.loads(out)["kept"] == ["verifylab"]
    (custom / "verifylab" / "references" / "referee.md").write_text("edited")           # a stale copy is replaced whole
    rc, out, _ = vl(capsys, "init", "--skills", str(custom), "--copy", "--json")
    assert rc == 0 and json.loads(out)["updated"] == ["verifylab"]
    assert (custom / "verifylab" / "references" / "referee.md").read_text().startswith("# Find traps")

    assert vl(capsys, "init", "--copy")[0] == 2


def test_init_skills_refuses_conflict_before_partial_install(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HOME", str(tmp_path))
    dest = tmp_path / "dest-skills"
    dest.mkdir()
    (dest / "verifylab").mkdir()    # non-symlink conflict

    rc, _, err = vl(capsys, "init", "--skills", str(dest))
    assert rc == 2 and "not a symlink" in err
    assert [p.name for p in dest.iterdir()] == ["verifylab"]

    # Control: removing the blocking folder allows clean install
    (dest / "verifylab").rmdir()
    rc, _, _ = vl(capsys, "init", "--skills", str(dest))
    assert rc == 0 and [p.name for p in dest.iterdir()] == ["verifylab"]


def test_existing_project_can_emit_current_instructions_without_writes(fresh_repo, monkeypatch, capsys):
    monkeypatch.chdir(fresh_repo)
    rc, _, _ = vl(capsys, "init", "--context", "--json")
    assert rc == 0
    before = git(fresh_repo, "status", "--porcelain")
    rc, out, err = vl(capsys, "init", "--print-agents", "--json")
    assert rc == 0, err
    snippet = json.loads(out)["snippet"]
    assert "research/problem.md" in snippet and "<!-- vl:context -->" not in snippet
    assert "load the `verifylab` skill" in snippet
    assert "`--status`" not in snippet and "MemoryMax" not in snippet
    assert git(fresh_repo, "status", "--porcelain") == before
    assert (fresh_repo / "AGENTS.md").read_text() == AGENTS


def test_codex_and_pi_install_the_same_version(tmp_path, monkeypatch, capsys):
    from verifylab import __version__

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    pi = tmp_path / "pi" / "agent" / "skills"
    contents = []
    for target, path in (("codex", tmp_path / ".codex" / "skills"), (str(pi), pi)):
        rc, out, err = vl(capsys, "init", "--skills", target, "--copy", "--json")
        assert rc == 0 and not err
        assert json.loads(out)["version"] == __version__
        contents.append((path / "verifylab/SKILL.md").read_text())
    assert contents[0] == contents[1]
    assert f'version: "{__version__}"' in contents[0]


@pytest.mark.parametrize("phase", ["first-copy", "upgrade-copy", "upgrade-rename"])
def test_failed_skill_install_preserves_the_previous_copy(tmp_path, monkeypatch, capsys, phase):
    import shutil

    dest = tmp_path / "skills"
    args = "init", "--skills", str(dest), "--copy"
    live = dest / "verifylab"
    note = live / "references/study.md"
    if phase.startswith("upgrade"):
        assert vl(capsys, *args)[0] == 0
        note.write_text("previous version")

    copy = shutil.copytree
    rename = Path.rename

    def truncated(src, path, **kwargs):
        if Path(src).name == "verifylab":
            Path(path).mkdir()
            (Path(path) / "SKILL.md").touch()
            raise OSError("no space during copy")
        return copy(src, path, **kwargs)

    def failed_rename(path, target):
        if path.name == "incoming":
            raise OSError("cannot publish")
        return rename(path, target)

    with monkeypatch.context() as patch:
        if phase == "upgrade-rename":
            patch.setattr(Path, "rename", failed_rename)
        else:
            patch.setattr(shutil, "copytree", truncated)
        assert vl(capsys, *args)[0] == 2
    if phase.startswith("upgrade"):
        assert note.read_text() == "previous version"
    else:
        assert not live.exists()
    assert vl(capsys, *args)[0] == 0 and note.read_text() != "previous version"
