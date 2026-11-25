"""The trusted build store (adapters/lean_build_cache.py, docs/adr/0005): what a key binds, how entries are written
and read back, and that a candidate cannot write the store. The Lean tests check that a warm store gives the verdicts
a cold one gives."""

from __future__ import annotations

import glob
import hashlib
import os
import threading
import time
from pathlib import Path

import pytest

from lean_helpers import LeanComparatorAdapter, explain, git, lean_unavailable, load_case, make_repo, request, run_case
from verifylab import jail, machine
from verifylab.adapters import lean_build_cache
from verifylab.adapters.lean_build_cache import module_keys, restore, save
from verifylab.adapters.lean_comparator import comparator_jail

from conftest import lean_elan_home, write_machine

LEAN_SKIP = lean_unavailable()

ENVIRONMENT = {"toolchain": "leanprover/lean4:v4.34.0-rc2", "lean": "Lean (version 4.34.0-rc2, commit 6a10ac8)",
               "lakefile": "a" * 64, "manifest": "b" * 64,
               "packages": {"revisions": {"mathlib": {"head": "c" * 40}}, "traces": {"Mathlib.Logic": {"depHash": "1"}}}}
SOURCES = {"VLChallenge": b"import Fixture.Defs\ntheorem t : Fixture.d = 1 := sorry\n",
           "Fixture.Defs": b"import Fixture.Base\ndef Fixture.d := 1\n",
           "Fixture.Base": b"def Fixture.b := 0\n",
           "Fixture.Other": b"def Fixture.o := 2\n"}


# Keys -------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("field, value", [
    ("toolchain", "leanprover/lean4:v4.35.0"), ("lean", "Lean (version 4.34.0-rc2, commit ffffff)"),
    ("lakefile", "d" * 64), ("manifest", "e" * 64),
    ("packages", {"revisions": {"mathlib": {"head": "f" * 40}}, "traces": {"Mathlib.Logic": {"depHash": "1"}}}),
    ("packages", {"revisions": {"mathlib": {"head": "c" * 40}}, "traces": {"Mathlib.Logic": {"depHash": "2"}}}),
])
def test_a_changed_environment_input_changes_every_key(field, value):
    keys = module_keys(ENVIRONMENT, SOURCES)
    assert module_keys(dict(ENVIRONMENT), dict(SOURCES)) == keys                 # clean control: same inputs, a hit
    changed = module_keys({**ENVIRONMENT, field: value}, SOURCES)
    assert all(changed[m] != keys[m] for m in SOURCES)


def test_a_changed_source_changes_its_key_and_every_key_above_it_only():
    keys = module_keys(ENVIRONMENT, SOURCES)
    changed = module_keys(ENVIRONMENT, {**SOURCES, "Fixture.Base": b"def Fixture.b := 1\n"})
    assert [m for m in SOURCES if changed[m] != keys[m]] == ["VLChallenge", "Fixture.Defs", "Fixture.Base"]
    renamed = module_keys(ENVIRONMENT, {**{k: v for k, v in SOURCES.items() if k != "Fixture.Other"},
                                        "VLTrusted.Fixture.Other": SOURCES["Fixture.Other"]})
    assert renamed["VLTrusted.Fixture.Other"] != keys["Fixture.Other"]          # the module name is bound too


def test_an_import_cycle_is_refused():
    with pytest.raises(ValueError):
        module_keys(ENVIRONMENT, {"A.X": b"import A.Y\n", "A.Y": b"import A.X\n"})


# Entries ----------------------------------------------------------------------------------------------------------

def _built(build: Path, module: str, content: bytes = b"olean") -> None:
    stem = Path(*module.split("."))
    for sub, names in (("lib/lean", (".olean", ".ilean", ".trace", ".olean.hash")), ("ir", (".c", ".setup.json"))):
        folder = build / sub / stem.parent
        folder.mkdir(parents=True, exist_ok=True)
        for suffix in names:
            (folder / f"{stem.name}{suffix}").write_bytes(content + suffix.encode())


