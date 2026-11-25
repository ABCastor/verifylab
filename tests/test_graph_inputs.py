"""`vl graph LOCK --dump DIR --lean LEAN` writes the two inputs of the removal view from a built checkout, or nothing.
Each refusal has a planted case next to the clean control: a compiled file that is missing, written by another Lean,
unreadable or at odds with the pinned source, and a working tree that is not the pin. The fast tests name a stand-in
executable that answers like Lean; the Lean-marked test builds the Tiny fixture and names the real one."""
import gzip
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import commit_all
from test_foreign import foreign, output
from verifylab import graph_inputs
from verifylab.cli import main

VERSION = "4.34.1"
COMMIT = "0123456789abcdef0123456789abcdef01234567"
KERNEL = {
    "Tiny": ["MOD\tTiny\t1\tTiny.Step", "DEP\tthm\tTiny\tTiny.conclusion\t-\tTiny.step\tpropext", "MODEND\tTiny"],
    "Tiny.Step": ["MOD\tTiny.Step\t1\tInit", "DEP\tthm\tTiny.Step\tTiny.step\t-\t\t", "MODEND\tTiny.Step"],
}
RANGES = {
    "Tiny": ["R\tTiny\tTiny.conclusion\t5\t0\t6\t26\t5", "T\tTiny\tTiny.conclusion\t17\t"],
    "Tiny.Step": ["R\tTiny.Step\tTiny.step\t3\t0\t3\t40\t3", "T\tTiny.Step\tTiny.step\t23\t"],
}
STAND_IN = """#!{python}
import json, os, sys
from pathlib import Path
plan = json.loads(Path(__file__).with_suffix(".json").read_text())
if sys.argv[1:] == ["--version"]:
    print(plan["reported"])
    sys.exit(0)
flag, script, listing = sys.argv[1:]
assert flag == "--run" and Path(script).is_file(), sys.argv
if "LEAN_PATH" in os.environ or Path.cwd().is_relative_to(plan["checkout"]):
    sys.exit("the extractor must run outside the checkout, without LEAN_PATH")
rows = plan["rows"][Path(script).name]
for line in Path(listing).read_text().splitlines():
    artifact, module = line.split("\\t")
    assert Path(artifact).is_file(), artifact
    print("\\n".join(rows.get(module, [])))
print(plan.get("stderr", ""), file=sys.stderr)
sys.exit(plan.get("exit", 0))
"""


def compiled(root, module, version=VERSION, commit=COMMIT):
    """A file with the header of a compiled module: marker, format, flags, Lean version, commit, base address."""
    path = root / ".lake/build/lib/lean" / (module.replace(".", "/") + ".olean")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"olean\x02\x01" + version.encode().ljust(33, b"\0") + commit.encode().ljust(42, b"\0") + bytes(8))
    return path


@pytest.fixture
def built(foreign, tmp_path, capsys):
    """The Tiny checkout with compiled files, its lock, and a stand-in for the Lean that wrote them."""
    (foreign / ".git/info/exclude").write_text(".lake/\n")
    for module in KERNEL:
        compiled(foreign, module)
    lean = tmp_path / "lean"
    lean.write_text(STAND_IN.format(python=sys.executable))
    lean.chmod(0o755)
    plan = {"reported": f"Lean (version {VERSION}, x86_64-unknown-linux-gnu, commit {COMMIT}, Release)",
            "checkout": str(foreign), "rows": {"dumpx.lean": KERNEL, "Ranges.lean": RANGES}}
    lean.with_suffix(".json").write_text(json.dumps(plan))
    output(capsys, "import", foreign)
    return foreign, lean, plan


def replan(lean, plan, **changes):
    lean.with_suffix(".json").write_text(json.dumps({**plan, **changes}))


def refused(capsys, lean, *extra, out="inputs", lock="vl.lock"):
    code = main(["graph", lock, "--dump", out, "--lean", str(lean), *extra])
    assert code == 2 and not any(Path(out, name).exists() for name in ("kernel.tsv.gz", "ranges.gz", "graph-inputs.json"))
    return capsys.readouterr().err


