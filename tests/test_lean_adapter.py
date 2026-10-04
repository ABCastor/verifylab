"""The Lean checker over Comparator, proven on the planted-defect fixture (fixtures/lean-planted/).

Every planted defect must be rejected with a reason that names its cause, every genuine control must
pass, and every guard must be shown able to fail: disabling it lets at least one defect through.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
import tomllib
from pathlib import Path

import pytest

from lean_helpers import (
    MATHLIB_MANIFEST, MATHLIB_PACKAGES, TOOLCHAIN, ScopePeakMemory, apply_case, assert_reasons, case_names, explain,
    git, lean_unavailable, load_case, make_repo, newer_than, request, run_case, tool_env, write_stamp,
)
from verifylab import gitref, jail
from verifylab.config import parse_config, rules_digest
from verifylab.adapters.lean_comparator import (
    GUARDS, NANODA_KERNEL, PROBE_GUARDS, VERDICT_GUARDS, LeanComparatorAdapter, classify, comparator_config, parse_spec,
    render_lakefile,
    resolve_tool, timings, toolchain_dir,
)
from verifylab.records import parse_item, sha256_hex

from conftest import lean_elan_home, lean_tools, write_machine

LEAN_SKIP = lean_unavailable()
NO_LEAN_CASES = {"d8-unknown-theorem", "d9-both-forms", "d9-target-outside", "d11-item-retargeted"}


@pytest.fixture
def lean_env(monkeypatch):
    if LEAN_SKIP:
        pytest.skip(LEAN_SKIP)
    for key, value in tool_env().items():
        monkeypatch.setenv(key, value)


# The [lean] table ---------------------------------------------------------------------------------

GOOD = {"target": "research/targets/t.lean", "theorems": ["A.main"], "proofs": {"A.main": "rfl"}, "imports": ["X.Y"]}


def test_parse_spec_accepts_both_forms():
    spec, problems = parse_spec(GOOD, "research/targets")
    assert problems == [] and spec.proofs == {"A.main": "rfl"} and spec.imports == ("X.Y",)
    spec, problems = parse_spec({"target": "research/targets/t.lean", "theorems": ["A.main"], "solution": "X.Sol"},
                                "research/targets")
    assert problems == [] and spec.solution == "X.Sol" and spec.proofs is None


@pytest.mark.parametrize("change, fragment", [
    ({"solution": "X.Sol"}, "exactly one of"),
    ({"proofs": None}, "exactly one of"),
    ({"target": "Fixture/Defs.lean"}, "under research/targets/"),
    ({"target": "research/targets/../vl.toml"}, "under research/targets/"),
    ({"target": "/abs/research/targets/t.lean"}, "under research/targets/"),
    ({"target": "research/targets/t.txt"}, "under research/targets/"),
    ({"theorems": []}, "non-empty list"),
    ({"theorems": ["A.main", "A.main"]}, "twice"),
    ({"theorems": ["A.«main»"]}, "invalid names"),
    ({"proofs": {"A.main": "  "}}, "non-empty proof terms"),
    ({"proofs": {"A.other": "rfl"}}, "no term for ['A.main']"),
    ({"imports": ["VLTrusted.Fixture.Defs"]}, "reserved"),
    ({"imports": "X.Y"}, "list of plain module names"),
    ({"extra": 1}, "unknown key"),
])
def test_parse_spec_rejects(change, fragment):
    table = {**GOOD, **change}
    table = {k: v for k, v in table.items() if v is not None}
    spec, problems = parse_spec(table, "research/targets")
    assert spec is None and any(fragment in p for p in problems), problems


def test_applies_only_to_items_with_a_lean_table():
    adapter = LeanComparatorAdapter()
    item = parse_item(b'+++\nid = "x1"\nkind = "question"\ntitle = "t"\nauthor = "human:a"\ncreated = "2026-10-01"\n'
                      b'statement = "s"\n+++\n', "research/items/x1.md")
    assert adapter.name == "lean-comparator" and not adapter.applies(item)
    fixture_item = Path(__file__).resolve().parents[1] / "fixtures/lean-planted/research/items/double-even.md"
    assert adapter.applies(parse_item(fixture_item.read_bytes(), "research/items/double-even.md"))


# Tools, toolchain, lakefile --------------------------------------------------------------------------

def test_toolchain_dir_mapping(monkeypatch):
    monkeypatch.setenv("ELAN_HOME", "/e")
    assert toolchain_dir("leanprover/lean4:v4.34.0-rc2\n") == Path("/e/toolchains/leanprover--lean4---v4.34.0-rc2")
    assert toolchain_dir("v4.27.0") == Path("/e/toolchains/leanprover--lean4---v4.27.0")
    assert toolchain_dir("leanprover/lean4:../../etc").parent == Path("/e/toolchains")   # one component, no traversal
    with pytest.raises(ValueError):
        toolchain_dir("leanprover/lean4:v4 $(rm)")


def test_resolve_tool_order_config_then_env_then_path(tmp_path):
    def exe(name):
        path = tmp_path / name
        path.write_text("#!/bin/sh\n")
        path.chmod(0o755)
        return path
    configured, from_env, on_path = exe("c1"), exe("c2"), tmp_path / "bin" / "comparator"
    on_path.parent.mkdir()
    on_path.write_text("#!/bin/sh\n")
    on_path.chmod(0o755)
    env = {"COMPARATOR_BIN": str(from_env), "PATH": str(on_path.parent)}
    assert resolve_tool("comparator", {"comparator": str(configured)}, tmp_path, env) == configured
    assert resolve_tool("comparator", {}, tmp_path, env) == from_env
    assert resolve_tool("comparator", {}, tmp_path, {"PATH": str(on_path.parent)}) == on_path
    assert resolve_tool("comparator", {}, tmp_path, {"PATH": str(tmp_path / "nowhere")}) is None
    assert resolve_tool("comparator", {"comparator": "c1"}, tmp_path, {"PATH": ""}) == configured   # relative to root


def test_render_lakefile_is_valid_toml():
    text = render_lakefile(
        Path("/pk"), [{"name": "mathlib", "scope": "leanprover-community", "url": "https://x/m.git",
                       "rev": "abc", "inputRev": "v1", "subDir": None}],
        {"autoImplicit": False, "pp": {"unicode": {"fun": True}}},
        [("VLChallenge", [], {}), ("VLRoot0", ["Fixture"], {"maxHeartbeats": 400000})])
    data = tomllib.loads(text)
    assert data["packagesDir"] == "/pk" and data["defaultTargets"] == []
    assert data["leanOptions"] == {"autoImplicit": False, "pp": {"unicode": {"fun": True}}}
    assert data["require"] == [{"name": "mathlib", "scope": "leanprover-community", "git": "https://x/m.git", "rev": "v1"}]
    assert data["lean_lib"][1] == {"name": "VLRoot0", "roots": ["Fixture"], "leanOptions": {"maxHeartbeats": 400000}}


# Comparator output classification (texts recorded from real runs on 1/10) --------------------------------

def _out(after_solution: str = "") -> str:
    return ("Building VLChallenge\n⚠ [3/4] Built VLChallenge (336ms)\nwarning: VLChallenge.lean:5:8: declaration uses `sorry`\n"
            "Exporting #[Nat, VL.DoubleEven.main] from VLChallenge\nBuilding VLSolution\n" + after_solution)


@pytest.mark.parametrize("stdout, stderr, rc, verdict, fragment", [
    (_out("Exporting #[Nat] from VLSolution\nRunning nanoda kernel on solution\nnanoda kernel accepts the solution\n"
          "Running Lean default kernel on solution.\nLean default kernel accepts the solution\nYour solution is okay!\n"),
     "", 0, "pass", "accepted"),
    (_out(), "uncaught exception: Illegal axiom detected: 'sorryAx'\n", 1, "fail", "contains `sorry`"),
    (_out(), "uncaught exception: Illegal axiom detected: 'VL.DoubleValue.main._native.native_decide.ax_1_1'\n", 1,
     "fail", "native_decide"),
    (_out(), "uncaught exception: Illegal axiom detected: 'cheat'\n", 1, "fail", "uses axiom 'cheat'"),
    (_out(), "uncaught exception: Const does not match between challenge and target 'Fixture.double'\n", 1,
     "fail", "declaration 'Fixture.double'"),
    (_out(), "uncaught exception: Challenge and solution theorem statement do not match: 'VL.DoubleEven.main'\n", 1,
     "fail", "differs from the target's"),
    (_out(), "uncaught exception: Challenge and solution constant kind don't match: 'VL.DoubleEven.main'\n", 1,
     "fail", "is not a theorem"),
    (_out("✖ [4/5] Building VLSolution\nerror: VLSolution.lean:7:55: Type mismatch\n  Fixture.double_eq_two_mul n\n"
          "has type\n  Fixture.double n = 2 * n\nerror: build failed\n"),
     "uncaught exception: Child exited with 1\n", 1, "fail", "does not compile: error: VLSolution.lean:7:55"),
    (_out("error: build failed\n"), "uncaught exception: Child exited with 137\n", 1, "error", "without a Lean diagnostic"),
    (_out("Exporting #[Nat, VL.DoubleEven.main] from VLSolution\n"),
     "PANIC at dumpConstant Export:237:48: Constant VL.DoubleEven.main not found in environment.\n"
     "uncaught exception: Child exited with 134\n", 1, "fail", "does not declare target theorem"),
    (_out("Exporting #[Nat] from VLSolution\n"),
     "PANIC at dumpConstant Export:237:48: Constant Nat not found in environment.\nuncaught exception: Child exited with 134\n",
     1, "error", "the solution's environment lacks 'Nat', which Comparator exports from both sides"),
    ("Building VLChallenge\nerror: VLChallenge.lean:3:0: unknown module prefix\nerror: build failed\n",
     "uncaught exception: Child exited with 1\n", 1, "error", "challenge (trusted target and definitions) did not build"),
    (_out("Exporting #[Nat] from VLSolution\nRunning Lean default kernel on solution.\nLean default kernel rejects the solution\n"),
     "uncaught exception: (kernel) declaration type mismatch\n", 1, "fail", "kernel rejected"),
    ("", "Killed\n", -9, "error", "Comparator failed during start"),
])
def test_classify(stdout, stderr, rc, verdict, fragment):
    result = classify(stdout, stderr, rc, ("VL.DoubleEven.main",))
    assert result.verdict == verdict and fragment in result.reason, result


_EXPORTED = _out("Exporting #[Nat] from VLSolution\n")
_KERNELS = _EXPORTED + "Running nanoda kernel on solution\n"


# One case per message Comparator can throw (its source at rev 19e111e: Compare.lean, Axioms.lean, Main.lean).
@pytest.mark.parametrize("stdout, message, verdict, fragment", [
    (_EXPORTED, "Solution constant is not a theorem: 'VL.DoubleEven.main'", "fail", "is not a theorem"),
    (_EXPORTED, "Solution constant is not a definition: 'Fixture.double'", "fail", "is not a definition in the solution"),
    (_EXPORTED, "Const not found in solution: 'VL.DoubleEven.main'", "fail", "does not declare 'VL.DoubleEven.main'"),
    (_EXPORTED, "Const not found in solution 'Fixture.double'", "fail", "does not declare 'Fixture.double'"),
    (_EXPORTED, "Constant not found in solution 'Fixture.helper'", "error",
     "axiom check reached 'Fixture.helper', which the solution's own export does not contain"),
    (_EXPORTED, "Const not found in challenge: 'VL.DoubleEven.main'", "error", "missing from the challenge's export"),
    (_EXPORTED, "Const not found in challenge 'Fixture.double'", "error", "missing from the challenge's export"),
    (_EXPORTED, "Challenge constant is not a definition: 'Fixture.double'", "error", "not a definition; a tool"),
    (_KERNELS + "Error while interacting with nanoda kernel\nRunning Lean default kernel on solution.\n"
     "Lean default kernel accepts the solution\n",
     "Error while interacting with nanoda kernel: no such file", "error", "the nanoda kernel could not be run"),
    (_KERNELS + "nanoda kernel rejected the solution\nRunning Lean default kernel on solution.\n"
     "Lean default kernel accepts the solution\n", "nanoda exited with 1", "fail", "the nanoda kernel rejected"),
    (_EXPORTED + "Running Lean default kernel on solution.\nLean default kernel accepts the solution\n"
     "Quotient post-check rejects the solution\n", "Quotient constant mismatch on: Quot.lift", "fail",
     "kernel rejected the solution: Quotient constant mismatch on: Quot.lift"),
    (_EXPORTED + "Running Lean default kernel on solution.\nLean default kernel accepts the solution\n"
     "Quotient post-check rejects the solution\n", "Could not find quotient constant in final kernel env: Quot",
     "fail", "Could not find quotient constant"),
    ("", "Cannot use enable_nanoda and an external kernel list at the same time, register nanoda in the list "
     "instead.", "error", "enable_nanoda together with external_kernels"),
    ("", "nanoda has an empty command", "error", "external kernel 'nanoda' has an empty command"),
    ("", "Expected config file path as first argument.", "error", "without its configuration file"),
])
def test_classify_every_comparator_message(stdout, message, verdict, fragment):
    result = classify(stdout, f"uncaught exception: {message}\n", 1, ("VL.DoubleEven.main",), ("lean", "nanoda"))
    assert result.verdict == verdict and fragment in result.reason, result
    assert result.message == message


def test_pass_needs_every_configured_kernel_to_report_acceptance():
    ok = _EXPORTED + ("Running nanoda kernel on solution\nnanoda kernel accepts the solution\n"
                      "Running Lean default kernel on solution.\nLean default kernel accepts the solution\n"
                      "Your solution is okay!\n")
    assert classify(ok, "", 0, (), ("lean", "nanoda")).verdict == "pass"
    silent = ok.replace("nanoda kernel accepts the solution\n", "")
    result = classify(silent, "", 0, (), ("lean", "nanoda"))
    assert result.verdict == "error" and "kernel(s) ['nanoda'] did not report accepting" in result.reason
    assert classify(silent, "", 0, (), ("lean",)).verdict == "pass"


def test_only_comparators_own_lines_after_the_solution_export_count_for_a_pass():
    """The candidate's build prints on Comparator's stdout before the solution is exported: a forged acceptance line
    (or success marker) there never stands for a kernel that did not report."""
    forged = _out("nanoda kernel accepts the solution\nYour solution is okay!\n")
    real_lean_only = ("Exporting #[Nat] from VLSolution\nRunning Lean default kernel on solution.\n"
                      "Lean default kernel accepts the solution\nYour solution is okay!\n")
    result = classify(forged + real_lean_only, "", 0, (), ("lean", "nanoda"))
    assert result.verdict == "error" and "kernel(s) ['nanoda'] did not report accepting" in result.reason, result
    assert classify(_out("Your solution is okay!\n"), "", 0, (), ("lean",)).verdict == "error"   # never exported


def test_worktree_files_the_check_reads_are_bounded(tmp_path, monkeypatch):
    from verifylab.adapters import lean_comparator
    root = make_repo(tmp_path)
    check = lean_comparator._Check(request(root, "double-even"))
    monkeypatch.setattr(lean_comparator, "MAX_SOURCE_BYTES", 16)
    with pytest.raises(lean_comparator._Stop) as stop:
        check.read_worktree("Fixture/Defs.lean")
    assert stop.value.verdict == "error" and "larger than 16 bytes" in stop.value.reasons[0]
    assert check.read_worktree("Fixture/Nothing.lean") is None and check.read_worktree("Fixture") is None


# A real run's stdout (1/10), each line with the second it arrived.
RUN = [(0.16, "Building VLChallenge"), (0.6, "✔ [2/4] Built Fixture.Defs (351ms)"),
       (0.9, "⚠ [3/4] Built VLChallenge (1.2s)"), (0.9, "warning: VLChallenge.lean:10:8: declaration uses `sorry`"),
       (0.96, "Build completed successfully (4 jobs)."), (0.96, "Exporting #[Nat, VL.DoubleEven.main] from VLChallenge"),
       (1.6, "Building VLSolution"), (2.0, "✔ [3/5] Built Fixture.Proofs (12s)"), (2.2, "✔ [4/5] Built VLSolution (174ms)"),
       (2.31, "Build completed successfully (5 jobs)."), (2.31, "Exporting #[Nat, VL.DoubleEven.main] from VLSolution"),
       (3.48, "Running nanoda kernel on solution"), (3.62, "nanoda kernel accepts the solution"),
       (3.62, "Running Lean default kernel on solution."), (3.82, "Lean default kernel accepts the solution"),
       (3.82, "Your solution is okay!")]


def test_timings_come_from_comparator_and_lake_lines_only():
    measured = timings([(at, "stdout", line) for at, line in RUN] + [(5.0, "stderr", "Building VLSolution")], 3.9)
    assert measured["phases"] == {"comparator_start": 0.16, "build_challenge": 0.8, "export_challenge": 0.64,
                                  "build_solution": 0.71, "export_solution_and_compare": 1.17, "kernel_nanoda": 0.14,
                                  "kernel_lean": 0.2}
    assert measured["modules"] == [{"module": "Fixture.Proofs", "ms": 12000, "side": "solution"},
                                   {"module": "VLChallenge", "ms": 1200, "side": "challenge"},
                                   {"module": "Fixture.Defs", "ms": 351, "side": "challenge"},
                                   {"module": "VLSolution", "ms": 174, "side": "solution"}]
    assert measured["jobs"] == {"build_challenge": 4, "build_solution": 5}
    cut = timings([(at, "stdout", line) for at, line in RUN[:8]], 1.9)        # killed while building the solution
    assert list(cut["phases"]) == ["comparator_start", "build_challenge", "export_challenge", "build_solution"]
    assert cut["phases"]["build_solution"] == 0.3 and cut["jobs"] == {"build_challenge": 4, "build_solution": 5}


def test_comparator_config_registers_nanoda_as_an_external_kernel(tmp_path):
    nanoda = tmp_path / "nanoda_bin"
    config = comparator_config(("VL.T.main",), ["propext"], nanoda)
    assert config == {"challenge_module": "VLChallenge", "solution_module": "VLSolution",
                      "theorem_names": ["VL.T.main"], "permitted_axioms": ["propext"],
                      "external_kernels": {"nanoda": [str(nanoda)]}}
    assert "noda" in NANODA_KERNEL      # Comparator hands a nanoda-style config only to names containing "noda"
    assert "external_kernels" not in comparator_config(("VL.T.main",), [], None)


# Planted cases that fail before any tool runs ------------------------------------------------------------

@pytest.mark.parametrize("name", sorted(NO_LEAN_CASES))
def test_planted_case_without_lean(tmp_path, name):
    case = load_case(name)
    outcome = run_case(tmp_path, name)
    assert outcome.verdict == case.expect, explain(outcome)
    assert_reasons(outcome, case.reasons)
    assert outcome.command == []                     # rejected before Comparator ran


def test_bad_request_is_an_error_never_an_exception(tmp_path):
    root = make_repo(tmp_path)
    adapter = LeanComparatorAdapter()
    outcome = adapter.check(request(root, "double-even", assurance="bogus"))
    assert outcome.verdict == "error" and "assurance" in outcome.reasons[0]
    outcome = adapter.check(request(root, "double-even", guards={"no_such_guard"}))
    assert outcome.verdict == "error" and "unknown guard" in outcome.reasons[0]


@pytest.mark.lean
def test_missing_tools_are_unsupported(tmp_path, monkeypatch):
    """With the toolchain and the jail present, an empty machine configuration leaves the tools themselves missing."""
    if not (toolchain_dir(TOOLCHAIN, elan_home=Path(lean_elan_home())) / "bin" / "lake").is_file():
        pytest.skip(f"toolchain {TOOLCHAIN} not installed, so the check stops before looking for tools")
    if not jail.available():
        pytest.skip("bwrap missing, so the check stops before looking for tools")
    for var in ("COMPARATOR_BIN", "COMPARATOR_LANDRUN", "COMPARATOR_LEAN4EXPORT", "COMPARATOR_NANODA"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    write_machine({}, elan_home=lean_elan_home())
    root = make_repo(tmp_path)
    outcome = LeanComparatorAdapter().check(request(root, "double-even"))
    assert outcome.verdict == "unsupported", explain(outcome)
    assert outcome.reasons[0].startswith("tool(s) not found: comparator, landrun, lean4export"), outcome.reasons


def test_candidate_importing_a_missing_module_fails(tmp_path):
    root = make_repo(tmp_path)
    item = root / "research/items/double-even.md"
    item.write_text(item.read_text().replace('imports = ["Fixture.Proofs"]', 'imports = ["Fixture.Proofs", "Fixture.Gone"]'))
    outcome = LeanComparatorAdapter().check(request(root, "double-even"))
    assert outcome.verdict == "fail", explain(outcome)
    assert "module Fixture.Gone not found at Fixture/Gone.lean" in outcome.reasons[0]


def test_candidate_importing_a_reserved_module_is_an_error(tmp_path):
    root = make_repo(tmp_path)
    (root / "Fixture/Sneaky.lean").write_text("import VLChallenge\n")
    item = root / "research/items/double-even.md"
    item.write_text(item.read_text().replace('imports = ["Fixture.Proofs"]', 'imports = ["Fixture.Sneaky"]'))
    outcome = LeanComparatorAdapter().check(request(root, "double-even"))
    assert outcome.verdict == "error" and "reserved check modules ['VLChallenge']" in outcome.reasons[0], explain(outcome)


def test_new_item_not_on_trusted_ref_is_an_error(tmp_path):
    root = make_repo(tmp_path)
    src = root / "research/items/double-even.md"
    (root / "research/items/double-new.md").write_text(src.read_text().replace('id = "double-even"', 'id = "double-new"'))
    outcome = LeanComparatorAdapter().check(request(root, "double-new"))
    assert outcome.verdict == "error" and "not on the trusted commit" in outcome.reasons[0]


# Planted defects and genuine controls (Lean) --------------------------------------------------------------

LEAN_CASES = [n for n in case_names() if n not in NO_LEAN_CASES]


def assert_flags(outcome, case) -> None:
    """The statement probes and lints flag what the case says, and nothing on the theorems it says are clean."""
    probes = outcome.checked.get("probes", {})
    for name in case.trivial:
        assert probes[name]["trivial_by"], f"{name} not flagged trivial\n{probes}\n{explain(outcome)}"
    for name in case.vacuous:
        assert probes[name]["vacuous_by"], f"{name} not flagged vacuous\n{probes}\n{explain(outcome)}"
    for name in case.clean:
        assert (probes[name]["trivial_by"], probes[name]["vacuous_by"]) == (None, None), probes
        assert probes[name]["prop_hypotheses"] is not None, probes
    if case.lint:
        assert case.lint in [x["kind"] for x in outcome.checked.get("lints", [])], outcome.checked.get("lints")


@pytest.mark.lean
@pytest.mark.parametrize("name", LEAN_CASES)
def test_planted_case(lean_env, shared_cases, name):
    case = load_case(name)
    outcome = shared_cases(name)
    assert outcome.verdict == case.expect, explain(outcome)
    assert_reasons(outcome, case.reasons)
    assert outcome.environment["isolation"]["kind"] == "bwrap+landrun", outcome.environment
    assert_flags(outcome, case)


@pytest.mark.lean
def test_receipt_fields_bind_candidate_and_trusted_inputs(lean_env, shared_cases):
    outcome, root = shared_cases.run("control-original")
    assert outcome.verdict == "pass", explain(outcome)
    trusted = gitref.rev_parse(root, "trusted")
    worktree = {p: sha256_hex((root / p).read_bytes()) for p in
                ("Fixture/Defs.lean", "Fixture/Proofs.lean", "lakefile.toml", "lake-manifest.json", "lean-toolchain")}
    assert outcome.files == worktree
    target = "research/targets/double-even.lean"
    for path in (target, "Fixture/Defs.lean", "lean-toolchain", "lake-manifest.json"):   # what Lake builds against
        assert outcome.trusted_files[path] == sha256_hex(gitref.show(root, trusted, path)), path
    config = parse_config(root, gitref.show(root, trusted, "research/vl.toml").decode())
    assert outcome.trusted_files["research/vl.toml#verdict-rules"] == rules_digest(config)
    assert "research/vl.toml" not in outcome.trusted_files     # only the verdict-relevant part is bound
    assert "Fixture/Proofs.lean" not in outcome.trusted_files      # not in the target's closure
    assert outcome.target == {"path": target, "sha256": outcome.trusted_files[target],
                              "theorems": ["VL.DoubleEven.main"], "source": "trusted-commit", "commit": trusted}
    from verifylab.commands.check import build_receipt
    from verifylab.records import receipt_problems
    from verifylab.repo import Repo
    repo = Repo.open(root)
    item = repo.load_item("double-even")
    receipt = build_receipt(repo, item, item, "lean-comparator", "protected", trusted, outcome, "t0", "t1")
    assert receipt_problems(json.loads(json.dumps(receipt))) == []


@pytest.mark.lean
def test_receipt_records_what_was_checked_and_with_which_tools(lean_env, shared_cases):
    outcome = shared_cases("control-original")
    nanoda = "COMPARATOR_NANODA" in tool_env()
    assert outcome.checked["kernels"] == (["lean", "nanoda"] if nanoda else ["lean"])
    if nanoda:     # registered through Comparator's own external_kernels interface, and it really ran
        assert f'"external_kernels": {{"nanoda": ["{Path(tool_env()["COMPARATOR_NANODA"]).resolve()}"]}}' in outcome.log
        assert "enable_nanoda" not in outcome.log and "COMPARATOR_NANODA" not in " ".join(outcome.command)
        assert "nanoda kernel accepts the solution" in outcome.log
    assert outcome.checked["permitted_axioms"] == ["propext", "Quot.sound", "Classical.choice"]
    assert outcome.checked["trusted_closure"] == ["Fixture/Defs.lean"]
    assert outcome.checked["definitions_mode"].startswith("shared")    # candidate did not touch the closure
    assert outcome.checked["trusted_modules"] == []
    assert outcome.checked["definitions_source"] == "trusted-commit"
    assert outcome.checked["comparator"]["result"] == "accepted"
    env = outcome.environment
    assert env["toolchain"] == TOOLCHAIN and "Lean (version 4.34.0-rc2" in env["lean_version"]
    for name in ("comparator", "landrun", "lean4export"):
        assert len(env["tools"][name]["sha256"]) == 64
        assert env["tools"][name]["source"] == f"machine.toml [tools] {name}"
        assert env["tools"][name]["pinned_by"].endswith("REVISIONS")
    assert env["machine_config"]["path"].endswith("verifylab/machine.toml") and len(env["machine_config"]["sha256"]) == 64


@pytest.mark.lean
def test_receipt_records_isolation_and_where_the_time_went(lean_env, shared_cases):
    outcome = shared_cases("control-original")
    env = outcome.environment
    assert "Landlock" in env["isolation"]["landrun"] and env["isolation"]["memory_max"] == "12G"
    assert "Your solution is okay!" in outcome.log and "PROBE-DENIED" in outcome.log
    assert outcome.command[:3] == [jail.program("systemd-run"), "--user", "--wait"]
    assert "RestrictAddressFamilies=~AF_UNIX" in outcome.command
    assert env["isolation"]["systemd_service"] and not env["isolation"]["systemd_scope"]
    assert jail.program("bwrap") in outcome.command
    assert "--slice=vl.slice" in outcome.command and env["isolation"]["slice"] == "vl.slice"
    # Phase timings: Comparator's lines arrive as printed (stdbuf -oL), Lake's per-module times, the scope's peak.
    stdbuf = next(k for k, part in enumerate(outcome.command) if part.endswith("/stdbuf"))
    assert outcome.command[stdbuf - 2:stdbuf + 2] == ["lake", "env", outcome.command[stdbuf], "-oL"]
    phases = outcome.extra["phases"]
    assert list(phases)[:5] == ["prepare", "isolation_probe", "prebuild_challenge", "comparator_start", "build_challenge"]
    assert {"export_challenge", "build_solution", "export_solution_and_compare", "kernel_lean"} <= set(phases)
    assert phases["export_challenge"] > 0 and phases["export_solution_and_compare"] > 0   # 0.0 when block-buffered
    assert sum(phases.values()) <= outcome.extra["seconds"] + 0.1
    assert any(m["module"] == "VLChallenge" and m["side"] == "challenge" for m in outcome.extra["modules"])
    assert outcome.extra["jobs"]["build_challenge"] >= 2 and outcome.extra["memory_peak_bytes"] > 50 * 2**20


# Guards: each one disabled lets a planted defect through ------------------------------------------------------

GUARD_DEFECTS = {
    "trusted_target": "d3-weakened-target-module",
    "trusted_definitions": "d7-altered-definition",
    "permitted_axioms": "d4-sorry",
}


PROBE_CASES = [n for n in LEAN_CASES if load_case(n).guard in PROBE_GUARDS]


def test_every_guard_has_a_planted_defect():
    assert set(GUARD_DEFECTS) == VERDICT_GUARDS
    assert {load_case(n).guard for n in PROBE_CASES} == PROBE_GUARDS == GUARDS - VERDICT_GUARDS


@pytest.mark.lean
@pytest.mark.parametrize("name", PROBE_CASES)
def test_disabling_a_probe_guard_lets_its_case_go_unflagged(tmp_path, lean_env, name):
    """Each statement probe, the pre-built copy they read, and the lints: off, the planted case is not flagged
    (the verdict, Comparator's, is the same either way). The guarded run is test_planted_case."""
    case = load_case(name)
    outcome = run_case(tmp_path, name, guards={case.guard})
    assert outcome.verdict == case.expect, explain(outcome)
    assert outcome.checked["guards_disabled"] == [case.guard]
    probes = outcome.checked.get("probes", {})
    for name in case.trivial if case.guard in ("probe_trivial", "probe_snapshot") else ():
        assert probes[name]["trivial_by"] is None, probes
    for name in case.vacuous if case.guard == "probe_vacuous" else ():
        assert probes[name]["vacuous_by"] is None, probes
    if case.guard == "lints":
        assert "lints" not in outcome.checked
    if case.guard == "probe_snapshot":   # the probes read the challenge the candidate's build emptied
        assert any("exited with" in p for p in outcome.checked["probe_run"]["problems"]), outcome.checked["probe_run"]


@pytest.mark.lean
@pytest.mark.parametrize("guard", sorted(GUARD_DEFECTS))
def test_disabling_guard_lets_its_defect_pass(tmp_path, lean_env, shared_cases, guard):
    defect = GUARD_DEFECTS[guard]
    guarded = shared_cases(defect)
    assert guarded.verdict == "fail", explain(guarded)
    unguarded = run_case(tmp_path, defect, guards={guard})
    assert unguarded.verdict == "pass", explain(unguarded)
    assert unguarded.checked["guards_disabled"] == [guard]


@pytest.mark.lean
def test_candidate_declared_axiom_cannot_be_permitted_even_with_the_guard_off(tmp_path, lean_env):
    """Defence in depth: Comparator compares permitted axioms between challenge and solution, so an
    axiom that only the candidate declares is rejected even when vl permits everything."""
    outcome = run_case(tmp_path, "d5-extra-axiom", guards={"permitted_axioms"})
    assert outcome.verdict == "error", explain(outcome)
    assert "'Fixture.cheat' does not exist in the challenge environment" in outcome.reasons[0]


@pytest.mark.lean
@pytest.mark.parametrize("defect, changed", [("d3-weakened-target-module", "research/targets/double-even.lean"),
                                             ("d7-altered-definition", "Fixture/Defs.lean")])
def test_exploratory_mode_passes_planted_defects_which_is_why_exploratory_never_verifies(
        tmp_path, lean_env, defect, changed):
    outcome = run_case(tmp_path, defect, assurance="exploratory")
    assert outcome.verdict == "pass", explain(outcome)
    assert outcome.target["source"] == "worktree" and outcome.trusted_files == {}
    assert changed in outcome.files


@pytest.mark.lean
def test_exploratory_mode_still_checks_axioms(tmp_path, lean_env):
    outcome = run_case(tmp_path, "d4-sorry", assurance="exploratory")
    assert outcome.verdict == "fail" and "sorry" in outcome.reasons[0], explain(outcome)


# Lake packages: read-only, a write attempt fails loudly ------------------------------------------------------

def _dep_package(tmp_path: Path) -> tuple[Path, str]:
    """A tiny git package `dep` with sources but no build products, cloned into a packages folder."""
    origin = tmp_path / "dep-origin"
    origin.mkdir()
    (origin / "lean-toolchain").write_text(TOOLCHAIN + "\n")
    (origin / "lakefile.toml").write_text('name = "dep"\nversion = "0.1.0"\n\n[[lean_lib]]\nname = "Dep"\n')
    (origin / "Dep.lean").write_text("def Dep.value : Nat := 7\n")
    git(origin, "init", "-q", "-b", "main")
    git(origin, "-c", "user.name=T", "-c", "user.email=t@e", "add", "-A")
    git(origin, "-c", "user.name=T", "-c", "user.email=t@e", "commit", "-q", "-m", "dep")
    rev = git(origin, "rev-parse", "HEAD").strip()
    packages = tmp_path / "packages"
    packages.mkdir()
    subprocess.run(["git", "clone", "-q", str(origin), str(packages / "dep")], check=True)
    return packages, rev


def _depend_on(tmp_path: Path, packages: Path, rev: str) -> Path:
    """The fixture, with a trusted target that imports module Dep of the package `dep` found in `packages`."""
    root = make_repo(tmp_path)
    git(root, "checkout", "-q", "trusted")
    manifest = {"version": "1.2.0", "packagesDir": ".lake/packages", "name": "fixture", "lakeDir": ".lake",
                "fixedToolchain": False,
                "packages": [{"url": str(tmp_path / "dep-origin"), "type": "git", "subDir": None, "scope": "",
                              "rev": rev, "name": "dep", "manifestFile": "lake-manifest.json", "inputRev": "main",
                              "inherited": False, "configFile": "lakefile.toml"}]}
    (root / "lake-manifest.json").write_text(json.dumps(manifest, indent=1))
    vl = root / "research/vl.toml"
    vl.write_text(vl.read_text() + f'packages = "{packages}"\n')
    target = root / "research/targets/double-value.lean"
    target.write_text("import Fixture.Defs\nimport Dep\n\nnamespace VL.DoubleValue\n\n"
                      "theorem main : Fixture.double 21 = 42 := sorry\n\nend VL.DoubleValue\n")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "depend on package dep")
    return root