def test_a_save_lists_each_build_folder_once_and_skips_what_the_store_holds(tmp_path, monkeypatch):
    """Many modules in one folder: the folder is listed once per save, not once per module, and a store that already
    holds every entry lists nothing at all."""
    modules = {f"Fixture.M{n}": f"{n:064x}" for n in range(20)}
    build, store = tmp_path / "build", tmp_path / "store"
    for module in modules:
        _built(build, module)
    listings = []
    real = Path.iterdir
    monkeypatch.setattr(Path, "iterdir", lambda self: listings.append(self) or real(self))
    assert save(store, build, modules) == sorted(modules)
    assert len(listings) == 2                                    # lib/lean/Fixture and ir/Fixture, once each
    listings.clear()
    assert save(store, build, modules) == [] and listings == []  # everything stored already: nothing to look at


def test_a_save_removes_what_a_killed_writer_left_and_only_that(tmp_path):
    """A writer killed mid-save leaves its `.tmp-*` folder: the next save removes it once it is old, and leaves a
    fresh one, which a writer may still be filling."""
    store, build = tmp_path / "store", tmp_path / "build"
    old, fresh = store / ".tmp-0000000000000000", store / ".tmp-1111111111111111"
    for folder in (old, fresh):
        (folder / "lib/lean").mkdir(parents=True)
    os.utime(old, (time.time() - 2 * lean_build_cache.STALE_TMP,) * 2)
    _built(build, "Fixture.Defs")
    assert save(store, build, {"Fixture.Defs": "k" * 64}) == ["Fixture.Defs"]
    assert not old.exists() and fresh.exists()


def test_entries_are_written_whole_and_restored_as_copies(tmp_path):
    keys = module_keys(ENVIRONMENT, SOURCES)
    build, store = tmp_path / "p1" / ".lake" / "build", tmp_path / "store"
    for module in ("VLChallenge", "Fixture.Defs"):
        _built(build, module)
    (build / "lib/lean/Fixture/Base.olean").write_bytes(b"no trace: not a finished build")
    (build / "lib/lean/Fixture/Other.olean").symlink_to(build / "lib/lean/Fixture/Defs.olean")
    (build / "lib/lean/Fixture/Other.trace").write_bytes(b"trace")
    assert save(store, build, keys) == ["Fixture.Defs", "VLChallenge"]
    assert sorted(p.name for p in store.iterdir()) == sorted([keys["Fixture.Defs"], keys["VLChallenge"]])
    assert save(store, build, keys) == []                                           # never rewritten
    fresh = tmp_path / "p2" / ".lake" / "build"
    assert restore(store, fresh, keys) == {m: keys[m] for m in ("Fixture.Defs", "VLChallenge")}
    restored = fresh / "lib/lean/Fixture/Defs.olean"
    assert restored.read_bytes() == b"olean.olean" and (fresh / "ir/Fixture/Defs.setup.json").is_file()
    restored.write_bytes(b"a later write into the check project")                 # a candidate's build may do this
    assert (store / keys["Fixture.Defs"] / "lib/lean/Defs.olean").read_bytes() == b"olean.olean"
    assert not restore(store, tmp_path / "p3", {"Fixture.Defs": "0" * 64})          # another key: a miss


