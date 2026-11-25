"""Read only committed bytes. A changed worktree, lock or unsupported signature cannot supply evidence."""
import json
import shutil
from pathlib import Path

import pytest

from conftest import commit_all, git
from verifylab.cli import main


@pytest.fixture
def foreign(tmp_path, monkeypatch):
    root = tmp_path / "Tiny"
    shutil.copytree(Path(__file__).resolve().parents[1] / "fixtures/foreign-lake", root)
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.name", "Test")
    git(root, "config", "user.email", "test@example.org")
    commit_all(root, "initial Tiny project")
    monkeypatch.chdir(tmp_path)
    return root


def output(capsys, *argv):
    code = main([*map(str, argv), "--json"])
    streams = capsys.readouterr()
    assert code == 0, streams.err
    return json.loads(streams.out)


def test_import_and_inspect_pinned_source(foreign, capsys):
    lock = output(capsys, "import", foreign)["project"]
    assert lock["commit"] == git(foreign, "rev-parse", "HEAD").strip()
    assert lock["toolchain"] == "leanprover/lean4:v4.34.1" and lock["default_targets"] == ["Tiny"]
    (foreign / "Tiny.lean").write_text("malformed uncommitted draft")
    data = output(capsys, "inspect", "vl.lock", "Tiny.conclusion")
    assert data["statement"] == "theorem conclusion (p : Prop) (certificate : p) : p"
    assert data["binders"] == ["(p : Prop)", "(certificate : p)"]
    assert set(data["channels"]) == {"statement", "axioms", "kernel", "build_code", "supply_chain"}
    assert data["channels"]["statement"]["imports"] == ["Tiny.Step"]
    path = Path("vl.lock")
    path.write_text(json.dumps({**lock, "toolchain": "fabricated"}))
    assert main(["inspect", str(path), "Tiny.conclusion"]) == 2


def test_unpinned_dependency_and_changed_lock_refused(foreign, capsys):
    output(capsys, "import", foreign)
    lock = Path("vl.lock").read_bytes()
    manifest = foreign / "lake-manifest.json"
    package = {"name": "dep", "type": "git", "url": "local", "rev": "moving"}
    manifest.write_text(json.dumps({"packages": [package]}))
    commit_all(foreign, "unpinned dependency")
    assert main(["import", str(foreign)]) == 2
    package["rev"] = "a" * 40
    manifest.write_text(json.dumps({"packages": [package]}))
    commit_all(foreign, "pinned dependency")
    assert main(["import", str(foreign)]) == 2
    assert output(capsys, "import", foreign, "--lock", "dep.lock")["project"]["dependencies"] == [package]
    assert Path("vl.lock").read_bytes() == lock


def test_find_claim_in_one_pinned_file(foreign, capsys):
    output(capsys, "import", foreign)
    found = output(capsys, "inspect", "vl.lock", "--find", "certificate", "--file", "Tiny.lean")
    assert found["matches"] == [{"name": "Tiny.conclusion", "file": "Tiny.lean", "line": 5}]
    assert output(capsys, "inspect", "vl.lock", "--find", "certificate", "--file", "Tiny/Step.lean")["total"] == 0