@pytest.mark.lean
def test_unbuilt_package_is_reported_by_lakes_own_preflight_and_never_written(tmp_path, lean_env):
    packages, rev = _dep_package(tmp_path)
    root = _depend_on(tmp_path, packages, rev)
    stamp = write_stamp(tmp_path)
    outcome = LeanComparatorAdapter().check(request(root, "double-value"))
    assert outcome.verdict == "error", explain(outcome)
    assert outcome.reasons[0] == (f"shared build cache is incomplete: run `lake build` in the project whose packages "
                                  f"directory is {packages} (Lake would rebuild Dep)"), explain(outcome)
    assert "lake build --no-build Dep" in outcome.log and "Building VLChallenge" not in outcome.log
    assert newer_than(packages, stamp) == []
    assert not (packages / "dep" / ".lake").exists()


@pytest.mark.lean
def test_built_package_passes_the_preflight(tmp_path, lean_env):
    packages, rev = _dep_package(tmp_path)
    env = {**os.environ, "PATH": f"{toolchain_dir(TOOLCHAIN) / 'bin'}:/usr/bin:/bin"}
    subprocess.run(["lake", "build", "Dep"], cwd=packages / "dep", env=env, check=True, capture_output=True)
    root = _depend_on(tmp_path, packages, rev)
    stamp = write_stamp(tmp_path)
    outcome = LeanComparatorAdapter().check(request(root, "double-value"))
    assert outcome.verdict == "pass", explain(outcome)
    assert "All targets up-to-date" in outcome.log and outcome.extra["phases"]["preflight"] >= 0
    assert newer_than(packages, stamp) == []