@pytest.mark.parametrize("damage", ["empty olean", "olean missing", "sums empty", "sums missing", "intact"])
def test_a_damaged_entry_is_never_restored(tmp_path, damage):
    keys = module_keys(ENVIRONMENT, SOURCES)
    build, store = tmp_path / "p1" / ".lake" / "build", tmp_path / "store"
    for module in ("VLChallenge", "Fixture.Defs"):
        _built(build, module)
    save(store, build, keys)
    entry = store / keys["Fixture.Defs"]
    {"empty olean": lambda: (entry / "lib/lean/Defs.olean").write_bytes(b""),
     "olean missing": lambda: (entry / "lib/lean/Defs.olean").unlink(),
     "sums empty": lambda: (entry / lean_build_cache.SUMS).write_text(""),
     "sums missing": lambda: (entry / lean_build_cache.SUMS).unlink(),
     "intact": lambda: None}[damage]()                                           # the clean control
    fresh = tmp_path / "p2" / ".lake" / "build"
    restored = restore(store, fresh, keys)
    assert "VLChallenge" in restored and ("Fixture.Defs" in restored) == (damage == "intact")
    assert (fresh / "lib/lean/Fixture/Defs.olean").exists() == (damage == "intact")


def test_a_concurrent_writer_of_the_same_entry_loses_the_rename(tmp_path, monkeypatch):
    keys = {"Fixture.Defs": module_keys(ENVIRONMENT, SOURCES)["Fixture.Defs"]}
    build, store = tmp_path / ".lake" / "build", tmp_path / "store"
    _built(build, "Fixture.Defs", b"first")
    rename = Path.rename

    def raced(self, target):                       # another check stores the same entry between our check and rename
        monkeypatch.setattr(Path, "rename", rename)
        other = tmp_path / "other"
        _built(other, "Fixture.Defs", b"second")
        assert save(store, other, keys) == ["Fixture.Defs"]
        return rename(self, target)

    monkeypatch.setattr(Path, "rename", raced)
    assert save(store, build, keys) == []
    assert [p.name for p in store.iterdir()] == [keys["Fixture.Defs"]]             # no half-written copy left behind
    assert (store / keys["Fixture.Defs"] / "lib/lean/Defs.olean").read_bytes() == b"second.olean"