def test_dump_writes_both_inputs_with_digests_and_the_removal_view_reads_them(built, capsys):
    root, lean, _ = built
    record = output(capsys, "graph", "vl.lock", "--dump", "inputs", "--lean", lean)
    assert record["schema"] == "vl.graph-inputs/1" and record["modules"] == 2 and record["targets"] == ["Tiny"]
    assert record["lean"]["version"] == VERSION and record["lean"]["commit"] == COMMIT
    assert record["toolchain"] == "leanprover/lean4:v4.34.1"
    assert record["module_paths"] == {"Tiny": "Tiny.lean", "Tiny.Step": "Tiny/Step.lean"}
    assert record["input_sha256"] == {path: hashlib.sha256((root / path).read_bytes()).hexdigest()
                                     for path in ("Tiny.lean", "Tiny/Step.lean", "lakefile.toml",
                                                  "lake-manifest.json", "lean-toolchain")}
    assert json.loads(Path("inputs/graph-inputs.json").read_text()) == {k: v for k, v in record.items()
                                                                        if k not in ("schema", "record")} | {
        "schema": "vl.graph-inputs/1"}
    for kind, name, rows in (("kernel", "kernel.tsv.gz", 2), ("ranges", "ranges.gz", 2)):
        entry = record["files"][kind]
        data = Path("inputs", name).read_bytes()
        assert (entry["path"], entry["rows"]) == (name, rows)
        assert entry["sha256"] == hashlib.sha256(data).hexdigest() and entry["bytes"] == len(data)
        extractor = graph_inputs.SCRIPTS / entry["extractor"]
        assert entry["extractor_sha256"] == hashlib.sha256(extractor.read_bytes()).hexdigest()
    assert gzip.decompress(Path("inputs/kernel.tsv.gz").read_bytes()).decode().splitlines() == [
        *KERNEL["Tiny"], *KERNEL["Tiny.Step"]]
    view = output(capsys, "value", "vl.lock", "--root", "Tiny.conclusion", "--deps", "inputs/kernel.tsv.gz",
                  "--removal", "inputs/ranges.gz")
    assert view["removal"]["union_located_lines"] == 3
    assert view["graph"]["artifact_sha256"] == record["files"]["kernel"]["sha256"]
    assert view["removal"]["ranges_sha256"] == record["files"]["ranges"]["sha256"]
    # The same rows give the same bytes, and the text names the next command.
    assert main(["graph", "vl.lock", "--dump", "again", "--lean", str(lean), "--build", "Tiny"]) == 0
    text = capsys.readouterr().out
    assert f"sha256 {record['files']['kernel']['sha256']}  2 DEP rows" in text
    assert "Artifact checks are structural. Check build freshness at the pin with Lake before extraction." in text
    assert "vl value vl.lock --root NAME --graph again/graph-inputs.json" in text
    assert main(["graph", "vl.lock", "--dump", "inputs", "--lean", str(lean)]) == 2
    assert "already holds kernel.tsv.gz, ranges.gz, graph-inputs.json" in capsys.readouterr().err


def test_dump_refuses_a_missing_compiled_file(built, capsys):
    root, lean, _ = built
    (root / ".lake/build/lib/lean/Tiny/Step.olean").unlink()
    assert "1 of 2 compiled files are missing: Tiny.Step. Build Tiny first" in refused(capsys, lean)


@pytest.mark.parametrize("version,commit", [("4.33.1", COMMIT), (VERSION, "f" * 40)])
def test_dump_refuses_a_file_compiled_by_another_lean(built, capsys, version, commit):
    root, lean, _ = built
    compiled(root, "Tiny.Step", version, commit)
    assert f"Step.olean was written by Lean {version} ({commit[:12]}), not by" in refused(capsys, lean)
    (root / ".lake/build/lib/lean/Tiny.olean").write_bytes(b"not a compiled module")
    compiled(root, "Tiny.Step")
    assert "Tiny.olean is not a compiled Lean module file" in refused(capsys, lean)


@pytest.mark.parametrize("path", ["Tiny/Step.lean", "lean-toolchain"])
def test_dump_refuses_a_working_tree_that_is_not_the_pin(built, capsys, path):
    root, lean, _ = built
    with (root / path).open("a") as stream:
        stream.write("\n")
    assert f"{path} in the working tree differs from the lock" in refused(capsys, lean)