# Mathlib smoke: reuse a built Mathlib read-only (lean_helpers: VL_TEST_MATHLIB or machine.toml [caches]) ---------

SMOKE_REPORT = Path(os.environ.get("VL_SMOKE_REPORT", "/dev/null"))


@pytest.mark.lean
def test_mathlib_smoke_reuses_built_packages_read_only(tmp_path, lean_env):
    if MATHLIB_PACKAGES is None:
        pytest.skip("no built Mathlib: set VL_TEST_MATHLIB to a Lake project whose .lake/packages holds one, or name "
                    "one under [caches] in machine.toml")
    manifest = json.loads(MATHLIB_MANIFEST.read_text())
    mathlib = next(p for p in manifest["packages"] if p["name"] == "mathlib")
    root = tmp_path / "smoke"
    (root / "Small").mkdir(parents=True)
    (root / "research" / "targets").mkdir(parents=True)
    (root / "research" / "items").mkdir(parents=True)
    (root / "lean-toolchain").write_text(TOOLCHAIN + "\n")
    (root / "lakefile.toml").write_text(
        'name = "smoke"\nversion = "0.1.0"\n\n[[require]]\nname = "mathlib"\n'
        f'scope = "leanprover-community"\ngit = "{mathlib["url"]}"\nrev = "{mathlib["inputRev"]}"\n\n'
        '[[lean_lib]]\nname = "Small"\n')
    (root / "lake-manifest.json").write_text(json.dumps({**manifest, "name": "smoke"}, indent=1))
    (root / "Small" / "Lemma.lean").write_text(
        "import Mathlib.Data.Finset.Card\n\nnamespace Small\n\n"
        "theorem range_card (n : ℕ) : (Finset.range n).card = n := Finset.card_range n\n\nend Small\n")
    (root / "research" / "vl.toml").write_text(
        '[project]\nname = "smoke"\n\n[lean]\nroots = ["Small"]\n'
        f'packages = "{MATHLIB_PACKAGES}"\n')
    (root / "research" / "targets" / "range-card.lean").write_text(
        "import Mathlib.Data.Finset.Card\n\nnamespace VL.RangeCard\n\n"
        "theorem main (n : ℕ) : (Finset.range n).card = n := sorry\n\nend VL.RangeCard\n")
    (root / "research" / "items" / "range-card.md").write_text(
        '+++\nid = "range-card"\nkind = "result"\ntitle = "card of range"\nauthor = "human:fixture"\n'
        'created = "2026-10-01"\nstatement = "card (range n) = n"\nclaim = "formal"\n\n[lean]\n'
        'target = "research/targets/range-card.lean"\ntheorems = ["VL.RangeCard.main"]\n'
        'proofs = { "VL.RangeCard.main" = "Small.range_card n" }\nimports = ["Small.Lemma"]\n+++\nSmoke.\n')
    git(root, "init", "-q", "-b", "trusted")
    git(root, "config", "vl.trustedRef", "trusted")
    git(root, "-c", "user.name=T", "-c", "user.email=t@e", "add", "-A")
    git(root, "-c", "user.name=T", "-c", "user.email=t@e", "commit", "-q", "-m", "smoke")
    stamp = write_stamp(tmp_path)
    req = request(root, "range-card")
    started = time.monotonic()
    with ScopePeakMemory(str(req.scratch)) as memory:
        outcome = LeanComparatorAdapter().check(req)
    wall = time.monotonic() - started
    written = newer_than(MATHLIB_PACKAGES, stamp)
    report = {"verdict": outcome.verdict, "wall_seconds": round(wall, 1), "peak_memory_bytes": memory.peak,
              "scopes_seen": len(memory.scopes), "packages_written_after_stamp": written[:10],
              "kernels": outcome.checked.get("kernels"), "isolation": outcome.environment.get("isolation"),
              "phases": outcome.extra.get("phases")}
    print("MATHLIB-SMOKE", json.dumps(report))
    if SMOKE_REPORT != Path("/dev/null"):
        SMOKE_REPORT.write_text(json.dumps(report, indent=2))
    assert outcome.verdict == "pass", explain(outcome)
    assert written == [], written
    assert outcome.environment["packages"]["dir"] == str(MATHLIB_PACKAGES.resolve())
    assert "lake build --no-build Mathlib.Data.Finset.Card" in outcome.log and "All targets up-to-date" in outcome.log


