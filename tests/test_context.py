"""Context path configuration has explicit roles and is validated before integration."""
from dataclasses import replace
import json

import pytest

from conftest import commit_all, git
from verifylab.cli import main
from verifylab.config import CONFIG_INPUTS, ConfigError, config_digest
from verifylab.context import settings
from verifylab.repo import Repo


@pytest.mark.parametrize("raw,message", [
    ({"unknown": "research/other.md"}, "unknown keys"),
    ({"problem": "research/problem.md", "map": "research/problem.md"}, "distinct files"),
    ({"problem": "research/./problem.md", "ideas": "research/problem.md"}, "distinct files"),
    ({"problem": "research/map.md"}, "distinct files"),
    ({"problem": "../problem.md"}, "repository-relative"),
    ({"problem": "/tmp/problem.md"}, "repository-relative"),
    ({"problem": "research/.hidden/problem.md"}, "non-hidden"),
    ({"problem": "research/prob\0lem.md"}, "repository-relative"),
    ({"problem": "research/problem.txt"}, "Markdown"),
    ({"notes": "research"}, "must be a list"),
])
def test_context_settings_reject_invalid_paths_and_keys(research_repo, raw, message):
    config = Repo.open(research_repo).config
    with pytest.raises(ConfigError, match=message):
        settings(replace(config, raw={**config.raw, "context": raw}))


def test_context_settings_keep_distinct_custom_roles_and_proof_digests(research_repo):
    original = Repo.open(research_repo).config
    raw = {"problem": "notes/goal.md", "map": "notes/routes.md", "ideas": "notes/ideas.md", "notes": ["notes", "papers/notes.md"]}
    custom = replace(original, raw={**original.raw, "context": raw})
    assert settings(custom) == raw
    assert all(config_digest(key, original) == config_digest(key, custom) for key in CONFIG_INPUTS)
    defaults = settings(replace(original, research_dir="study", raw={}))
    assert defaults == {"problem": "study/problem.md", "map": "study/map.md", "ideas": "study/ideas.md", "notes": ["study"]}


@pytest.mark.parametrize("location", ["trusted", "worktree", "incoming"])
@pytest.mark.parametrize("bad,message", [
    ('problem="../outside.md"', "repository-relative"),
    ('problem="research/map.md"', "distinct files"),
    ('unexpected="research/other.md"', "unknown keys"),
])
def test_validate_checks_context_without_notes_or_appraisals(research_repo, monkeypatch, capsys, location, bad, message):
    root = research_repo
    monkeypatch.chdir(root)
    args = ["validate", "--json"]
    if location == "incoming":
        git(root, "checkout", "-qb", "new-context")
        args += ["--incoming", "new-context"]
    config = root / "research/vl.toml"
    config.write_text(config.read_text() + '\n[context]\n' + bad + '\n')
    if location in ("trusted", "incoming"):
        commit_all(root)
    if location == "incoming":
        git(root, "checkout", "-q", "trusted")
    assert main(args) == 1
    data = json.loads(capsys.readouterr().out)
    assert any("context" in error["message"] and message in error["message"] for error in data["errors"])


def test_validate_checks_changed_research_dir_used_by_default_context_paths(research_repo, monkeypatch, capsys):
    root = research_repo
    monkeypatch.chdir(root)
    config = root / "research/vl.toml"
    config.write_text(config.read_text().replace('[project]\n', '[project]\nresearch_dir=".hidden"\n'))
    assert main(["validate", "--json"]) == 1
    data = json.loads(capsys.readouterr().out)
    assert any("worktree context:" in error["message"] and "non-hidden" in error["message"] for error in data["errors"])
