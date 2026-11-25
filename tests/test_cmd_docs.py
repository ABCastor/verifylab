"""`vl docs [NAME]`: the documents under docs/ printed by the command, for a reader who has the installed skill and
no checkout. The wheel carries them, a name is looked up and never joined to a path, and a closed pipe is no error."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

import verifylab
from verifylab.cli import main
from verifylab.commands import docs

ROOT = Path(__file__).resolve().parents[1]


def vl(capsys, *args):
    rc = main(["docs", *args])
    captured = capsys.readouterr()
    return rc, captured.out, captured.err


@pytest.mark.parametrize("name", ["CHEATS", "cheats", "CHEATS.md", "docs/CHEATS.md", "docs/cheats"])
def test_a_document_is_printed_whole_under_each_way_of_naming_it(capsys, name):
    rc, out, err = vl(capsys, name)
    assert (rc, err) == (0, "") and out == (ROOT / "docs" / "CHEATS.md").read_text()


def test_without_a_name_the_documents_are_listed_with_their_titles(capsys):
    rc, out, _ = vl(capsys)
    lines = [line.split(None, 1) for line in out.splitlines()]
    assert rc == 0 and ["PURPOSE-AND-ATTEMPTS", "Purpose and attempts — a text convention for shared formal mathematics (draft v0)"] in lines
    assert {name for name, _ in lines} == {path.relative_to(ROOT / "docs").with_suffix("").as_posix()
                                           for path in (ROOT / "docs").rglob("*.md")}
    data = json.loads(vl(capsys, "--json")[1])
    assert data["schema"] == "vl.output/1" and {"name": "CHEATS", "title": "Cheat catalog"} in data["documents"]
    data = json.loads(vl(capsys, "adr/0009-clean-clone-replay.md", "--json")[1])
    assert data["name"] == "adr/0009-clean-clone-replay" and data["text"].startswith("# ADR 0009")


def test_a_name_is_looked_up_among_the_documents_and_never_joined_to_a_path(capsys):
    """Planted: `docs/../README.md` and `docs/../CHANGELOG.md` exist, and joining the name to the folder would print
    them. Control: the document next to them is printed."""
    assert (ROOT / "docs" / ".." / "README.md").is_file()
    for name in ("../README", "docs/../README.md", "../CHANGELOG", "/etc/hostname", "NOPE", ""):
        rc, out, err = vl(capsys, name)
        assert (rc, out) == (2, "")
        assert err == (f"vl: no document '{name}': `vl docs` lists the {len(docs.documents())} that ship. "
                       "Next, run vl docs --help.\n")
    assert vl(capsys, "GUIDE")[0] == 0


def test_the_wheel_carries_the_documents_where_the_command_looks_for_them(tmp_path, monkeypatch, capsys):
    """The skill is copied alone into an agent's folder, so its documents must come with the command. In a wheel
    they sit inside the package; without them the command says that the installation is incomplete."""
    wheel = tomllib.loads((ROOT / "pyproject.toml").read_text())["tool"]["hatch"]["build"]["targets"]["wheel"]
    assert wheel["force-include"]["docs"] == "verifylab/docs" and wheel["force-include"]["skills"] == "verifylab/skills"
    package = tmp_path / "site-packages" / "verifylab"
    (package / "docs" / "adr").mkdir(parents=True)
    (package / "docs" / "CHEATS.md").write_text("# The copy in the wheel\n")
    (package / "docs" / "adr" / "0001-x.md").write_text("# ADR 0001\n")
    monkeypatch.setattr(verifylab, "__file__", str(package / "__init__.py"))
    assert docs.docs_root() == package / "docs" and list(docs.documents()) == ["CHEATS", "adr/0001-x"]
    assert vl(capsys, "docs/CHEATS.md") == (0, "# The copy in the wheel\n", "")
    (package / "docs" / "CHEATS.md").unlink()
    (package / "docs" / "adr" / "0001-x.md").unlink()
    rc, out, err = vl(capsys, "CHEATS")
    assert (rc, out) == (2, "") and "bundled documents not found next to the package" in err


def test_a_reader_who_stops_early_closes_the_pipe_and_that_is_no_error():
    """`vl docs SPEC | head -1`: the reader has what it wants and closes the pipe while the document is still being
    written. Planted: a pipe whose reading end is already closed. Control: a pipe that is read to the end."""
    command = [sys.executable, "-m", "verifylab.cli", "docs", "SPEC"]
    read_end, write_end = os.pipe()
    os.close(read_end)
    try:
        closed = subprocess.run(command, stdout=write_end, stderr=subprocess.PIPE, text=True, timeout=60)
    finally:
        os.close(write_end)
    assert (closed.returncode, closed.stderr) == (0, "")
    read = subprocess.run(command, capture_output=True, text=True, timeout=60)
    assert (read.returncode, read.stderr) == (0, "") and read.stdout == (ROOT / "docs" / "SPEC.md").read_text()