def test_the_store_lives_outside_every_folder_a_check_jail_mounts(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    store = machine.build_store_root()
    scratch = tmp_path / "cache" / "verifylab" / "scratch" / "check-1"
    box = comparator_jail(scratch, tmp_path / "toolchain", {"landrun": tmp_path / "landrun",
                                                            "lean4export": tmp_path / "lean4export"}, tmp_path / "pkgs")
    mounted = [Path(p) for p in (box.workdir, *box.read_write, *box.read_only)]
    assert store.is_relative_to(Path(os.environ["XDG_DATA_HOME"]))
    assert not any(store.is_relative_to(p) or p.is_relative_to(store) for p in mounted), (store, mounted)


@pytest.mark.jail
@pytest.mark.skipif(not jail.available(), reason="bwrap missing")
def test_a_write_from_the_jail_never_reaches_the_store_unless_it_is_mounted(tmp_path):
    """The planted case of the mount guard: a jail that binds the store (the guard disabled) lets the write through."""
    store = tmp_path / "store"
    store.mkdir()
    work = tmp_path / "work"
    work.mkdir()
    write = ["/bin/sh", "-c", f"mkdir -p '{store}' && echo planted > '{store}/x'"]
    jail.run(jail.Jail(workdir=work), write, timeout=30)
    assert not (store / "x").exists()
    jail.run(jail.Jail(workdir=work, read_write=(store,)), write, timeout=30)
    assert (store / "x").read_text() == "planted\n"


def test_cancel_stops_a_run_early_like_its_deadline(tmp_path, monkeypatch):
    monkeypatch.setattr(jail, "available", lambda: True)
    monkeypatch.setattr(jail.Jail, "argv", lambda self, command: list(command))
    monkeypatch.setattr(jail, "memory_capped", lambda argv, *args, **kwargs: list(argv))
    cancel = threading.Event()
    threading.Timer(0.5, cancel.set).start()
    start = time.monotonic()
    done = jail.stream(jail.Jail(workdir=tmp_path), ["/bin/sh", "-c", "exec sleep 30"], timeout=60, cancel=cancel)
    assert done.returncode is None and time.monotonic() - start < 10
    done = jail.stream(jail.Jail(workdir=tmp_path), ["/bin/sh", "-c", "exit 4"], timeout=60, cancel=threading.Event())
    assert done.returncode == 4                                                    # control: nothing cancels it


def test_the_machine_can_turn_the_store_off(tmp_path):
    assert machine.load().build_cache is True
    write_machine(check={"build_cache": False})
    assert machine.load().build_cache is False
    write_machine(check={"build_cache": "no"})
    with pytest.raises(machine.MachineError, match="build_cache"):
        machine.load()



@pytest.mark.parametrize("assurance, guards, flag", [
    ("protected", (), True),                                   # the clean control: the one check that uses it
    ("exploratory", (), True),                                 # its target and definitions come from the worktree
    ("protected", ("trusted_target",), True),                  # a guard off: the candidate may shape the challenge
    ("protected", ("probe_snapshot",), True),
    ("protected", (), False),                                  # [check] build_cache = false
])
def test_only_a_protected_check_with_every_guard_on_uses_the_store(tmp_path, assurance, guards, flag):
    from types import SimpleNamespace
    from verifylab.adapters import lean_comparator
    check = lean_comparator._Check.__new__(lean_comparator._Check)
    check.req, check.protected = SimpleNamespace(guards=frozenset(guards)), assurance == "protected"
    check.environment, check.notes = {"lean_version": ENVIRONMENT["lean"], "build_tools": {"lean": "a" * 64, "lake": "b" * 64}, "packages": None}, []
    outputs = {"lakefile.toml": b"", "lake-manifest.json": b"{}"}
    cache = check.build_cache(SimpleNamespace(build_cache=flag), ENVIRONMENT["toolchain"], outputs, SOURCES)
    uses = (assurance, guards, flag) == ("protected", (), True)
    assert (cache is not None) == uses
    if uses:
        bound = {**ENVIRONMENT, "compiler": {"lean": "a" * 64, "lake": "b" * 64}, "lakefile": hashlib.sha256(b"").hexdigest(),
                 "manifest": hashlib.sha256(b"{}").hexdigest(), "packages": None}
        assert cache == (machine.build_store_root(), module_keys(bound, SOURCES))

# Lean: the store in real checks -------------------------------------------------------------------------------------

lean = pytest.mark.skipif(LEAN_SKIP is not None, reason=str(LEAN_SKIP))


def _snapshot(store: Path) -> dict[str, str]:
    return {str(p.relative_to(store)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(store.rglob("*")) if p.is_file()}


def _summary(outcome) -> tuple:
    probes = {name: (row.get("trivial_by"), row.get("vacuous_by")) for name, row in outcome.checked.get("probes", {}).items()}
    return outcome.verdict, outcome.reasons, probes, outcome.checked.get("definitions_mode")


@pytest.mark.lean
@lean
def test_a_warm_store_gives_the_verdicts_of_a_cold_one_and_of_none(tmp_path, pinned_tools):
    names = ["control-original", "d7-altered-definition", "e2-overwrite-challenge-build",
             "s6-contradictory-hypotheses"]
    store = machine.build_store_root()
    cold = {n: run_case(tmp_path / "cold", n) for n in names}
    assert all(o.environment["build_cache"]["store"] == str(store) for o in cold.values())
    warm = {n: run_case(tmp_path / "warm", n) for n in names}
    write_machine(pinned_tools, elan_home=lean_elan_home(), check={"build_cache": False})
    off = {n: run_case(tmp_path / "off", n) for n in names}
    for name in names:
        expect = load_case(name).expect
        assert cold[name].verdict == expect, explain(cold[name])
        assert _summary(warm[name]) == _summary(cold[name]) == _summary(off[name]), explain(warm[name])
        assert "VLChallenge" in warm[name].environment["build_cache"]["restored"], warm[name].environment
        assert "build_cache" not in off[name].environment


@pytest.mark.lean
@lean
def test_a_changed_trusted_definition_misses_the_store(tmp_path):
    root = make_repo(tmp_path / "a", load_case("control-original"))
    first = LeanComparatorAdapter().check(request(root, "double-even"))
    assert first.verdict == "pass" and set(first.environment["build_cache"]["stored"]) == {"Fixture.Defs", "VLChallenge"}
    git(root, "checkout", "-q", "trusted")                  # the integrator changes the definition the target uses
    defs = root / "Fixture" / "Defs.lean"
    defs.write_text(defs.read_text().replace("n + n", "n + n + 1"))
    git(root, "commit", "-q", "-am", "double is odd now")
    git(root, "checkout", "-q", "candidate")
    git(root, "merge", "-q", "trusted", "-m", "integrate")
    second = LeanComparatorAdapter().check(request(root, "double-even"))
    assert second.verdict == "fail", explain(second)        # the candidate's proof is about the old definition
    cache = second.environment["build_cache"]
    assert cache["restored"] == {} and set(cache["stored"]) == {"Fixture.Defs", "VLChallenge"}, cache


WRITER = """import Lean
import Fixture.Proofs

/- Build-time code that tries to rewrite every file of the trusted build store, and leaves a marker in the one
folder its build may write, which shows that it ran. -/
run_cmd do
  IO.FS.writeFile ".lake/vl-store-canary" "ran"
  try
    for path in (← System.FilePath.walkDir "{store}") do
      unless (← path.isDir) do IO.FS.writeFile path ""
  catch _ => pure ()
  try IO.FS.writeFile "{store}/planted" "" catch _ => pure ()

namespace VL.DoubleEven

theorem main (n : Nat) : Fixture.double n % 2 = 0 := Fixture.double_mod_two n

end VL.DoubleEven
"""


@pytest.mark.lean
@lean
def test_candidate_code_cannot_write_the_store(tmp_path):
    store = machine.build_store_root()
    root = make_repo(tmp_path / "a", load_case("control-original"))
    assert LeanComparatorAdapter().check(request(root, "double-even")).verdict == "pass"
    before = _snapshot(store)
    assert before
    (root / "Fixture" / "Solutions").mkdir(parents=True)
    (root / "Fixture" / "Solutions" / "Writer.lean").write_text(WRITER.replace("{store}", str(store)))
    item = root / "research" / "items" / "double-even.md"
    text = item.read_text()
    item.write_text(text[:text.index("proofs =")] + 'solution = "Fixture.Solutions.Writer"\n'
                    + text[text.index("+++\nThe genuine"):])
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "a solution that writes the store")
    outcome = LeanComparatorAdapter().check(request(root, "double-even"))
    assert outcome.verdict == "pass", explain(outcome)
    canaries = glob.glob(str(root.parent / "scratch-*" / "project" / ".lake" / "vl-store-canary"))
    assert canaries, "control: the candidate's build-time code did not run"
    assert _snapshot(store) == before and not (store / "planted").exists()


@pytest.mark.lean
@lean
def test_a_damaged_entry_is_not_restored(tmp_path):
    """An entry whose files no longer hold what was stored (a crash after the rename, before the data reached the
    disk, leaves them empty) must not reach a check: Lake replays a restored module from its trace without reading
    its olean, so a damaged olean would turn every later check of that key into an error."""
    cold = run_case(tmp_path / "cold", "control-original")
    store = machine.build_store_root()
    for olean in store.rglob("*.olean"):
        olean.write_bytes(b"")
    damaged = run_case(tmp_path / "damaged", "control-original")
    assert _summary(damaged) == _summary(cold), explain(damaged)
    assert "VLChallenge" not in damaged.environment["build_cache"]["restored"], damaged.environment
    assert any("damaged entries" in n for n in damaged.checked["notes"]), damaged.checked
