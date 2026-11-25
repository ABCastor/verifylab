from pathlib import Path
import hashlib
import json
import shlex

from conftest import commit_all
from test_foreign import foreign, output
from verifylab.cli import main


def test_route_value_and_checked_packet(foreign, capsys):
    pin = output(capsys, "import", foreign)["project"]["commit"]
    args = ["vl.lock", "--root", "Tiny.conclusion"]
    assert output(capsys, "graph", "vl.lock")["edges"]["Tiny.conclusion"] == ["Tiny.step"]
    assert output(capsys, "graph", "vl.lock", "--modules")["edges"]["Tiny.lean"] == ["Tiny.Step"]
    rows = output(capsys, "value", *args)["rows"]
    assert [(r["name"], r["level"], r["users"], r["demand50"]) for r in rows] == [
        ("Tiny.conclusion", 0, 0, 0.5), ("Tiny.step", 1, 1, 0.25)]
    order = output(capsys, "order", *args, "--budget", "1")
    assert order["input"] == "Tiny.conclusion\ntheorem conclusion (p : Prop) (certificate : p) : p\n"
    assert order["root_source"] == {"path": "Tiny.lean", "line": 5}
    assert order["bytes"] == len(order["input"].encode())
    assert order["omitted"] == [{"name": "Tiny.step", "reason": "budget"}]
    assert output(capsys, "order", *args, "--bytes", "5")["input"] == ""
    assert main(["order", *args, "--bytes", "5"]) == 0 and capsys.readouterr().out == ""
    score = {"schema": "vl.judgments/1", "model": "fixture", "cost_usd": 0.02, "commit": pin,
             "rows": {"Tiny.conclusion": {"statement_sha256": order["sources"][0]["statement_sha256"], "relevance": 0.3}}}
    Path("model.json").write_text(json.dumps(score))
    data = output(capsys, "value", *args, "--judgments", "model.json")
    assert data["judgments"]["cost_usd"] == 0.02 and data["rows"][0]["model_relevance"] == 0.3
    score["rows"]["Tiny.conclusion"]["statement_sha256"] = "mismatched"
    Path("model.json").write_text(json.dumps(score))
    assert main(["value", *args, "--judgments", "model.json"]) == 2


def test_dump_separates_recorded_axioms_and_missing_coverage(foreign, capsys):
    output(capsys, "import", foreign)
    path = Path("deps.tsv")
    path.write_text("DEP\tthm\tTiny\tTiny.conclusion\t-\t\tTiny.step\n")
    d = output(capsys, "graph", "vl.lock", "--deps", path)
    assert d["edges"] == {"Tiny.conclusion": []} and d["graph"]["covered"] == 1
    assert main(["value", "vl.lock", "--deps", str(path), "--root", "Tiny.step"]) == 2


def test_packet_omits_headers_with_embedded_answers(foreign, capsys):
    path = foreign / "Tiny.lean"
    path.write_text(path.read_text().replace("(certificate : p)", "(certificate /- the proof is here -/ : p)"))
    output(capsys, "import", foreign, "--lock", "clean.lock")
    assert "Tiny.conclusion" in output(capsys, "order", "clean.lock", "--root", "Tiny.conclusion")["input"]
    commit_all(foreign, "annotated binder")
    output(capsys, "import", foreign)
    data = output(capsys, "order", "vl.lock", "--root", "Tiny.conclusion")
    assert "the proof is here" not in data["input"]
    assert data["omitted"][0]["reason"] == "outside plain statement contract"
    assert data["root_source"] == {"path": "Tiny.lean", "line": 5}

def test_target_estimate_uses_pinned_imports_and_labels_local_cache(foreign, capsys):
    output(capsys, "import", foreign)
    args = ("graph", "vl.lock", "--build", "Tiny")
    assert output(capsys, *args)["missing_oleans"] == ["Tiny", "Tiny.Step"]
    artifact = foreign / ".lake/build/lib/lean/Tiny/Step.olean"
    artifact.parent.mkdir(parents=True)
    artifact.write_bytes(b"unqualified artifact")
    result = output(capsys, *args)
    assert result["missing_oleans"] == ["Tiny"] and result["source_modules"] == 2
    assert "validity and time unmeasured" in result["cache_scope"]


