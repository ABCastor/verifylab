"""Reused solution builds (adapters/lean_build_cache.py, docs/adr/0008-reused-solution-builds.md): what a key
binds, that an entry holds only products of the solution side and is refused when it is not what was stored, that a
check mounts it read-only, and, with Lean, that a poisoned entry still meets the kernels."""

from __future__ import annotations

import hashlib
import os
import shutil
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from lean_helpers import LeanComparatorAdapter, explain, git, lean_unavailable, load_case, make_repo, request, run_case
from verifylab import jail, machine
from verifylab.adapters import lean_build_cache, lean_comparator
from verifylab.adapters.lean_build_cache import SUMS, TREE, save_tree, tree, tree_key
from verifylab.adapters.lean_comparator import comparator_jail

from conftest import lean_elan_home, write_machine

ENVIRONMENT = {"toolchain": "leanprover/lean4:v4.34.0-rc2", "lean": "Lean (version 4.34.0-rc2, commit 6a10ac8)",
               "lakefile": "a" * 64, "manifest": "b" * 64, "packages": None,
               "tools": {"comparator": "c" * 64, "lean4export": "d" * 64, "landrun": "e" * 64}}
INPUTS = {"Fixture/Proofs.lean": "1" * 64, "Fixture/Defs.lean": "2" * 64, "VLSolution.lean": "3" * 64}
SOLUTION_SIDE = ["Fixture.Proofs", "VLSolution"]


# Keys -------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("change", [
    {"environment": {**ENVIRONMENT, "toolchain": "leanprover/lean4:v4.34.1"}},
    {"environment": {**ENVIRONMENT, "lakefile": "f" * 64}},                    # the lake options live in it
    {"environment": {**ENVIRONMENT, "manifest": "f" * 64}},
    {"environment": {**ENVIRONMENT, "tools": {**ENVIRONMENT["tools"], "comparator": "f" * 64}}},
    {"inputs": {**INPUTS, "Fixture/Proofs.lean": "f" * 64}},                    # another candidate file
    {"inputs": {**INPUTS, "Fixture/Extra.lean": "f" * 64}},                     # one more candidate file
    {"inputs": {**INPUTS, "VLSolution.lean": "f" * 64}},                        # another generated solution
    {"modules": ["VLSolution"]},
])
def test_every_input_of_the_solution_build_is_in_its_key(change):
    base = {"environment": ENVIRONMENT, "inputs": INPUTS, "modules": SOLUTION_SIDE}
    key = tree_key(**base)
    assert tree_key(**{k: (dict(v) if isinstance(v, dict) else list(v)) for k, v in base.items()}) == key  # control
    assert tree_key(**{**base, **change}) != key


# Entries ----------------------------------------------------------------------------------------------------------

def _built(build: Path, module: str, content: bytes = b"olean") -> None:
    stem = Path(*module.split("."))
    for sub, names in (("lib/lean", (".olean", ".ilean", ".trace", ".olean.hash")), ("ir", (".c",))):
        folder = build / sub / stem.parent
        folder.mkdir(parents=True, exist_ok=True)
        for suffix in names:
            (folder / f"{stem.name}{suffix}").write_bytes(content + suffix.encode())


def _project(tmp_path: Path) -> Path:
    build = tmp_path / "project" / ".lake" / "build"
    for module in ("VLChallenge", "Fixture.Defs", *SOLUTION_SIDE):          # the challenge side and the solution side
        _built(build, module)
    return build


def test_an_entry_holds_the_solution_side_only_and_is_written_once(tmp_path):
    build, store = _project(tmp_path), tmp_path / "store"
    (build / "lib/lean/Fixture/Proofs.extra").symlink_to("/etc/passwd")            # a link the build left: not taken
    key = tree_key(ENVIRONMENT, INPUTS, SOLUTION_SIDE)
    assert save_tree(store, build, key, SOLUTION_SIDE)
    root = tree(store, key, SOLUTION_SIDE)
    assert root == store / key / TREE
    stored = sorted(p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file())
    assert stored == ["ir/Fixture/Proofs.c", "ir/VLSolution.c", "lib/lean/Fixture/Proofs.ilean",
                      "lib/lean/Fixture/Proofs.olean", "lib/lean/Fixture/Proofs.olean.hash",
                      "lib/lean/Fixture/Proofs.trace", "lib/lean/VLSolution.ilean", "lib/lean/VLSolution.olean",
                      "lib/lean/VLSolution.olean.hash", "lib/lean/VLSolution.trace"]
    (build / "lib/lean/VLSolution.olean").write_bytes(b"a later build")
    assert not save_tree(store, build, key, SOLUTION_SIDE)                          # never rewritten
    assert (root / "lib/lean/VLSolution.olean").read_bytes() == b"olean.olean"
    assert [p.name for p in store.iterdir()] == [key]