def test_exact_inspection_prefers_library_sources_but_can_read_archive(foreign, capsys):
    archived = foreign / "archive/Tiny.lean"
    archived.parent.mkdir()
    archived.write_text("theorem Tiny.conclusion : True := trivial\n"
                        "theorem Tiny.archiveOnly : True := trivial\n")
    commit_all(foreign, "archive a copy of the live declaration")
    output(capsys, "pin", foreign)
    args = ["inspect", "vl.lock"]

    data = output(capsys, *args, "Tiny.conclusion")
    assert data["channels"]["statement"]["file"] == "Tiny.lean"
    assert data["source_scope"] == "pinned Lake library source roots"
    assert data["excluded_by_library_scope"] == 1 and not data["omissions"]
    assert main([*args, "Tiny.conclusion"]) == 0
    assert "Lean files outside this scope: 1" in capsys.readouterr().out

    data = output(capsys, *args, "Tiny.conclusion", "--file", "archive/Tiny.lean")
    assert data["statement"].endswith(": True") and data["source_scope"] == "archive/Tiny.lean"
    data = output(capsys, *args, "Tiny.archiveOnly")
    assert data["channels"]["statement"]["file"] == "archive/Tiny.lean"
    assert data["source_scope"] == "all pinned Lean files"
    hits = output(capsys, *args, "--find", "archiveOnly")
    assert hits["matches"] == [{"name": "Tiny.archiveOnly", "file": "archive/Tiny.lean", "line": 2}]
    assert not hits["ambiguous"]

    (foreign / "Tiny/Other.lean").write_text(archived.read_text())
    commit_all(foreign, "add a second live match")
    output(capsys, "pin", foreign, "--lock", "later.lock")
    assert main(["inspect", "later.lock", "Tiny.conclusion"]) == 2
    error = capsys.readouterr().err
    assert "candidate files: Tiny.lean, Tiny/Other.lean" in error and "archive/Tiny.lean" not in error
    assert "vl inspect later.lock Tiny.conclusion --file Tiny/Other.lean" in error
    assert "Next, run" not in error
    hits = output(capsys, "inspect", "later.lock", "--find", "conclusion")
    assert hits["ambiguous"] == [{"name": "Tiny.conclusion",
                                  "candidate_files": ["Tiny.lean", "Tiny/Other.lean", "archive/Tiny.lean"]}]
    assert output(capsys, "inspect", "later.lock", "Tiny.conclusion", "--file",
                  hits["ambiguous"][0]["candidate_files"][0])["channels"]["statement"]["file"] == "Tiny.lean"


def test_source_order_recovers_from_a_short_name_in_a_library(foreign, capsys):
    output(capsys, "pin", foreign)
    args = ["order", "vl.lock", "--root"]
    assert main([*args, "conclusion", "--json"]) == 2
    error = capsys.readouterr()
    assert not error.out and "vl inspect vl.lock --find conclusion" in error.err
    hit = output(capsys, "inspect", "vl.lock", "--find", "conclusion")["matches"][0]
    assert output(capsys, *args, hit["name"], "--file", hit["file"])["root_source"] == {
        "path": "Tiny.lean", "line": 5,
    }


def test_exact_inspection_reads_dynamic_layout_without_using_the_toml_roots(foreign, capsys):
    (foreign / "lakefile.lean").write_text("import Lake\nopen Lake DSL\npackage tiny\nlean_lib Tiny\n")
    commit_all(foreign, "add a dynamic lakefile with the existing TOML still present")
    output(capsys, "pin", foreign)
    data = output(capsys, "inspect", "vl.lock", "Tiny.conclusion")
    assert data["source_scope"] == "all pinned Lean files"


def test_readme_two_prompts_quote_the_tiny_project(foreign, capsys):
    readme = (Path(__file__).resolve().parents[1] / "README.md").read_text()
    output(capsys, "import", foreign, "--at", "HEAD")
    assert main(["inspect", "vl.lock", "Tiny.conclusion"]) == 0
    opening = "\n".join(capsys.readouterr().out.splitlines()[:3])
    assert f"```text\n{opening}\n```" in readme
    assert main(["order", "vl.lock", "--root", "Tiny.conclusion", "--budget", "2"]) == 0
    packet = capsys.readouterr().out.rstrip("\n")
    assert f"```text\n{packet}\n```" in readme


def test_inspect_excludes_type_ascriptions_in_the_result_from_the_hypotheses(foreign, capsys):
    (foreign / "Tiny.lean").write_text("theorem ascribed (n : Nat) : (n : Nat) = n := rfl\n")
    commit_all(foreign, "ascribed result")
    output(capsys, "import", foreign)
    data = output(capsys, "inspect", "vl.lock", "ascribed")
    assert data["binders"] == ["(n : Nat)"]
    assert data["statement"].endswith(": (n : Nat) = n")


# The source reader ------------------------------------------------------------------------------------------------