def test_duplicate_names_cannot_enter_a_reading_packet(foreign, capsys):
    path = foreign / "Tiny.lean"
    text = path.read_text()
    path.write_text(text.replace("end Tiny", "theorem conclusion : False := sorry\n\nend Tiny"))
    commit_all(foreign, "ambiguous source names")
    output(capsys, "import", foreign)
    assert main(["inspect", "vl.lock", "Tiny.conclusion"]) == 2
    result = output(capsys, "graph", "vl.lock")
    assert "Tiny.conclusion" not in result["edges"] and result["source_omissions"]


def test_dump_module_disambiguates_only_one_supported_source(foreign, capsys):
    challenge = foreign / "Challenge.lean"
    challenge.write_text("namespace Tiny\n\ntheorem conclusion : False := sorry\n\nend Tiny\n")
    commit_all(foreign, "duplicate theorem in a challenge module")
    output(capsys, "import", foreign)
    args = ["vl.lock", "--root", "Tiny.conclusion"]
    assert output(capsys, "value", *args)["rows"][0]["name"] == "Tiny.conclusion"
    dump = Path("deps.tsv")
    dump.write_text("DEP\tthm\tTiny\tTiny.conclusion\t-\t\t\n")
    order = output(capsys, "order", *args, "--deps", dump)
    assert order["sources"][0]["path"] == "Tiny.lean"
    assert "certificate : p" in order["input"] and "False" not in order["input"]
    assert "caller supplied" in order["graph"]["artifact_origin"]

    dump.write_text("DEP\tthm\tMissing\tTiny.conclusion\t-\t\t\n")
    assert main(["value", *args, "--deps", str(dump)]) == 2
    capsys.readouterr()

    path = foreign / "Tiny.lean"
    text = path.read_text()
    path.write_text(text.replace("end Tiny", "theorem conclusion : False := sorry\n\nend Tiny"))
    commit_all(foreign, "duplicate theorem within the artifact module")
    output(capsys, "import", foreign, "--lock", "ambiguous.lock")
    dump.write_text("DEP\tthm\tTiny\tTiny.conclusion\t-\t\t\n")
    assert main(["order", "ambiguous.lock", *args[1:], "--deps", str(dump)]) == 2


def test_dump_selects_a_module_whose_public_section_runs_to_eof(foreign, capsys):
    implementation = foreign / "Tiny.lean"
    code = implementation.read_text().replace("namespace Tiny", "public section\n\nnamespace Tiny", 1)
    implementation.write_text("module\n\n" + code)
    challenge = foreign / "Challenge.lean"
    challenge.write_text(
        "module\n\n@[expose] public section\n\nnamespace Tiny\n"
        "theorem conclusion : False := sorry\nend Tiny\n\nend\n"
    )
    commit_all(foreign, "scope implementation and challenge declarations")
    output(capsys, "import", foreign)
    args = ["vl.lock", "--root", "Tiny.conclusion"]

    assert output(capsys, "inspect", "vl.lock", "Tiny.conclusion", "--file", "Tiny.lean")["statement"].endswith(" : p")
    assert output(capsys, "value", *args)["source_scope"] == "pinned Lake library source roots"
    dump = Path("deps.tsv")
    dump.write_text("DEP\tthm\tTiny\tTiny.conclusion\t-\t\t\n")
    order = output(capsys, "order", *args, "--deps", dump)
    assert [source["path"] for source in order["sources"]] == ["Tiny.lean"]
    assert "certificate : p" in order["input"]