def test_dump_checks_the_prepared_tree_instead_of_the_git_worktree(built, capsys, tmp_path):
    root, lean, plan = built
    prepared = tmp_path / "prepared"
    shutil.copytree(root, prepared, ignore=shutil.ignore_patterns(".git", ".lake"))
    for module in KERNEL:
        compiled(prepared, module)
    proof = prepared / ".lake/build/lib/lean/Tiny.olean.private"
    proof.write_bytes(b"private proof part")
    replan(lean, plan, checkout=str(prepared))
    (root / "Tiny.lean").write_text("unrelated draft in the Git worktree")
    args = ["--built-tree", str(prepared)]
    record = output(capsys, "graph", "vl.lock", "--dump", "inputs", "--lean", lean, *args)
    assert not (prepared / ".git").exists()
    assert record["olean_sha256"][proof.relative_to(prepared).as_posix()] == hashlib.sha256(proof.read_bytes()).hexdigest()
    assert len(record["olean_sha256"]) == 3
    assert "expected a prepared Lake build directory" in refused(
        capsys, lean, "--built-tree", str(prepared / "absent"), out="absent")
    (prepared / "lakefile.lean").write_text("untracked configuration")
    assert "present in the build tree and absent from the pin" in refused(capsys, lean, *args, out="config")
    (prepared / "lakefile.lean").unlink()
    outside = tmp_path / proof.name
    proof.rename(outside)
    proof.symlink_to(outside)
    assert "expected a compiled file inside the build tree" in refused(capsys, lean, *args, out="outside")
    (prepared / "Tiny.lean").write_text("different source beside the built objects")
    assert f"Tiny.lean in the prepared build {prepared} differs from the lock" in refused(
        capsys, lean, *args, out="different")


def test_dump_refuses_rows_lean_could_not_read_or_did_not_finish(built, capsys):
    root, lean, plan = built
    unread = {**KERNEL, "Tiny.Step": ["FAIL\tTiny.Step\tread: incompatible header"]}
    replan(lean, plan, rows={"dumpx.lean": unread, "Ranges.lean": RANGES}, exit=1)     # the extractor's own exit
    assert "Lean could not read 1 compiled files: Tiny.Step read: incompatible header" in refused(capsys, lean)
    replan(lean, plan, rows={"dumpx.lean": {**KERNEL, "Tiny.Step": KERNEL["Tiny.Step"][:2]}, "Ranges.lean": RANGES})
    assert "the kernel rows of 1 modules are missing or cut short: Tiny.Step" in refused(capsys, lean)
    replan(lean, plan, exit=1, stderr="uncaught exception: missing proof data")
    assert "--run dumpx.lean exited 1: uncaught exception: missing proof data" in refused(capsys, lean)
    replan(lean, plan, reported="Lean, some other program")
    assert "--version did not report a Lean version and commit" in refused(capsys, lean)


@pytest.mark.parametrize("script,rows,message", [
    ("Ranges.lean", [*RANGES["Tiny"], "R\tTiny\tbroken"], "expected an eight-column R row"),
    ("Ranges.lean", [*RANGES["Tiny"], "R\tTiny\tTiny.generated\t1\t0\t999\t1\t1"],
     "ranges do not match the pinned source"),
    ("Ranges.lean", ["R\tTiny\tTiny.conclusion\t4\t0\t4\t1\t4", *RANGES["Tiny"]], "conflicting ranges"),
    ("Ranges.lean", [*RANGES["Tiny"], "T\tTiny\tTiny.conclusion\tbad\t"], "expected a five-column T row"),
    ("Ranges.lean", RANGES["Tiny"][:1], "missing T rows for 1 constants: Tiny.conclusion"),
    ("Ranges.lean", [*RANGES["Tiny"], RANGES["Tiny"][-1]], "duplicate T row"),
    ("dumpx.lean", [*KERNEL["Tiny"][:2], "DEP\tthm\tTiny", KERNEL["Tiny"][2]],
     "expected a seven-column dumpx DEP row"),
    ("dumpx.lean", [KERNEL["Tiny"][0].replace("\t1\t", "\t2\t"), *KERNEL["Tiny"][1:]],
     "Tiny declares 2 DEP rows but has 1"),
    ("dumpx.lean", [KERNEL["Tiny"][0].replace("\t1\t", "\t2\t"), KERNEL["Tiny"][1],
                   KERNEL["Tiny"][1], KERNEL["Tiny"][2]], "duplicate DEP row"),
])
def test_dump_refuses_rows_the_reader_cannot_use_or_count(built, capsys, script, rows, message):
    _, lean, plan = built
    base = plan["rows"]
    replan(lean, plan, rows={**base, script: {**base[script], "Tiny": rows}})
    assert message in refused(capsys, lean)
    assert not Path("inputs").exists()
    replan(lean, plan)
    output(capsys, "graph", "vl.lock", "--dump", "inputs", "--lean", lean)
    assert output(capsys, "value", "vl.lock", "--root", "Tiny.conclusion",
                  "--graph", "inputs/graph-inputs.json")["removal"]["union_located_lines"] == 3