READ = [
    # A named argument in a binder type and in the stated type.
    ("theorem probe (m : Nat) (h : id (α := Nat) m = 0) : m = 0 := h",
     "theorem probe (m : Nat) (h : id (α := Nat) m = 0) : m = 0", ("(m : Nat)", "(h : id (α := Nat) m = 0)")),
    ("theorem t {R : Type*} [CommRing R] (x : R) : f (R := R) x = x := rfl",
     "theorem t {R : Type*} [CommRing R] (x : R) : f (R := R) x = x", ("{R : Type*}", "(x : R)")),
    # Set builders, a structure instance, a tactic block and a local instance, each inside a bracket.
    ("theorem bounded (t : Set Nat) : {n ∈ t | n ≤ 5} ⊆ t := by\n  intro n hn\n  exact hn.1\n",
     "theorem bounded (t : Set Nat) : {n ∈ t | n ≤ 5} ⊆ t", ("(t : Set Nat)",)),
    ("theorem span (m : Nat) : I ^ m = Ideal.span {z : R | ∃ a : Nat, a = m ∧ z = x ^ a} := by\n  simp\n",
     "theorem span (m : Nat) : I ^ m = Ideal.span {z : R | ∃ a : Nat, a = m ∧ z = x ^ a}", ("(m : Nat)",)),
    ("theorem same (T : Triple) : ({ T with E := E', isSnc := h } : Triple).I = T.I := rfl",
     "theorem same (T : Triple) : ({ T with E := E', isSnc := h } : Triple).I = T.I", ("(T : Triple)",)),
    ("theorem germ (hp : p ∈ V) : f (show q ∈ V by rw [h]; exact hp) = g := by\n  simp\n",
     "theorem germ (hp : p ∈ V) : f (show q ∈ V by rw [h]; exact hp) = g", ("(hp : p ∈ V)",)),
    ("theorem stalk (c : letI := f.alg p\n      Coords p) (n : Nat := 0) : c.dim = n := rfl",
     "theorem stalk (c : letI := f.alg p\n      Coords p) (n : Nat := 0) : c.dim = n",
     ("(c : letI := f.alg p\n      Coords p)", "(n : Nat := 0)")),
    ("theorem index (h : n < S.length) : S.center ⟨n, by omega⟩ ≤ S.stage ⟨n, h⟩ := le_rfl",
     "theorem index (h : n < S.length) : S.center ⟨n, by omega⟩ ≤ S.stage ⟨n, h⟩", ("(h : n < S.length)",)),
    # An absolute value and an operator written with a bar; an escaped name is one word, whatever it holds.
    ("theorem near (x y : Int) : |x - y| ≤ 1 ↔ x - y ≤ 1 ∧ y - x ≤ 1 := abs_sub_le_one",
     "theorem near (x y : Int) : |x - y| ≤ 1 ↔ x - y ≤ 1 ∧ y - x ≤ 1", ("(x y : Int)",)),
    ("theorem close (x : Int) :\n    |x| ≤ 1 →\n      |x * x| ≤ 1 := abs_mul_self_le",
     "theorem close (x : Int) :\n    |x| ≤ 1 →\n      |x * x| ≤ 1", ("(x : Int)",)),
    ("theorem prod_set : ∀ (l : List Nat) (a : Nat), (a :: l).prod = a * l.prod\n  | x :: xs, a => by simp\n",
     "theorem prod_set : ∀ (l : List Nat) (a : Nat), (a :: l).prod = a * l.prod", ()),
    ("theorem piped (f : Nat → Nat) : f <| g 0 = (g 0 |> f) ∨ (a || b) = true := Or.inl rfl",
     "theorem piped (f : Nat → Nat) : f <| g 0 = (g 0 |> f) ∨ (a || b) = true", ("(f : Nat → Nat)",)),
    ("theorem unit : «ε := (» (forget C) = 𝟙 (𝟙_ C) := rfl", "theorem unit : «ε := (» (forget C) = 𝟙 (𝟙_ C)", ()),
    # A body introduced by `where`: definition, abbreviation, instance and theorem.
    ("noncomputable def data (n : Nat) : Foo n where\n  x := 1", "noncomputable def data (n : Nat) : Foo n",
     ("(n : Nat)",)),
    ("abbrev unit : Foo 0 where\n  x := 1", "abbrev unit : Foo 0", ()),
    ("instance (priority := 100) fooBar [Bar α] (n : Nat) : Foo n where\n  x := 1",
     "instance (priority := 100) fooBar [Bar α] (n : Nat) : Foo n", ("(n : Nat)",)),
    ("theorem etale (h : Bijective f) : Etale A S where\n  formallyEtale := by\n    exact h.1\n  finite := h.2\n",
     "theorem etale (h : Bijective f) : Etale A S", ("(h : Bijective f)",)),
    # A local definition in the stated type keeps its own `:=`.
    ("theorem prime (m : Nat) :\n    haveI := isPrime x\n    (I ^ m).comap g = I ^ m := by\n  simp\n",
     "theorem prime (m : Nat) :\n    haveI := isPrime x\n    (I ^ m).comap g = I ^ m", ("(m : Nat)",)),
    ("theorem pair : haveI : Compact X := inst\n    let S := seq X; let T := seq X\n    S = T := rfl",
     "theorem pair : haveI : Compact X := inst\n    let S := seq X; let T := seq X\n    S = T", ()),
    # Equations: the statement ends before the first alternative.
    ("theorem reflexive : ∀ n : Nat, n = n\n  | 0 => rfl\n  | n + 1 => rfl\n", "theorem reflexive : ∀ n : Nat, n = n", ()),
    ("def length : {X : Scheme} → Seq X → Nat\n  | _, nil _ => 0\n  | _, cons _ _ r => r.length + 1\n",
     "def length : {X : Scheme} → Seq X → Nat", ()),
    # An attribute is not a binder, with a named argument or a type ascription in it.
    ("@[to_additive (attr := simp)] theorem one (x : G) : x * 1 = x := mul_one x",
     "@[to_additive (attr := simp)] theorem one (x : G) : x * 1 = x", ("(x : G)",)),
    ("@[simps (config := ({ simpRhs := true } : Simps.Config))] def e (n : Nat) : Foo n := mk n",
     "@[simps (config := ({ simpRhs := true } : Simps.Config))] def e (n : Nat) : Foo n", ("(n : Nat)",)),
]

