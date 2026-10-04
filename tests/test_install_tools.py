"""Installer preflight controls, with local Git repositories and fake build commands (no downloads)."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/install-tools.sh"
PINS = {
    "comparator": "19e111e2141cf333c7daff0f64c5f24acc91dd2e",
    "lean4export": "cacf989bd75f608700820f6afc595f32e7a99a4d",
    "landrun": "811cfff51ceaf3d9843708aa6d22e9b84ccac8b4",
    "nanoda": "4c544ed4099c8227f07d5de77ad1e69fb0740a27",
}


def _executable(path: Path, text: str) -> None:
    path.write_text(text)
    path.chmod(0o755)


@pytest.fixture
def installer(tmp_path):
    """Synthetic source commits: the Git wrapper supplies expected upstream ids, preserving real dirt checks."""
    git = shutil.which("git")
    assert git is not None
    build = tmp_path / "sources"
    identities = {}
    paths = {name: build / name for name in PINS}
    paths["dependency"] = build / "comparator/.lake/packages/lean4export"
    for name, path in paths.items():
        path.mkdir(parents=True)
        (path / ".gitignore").write_text(".lake/\ntarget/\n")
        (path / "Main.lean").write_text("-- trusted source\n")
        for args in (("init", "-q"), ("add", "."),
                     ("-c", "user.name=Installer test", "-c", "user.email=test@invalid",
                      "commit", "-qm", "source control")):
            subprocess.run([git, "-C", str(path), *args], check=True, capture_output=True)
        identities[str(path)] = PINS["lean4export" if name == "dependency" else name]
    bin_dir = tmp_path / "commands"
    bin_dir.mkdir()
    log = tmp_path / "build.log"
    _executable(bin_dir / "git", f'''#!/usr/bin/python3
import json, os, subprocess, sys
identities = json.loads({json.dumps(json.dumps(identities))})
args = sys.argv[1:]
if args[:1] == ["-C"] and args[2:] == ["rev-parse", "HEAD"]:
    value = identities[args[1]]
    if os.environ.get("INSTALL_TEST_WRONG_PIN") == args[1]: value = "0" * 40
    print(value)
    raise SystemExit(0)
if os.environ.get("INSTALL_TEST_DISABLE_CLEANLINESS") and ("diff" in args or "ls-files" in args):
    raise SystemExit(0)
if "clone" in args or "fetch" in args:
    raise SystemExit("test forbids Git downloads")
raise SystemExit(subprocess.call([{json.dumps(git)}, *args]))
''')
    # Build tools make tiny executable artifacts. Each call is logged, so a rejected source must never reach one.
    runner = '''#!/usr/bin/python3
import os, pathlib, sys
name = pathlib.Path(sys.argv[0]).name
args = sys.argv[1:]
with open(os.environ["INSTALL_TEST_LOG"], "a") as log: log.write(name + " " + " ".join(args) + "\\n")
def output(path):
    path = pathlib.Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\\nexit 0\\n"); path.chmod(0o755)
if name == "elan": print(pathlib.Path(sys.argv[0]).with_name("lake"))
elif name == "lake":
    if args == ["--version"]: print("Lake test compiler")
    else: output(pathlib.Path.cwd() / ".lake/build/bin" / args[-1])
elif name == "go":
    if args == ["version"]: print("go version test")
    else: output(args[args.index("-o") + 1])
elif name == "cargo":
    if args == ["--version"]: print("cargo test")
    else: output(pathlib.Path.cwd() / "target/release/nanoda_bin")
'''
    for name in ("elan", "lake", "go", "cargo", "uv"):
        _executable(bin_dir / name, runner)
    env = {**os.environ, "PATH": str(bin_dir) + os.pathsep + os.environ["PATH"],
           "INSTALL_TEST_LOG": str(log)}

    def run():
        return subprocess.run([str(SCRIPT), "--offline", str(build)], env=env,
                              capture_output=True, text=True, timeout=20)

    return build, env, log, run


def test_offline_install_refuses_missing_source_without_downloading(installer):
    build, _, log, run = installer
    shutil.rmtree(build / "comparator")
    result = run()
    assert result.returncode == 2 and "offline mode needs a checkout" in result.stderr
    assert not log.exists()


def test_install_refuses_a_source_at_another_revision(installer, tmp_path, monkeypatch):
    build, env, log, run = installer
    env["INSTALL_TEST_WRONG_PIN"] = str(build / "comparator")
    result = run()
    assert result.returncode == 2 and "wrong revision" in result.stderr
    assert not log.exists()
    disabled = tmp_path / "disabled/scripts/install-tools.sh"
    disabled.parent.mkdir(parents=True)
    _executable(disabled, SCRIPT.read_text().replace(
        'if [[ $(git -C "$directory" rev-parse HEAD) != "$revision" ]]; then', 'if false; then'))
    toolchain = disabled.parents[1] / "fixtures/lean-planted/lean-toolchain"
    toolchain.parent.mkdir(parents=True)
    toolchain.write_text("leanprover/lean4:v4.34.0-rc2\n")
    monkeypatch.setitem(globals(), "SCRIPT", disabled)
    assert run().returncode == 0  # the planted wrong revision slips through when its guard is disabled


@pytest.mark.parametrize("name", ["comparator", "lean4export", "landrun", "nanoda", "dependency"])
def test_install_refuses_tracked_source_modifications(installer, name):
    build, env, log, run = installer
    source = build / ("comparator/.lake/packages/lean4export" if name == "dependency" else name)
    (source / "Main.lean").write_text("-- modified build input\n")
    result = run()
    assert result.returncode == 2 and "modified tracked files" in result.stderr
    # The dependency is checked after elan resolves the toolchain, but no build may run.
    assert not log.exists() or "build " not in log.read_text()
    env["INSTALL_TEST_DISABLE_CLEANLINESS"] = "1"
    assert run().returncode == 0  # disabling the source-cleanliness guard lets this planted input through


@pytest.mark.parametrize("name", ["comparator", "landrun", "dependency"])
def test_install_refuses_nonignored_source_injection(installer, name):
    build, env, log, run = installer
    source = build / ("comparator/.lake/packages/lean4export" if name == "dependency" else name)
    (source / "injected.go").write_text("package main\n")
    result = run()
    assert result.returncode == 2 and "untracked files" in result.stderr
    assert not log.exists() or "build " not in log.read_text()
    env["INSTALL_TEST_DISABLE_CLEANLINESS"] = "1"
    assert run().returncode == 0  # disabling the source-cleanliness guard lets this planted input through


def test_clean_pinned_sources_reach_build_and_offline_tool_admission(installer):
    build, _, log, run = installer
    result = run()
    assert result.returncode == 0, result.stdout + result.stderr
    calls = log.read_text()
    assert "lake build lean4export" in calls and "lake build comparator" in calls
    assert "go build -mod=readonly -trimpath" in calls and "cargo build --release --locked" in calls
    assert "uv run --frozen --offline python -m verifylab.cli init --tools" in calls
    assert all((build / "bin" / name).is_file() for name in PINS)
    provenance = (build / "SOURCE-REVISIONS").read_text()
    assert all(revision in provenance for revision in PINS.values())