@pytest.mark.parametrize("failure", ["manifest", "rename"])
def test_dump_publishes_the_complete_set_or_leaves_an_empty_directory_for_retry(built, capsys, monkeypatch, failure):
    _, lean, _ = built
    dest = Path("inputs")
    dest.mkdir()
    rename = Path.rename

    def no_manifest(path, *_):
        assert {file.name for file in path.parent.iterdir()} == {"kernel.tsv.gz", "ranges.gz"}
        raise OSError("manifest write failed")

    def no_rename(path, target):
        if target == dest:
            assert {file.name for file in path.iterdir()} == {"kernel.tsv.gz", "ranges.gz", "graph-inputs.json"}
            raise OSError("rename failed")
        return rename(path, target)

    with monkeypatch.context() as patch:
        if failure == "manifest":
            patch.setattr(graph_inputs, "write_new_json", no_manifest)
        else:
            patch.setattr(Path, "rename", no_rename)
        assert f"{failure} " in refused(capsys, lean)
    assert list(dest.iterdir()) == [] and not list(Path().glob(".vl-dump-*"))
    output(capsys, "graph", "vl.lock", "--dump", dest, "--lean", lean)
    assert {file.name for file in dest.iterdir()} == {"kernel.tsv.gz", "ranges.gz", "graph-inputs.json"}


def test_dump_leaves_unrelated_files_in_an_existing_directory(built, capsys):
    _, lean, _ = built
    dest = Path("inputs")
    dest.mkdir()
    (dest / "keep.txt").write_text("unrelated")
    assert "choose a new or empty folder" in refused(capsys, lean)
    assert (dest / "keep.txt").read_text() == "unrelated"


@pytest.mark.parametrize("path,old,new,why", [
    # The declaration left the lines it was compiled at, although a neighbour of a similar name now stands there.
    ("Tiny/Step.lean", "theorem step ", "theorem step' : True := trivial\n\ntheorem step ",
     "Tiny.step (Tiny/Step.lean:5) was compiled at lines 3-3"),
    # It still starts inside the compiled range, but the line that named it holds something else.
    ("Tiny.lean", "theorem conclusion", "-- one more line\ntheorem conclusion",
     "Tiny.conclusion (Tiny.lean:6) was compiled at lines 5-6"),
    ("Tiny/Step.lean", "end Tiny", "theorem later : True := trivial\n\nend Tiny",
     "Tiny.later (Tiny/Step.lean:5) has no compiled constant in Tiny.Step"),
])
def test_dump_refuses_compiled_files_of_an_older_source(built, capsys, path, old, new, why):
    """The edit is committed and locked, so the working tree is the pin; only the compiled files stayed behind."""
    root, lean, _ = built
    (root / path).write_text((root / path).read_text().replace(old, new))
    commit_all(root, "edit after the build")
    output(capsys, "import", root, "--lock", "later.lock")
    assert main(["graph", "later.lock", "--dump", "inputs", "--lean", str(lean)]) == 2
    message = capsys.readouterr().err
    assert "do not match the pinned source" in message and why in message
    assert "Rebuild Tiny at the pin" in message and not Path("inputs/kernel.tsv.gz").exists()