PRIVATE_DEFS = """namespace Fixture

private theorem pos_succ (n : Nat) : 0 < n + 1 := Nat.succ_pos n

/-- A definition whose value uses a private lemma: its compiled form carries a module-dependent name. -/
def succPos (n : Nat) : {m : Nat // 0 < m} := ⟨n + 1, pos_succ n⟩

end Fixture
"""

PRIVATE_TARGET = """import Fixture.Private

namespace VL.SuccPos

theorem main (n : Nat) : (Fixture.succPos n).val = n + 1 := sorry

end VL.SuccPos
"""

PRIVATE_ITEM = """+++
id = "succ-pos"
kind = "result"
title = "succPos adds one"
author = "human:fixture"
created = "2026-10-01"
statement = "The value of succPos n is n + 1."
claim = "formal"

[lean]
target = "research/targets/succ-pos.lean"
theorems = ["VL.SuccPos.main"]
proofs = { "VL.SuccPos.main" = "rfl" }
imports = []
+++
A definition built with a private lemma, as definitions in real projects often are.
"""


def _private_repo(tmp_path: Path) -> Path:
    root = make_repo(tmp_path)
    git(root, "checkout", "-q", "trusted")
    (root / "Fixture" / "Private.lean").write_text(PRIVATE_DEFS)
    (root / "research" / "targets" / "succ-pos.lean").write_text(PRIVATE_TARGET)
    (root / "research" / "items" / "succ-pos.md").write_text(PRIVATE_ITEM)
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "private helper")
    git(root, "checkout", "-q", "candidate")
    git(root, "merge", "-q", "--ff-only", "trusted")
    return root


