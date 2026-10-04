"""The machine configuration: where a protected check takes its tools from, and `vl init --tools`, which pins them."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from verifylab import machine
from verifylab.adapters.lean_comparator import protected_tool
from verifylab.cli import main

from conftest import write_machine

TOOLCHAIN = "leanprover/lean4:v4.0.0"


def vl(capsys, *args):
    rc = main(list(args))
    captured = capsys.readouterr()
    return rc, captured.out, captured.err


def exe(path: Path, text: str = "#!/bin/sh\necho ok\n") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    path.chmod(0o755)
    return path


def test_machine_file_paths_must_be_absolute_and_known(tmp_path):
    write_machine({"comparator": "/opt/c"}, elan_home="/opt/elan", caches={"/proj": "/proj/.lake"})
    loaded = machine.load()
    assert loaded.tools == {"comparator": "/opt/c"} and loaded.protected_elan_home() == Path("/opt/elan")
    assert loaded.cache_for(Path("/proj")) == "/proj/.lake" and len(loaded.sha256) == 64
    write_machine({"comparator": "tools/comparator"})
    with pytest.raises(machine.MachineError, match="relative"):
        machine.load()
    write_machine({"compiler": "/opt/c"})
    with pytest.raises(machine.MachineError, match="unknown"):
        machine.load()
    machine.config_path().unlink()
    machine.forget()
    assert machine.load().sha256 is None and machine.load().tools == {}


def pin(path: Path) -> Path:
    (path.parent / "REVISIONS").write_text(f"{machine.file_sha256(path)}  {path.name}\n")
    return path


def test_protected_tool_order_and_refusals(tmp_path):
    configured = pin(exe(tmp_path / "machine" / "comparator"))
    vendored = pin(exe(tmp_path / "vendored" / "comparator"))
    empty = machine.Machine(tmp_path / "none.toml", None)
    tool, problem = protected_tool("comparator", empty, TOOLCHAIN, {"comparator": "tools/comparator"})
    assert tool is None and "relative path 'tools/comparator'" in problem
    tool, problem = protected_tool("comparator", empty, TOOLCHAIN, {"comparator": str(vendored)})
    assert tool.path == vendored and "deprecated" in tool.source and problem is None
    with_machine = machine.Machine(tmp_path / "m.toml", "x", {"comparator": str(configured)})
    tool, _ = protected_tool("comparator", with_machine, TOOLCHAIN, {"comparator": str(vendored)})
    assert tool.path == configured and tool.source == "machine.toml [tools] comparator"
    assert tool.pin == str(configured.parent / "REVISIONS")
    assert protected_tool("comparator", empty, TOOLCHAIN, {}) == (None, None)
    configured.write_text("#!/bin/sh\necho changed\n")
    tool, problem = protected_tool("comparator", with_machine, TOOLCHAIN, {})
    assert tool is None and "refusing to run a changed tool" in problem


@pytest.mark.parametrize("where", ["machine.toml", "project [tools]"])
def test_an_unpinned_tool_is_refused_wherever_it_is_named(tmp_path, where):
    """No REVISIONS pin next to it: a protected check refuses the tool and says how to pin it."""
    unpinned = exe(tmp_path / "bin" / "comparator")
    tools = {"comparator": str(unpinned)}
    config = machine.Machine(tmp_path / "m.toml", "x", tools if where == "machine.toml" else {})
    tool, problem = protected_tool("comparator", config, TOOLCHAIN, tools if where != "machine.toml" else {})
    assert tool is None, tool
    assert f"{unpinned} is not pinned" in problem and f"vl init --tools --comparator {unpinned}" in problem
    (unpinned.parent / "REVISIONS").write_text(f"{machine.file_sha256(unpinned)}  landrun\n")   # pins another tool
    assert protected_tool("comparator", config, TOOLCHAIN, tools if where != "machine.toml" else {})[0] is None


def test_init_tools_copies_pins_and_never_overwrites(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    write_machine({"comparator": "/opt/comparator", "landrun": "/opt/landrun"})
    comparator, landrun = exe(tmp_path / "src" / "comparator"), exe(tmp_path / "src" / "landrun", "#!/bin/sh\n")
    rc, out, err = vl(capsys, "init", "--tools", "--toolchain", TOOLCHAIN, "--comparator", str(comparator),
                      "--landrun", str(landrun), "--json")
    assert rc == 0, err
    store = machine.store_root() / "leanprover--lean4---v4.0.0"
    assert json.loads(out)["store"] == str(store) and sorted(json.loads(out)["copied"]) == ["comparator", "landrun"]
    assert (store / "comparator").read_bytes() == comparator.read_bytes()
    assert machine.read_revisions(store) == {"comparator": machine.file_sha256(comparator),
                                             "landrun": machine.file_sha256(landrun)}
    check = subprocess.run(["sha256sum", "-c", "REVISIONS"], cwd=store, capture_output=True, text=True)
    assert check.returncode == 0 and "comparator: OK" in check.stdout
    assert json.loads(out)["overridden_by_machine_toml"] == ["comparator", "landrun"]
    write_machine({})
    tool, problem = protected_tool("comparator", machine.load(), TOOLCHAIN, {})
    assert problem is None and tool.source.startswith("tools store") and tool.pin == str(store / "REVISIONS")
    assert vl(capsys, "init", "--tools", "--toolchain", TOOLCHAIN, "--comparator", str(comparator))[0] == 0
    other = exe(tmp_path / "other" / "comparator", "#!/bin/sh\necho other\n")
    rc, _, err = vl(capsys, "init", "--tools", "--toolchain", TOOLCHAIN, "--comparator", str(other))
    assert rc == 2 and "never overwrites" in err
    assert machine.read_revisions(store)["comparator"] == machine.file_sha256(comparator)
    assert vl(capsys, "init", "--comparator", str(comparator))[0] == 2      # tool paths only with --tools


def test_validate_warns_about_tool_paths_in_the_project_configuration(research_repo, monkeypatch, capsys):
    from conftest import commit_all
    cfg = research_repo / "research" / "vl.toml"
    cfg.write_text(cfg.read_text() + '\n[tools]\ncomparator = "/opt/comparator"\n')
    commit_all(research_repo, "tools in the project")
    monkeypatch.chdir(research_repo)
    rc, out, _ = vl(capsys, "validate")
    assert rc == 0 and any("[tools] comparator" in line and "deprecated" in line and "machine.toml" in line
                           for line in out.splitlines() if line.startswith("WARNING")), out


def test_the_machine_file_is_read_once_per_command(research_repo, monkeypatch, capsys):
    """Every git call names its launcher from machine.toml; one command reads the file once, so it also runs under
    one machine policy from start to end."""
    reads, real = [], Path.read_bytes
    path = machine.config_path()

    def counting(self):
        if self == path:
            reads.append(self)
        return real(self)
    getattr(machine, "forget", lambda: None)()
    monkeypatch.setattr(Path, "read_bytes", counting)
    monkeypatch.chdir(research_repo)
    assert main(["validate"]) == 0
    assert main(["show", "add-zero"]) == 0
    assert len(reads) == 1, f"machine.toml read {len(reads)} times"
    path.write_text(path.read_text() + '\n[check]\nmemory_total = "3G"\n')
    assert machine.load().memory_total is None                      # the policy this process started with
    machine.forget()
    assert machine.load().memory_total == "3G"