def test_a_build_that_stopped_part_way_is_not_stored(tmp_path):
    build, store = _project(tmp_path), tmp_path / "store"
    (build / "lib/lean/VLSolution.trace").unlink()                                 # the solution never finished
    assert not save_tree(store, build, "k" * 64, SOLUTION_SIDE)
    assert not store.exists() or not any(store.iterdir())


def _outside(tmp_path: Path, module: str) -> Path:
    """A folder of this machine the jail never sees, holding what looks like a module's products and a secret."""
    outside = tmp_path / "outside"
    _built(outside, module)
    (outside / "lib/lean" / Path(*module.split("."))).with_suffix(".secret").write_text("not the jail's")
    return outside


@pytest.mark.parametrize("planted", [False, True])
def test_a_product_reached_through_a_link_is_never_stored(tmp_path, planted):
    """Build-time code may turn a folder of .lake/build into a link to a folder of this machine; `vl` copies the
    entry outside the jail and a later jail of that key mounts it, so a link would carry host files into it."""
    build, store = _project(tmp_path), tmp_path / "store"
    if planted:
        outside = _outside(tmp_path, "Fixture.Proofs")
        shutil.rmtree(build / "lib/lean/Fixture")
        (build / "lib/lean/Fixture").symlink_to(outside / "lib/lean/Fixture")
    assert save_tree(store, build, "k" * 64, SOLUTION_SIDE) is not planted
    assert planted or tree(store, "k" * 64, SOLUTION_SIDE) is not None


@pytest.mark.parametrize("planted", [False, True])
def test_a_build_folder_turned_into_a_link_is_never_stored(tmp_path, planted):
    project = tmp_path / "project"
    build = _project(tmp_path)
    if planted:
        moved = tmp_path / "elsewhere"
        build.rename(moved)
        build.symlink_to(moved)
    check = _check()
    store = tmp_path / "store"
    check.environment["solution_build"] = {"reused": False, "stored": False}
    check.store_solution(project, (store, "k" * 64, SOLUTION_SIDE))
    assert check.environment["solution_build"]["stored"] is not planted
    assert any("is a link" in note for note in check.notes) is planted


def _resum(entry: Path) -> None:
    """What a process of the same user can do: rewrite the sums to match whatever the entry now holds."""
    root = entry / TREE
    (entry / SUMS).write_text("".join(f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.relative_to(root).as_posix()}\n"
                                      for p in sorted(root.rglob("*")) if p.is_file()))


DAMAGE = {
    "intact": lambda e: None,                                                      # the clean control
    "olean changed": lambda e: (e / TREE / "lib/lean/VLSolution.olean").write_bytes(b""),
    "olean missing": lambda e: (e / TREE / "lib/lean/VLSolution.olean").unlink(),
    "file not listed": lambda e: (e / TREE / "lib/lean/Fixture/Other.olean").write_bytes(b"x"),
    "sums missing": lambda e: (e / SUMS).unlink(),
    "sums empty": lambda e: (e / SUMS).write_text(""),
    "a link": lambda e: ((e / TREE / "lib/lean/VLSolution.olean").unlink(),
                         (e / TREE / "lib/lean/VLSolution.olean").symlink_to("/etc/passwd")),
    # A challenge module planted in the entry, sums rewritten: it would stand in for the check's own challenge build.
    "challenge module, resummed": lambda e: ((e / TREE / "lib/lean/VLChallenge.olean").write_bytes(b"x"), _resum(e)),
    "trusted module, resummed": lambda e: ((e / TREE / "lib/lean/Fixture/Defs.olean").write_bytes(b"x"), _resum(e)),
    # A changed product of the solution side, sums rewritten: not detectable here; the kernels replay it (Lean test).
    "solution olean, resummed": lambda e: ((e / TREE / "lib/lean/VLSolution.olean").write_bytes(b"x"), _resum(e)),
}