# Each planted text differs from a form read above by the one thing its guard is there for.
REFUSED = {
    "a bracket that does not close": "theorem t (I : Set Nat) : I = (0, 1] := rfl",
    "a bracket that is never opened": "theorem t (I : Set Nat) : I = 0, 1) := rfl",
    "a `where` with no stated type": "instance fooBar where\n  x := 1",
    "a definition with no stated type": "def two (n : Nat) := n + 2",
    "a local definition by alternatives": "theorem t : let f : Nat → Nat\n      | 0 => 1\n      | _ => 2\n    f 0 = 1 := rfl",
    "a local definition that never gets its `:=`": "def d : let x where\n  y := 1",
    "a tactic block outside brackets": "theorem t : f 0 <| by obtain ⟨a, _⟩ := h; exact a := rfl",
    "a `do` block outside brackets": "theorem t : f = do\n    let mut x := 1\n    x := 2\n    pure x := rfl",
    "calculation steps outside brackets": "theorem t : True ∧ calc a = b := h := trivial",
    "a `match` in the stated type": "theorem t (n : Nat) : match n with\n  | 0 => True\n  | _ => False := by\n  cases n <;> trivial",
    "alternatives of a `fun`": "theorem t : f = fun\n  | 0 => 1\n  | _ => 2 := by\n  rfl",
    "an alternative with two patterns": "theorem t : ∀ n : Fin 2, n = n\n  | 0 | 1 => rfl",
    "a bar inside a line": "theorem t (p q : Prop) : p | q => p := trivial",
    "a bar with no stated type before it": "theorem t (x : Int) :\n  | x => x := trivial",
    "a bar that opens a line before a tactic block": "theorem t (x : Int) : 0 ≤\n    |x - by exact 1| := abs_nonneg _",
    "a `sorry` in the statement": "theorem t (h : sorry) : True := trivial",
    "a name literal": "theorem t (h : f `x = 0) : True := trivial",
    "an escaped name that does not close": "theorem t (h : «a = 0) : True := trivial",
    "alternatives on one line": "def pred : Nat → Nat | 0 => 0 | n + 1 => n",
}