def test_source_order_uses_pinned_lake_layout_and_accepts_explicit_file(foreign, capsys):
    archived = foreign / "archive" / "Tiny.lean"
    archived.parent.mkdir()
    archived.write_text("theorem Tiny.conclusion : True := trivial\n")
    active = foreign / "lib"
    active.mkdir()
    (foreign / "Tiny.lean").rename(active / "Tiny.lean")
    (foreign / "Tiny").rename(active / "Tiny")
    config = foreign / "lakefile.toml"
    config.write_text(config.read_text().replace("[[lean_lib]]", 'srcDir = "lib"\n\n[[lean_lib]]'))
    commit_all(foreign, "non-default library source and an old version of its theorem")
    origin = ["--from", str(foreign), "--at", "HEAD"]
    args = [*origin, "--root", "Tiny.conclusion"]
    data = output(capsys, "order", *args)
    assert [row["path"] for row in data["sources"]] == ["lib/Tiny.lean", "lib/Tiny/Step.lean"]
    assert data["source_scope"] == "pinned Lake library source roots"
    assert [s["path"] for s in output(capsys, "order", *args, "--file", "archive/Tiny.lean")["sources"]] == [
        "archive/Tiny.lean"]

    duplicate = active / "Tiny" / "Other.lean"
    duplicate.write_text(archived.read_text())
    commit_all(foreign, "two candidates inside the configured library")
    assert main(["order", *args]) == 2
    error = capsys.readouterr().err
    assert "candidate files: lib/Tiny.lean, lib/Tiny/Other.lean" in error
    assert "archive/Tiny.lean" not in error
    recovery = next(line for line in error.splitlines() if line.startswith("vl order") and "lib/Tiny.lean" in line)
    data = output(capsys, *shlex.split(recovery)[1:])
    assert "certificate : p" in data["input"]
    assert [s["path"] for s in data["sources"]] == ["lib/Tiny.lean"]

    # The override must still come from committed bytes.
    duplicate.write_text("theorem Tiny.conclusion : False := sorry\n")
    assert ": True" in output(capsys, "order", *args, "--file", "lib/Tiny/Other.lean")["input"]
    assert main(["order", *args, "--file", "lib/Tiny/Step.lean"]) == 2


def test_source_order_multi_root_error_gives_runnable_single_root_commands(foreign, capsys):
    args = ["--from", str(foreign), "--at", "HEAD", "--budget", "1", "--bytes", "1024", "--json"]
    assert main(["order", *args, "--root", "Tiny.conclusion", "--root", "Tiny.step"]) == 2
    error = capsys.readouterr().err
    assert "source-only order reads one root per call" in error
    roots = []
    for line in error.splitlines():
        if line.startswith("vl order"):
            assert main(shlex.split(line)[1:]) == 0
            data = json.loads(capsys.readouterr().out)
            assert len(data["sources"]) == 1
            roots.append(data["root"])
    assert roots == ["Tiny.conclusion", "Tiny.step"]


def test_order_omissions_are_bounded_by_default_and_explicitly_recoverable(foreign, capsys):
    path = foreign / "Tiny.lean"
    source = path.read_text()
    bad = "".join(f"def skipped{i} := 0\n" for i in range(18))
    steps = "".join(f"theorem helper{i} : True := trivial\n" for i in range(18))
    proof = "  have unused := (" + ", ".join(f"helper{i}" for i in range(18)) + ")\n  exact certificate"
    path.write_text(source.replace("  exact step p certificate", proof).replace("namespace Tiny\n", "namespace Tiny\n\n"
                                                                                     + bad + steps))
    commit_all(foreign, "many unsupported signatures and reached candidates")
    args = ["--from", str(foreign), "--root", "Tiny.conclusion", "--budget", "1"]
    default = output(capsys, "order", *args)
    full = output(capsys, "order", *args, "--full-omissions")
    for key in ("input_sha256", "source_omission_count", "omitted_counts"):
        assert default[key] == full[key]
    assert default["source_omission_count"] == len(full["source_omissions"]) == 18
    assert default["omitted_counts"] == {"budget": 18}
    assert len(full["omitted"]) == 18
    assert len(default["source_omissions"]) == len(default["omitted"]) == 10