@pytest.mark.parametrize("damage", DAMAGE)
def test_an_entry_that_is_not_what_was_stored_is_never_mounted(tmp_path, damage):
    build, store = _project(tmp_path), tmp_path / "store"
    key = tree_key(ENVIRONMENT, INPUTS, SOLUTION_SIDE)
    save_tree(store, build, key, SOLUTION_SIDE)
    DAMAGE[damage](store / key)
    used = tree(store, key, SOLUTION_SIDE) is not None
    assert used == (damage in ("intact", "solution olean, resummed"))


def _check(protected: bool = True, guards=(), files=None) -> lean_comparator._Check:
    check = lean_comparator._Check.__new__(lean_comparator._Check)
    check.req, check.protected = SimpleNamespace(guards=frozenset(guards)), protected
    check.files = dict(files or {k: v for k, v in INPUTS.items() if k != "VLSolution.lean"})
    check.environment, check.notes = {"lean_version": ENVIRONMENT["lean"], "build_tools": {"lean": "a" * 64, "lake": "b" * 64}, "packages": None, "tools": {
        name: {"path": f"/t/{name}", "sha256": sha} for name, sha in ENVIRONMENT["tools"].items()}}, []
    return check


OUTPUTS = {"lakefile.toml": b"", "lake-manifest.json": b"{}", "VLSolution.lean": b"import Fixture.Proofs\n"}


@pytest.mark.parametrize("protected, guards, flag", [
    (True, (), True),                                          # the clean control: the one check that uses it
    (True, (), False),                                         # off by default
    (False, (), True),                                         # exploratory
    (True, ("trusted_definitions",), True),                    # a guard off
])
def test_only_a_protected_check_with_every_guard_on_and_the_flag_uses_the_store(protected, guards, flag):
    check = _check(protected, guards)
    reuse = check.solution_store(SimpleNamespace(reuse_solution_builds=flag), ENVIRONMENT["toolchain"], OUTPUTS,
                                 SOLUTION_SIDE)
    assert (reuse is not None) == (protected and not guards and flag)
    assert ("solution_build" in check.environment) == (reuse is not None)


def test_nanoda_is_not_in_the_key_and_the_tools_of_the_build_are():
    """nanoda only replays the export: turning external kernels on reuses the build; a changed Comparator does not."""
    def key(**tools):
        check = _check()
        check.environment["tools"].update({n: {"path": f"/t/{n}", "sha256": sha} for n, sha in tools.items()})
        return check.solution_store(SimpleNamespace(reuse_solution_builds=True), ENVIRONMENT["toolchain"], OUTPUTS,
                                    SOLUTION_SIDE)[1]
    assert key(nanoda="f" * 64) == key()
    assert key(comparator="f" * 64) != key()


def test_a_different_candidate_never_reaches_another_key(tmp_path):
    first = _check().solution_store(SimpleNamespace(reuse_solution_builds=True), ENVIRONMENT["toolchain"], OUTPUTS,
                                    SOLUTION_SIDE)
    again = _check().solution_store(SimpleNamespace(reuse_solution_builds=True), ENVIRONMENT["toolchain"], OUTPUTS,
                                    SOLUTION_SIDE)
    other = _check(files={"Fixture/Proofs.lean": "9" * 64, "Fixture/Defs.lean": "2" * 64}).solution_store(
        SimpleNamespace(reuse_solution_builds=True), ENVIRONMENT["toolchain"], OUTPUTS, SOLUTION_SIDE)
    assert first[1] == again[1] != other[1]
    save_tree(first[0], _project(tmp_path), first[1], SOLUTION_SIDE)
    assert tree(*first) is not None and tree(*other) is None


def test_reuse_is_off_unless_the_machine_turns_it_on():
    assert machine.load().reuse_solution_builds is False
    write_machine(check={"reuse_solution_builds": True})
    assert machine.load().reuse_solution_builds is True
    write_machine(check={"reuse_solution_builds": "yes"})
    with pytest.raises(machine.MachineError, match="reuse_solution_builds"):
        machine.load()