@pytest.mark.parametrize("text,statement,binders", READ, ids=[row[1].split("(")[0].split(":")[0].strip() for row in READ])
def test_source_signature_reads_common_forms(text, statement, binders):
    from verifylab.foreign import signature

    assert signature(text) == (statement, binders)


@pytest.mark.parametrize("why", REFUSED)
def test_source_signature_refuses_what_it_cannot_decide(why):
    from verifylab.foreign import signature

    assert signature(REFUSED[why]) is None


def test_reader_indexes_definitions_and_named_instances_never_for_a_trusted_target(foreign, capsys):
    from verifylab import leanmod

    source = (
        "namespace Tiny\n\n"
        "structure Foo (n : Nat) where\n  x : Nat\n\n"
        "noncomputable def data (n : Nat) : Foo n where\n  x := 1\n\n"
        "abbrev short : Foo 0 := data 0\n\n"
        "instance (priority := 100) inhabited (n : Nat) : Inhabited (Foo n) where\n  default := data n\n\n"
        "instance : Inhabited (Foo 0) := ⟨data 0⟩\n\n"
        "def untyped := data 1\n\n"
        "theorem uses (n : Nat) (h : id (α := Nat) n = 0) : (data n).x = 1 := rfl\n\n"
        "end Tiny\n"
    )
    assert leanmod.find_theorems(source).keys() == {"Tiny.uses"}
    (foreign / "Tiny" / "Data.lean").write_text(source)
    commit_all(foreign, "definitions and a named argument")
    output(capsys, "import", foreign)
    data = output(capsys, "inspect", "vl.lock", "Tiny.data")
    assert data["statement"] == "noncomputable def data (n : Nat) : Foo n" and data["binders"] == ["(n : Nat)"]
    assert data["omissions"] == ["Tiny/Data.lean:16 Tiny.untyped: unsupported signature"]
    assert output(capsys, "inspect", "vl.lock", "Tiny.inhabited")["statement"] == (
        "instance (priority := 100) inhabited (n : Nat) : Inhabited (Foo n)")
    found = output(capsys, "inspect", "vl.lock", "--find", "Foo", "--file", "Tiny/Data.lean")
    assert [m["name"] for m in found["matches"]] == ["Tiny.data", "Tiny.short", "Tiny.inhabited"]
    assert main(["inspect", "vl.lock", "Tiny.untyped"]) == 2
    capsys.readouterr()
    assert output(capsys, "graph", "vl.lock")["edges"]["Tiny.uses"] == ["Tiny.data"]
    packet = output(capsys, "order", "vl.lock", "--root", "Tiny.uses")
    assert packet["input"] == ("Tiny.uses\ntheorem uses (n : Nat) (h : id (α := Nat) n = 0) : (data n).x = 1\n"
                               "Tiny.data\nnoncomputable def data (n : Nat) : Foo n\n")


def test_order_can_pin_an_old_revision_without_writing_a_lock(foreign, capsys):
    old = git(foreign, "rev-parse", "HEAD").strip()
    (foreign / "Tiny.lean").write_text("import Tiny.Step\n")
    commit_all(foreign, "remove the declaration")
    expected = output(capsys, "pin", foreign, "--at", old, "--lock", "expected.lock")
    packet = output(capsys, "order", "expected.lock", "--root", "Tiny.conclusion")
    actual = output(capsys, "order", "--from", foreign, "--at", old, "--root", "Tiny.conclusion")
    assert actual == packet and actual["commit"] == expected["project"]["commit"]
    assert not Path("vl.lock").exists()
    assert main(["order", "--from", str(foreign), "--root", "Tiny.conclusion"]) == 2
    assert "Next, run vl order --help." in capsys.readouterr().err


def test_pin_legacy_import_emits_only_a_stderr_deprecation(foreign, capsys):
    native = output(capsys, "pin", foreign)
    assert main(["import", "lake", str(foreign), "--json"]) == 0
    streams = capsys.readouterr()
    assert json.loads(streams.out) == native
    assert "'import lake' is deprecated. Use 'vl pin'." in streams.err