def test_dump_refuses_rows_without_proofs(built, capsys):
    """What the public part of a module-system file gives: the theorem without its proof, as an axiom. A graph
    made of such rows stops at the statements (83 names where the proof of a real library reaches 5,307)."""
    root, lean, plan = built

    def kernel(**step):
        return {"dumpx.lean": {**KERNEL, **step}, "Ranges.lean": RANGES}

    public_part = ["MOD\tTiny.Step\t1\tInit", "DEP\taxiom\tTiny.Step\tTiny.step\t-\t\t?", "MODEND\tTiny.Step"]
    replan(lean, plan, rows=kernel(**{"Tiny.Step": public_part}))
    assert ("the kernel rows hold no proof (1): Tiny.step (Tiny.Step) is recorded as an axiom and Tiny/Step.lean "
            "declares no such axiom") in refused(capsys, lean)
    # No theorem row at all, for a source that states theorems.
    bare = {module: [rows[0].replace("\t1\t", "\t0\t"), rows[2]] for module, rows in KERNEL.items()}
    replan(lean, plan, rows={"dumpx.lean": bare, "Ranges.lean": {module: rows[:1] for module, rows in RANGES.items()}})
    assert "hold no proof (1): the pinned source has theorems and the rows hold none" in refused(capsys, lean)
    # A theorem of the source under another kind.
    other = [row.replace("DEP\tthm", "DEP\topaque") for row in KERNEL["Tiny.Step"]]
    replan(lean, plan, rows=kernel(**{"Tiny.Step": other}))
    assert "Tiny.step (Tiny/Step.lean:3) is a theorem in the source and a row of kind opaque" in refused(capsys, lean)
    # Control: an axiom that the pinned source declares is an axiom row, and the dump is written.
    path = root / "Tiny/Step.lean"
    path.write_text(path.read_text().replace("end Tiny", "axiom assumed : True\n\nend Tiny"))
    commit_all(root, "a declared axiom")
    output(capsys, "import", root, "--lock", "axiom.lock")
    header, step, end = KERNEL["Tiny.Step"]
    declared = [header.replace("\t1\t", "\t2\t"), step, "DEP\taxiom\tTiny.Step\tTiny.assumed\t-\tTrue\t?", end]
    rows = kernel(**{"Tiny.Step": declared})
    rows["Ranges.lean"] = {**RANGES, "Tiny.Step": [*RANGES["Tiny.Step"], "T\tTiny.Step\tTiny.assumed\t3\tTrue"]}
    replan(lean, plan, rows=rows)
    assert output(capsys, "graph", "axiom.lock", "--dump", "inputs", "--lean", lean)["files"]["kernel"]["rows"] == 3


def test_dump_names_its_lean_and_keeps_to_its_own_view(built, capsys, tmp_path):
    root, lean, _ = built
    assert main(["graph", "vl.lock", "--dump", "inputs"]) == 2
    assert "needs --lean LEAN" in capsys.readouterr().err
    assert main(["graph", "vl.lock", "--lean", str(lean)]) == 2
    assert "needs --lean LEAN" in capsys.readouterr().err
    assert "--modules and --deps select another view" in refused(capsys, lean, "--modules")
    assert "--modules and --deps select another view" in refused(capsys, lean, "--deps", "deps.tsv")
    plain = tmp_path / "notes.txt"
    plain.write_text("not a program")
    assert "--lean must be an executable file" in refused(capsys, plain)
    assert "--lean must be an executable file" in refused(capsys, tmp_path / "absent")
    assert "no module or library target Other" in refused(capsys, lean, "--build", "Other")


def test_the_extractors_ship_inside_the_package():
    assert sorted(p.name for p in graph_inputs.SCRIPTS.iterdir()) == ["Ranges.lean", "dumpx.lean"]
    assert graph_inputs.SCRIPTS.parent == Path(graph_inputs.__file__).parent
    pyproject = (Path(__file__).resolve().parents[1] / "pyproject.toml").read_text()
    assert 'packages = ["src/verifylab"]' in pyproject


def real_lean(root):
    """The Lean and Lake of the fixture's toolchain, as the Lean tests find them."""
    from conftest import lean_elan_home
    from verifylab.adapters.lean_comparator import toolchain_dir
    spec = (root / "lean-toolchain").read_text().strip()
    binaries = toolchain_dir(spec, elan_home=Path(lean_elan_home())) / "bin"
    assert (binaries / "lake").is_file() and (binaries / "lean").is_file(), f"remote gate needs {spec}"
    (root / ".git/info/exclude").write_text(".lake/\n")
    return spec, binaries


