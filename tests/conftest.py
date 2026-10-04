from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

import lean_helpers

CONFIG = """[project]
name = "fixture"

[lean]
roots = ["Fixture"]
"""

RESULT = """+++
id = "{id}"
kind = "result"
title = "A small result"
author = "agent:test"
created = "2026-10-01"
statement = "For every n, n + 0 = n."
claim = "formal"
limits = ["Only natural numbers."]
uses = {uses}
refutes = {refutes}
+++
Body text.
"""


def system_python() -> str:
    """A python3 the jail can run (under one of its read-only system folders), else the test is skipped."""
    import shutil
    from verifylab import jail
    found = shutil.which("python3", path="/usr/bin:/bin")
    resolved = os.path.realpath(found) if found else None
    if not resolved or not any(resolved.startswith(p + "/") for p in jail.SYSTEM_RO):
        pytest.skip(f"no python3 under {list(jail.SYSTEM_RO)}, so a jailed command cannot run one")
    return resolved


GATED_MARKERS = ("lean", "jail")


def gated_skips(skipped_reports) -> int:
    """How many skipped tests carry a marker the commit gate requires to run (GATED_MARKERS)."""
    return sum(1 for report in skipped_reports if any(m in getattr(report, "keywords", {}) for m in GATED_MARKERS))


def pytest_terminal_summary(terminalreporter):
    """The line scripts/gate reads: a skipped `lean` or `jail` test is a test the gate did not run."""
    terminalreporter.write_line(f"vl-gate: {gated_skips(terminalreporter.stats.get('skipped', []))} lean/jail tests skipped")


def git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, text=True).stdout


def write_item(root: Path, item_id: str, uses=(), refutes=()) -> Path:
    path = root / "research" / "items" / f"{item_id}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(RESULT.format(id=item_id, uses=list(uses), refutes=list(refutes)).replace("'", '"'))
    return path


@pytest.fixture
def research_repo(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    root.mkdir()
    git(root, "init", "-q", "-b", "trusted")
    git(root, "config", "vl.trustedRef", "trusted")
    git(root, "config", "user.name", "Test")
    git(root, "config", "user.email", "test@example.com")
    (root / "research").mkdir()
    (root / "research" / "vl.toml").write_text(CONFIG)
    (root / "Fixture").mkdir()
    (root / "Fixture" / "Basic.lean").write_text("theorem add_zero' (n : Nat) : n + 0 = n := rfl\n")
    target = root / "ReceiptFixture.lean"
    target.write_text("theorem receipt : True := sorry\n")
    (root / "Fixture" / "Evaluator.py").write_text("CASES = [1]\n")
    write_item(root, "add-zero")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "init")
    return root


def item_question(root: Path, item_id: str = "add-zero") -> str:
    """The question digest of the item as it is in the worktree, for hand-made receipts that answer it."""
    from verifylab.records import parse_item, question_digest
    rel = f"research/items/{item_id}.md"
    return question_digest(parse_item((root / rel).read_bytes(), rel))


