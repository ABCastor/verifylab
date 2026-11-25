"""Crash and cache probes that execute no Lean; real package tests live separately."""

from __future__ import annotations

import copy
from pathlib import Path

import pytest

from test_lean_adapter import _out
from verifylab.adapters import lean_build_cache
from verifylab.adapters.lean_comparator import _Check, classify


def test_saved_435_panic_transcript_is_not_a_mathematical_rejection():
    folder = Path(__file__).parent / "fixtures/lean435-transcripts"
    kernels = ("lean", "Lean paranoid", "lean4lean", "nanoda", "con-leche", "con-ron")
    good, _, good_err = (folder / "good-std-paranoid.log").read_text().partition("\n--- stderr ---\n")
    control = classify(good, good_err, 0, ("Probe.goal",), kernels, challenge="Challenge", solution="Good")
    assert control.verdict == "pass", control
    bad, _, bad_err = (folder / "good-empty-axioms-paranoid.log").read_text().partition("\n--- stderr ---\n")
    assert "panicked at" in bad_err and "error: nanoda exited with 101" in bad_err
    result = classify(bad, bad_err, 1, ("Probe.goal",), kernels, challenge="Challenge", solution="Good")
    assert result.verdict == "error", result


@pytest.mark.parametrize("framing", ["uncaught exception: ", "error: "])
def test_nanoda_panic_101_is_tool_error_even_after_rejection_line(framing):
    exported = _out("Exporting #[Nat] from VLSolution\n")
    rejected = exported + "Running nanoda kernel on solution\nnanoda kernel rejected the solution\n"
    # A normal mathematical rejection remains a failure.
    control = classify(rejected, "uncaught exception: nanoda exited with 1\n", 1, (), ("lean", "nanoda"))
    assert control.verdict == "fail"
    stderr = "thread 'main' panicked at src/tc.rs:173:26\n" + framing + "nanoda exited with 101\n"
    result = classify(rejected, stderr, 1, (), ("lean", "nanoda"))
    assert result.verdict == "error", result


@pytest.mark.parametrize("field", ["toolchain", "manifest"])
def test_internal_build_stores_do_not_cross_toolchain_or_manifest(field):
    environment = {"toolchain": "leanprover/lean4:v4.34.1", "manifest": "a" * 64}
    sources = {"VLChallenge": b"theorem goal : True := sorry\n"}
    base = lean_build_cache.module_keys(environment, sources)
    changed = {**environment, field: "leanprover/lean4:v4.35.0" if field == "toolchain" else "b" * 64}
    assert lean_build_cache.module_keys(copy.deepcopy(environment), sources) == base
    assert lean_build_cache.module_keys(changed, sources) != base
    assert lean_build_cache.tree_key(environment, {"A.lean": "c" * 64}, ["A"]) != (
        lean_build_cache.tree_key(changed, {"A.lean": "c" * 64}, ["A"]))


@pytest.mark.parametrize("tool", ["lean", "lake"])
def test_build_identity_changes_when_compiler_content_changes_without_version_change(tool):
    # Call the actual adapter's build-identity projection. Tool hashes, though supplied,
    # must not disappear from the key when the compiler prints an unchanged version.
    run = object.__new__(_Check)
    run.environment = {"lean_version": "Lean 4.34.1", "packages": None,
                       "tools": {name: {"sha256": "a" * 64} for name in ("lean", "lake")}}
    outputs = {"lakefile.toml": b'name = "vlcheck"', "lake-manifest.json": b'{"packages":[]}'}
    sources = {"VLChallenge": b"theorem goal : True := sorry\n"}
    before = run.build_environment("leanprover/lean4:v4.34.1", outputs)
    assert run.build_environment("leanprover/lean4:v4.34.1", outputs) == before
    run.environment["tools"][tool]["sha256"] = "b" * 64
    after = run.build_environment("leanprover/lean4:v4.34.1", outputs)
    assert lean_build_cache.module_keys(before, sources) != lean_build_cache.module_keys(after, sources)