def test_dump_reading_follows_kernel_connectors_without_inventing_edges(foreign, capsys):
    output(capsys, "import", foreign)
    path = Path("deps.tsv")
    path.write_text(
        "DEP\tthm\tTiny\tTiny.conclusion\t-\tTiny.hidden Mathlib.remote\t\n"
        "DEP\tdef\tTiny\tTiny.hidden\t-\tTiny.aux\t\n"
        "DEP\tthm\tTiny\tTiny.aux\t-\tTiny.step\t\n"
        "DEP\tthm\tTiny.Step\tTiny.step\t-\t\t\n"
        "DEP\tthm\tMathlib.Remote\tMathlib.remote\t-\tTiny.step\t\n"
        "DEP\tthm\tTiny\tTiny.unreached\t-\t\t\n"
        "DEP\tthm\tTiny\tTiny.unreached\t-\tTiny.aux\t\n"
    )
    graph = output(capsys, "graph", "vl.lock", "--deps", path)
    assert graph["edges"] == {"Tiny.conclusion": [], "Tiny.step": []}
    args = ["vl.lock", "--deps", str(path), "--root", "Tiny.conclusion"]
    result = output(capsys, "value", *args)
    assert [(r["name"], r["level"], r["users"], r["demand50"]) for r in result["rows"]] == [
        ("Tiny.conclusion", 0, 0, 0.5), ("Tiny.step", 3, 1, 0.0625)
    ]
    assert result["graph"]["reachable_kernel"] == 4
    assert result["graph"]["reachable_indexed"] == 2
    assert result["graph"]["frontier_constants"] == 1
    packet = output(capsys, "order", *args)
    assert {s["name"] for s in packet["sources"]} == {"Tiny.conclusion", "Tiny.step"}
    assert "Tiny.hidden" not in packet["input"]
    original = path.read_text()
    path.write_text(original + "DEP\tdef\tTiny.Step\tTiny.hidden\t-\tTiny.aux\t\n")
    assert output(capsys, "value", *args)["rows"] == result["rows"]
    path.write_text(original + "DEP\tdef\tTiny\tTiny.hidden\t-\tTiny.step\t\n")
    assert main(["value", *args]) == 2
    assert "conflicting rows for reached constant Tiny.hidden" in capsys.readouterr().err


def test_crlf_source_keeps_its_hash_and_enters_the_packet(foreign, capsys):
    path = foreign / "Tiny.lean"
    original = path.read_text().replace("theorem conclusion (p : Prop) (certificate : p) : p",
                                        "theorem conclusion (p : Prop)\n    (certificate : p) : p")
    path.write_bytes(original.replace("\n", "\r\n").encode())
    commit_all(foreign, "windows newlines")
    output(capsys, "import", foreign)
    data = output(capsys, "order", "vl.lock", "--root", "Tiny.conclusion", "--json")
    assert data["input"].startswith("Tiny.conclusion\ntheorem conclusion (p : Prop)\n    (certificate : p) : p\n")
    assert "\r" not in data["input"] and data["omitted"] == []
    source = data["sources"][0]
    assert source["newlines"] == "normalized"
    assert source["statement_sha256"] == hashlib.sha256(
        "theorem conclusion (p : Prop)\r\n    (certificate : p) : p".encode()).hexdigest()
    assert source["emitted_sha256"] != source["statement_sha256"]


def test_a_supplied_graph_without_proofs_is_refused(foreign, capsys):
    """The public part of a module-system file holds a theorem as an axiom, with the constants of its type only.
    Such rows gave 83 reached names where the proof of a real library has 5,307, and the command exited 0."""
    (foreign / "Tiny/Step.lean").write_text(
        "namespace Tiny\n\ntheorem step (p : Prop) (h : p) : p := h\n\ndef same (p : Prop) : Prop := p\n\nend Tiny\n")
    commit_all(foreign, "a definition beside the theorems")
    output(capsys, "import", foreign)
    path = Path("deps.tsv")
    proofs = ("DEP\tthm\tTiny\tTiny.conclusion\t-\tTiny.step\t\nDEP\tthm\tTiny.Step\tTiny.step\t-\t\t\n"
              "DEP\tdef\tTiny.Step\tTiny.same\t-\t\t\n")
    path.write_text(proofs)
    args = ["vl.lock", "--deps", str(path), "--root", "Tiny.conclusion"]
    assert [r["name"] for r in output(capsys, "value", *args)["rows"]] == ["Tiny.conclusion", "Tiny.step"]   # control
    path.write_text(proofs.replace("DEP\tthm\tTiny.Step\tTiny.step", "DEP\taxiom\tTiny.Step\tTiny.step"))
    for command in (["value", *args], ["order", *args], ["graph", "vl.lock", "--deps", str(path)],
                    ["value", *args, "--root", "Tiny.step"]):
        assert main(command) == 2
        assert ("1 theorems of the pinned source are not theorem rows, first Tiny.step (axiom): these rows hold no "
                "proof") in capsys.readouterr().err
    # A definition of the source is no theorem: its row may be of any kind.
    path.write_text(proofs.replace("DEP\tdef\tTiny.Step\tTiny.same", "DEP\topaque\tTiny.Step\tTiny.same"))
    assert main(["value", *args]) == 0