def test_the_store_lives_outside_every_folder_a_check_jail_mounts(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    store = machine.solution_store_root()
    assert store != machine.build_store_root() and not store.is_relative_to(machine.build_store_root())
    scratch = tmp_path / "cache" / "verifylab" / "scratch" / "check-1"
    box = comparator_jail(scratch, tmp_path / "toolchain", {"landrun": tmp_path / "landrun",
                                                            "lean4export": tmp_path / "lean4export"}, tmp_path / "pkgs")
    mounted = [Path(p) for p in (box.workdir, *box.read_write, *box.read_only)]
    assert not any(store.is_relative_to(p) or p.is_relative_to(store) for p in mounted), (store, mounted)


@pytest.mark.jail
@pytest.mark.skipif(not jail.available(), reason="bwrap missing")
def test_a_mounted_entry_is_read_only_and_the_scratch_folder_can_go(tmp_path):
    """The planted case of the overlay: an entry bound read-write (the guard disabled) takes the candidate's write."""
    store, scratch = tmp_path / "store", tmp_path / "scratch"
    build = _project(scratch)
    key = tree_key(ENVIRONMENT, INPUTS, SOLUTION_SIDE)
    save_tree(store, build, key, SOLUTION_SIDE)
    shutil.rmtree(build / "lib/lean/Fixture")                                     # as in a fresh check project
    (build / "ir" / "VLSolution.c").unlink()
    (build / "lib/lean" / "VLSolution.olean").unlink()
    check = _check()
    check.environment["solution_build"] = {"reused": False}
    box = check.solution_overlay(jail.Jail(workdir=scratch), scratch / "project", scratch, (store, key, SOLUTION_SIDE))
    assert check.environment["solution_build"]["reused"] and box.overlays
    olean = build / "lib/lean/VLSolution.olean"
    write = ["/bin/sh", "-c", f"cat '{olean}' && echo planted > '{olean}' && echo new > '{build}/lib/lean/New.olean'"]
    seen = jail.run(box, write, timeout=60)
    assert seen.returncode == 0 and seen.stdout == b"olean.olean", seen
    assert tree(store, key, SOLUTION_SIDE) is not None                             # the entry is as stored
    assert olean.read_bytes() == b"planted\n"                                      # the write landed in the project
    lean_comparator._owner_may_remove(scratch / lean_comparator.OVERLAY_WORK)
    shutil.rmtree(scratch)
    assert not scratch.exists()
    planted = jail.Jail(workdir=tmp_path / "work", read_write=(store,))
    (tmp_path / "work").mkdir()
    jail.run(planted, ["/bin/sh", "-c", f"echo planted > '{store / key / TREE}/lib/lean/VLSolution.olean'"],
             timeout=60)
    assert tree(store, key, SOLUTION_SIDE) is None


# Lean: reuse in real checks ----------------------------------------------------------------------------------------

LEAN_SKIP = lean_unavailable()
lean = pytest.mark.skipif(LEAN_SKIP is not None, reason=str(LEAN_SKIP))

FALSE_TARGET = """import Fixture.Defs

namespace VL.FalseClaim

theorem main : Fixture.double 1 = 3 := sorry

end VL.FalseClaim
"""

FALSE_ITEM = """+++
id = "false-claim"
kind = "result"
title = "A false claim"
author = "human:fixture"
created = "2026-10-11"
statement = "double 1 = 3."
claim = "formal"

[lean]
target = "research/targets/false-claim.lean"
theorems = ["VL.FalseClaim.main"]
solution = "Fixture.Solutions.Claim"
+++
False on purpose: no solution may pass.
"""

# The candidate's honest attempt: it compiles, and Comparator refuses its `sorry`.
HONEST = """import Fixture.Defs

namespace VL.FalseClaim

theorem main : Fixture.double 1 = 3 := sorry

end VL.FalseClaim
"""

# A module whose olean declares the false theorem with a proof the kernel never checked (E3 in docs/CHEATS.md).
UNCHECKED = """import Lean
import Fixture.Defs

theorem VL.FalseClaim.statement : Fixture.double 1 = 3 := sorry

open Lean Elab Command in
set_option debug.skipKernelTC true in
run_cmd liftCoreM do
  let type := (← getConstInfo ``VL.FalseClaim.statement).type
  addDecl <| .thmDecl { name := `VL.FalseClaim.main, levelParams := [], type,
                        value := mkApp2 (.const ``Eq.refl [1]) (.const ``Nat []) (.lit (.natVal 3)) }
"""


def _false_claim_repo(tmp_path: Path, solution: str) -> Path:
    root = make_repo(tmp_path)
    git(root, "checkout", "-q", "trusted")
    (root / "research" / "targets" / "false-claim.lean").write_text(FALSE_TARGET)
    (root / "research" / "items" / "false-claim.md").write_text(FALSE_ITEM)
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "a false claim")
    git(root, "checkout", "-q", "candidate")
    git(root, "merge", "-q", "trusted", "-m", "integrate")
    (root / "Fixture" / "Solutions").mkdir(parents=True, exist_ok=True)
    (root / "Fixture" / "Solutions" / "Claim.lean").write_text(solution)
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "a solution")
    return root