@pytest.mark.lean
@pytest.mark.skipif(LEAN_SKIP is not None, reason=str(LEAN_SKIP))
def test_unchanged_closure_is_compared_in_shared_mode_so_private_helpers_pass(tmp_path, lean_env):
    root = _private_repo(tmp_path)
    outcome = LeanComparatorAdapter().check(request(root, "succ-pos"))
    assert outcome.verdict == "pass", explain(outcome)
    assert outcome.checked["definitions_mode"].startswith("shared")


@pytest.mark.lean
@pytest.mark.skipif(LEAN_SKIP is not None, reason=str(LEAN_SKIP))
def test_changed_closure_is_renamed_and_fails_closed_with_a_hint(tmp_path, lean_env):
    root = _private_repo(tmp_path)
    (root / "Fixture" / "Private.lean").write_text(PRIVATE_DEFS + "\n-- an edit that changes nothing\n")
    outcome = LeanComparatorAdapter().check(request(root, "succ-pos"))
    assert outcome.verdict == "fail", explain(outcome)
    assert outcome.checked["definitions_mode"].startswith("renamed")
    assert any("integrate that module first" in r for r in outcome.reasons), explain(outcome)


# Tools are not chosen by the caller's environment or the worktree ---------------------------------------------------

FAKE_COMPARATOR = ("#!/bin/sh\necho 'Exporting #[Nat] from VLSolution'\necho 'Lean default kernel accepts the solution'\n"
                   "echo 'nanoda kernel accepts the solution'\necho 'Your solution is okay!'\n")