def lake_build(root, binaries):
    env = {**os.environ, "PATH": f"{binaries}:{os.environ['PATH']}"}
    subprocess.run([str(binaries / "lake"), "build", "Tiny"], cwd=root, env=env, check=True, capture_output=True)



def use_side_module(root):
    lakefile = root / "lakefile.toml"
    lakefile.write_text(lakefile.read_text() + 'globs = ["Tiny", "Side"]\n')
    (root / "Side.lean").write_text("theorem side (p : Prop) (h : p) : p := h\n")
    tiny = root / "Tiny.lean"
    tiny.write_text(tiny.read_text().replace("import Tiny.Step", "import Side").replace("step p certificate",
                                                                                     "side p certificate"))
    commit_all(root, "a library glob outside the root")


def test_dump_refuses_a_covered_import_omitted_from_the_pin(built, capsys):
    root, lean, _ = built
    use_side_module(root)
    (root / "Side.lean").unlink()
    commit_all(root, "omit a covered import")
    output(capsys, "pin", root, "--lock", "missing.lock")
    error = refused(capsys, lean, lock="missing.lock")
    assert "imports local module Side, whose source is missing from the pin" in error


@pytest.mark.lean
def test_real_glob_outside_the_library_root_reaches_value_study_and_order(foreign, capsys):
    _, binaries = real_lean(foreign)
    use_side_module(foreign)
    lake_build(foreign, binaries)
    assert (foreign / ".lake/build/lib/lean/Side.olean").is_file()
    output(capsys, "pin", foreign)
    estimate = output(capsys, "graph", "vl.lock", "--build", "Tiny")
    assert estimate["source_modules"] == 2
    assert "Side" not in estimate["external_imports"]
    record = output(capsys, "graph", "vl.lock", "--dump", "inputs", "--lean", binaries / "lean")
    assert record["modules"] == 2
    flags = ["vl.lock", "--root", "Tiny.conclusion", "--graph", "inputs/graph-inputs.json"]
    view = output(capsys, "value", *flags, "--study")
    assert {row["name"] for row in view["rows"]} == {"Tiny.conclusion", "side"}
    assert view["removal"]["union_located_lines"] == 3
    assert view["study"]["study_first"][0]["name"] == "side"
    packet = output(capsys, "order", *flags)
    assert {row["name"] for row in packet["sources"]} == {"Tiny.conclusion", "side"}


@pytest.mark.lean
def test_removal_comparison_checks_type_syntax_not_a_referenced_definition(foreign, capsys):
    _, binaries = real_lean(foreign)
    main_source = foreign / "Tiny.lean"
    main_source.write_text("namespace Tiny\n\ndef Goal : Prop := ∀ n : Nat, n + 0 = n\n\n"
                           "theorem conclusion : Goal := by intro n; exact Nat.add_zero n\n\nend Tiny\n")
    commit_all(foreign, "first meaning of Goal")
    lake_build(foreign, binaries)
    output(capsys, "pin", foreign)
    output(capsys, "graph", "vl.lock", "--dump", "before", "--lean", binaries / "lean")
    flags = ["vl.lock", "--root", "Tiny.conclusion", "--graph"]
    before = output(capsys, "value", *flags, "before/graph-inputs.json")
    Path("before.json").write_text(json.dumps(before))

    main_source.write_text(main_source.read_text().replace("∀ n : Nat, n + 0 = n", "True")
                           .replace("by intro n; exact Nat.add_zero n", "True.intro"))
    commit_all(foreign, "change the definition used in the type")
    lake_build(foreign, binaries)
    output(capsys, "pin", foreign, "--lock", "later.lock")
    output(capsys, "graph", "later.lock", "--dump", "after", "--lean", binaries / "lean")
    after = output(capsys, "value", "later.lock", *flags[1:], "after/graph-inputs.json", "--against", "before.json")
    first = before["removal"]["root_statements"]["Tiny.conclusion"]
    assert first["type_hash"] is not None
    assert first == after["removal"]["root_statements"]["Tiny.conclusion"]
    assert after["against"]["prior_commit"] != after["commit"]

def proof_rows(path):
    """{theorem: (kind, used constants)} of the Tiny rows in a kernel dump."""
    rows = [line.split("\t") for line in gzip.decompress(Path(path).read_bytes()).decode().splitlines()]
    return {row[3]: (row[1], set(row[5].split())) for row in rows if row[0] == "DEP" and row[3].startswith("Tiny.")}