def _reuse_on(pinned_tools) -> None:
    write_machine(pinned_tools, elan_home=lean_elan_home(), check={"reuse_solution_builds": True})


@pytest.mark.lean
@lean
def test_a_poisoned_entry_still_meets_the_kernels_and_a_damaged_one_is_not_mounted(tmp_path, pinned_tools):
    _reuse_on(pinned_tools)
    honest_root = _false_claim_repo(tmp_path / "honest", HONEST)
    honest = LeanComparatorAdapter().check(request(honest_root, "false-claim"))
    assert honest.verdict == "fail" and "sorry" in " ".join(honest.reasons), explain(honest)
    built = honest.environment["solution_build"]
    assert built["stored"] and not built["reused"], built
    assert built["modules"] == ["Fixture.Solutions.Claim", "VLSolution"]

    # Another candidate: never the honest key, even with that entry in the store.
    other = LeanComparatorAdapter().check(request(_false_claim_repo(tmp_path / "other", UNCHECKED), "false-claim"))
    # Nanoda can reject normally or panic on this export. A panic is an inconclusive tool error.
    if other.verdict == "error":
        assert other.reasons == ["nanoda exited with 101 (kernel panic)"], explain(other)
    else:
        assert other.verdict == "fail" and "kernel" in " ".join(other.reasons), explain(other)
    assert other.environment["solution_build"]["key"] != built["key"]
    assert not other.environment["solution_build"]["reused"] and other.environment["solution_build"]["stored"]

    # A process of the same user puts the unchecked olean under the honest key and rewrites the sums.
    store = machine.solution_store_root()
    poisoned = store / built["key"]
    shutil.copy2(store / other.environment["solution_build"]["key"] / TREE / "lib/lean/Fixture/Solutions/Claim.olean",
                 poisoned / TREE / "lib/lean/Fixture/Solutions/Claim.olean")
    _resum(poisoned)
    again = LeanComparatorAdapter().check(request(honest_root, "false-claim"))
    assert again.environment["solution_build"] == {**built, "reused": True, "stored": False}
    assert (again.verdict, again.reasons) == (other.verdict, other.reasons), explain(again)
    assert "kernel" in " ".join(again.reasons) and "sorry" not in " ".join(again.reasons), explain(again)

    # Damaged without the sums rewritten: not mounted, the check builds its own solution again.
    (poisoned / TREE / "lib/lean/Fixture/Solutions/Claim.olean").write_bytes(b"")
    damaged = LeanComparatorAdapter().check(request(honest_root, "false-claim"))
    assert not damaged.environment["solution_build"]["reused"], damaged.environment
    assert damaged.verdict == "fail" and "sorry" in " ".join(damaged.reasons), explain(damaged)
    assert any("damaged entry" in r for r in damaged.reasons), damaged.reasons


def _summary(outcome) -> tuple:
    probes = {n: (r.get("trivial_by"), r.get("vacuous_by")) for n, r in outcome.checked.get("probes", {}).items()}
    return outcome.verdict, outcome.reasons, probes, outcome.checked.get("definitions_mode")


@pytest.mark.lean
@lean
def test_a_reused_solution_build_gives_the_verdict_of_a_fresh_one(tmp_path, pinned_tools):
    """Also the measurement: VL_MEASURE=1 prints the solution build's seconds with reuse off, cold and warm."""
    runs = {}
    for name in ("control-solution-module", "d4-sorry"):
        off = run_case(tmp_path / "off", name)
        _reuse_on(pinned_tools)
        cold = run_case(tmp_path / "cold", name)
        warm = run_case(tmp_path / "warm", name)
        write_machine(pinned_tools, elan_home=lean_elan_home())
        assert off.verdict == load_case(name).expect, explain(off)
        assert _summary(warm) == _summary(cold) == _summary(off), explain(warm)
        assert "solution_build" not in off.environment
        assert cold.environment["solution_build"]["stored"] and warm.environment["solution_build"]["reused"]
        runs[name] = {k: o.extra["phases"].get("build_solution") for k, o in (("off", off), ("cold", cold),
                                                                               ("warm", warm))}
    if os.environ.get("VL_MEASURE"):
        print("\nbuild_solution seconds:", runs, time.strftime("%H:%M"))