def fake_comparator(folder: Path) -> Path:
    """A 'comparator' that accepts every solution."""
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "comparator"
    path.write_text(FAKE_COMPARATOR)
    path.chmod(0o755)
    return path


@pytest.mark.lean
def test_a_comparator_named_by_the_callers_environment_cannot_pass_a_protected_check(tmp_path, lean_env, monkeypatch):
    fake = fake_comparator(tmp_path / "fake")
    monkeypatch.setenv("COMPARATOR_BIN", str(fake))
    outcome = run_case(tmp_path, "d4-sorry")
    assert outcome.verdict == "fail", explain(outcome)
    assert outcome.environment["tools"]["comparator"]["path"] != str(fake.resolve())
    exploratory = run_case(tmp_path, "d4-sorry", assurance="exploratory")    # exploratory checks keep the override
    assert exploratory.verdict == "pass" and exploratory.environment["tools"]["comparator"]["path"] == str(fake.resolve())
    assert exploratory.environment["tools"]["comparator"]["pinned_by"] is None   # and say it was not pinned
    assert any(f"exploratory: comparator {fake.resolve()} is not pinned" in n and "a protected check would refuse it"
               in n for n in exploratory.checked["notes"]), exploratory.checked.get("notes")


@pytest.mark.lean
def test_an_unpinned_tool_in_the_machine_configuration_is_refused_with_exit_3(tmp_path, lean_env, pinned_tools,
                                                                               monkeypatch, capsys):
    """The real Comparator, but named in machine.toml without a REVISIONS pin next to it: refused before it runs."""
    unpinned = tmp_path / "unpinned" / "comparator"
    unpinned.parent.mkdir()
    unpinned.symlink_to(Path(pinned_tools["comparator"]).resolve())
    write_machine({**pinned_tools, "comparator": str(unpinned)}, elan_home=lean_elan_home())
    root = make_repo(tmp_path, load_case("control-original"))
    monkeypatch.chdir(root)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    from verifylab import cli
    assert cli.main(["check", "double-even"]) == 3
    out = capsys.readouterr().out
    assert f"{unpinned} is not pinned" in out and f"vl init --tools --comparator {unpinned}" in out, out
    assert "Building VLChallenge" not in out