@pytest.mark.lean
def test_real_lean_dump_of_the_built_tiny_project(foreign, capsys):
    spec, binaries = real_lean(foreign)
    lake_build(foreign, binaries)
    output(capsys, "import", foreign)
    lean = binaries / "lean"
    record = output(capsys, "graph", "vl.lock", "--dump", "inputs", "--lean", lean)
    assert record["modules"] == 2 and record["lean"]["version"] == spec.rsplit(":v", 1)[-1]
    assert record["files"]["kernel"]["rows"] >= 2 and record["files"]["ranges"]["rows"] >= 2
    rows = proof_rows("inputs/kernel.tsv.gz")
    assert rows["Tiny.conclusion"][0] == rows["Tiny.step"][0] == "thm" and "Tiny.step" in rows["Tiny.conclusion"][1]
    view = output(capsys, "value", "vl.lock", "--root", "Tiny.conclusion", "--graph", "inputs/graph-inputs.json",
                  "--study")
    assert {"Tiny.conclusion", "Tiny.step"} <= {row["name"] for row in view["rows"]}
    assert view["removal"]["union_located_lines"] == 3
    assert view["removal"]["ranges_sha256"] == record["files"]["ranges"]["sha256"]
    assert view["study"]["study_first"][0]["name"] == "Tiny.step"
    # Planted on the real build: an edit committed after the build, then a compiled file removed.
    path = foreign / "Tiny/Step.lean"
    path.write_text(path.read_text().replace("theorem step", "-- one more line\ntheorem step"))
    commit_all(foreign, "edit after the build")
    output(capsys, "import", foreign, "--lock", "later.lock")
    assert main(["graph", "later.lock", "--dump", "stale", "--lean", str(lean)]) == 2
    assert "do not match the pinned source" in capsys.readouterr().err
    (foreign / ".lake/build/lib/lean/Tiny/Step.olean").unlink()
    assert main(["graph", "later.lock", "--dump", "missing", "--lean", str(lean)]) == 2
    assert "compiled files are missing: Tiny.Step" in capsys.readouterr().err


@pytest.mark.lean
def test_real_lean_dump_reads_the_proofs_of_module_system_files(foreign, capsys):
    """Sources with a `module` header: Lean keeps the proofs in a separate compiled file, and the public one holds
    a theorem as an axiom. The rows must be theorems with their proof dependencies, as for the old-style files."""
    spec, binaries = real_lean(foreign)
    (foreign / "Tiny/Step.lean").write_text(
        "module\n\npublic section\n\nnamespace Tiny\n\ntheorem step (p : Prop) (h : p) : p := h\n\nend Tiny\n")
    (foreign / "Tiny.lean").write_text(
        "module\n\npublic import Tiny.Step\n\npublic section\n\nnamespace Tiny\n\n"
        "theorem conclusion (p : Prop) (certificate : p) : p := by\n  exact step p certificate\n\nend Tiny\n")
    commit_all(foreign, "module-system sources")
    prepared = foreign.with_name("prepared")
    shutil.copytree(foreign, prepared, ignore=shutil.ignore_patterns(".git", ".lake"))
    lake_build(prepared, binaries)
    assert (prepared / ".lake/build/lib/lean/Tiny.olean.private").is_file(), "not a module-system build"
    output(capsys, "import", foreign)
    record = output(capsys, "graph", "vl.lock", "--dump", "inputs", "--lean", binaries / "lean",
                    "--built-tree", prepared)
    assert record["modules"] == 2
    assert ".lake/build/lib/lean/Tiny.olean.private" in record["olean_sha256"]
    rows = proof_rows("inputs/kernel.tsv.gz")
    assert rows["Tiny.conclusion"][0] == rows["Tiny.step"][0] == "thm" and "Tiny.step" in rows["Tiny.conclusion"][1]
    view = output(capsys, "value", "vl.lock", "--root", "Tiny.conclusion", "--deps", "inputs/kernel.tsv.gz",
                  "--removal", "inputs/ranges.gz")
    assert {"Tiny.conclusion", "Tiny.step"} <= {row["name"] for row in view["rows"]}
    assert view["removal"]["union_located_lines"] == 3