def test_pin_checks_default_target_types_before_writing(foreign, capsys):
    config = foreign / "lakefile.toml"
    config.write_text(config.read_text().replace('defaultTargets = ["Tiny"]', "defaultTargets = [42]"))
    commit_all(foreign, "invalid targets")
    assert main(["pin", str(foreign)]) == 2
    assert "defaultTargets must be a list of strings" in capsys.readouterr().err
    assert not Path("vl.lock").exists()
    assert output(capsys, "pin", foreign, "--at", "HEAD^")["project"]["default_targets"] == ["Tiny"]


def test_graph_reports_unsupported_static_layout_without_a_traceback(foreign, capsys):
    config = foreign / "lakefile.toml"
    with config.open("a") as handle:
        handle.write('globs = [{ subModules = "Tiny" }]\n')
    commit_all(foreign, "a glob table")
    output(capsys, "pin", foreign)
    assert main(["graph", "vl.lock", "--build", "Tiny"]) == 2
    err = capsys.readouterr().err
    assert "cannot resolve pinned Lake library layout" in err and "internal error" not in err
    output(capsys, "pin", foreign, "--at", "HEAD^", "--lock", "clean.lock")
    assert output(capsys, "graph", "clean.lock", "--build", "Tiny")


def test_native_project_check_points_to_native_tools(foreign, monkeypatch, capsys):
    monkeypatch.chdir(foreign)
    assert main(["check", "missing"]) == 2
    assert "native Lake checkout, use its Lean and Comparator workflow" in capsys.readouterr().err
    assert not (foreign / "research").exists()


def test_order_reports_executable_recovery_without_overriding_library_scope(foreign, capsys):
    with (foreign / "lakefile.toml").open("a") as config:
        config.write('\n[[lean_exe]]\nname = "demo"\nroot = "Main"\n')
    (foreign / "Main.lean").write_text("import Tiny\n\ntheorem launch : True := trivial\n")
    commit_all(foreign, "executable source")
    output(capsys, "pin", foreign)
    assert output(capsys, "inspect", "vl.lock", "launch")["channels"]["statement"]["file"] == "Main.lean"
    assert main(["order", "vl.lock", "--root", "launch"]) == 2
    err = capsys.readouterr().err
    assert "Source-only order searched pinned Lake libraries" in err
    assert "vl inspect vl.lock --find launch" in err and "Next, run" not in err
    assert output(capsys, "order", "vl.lock", "--root", "launch", "--file", "Main.lean")["sources"]


def test_missing_pin_points_to_pin_command_without_a_second_help_hint(foreign, capsys):
    assert main(["inspect", "new.lock", "Tiny.conclusion"]) == 2
    err = capsys.readouterr().err
    assert "vl pin PROJECT --at REV --lock new.lock" in err and "Next, run" not in err
    output(capsys, "pin", foreign, "--lock", "new.lock")
    assert output(capsys, "inspect", "new.lock", "Tiny.conclusion")["name"] == "Tiny.conclusion"


@pytest.mark.parametrize("existing_directory", [False, True])
def test_moved_pin_names_the_resolved_checkout_and_prints_a_working_repin(
        foreign, capsys, monkeypatch, existing_directory):
    import shlex

    from verifylab import gitref

    output(capsys, "pin", foreign)
    destination = foreign if existing_directory else foreign / "relocated"
    destination.mkdir(exist_ok=True)
    lock = destination / "moved lock"
    Path("vl.lock").rename(lock)
    monkeypatch.chdir(foreign)
    with monkeypatch.context() as guard:
        def unexpected_git(*args, **kwargs):
            pytest.fail("missing checkout should be reported before running Git")

        guard.setattr(gitref, "git", unexpected_git)
        guard.setattr(gitref, "_git", unexpected_git)
        assert main(["inspect", str(lock), "Tiny.conclusion"]) == 2
    err = capsys.readouterr().err
    assert str(lock) in err and str(destination / foreign.name) in err
    assert "Next, run" not in err
    program, *command = shlex.split(err.splitlines()[-1])
    assert program == "vl"
    recovered = output(capsys, *command)["lock"]
    assert output(capsys, "inspect", recovered, "Tiny.conclusion")["name"] == "Tiny.conclusion"