@pytest.mark.lean
def test_a_protected_check_without_the_second_kernel_the_rules_ask_for_is_unsupported(tmp_path, lean_env,
                                                                                      pinned_tools):
    """[lean] external_kernels is on (the fixture's default) and the machine has no nanoda: the check stops before
    Comparator runs, `unsupported`, instead of a pass that only one kernel replayed."""
    write_machine({k: v for k, v in pinned_tools.items() if k != "nanoda"}, elan_home=lean_elan_home())
    root = make_repo(tmp_path, load_case("control-original"))
    outcome = LeanComparatorAdapter().check(request(root, "double-even"))
    assert outcome.verdict == "unsupported", explain(outcome)
    assert "external_kernels is on, but nanoda" in outcome.reasons[0] and "kernels" not in outcome.checked
    assert "Comparator accepted" not in outcome.log


@pytest.mark.lean
def test_a_relative_tool_path_in_the_trusted_config_is_refused(tmp_path, lean_env, pinned_tools):
    """A vendored `tools/comparator` named relatively would run whatever the candidate's worktree holds there."""
    root = make_repo(tmp_path)
    git(root, "checkout", "-q", "trusted")
    fake_comparator(root / "tools")
    cfg = root / "research" / "vl.toml"
    cfg.write_text(cfg.read_text() + '\n[tools]\ncomparator = "tools/comparator"\n')
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "vendored comparator")
    git(root, "checkout", "-q", "-B", "candidate")
    case = load_case("d4-sorry")
    apply_case(root, case)
    git(root, "commit", "-q", "-am", "sorry")
    write_machine({k: v for k, v in pinned_tools.items() if k != "comparator"}, elan_home=lean_elan_home())
    outcome = LeanComparatorAdapter().check(request(root, case.item))
    assert outcome.verdict == "error", explain(outcome)
    assert "relative path 'tools/comparator'" in outcome.reasons[0]