def synthetic_receipt(root: Path | None, fields: dict) -> dict:
    """Generated-shaped test evidence; these fixtures do not claim a verifier ran."""
    from verifylab.records import canonical_json, seal_receipt, sha256_hex
    target_path = "ReceiptFixture.lean"
    digest = sha256_hex((root / target_path).read_bytes()) if root else "b" * 64
    commit = git(root, "rev-parse", "HEAD").strip() if root else "a" * 40
    target = {"path": target_path, "sha256": digest, "source": "trusted-commit", "commit": commit,
              "theorems": ["Fixture.receipt"]}
    checked = {"theorems": target["theorems"], "kernels": ["lean", "nanoda"], "permitted_axioms": []}
    tools = {name: {"path": f"/fixture/{name}", "sha256": "c" * 64}
             for name in ("comparator", "lean4export", "landrun", "nanoda")}
    environment = {"toolchain": "leanprover/lean4:v4.24.0", "lean_version": "Lean fixture",
                   "tools": tools, "isolation": {"kind": "bwrap+landrun"}}
    inputs = {"trusted_commit": commit, "trusted_files": {target_path: digest}, **fields.get("inputs", {})}
    inputs["trusted_files"] = {target_path: digest, **inputs["trusted_files"]} if isinstance(
        inputs["trusted_files"], dict) else inputs["trusted_files"]
    fields = {**fields, "inputs": inputs, "target": fields.get("target", target),
              "checked": {**checked, **fields.get("checked", {})},
              "environment": {**environment, **fields.get("environment", {})}}
    basis = {"files": inputs["files"], "trusted_files": inputs["trusted_files"], "target": fields["target"]}
    inputs["digest"] = "sha256:" + sha256_hex(canonical_json(basis).encode())
    return seal_receipt(fields)


def commit_all(root: Path, message: str = "update") -> None:
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", message)


def write_machine(tools: dict[str, str] | None = None, elan_home: str | None = None,
                  caches: dict[str, str] | None = None, check: dict[str, str] | None = None) -> Path:
    """Write this test's machine.toml (under its own XDG_CONFIG_HOME) and return its path."""
    from verifylab import machine
    path = machine.config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    machine.forget()      # vl reads the machine file once per process; this one rewrites it
    lines = ["[tools]", *(f"{name} = {json.dumps(value)}" for name, value in (tools or {}).items())]
    if elan_home:
        lines.append(f"elan_home = {json.dumps(elan_home)}")
    lines += ["", "[caches]", *(f"{json.dumps(k)} = {json.dumps(v)}" for k, v in (caches or {}).items())]
    if check:
        lines += ["", "[check]", *(f"{k} = {json.dumps(v)}" for k, v in check.items())]
    path.write_text("\n".join(lines) + "\n")
    return path


def lean_tools() -> dict[str, str]:
    """The Comparator tools the Lean tests use (lean_helpers.TOOL_PATHS), by machine.toml key."""
    return {name: str(path) for name, path in lean_helpers.TOOL_PATHS.items()}


def lean_elan_home() -> str:
    """Where the Lean toolchains are: this account's machine.toml elan_home, else $ELAN_HOME, else ~/.elan."""
    account = lean_helpers.ACCOUNT.elan_home if lean_helpers.ACCOUNT else None
    return str(Path(account or os.environ.get("ELAN_HOME") or Path.home() / ".elan").resolve())


@pytest.fixture(scope="session")
def shared_cases(tmp_path_factory) -> lean_helpers.SharedCases:
    """Planted Lean cases run once per session per settings, for the tests that only read their outcome."""
    return lean_helpers.SharedCases(tmp_path_factory)


@pytest.fixture(scope="session")
def pinned_tools(tmp_path_factory) -> dict[str, str]:
    """The Lean tests' tools as a protected check accepts them: links in one folder whose REVISIONS file pins the
    sha256 of each (computed once per session), by machine.toml key."""
    from verifylab import machine
    folder = tmp_path_factory.mktemp("pinned-tools")
    links, pins = {}, {}
    for name, path in lean_tools().items():
        (folder / name).symlink_to(path)
        links[name], pins[name] = str(folder / name), machine.file_sha256(Path(path))
    if pins:
        machine.write_revisions(folder, pins)
    return links


@pytest.fixture(autouse=True)
def machine_home(tmp_path_factory, monkeypatch, pinned_tools):
    """Every test has its own XDG config and data homes, never the real ~/.config/verifylab. Its machine.toml names
    the pinned tools the Lean tests use, since a protected check takes them from there, pinned, and never from
    COMPARATOR_*."""
    base = tmp_path_factory.mktemp("xdg")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(base / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(base / "data"))
    write_machine(pinned_tools, elan_home=lean_elan_home())
    return base