def test_value_and_order_share_the_verified_dump_manifest(built, capsys):
    _, lean, _ = built
    output(capsys, "graph", "vl.lock", "--dump", "inputs", "--lean", lean)
    manifest = Path("inputs/graph-inputs.json")
    flags = ["vl.lock", "--root", "Tiny.conclusion", "--graph", str(manifest)]
    by_graph = output(capsys, "value", *flags)
    explicit = output(capsys, "value", *flags[:3], "--deps", "inputs/kernel.tsv.gz", "--removal", "inputs/ranges.gz")
    assert by_graph.pop("graph_manifest") == str(manifest)
    assert by_graph.pop("source_scope") == "pinned graph manifest modules"
    assert explicit.pop("source_scope") == "all pinned Lean files"
    assert by_graph == explicit
    assert output(capsys, "order", *flags)["sources"]
    ranked = output(capsys, "order", *flags, "--aim", "simplify", "--rank-by", "removal")
    assert ranked["ranked_by"] == "exclusive_source_lines"
    tiny_budget = output(capsys, "order", *flags, "--aim", "simplify", "--rank-by", "removal", "--bytes", 1)
    assert tiny_budget["input"] == ""
    assert tiny_budget["root_source"] == ranked["root_source"] == {"path": "Tiny.lean", "line": 5}

    original = manifest.read_text()
    record = json.loads(original)
    manifest.write_text(json.dumps({**record, "commit": "a" * 40}))
    assert main(["order", *flags]) == 2
    assert "same commit and toolchain" in capsys.readouterr().err
    manifest.write_text(original)
    with Path("inputs/ranges.gz").open("ab") as stream:
        stream.write(b"tampered bytes")
    assert main(["value", *flags]) == 2
    assert "bytes differ from the graph manifest" in capsys.readouterr().err
    assert main(["order", *flags]) == 2
    assert "bytes differ from the graph manifest" in capsys.readouterr().err


def test_selected_anchors_use_the_verified_module_paths(built, capsys):
    root, lean, _ = built
    archive = root / "archive/Step.lean"
    archive.parent.mkdir()
    archive.write_text((root / "Tiny/Step.lean").read_text())
    commit_all(root, "retain an archived copy of a compiled source")
    output(capsys, "pin", root, "--lock", "archive.lock")
    output(capsys, "graph", "archive.lock", "--dump", "inputs", "--lean", lean)
    manifest = Path("inputs/graph-inputs.json")
    args = ["value", "archive.lock", "--root", "Tiny.conclusion", "--select", "Tiny.step"]
    selection = output(capsys, *args, "--graph", manifest)
    assert not selection["source_omissions"]
    row, = selection["rows"]
    assert (row["path"], row["first_line"], row["root_distances"]) == (
        "Tiny/Step.lean", 3, {"Tiny.conclusion": 1})
    record = json.loads(manifest.read_bytes())
    manifest.write_text(json.dumps({**record, "module_paths": {**record["module_paths"], "Tiny.Step": "archive/Step.lean"}}))
    assert main([*args, "--graph", str(manifest)]) == 2
    assert "module paths differ from the pinned target closure" in capsys.readouterr().err
    manifest.write_text(json.dumps({**record, "input_sha256": {**record["input_sha256"], "Tiny/Step.lean": "bad"}}))
    assert main([*args, "--graph", str(manifest)]) == 2
    assert "source and Lake input digests differ from the pin" in capsys.readouterr().err


def test_manifest_parses_the_same_bytes_it_verified(built, capsys, monkeypatch):
    _, lean, _ = built
    output(capsys, "graph", "vl.lock", "--dump", "inputs", "--lean", lean)
    args = ["value", "vl.lock", "--root", "Tiny.conclusion", "--graph", "inputs/graph-inputs.json"]
    original = output(capsys, *args)
    read_manifest = graph_inputs.read_manifest

    def replace_after_verification(path, project):
        snapshot = read_manifest(path, project)
        (path.parent / "kernel.tsv.gz").write_bytes(gzip.compress(b""))
        (path.parent / "ranges.gz").write_bytes(gzip.compress(b""))
        return snapshot

    monkeypatch.setattr(graph_inputs, "read_manifest", replace_after_verification)
    assert output(capsys, *args) == original
    assert main(args) == 2
    assert "bytes differ from the graph manifest" in capsys.readouterr().err