@pytest.mark.lean
def test_a_tool_that_differs_from_its_pin_is_refused_and_elan_home_is_the_machines(tmp_path, lean_env, monkeypatch,
                                                                                   pinned_tools):
    tools = tmp_path / "pinned"
    fake = fake_comparator(tools)
    (tools / "REVISIONS").write_text(f"{'0' * 64}  comparator\n")
    write_machine({**pinned_tools, "comparator": str(fake)}, elan_home=lean_elan_home())
    monkeypatch.setenv("ELAN_HOME", str(tmp_path / "no-elan"))        # ignored by a protected check
    monkeypatch.setenv("COMPARATOR_BIN", lean_tools()["comparator"])   # ignored too
    root = make_repo(tmp_path, load_case("control-original"))
    outcome = LeanComparatorAdapter().check(request(root, "double-even"))
    assert outcome.verdict == "error", explain(outcome)
    assert "not the " + "0" * 64 + " pinned in" in outcome.reasons[0], outcome.reasons
    assert cli_exit_code(root) == 3


def cli_exit_code(root: Path) -> int:
    from verifylab import cli
    cwd = os.getcwd()
    os.chdir(root)
    try:
        return cli.main(["check", "double-even"])
    finally:
        os.chdir(cwd)


def test_the_receipt_records_what_the_shared_build_cache_held(tmp_path):
    """Each package's checked-out revision next to the manifest's, and Lake's trace of each dependency module the
    trusted side imports: what a replay of the receipt compares against."""
    from verifylab.adapters.lean_comparator import dependency_identity
    from conftest import git
    pkg = tmp_path / "packages" / "dep"
    (pkg / ".lake" / "build" / "lib" / "lean" / "Dep").mkdir(parents=True)
    git(pkg, "init", "-q")
    (pkg / "Dep.lean").write_text("def x := 1\n")
    git(pkg, "add", "Dep.lean")
    git(pkg, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "dep")
    head = git(pkg, "rev-parse", "HEAD").strip()
    trace = pkg / ".lake" / "build" / "lib" / "lean" / "Dep" / "Basic.trace"
    trace.write_text('{"schemaVersion":"2025-09-10","depHash":"6610019e4aa88425","outputs":{}}')
    identity = dependency_identity(tmp_path / "packages", [{"name": "dep", "rev": head}], ["Dep.Basic", "Dep.Gone"])
    assert identity["revisions"] == {"dep": {"head": head, "manifest": head, "matches": True}}
    assert identity["traces"] == {"Dep.Basic": {"package": "dep", "depHash": "6610019e4aa88425",
                                                "sha256": sha256_hex(trace.read_bytes())}}
    moved = dependency_identity(tmp_path / "packages", [{"name": "dep", "rev": "0" * 40}], [])
    assert moved["revisions"]["dep"]["matches"] is False
