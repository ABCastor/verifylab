"""`vl import comparator`: Comparator challenges (a config + a challenge file with `sorry`) become items. Synthetic
challenges only; every guard has a planted case next to its clean control."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from verifylab import formalization_yaml
from verifylab.cli import main
from verifylab.commands import importer
from verifylab.records import parse_item

from conftest import commit_all, git, write_item

PROVENANCE = ["--source-url", "https://example.org/corpus", "--source-commit", "abc1234", "--source-path",
              "lean/ComparatorChallenges"]
LEAN = """import Mathlib.Data.Nat.Basic

namespace Demo
{decls}
end Demo
"""
THEOREM = """
theorem {name} (n : Nat) : n + 0 = n := by
  sorry
"""
STANDARD_AXIOMS = ["propext", "Quot.sound", "Classical.choice"]


def vl(capsys, *args):
    rc = main(list(args))
    captured = capsys.readouterr()
    return rc, captured.out, captured.err


def challenge(folder: Path, name: str, theorems=("alpha",), *, lean: str | None = None, **config) -> None:
    """`name.lean` declaring Demo.<theorem> for each name, and `name.json` as OpenAI's configs are."""
    folder.mkdir(parents=True, exist_ok=True)
    text = lean if lean is not None else LEAN.format(decls="".join(THEOREM.format(name=t) for t in theorems))
    (folder / f"{name}.lean").write_text(text)
    body = {"challenge_module": f"ComparatorChallenges.{name}", "solution_module": f"Fixture.{name}Proof",
            "theorem_names": [f"Demo.{t}" for t in theorems], "definition_names": [],
            "permitted_axioms": STANDARD_AXIOMS, "enable_nanoda": False, **config}
    (folder / f"{name}.json").write_text(json.dumps({k: v for k, v in body.items() if v is not ...}, indent=2))


@pytest.fixture
def corpus(tmp_path: Path) -> Path:
    folder = tmp_path / "ComparatorChallenges"
    challenge(folder, "Alpha")
    challenge(folder, "BetaGamma", ("one", "two"))
    return folder


@pytest.fixture
def project(research_repo, monkeypatch) -> Path:
    monkeypatch.chdir(research_repo)
    monkeypatch.setattr(importer, "today", lambda: "2026-10-11")
    return research_repo


def snapshot(root: Path) -> dict[str, bytes]:
    return {str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*"))
            if p.is_file() and ".git" not in p.parts}


def counts(capsys_json: str) -> dict[str, int]:
    return json.loads(capsys_json)["counts"]


# What it creates -----------------------------------------------------------------------------------------------

def test_a_dry_run_prints_the_summary_and_writes_nothing(project, corpus, capsys):
    before = snapshot(project)
    rc, out, _ = vl(capsys, "import", "comparator", str(corpus))
    assert rc == 0 and "dry run, nothing written" in out and "2 new" in out
    assert snapshot(project) == before


def test_write_makes_a_result_item_per_challenge_with_the_challenge_as_target(project, corpus, capsys):
    rc, out, _ = vl(capsys, "import", "comparator", str(corpus), *PROVENANCE, "--write")
    assert rc == 0 and "wrote: 2 challenges" in out
    item = parse_item((project / "research/items/alpha.md").read_bytes(), "research/items/alpha.md")
    assert (item.kind, item.claim, item.author, item.created) == ("result", "formal", "agent:vl-import-comparator",
                                                                  "2026-10-11")
    assert item.lean == {"target": "research/targets/alpha.lean", "theorems": ["Demo.alpha"],
                         "solution": "Fixture.AlphaProof"}
    assert (project / "research/targets/alpha.lean").read_bytes() == (corpus / "Alpha.lean").read_bytes()
    assert "comparator:Alpha" in item.tags and any(t.startswith("import-digest:") for t in item.tags)
    assert "https://example.org/corpus" in item.body and "abc1234" in item.body
    assert "lean/ComparatorChallenges/Alpha.lean" in item.body
    two = parse_item((project / "research/items/beta-gamma.md").read_bytes(), "x/beta-gamma.md")
    assert two.lean["theorems"] == ["Demo.one", "Demo.two"] and "theorems" in two.statement
    assert any("not established" in limit for limit in item.limits)


def test_an_item_carries_its_area_and_its_source_where_the_catalog_reads_them(project, tmp_path, capsys):
    """Planted: solution modules nested under an area (OAI.Analysis.X, OAI.NumberTheory.Y). The control, a solution
    module with no area in its path, gets no area tag. The catalog filters by the tag and shows the `ref`."""
    folder = tmp_path / "OAI"
    challenge(folder, "Alpha", solution_module="OAI.Analysis.Alpha")
    challenge(folder, "BetaGamma", solution_module="OAI.NumberTheory.Sums.BetaGamma")
    challenge(folder, "Plain")
    assert vl(capsys, "import", "comparator", str(folder), *PROVENANCE, "--write")[0] == 0
    items = {n: parse_item((project / f"research/items/{n}.md").read_bytes(), f"research/items/{n}.md")
             for n in ("alpha", "beta-gamma", "plain")}
    areas = {n: sorted(t for t in i.tags if t.startswith("area:")) for n, i in items.items()}
    assert areas == {"alpha": ["area:analysis"], "beta-gamma": ["area:number-theory"], "plain": []}
    assert items["alpha"].ref == "https://example.org/corpus@abc1234:lean/ComparatorChallenges/Alpha.lean"
    assert items["alpha"].access is None
    commit_all(project)
    out = tmp_path / "catalog"
    assert vl(capsys, "export", "catalog", str(out))[0] == 0
    catalog = {i["id"]: i for i in json.loads((out / "catalog.json").read_text())["items"]}
    assert {n: catalog[n]["areas"] for n in items} == {"alpha": ["analysis"], "beta-gamma": ["number-theory"],
                                                          "plain": []}
    assert catalog["alpha"]["provenance"]["ref"] == items["alpha"].ref
    page = (out / "index.html").read_text()
    assert 'data-area="|analysis|"' in page and 'data-area="|(none)|"' in page and '<select name="area"' in page
    assert "abc1234:lean/ComparatorChallenges/Alpha.lean</code></li>" in page      # no "(access: None)"


def test_the_source_ref_needs_the_url_and_the_commit(tmp_path):
    from types import SimpleNamespace
    args = SimpleNamespace(source_url="https://example.org/c", source_commit="abc1234", source_path=None,
                           dir=str(tmp_path / "Challenges"))
    assert importer.source_ref(args, "A") == "https://example.org/c@abc1234:Challenges/A.lean"
    assert importer.source_ref(SimpleNamespace(**{**vars(args), "source_commit": None}), "A") is None
    assert importer.area("OAI.Analysis.Foo") == "analysis" and importer.area("Fixture.AProof") is None


def test_the_imported_items_pass_vl_validate_and_start_unverified(project, corpus, capsys):
    vl(capsys, "import", "comparator", str(corpus), *PROVENANCE, "--write")
    rc, out, _ = vl(capsys, "validate")
    assert rc == 0 and "0 errors" in out and "target not admitted" in out
    rc, out, _ = vl(capsys, "show", "alpha", "--brief")
    assert "unverified" in out


def test_write_needs_the_provenance_flags(project, corpus, capsys):
    before = snapshot(project)
    rc, _, err = vl(capsys, "import", "comparator", str(corpus), "--write")
    assert rc == 2 and "--source-url" in err and snapshot(project) == before
    rc, _, err = vl(capsys, "import", "comparator", str(corpus), "--source-commit", "not-a-commit")
    assert rc == 2 and "hex commit" in err


def test_only_limits_the_import_to_the_named_challenges(project, corpus, capsys):
    rc, _, _ = vl(capsys, "import", "comparator", str(corpus), *PROVENANCE, "--write", "--only", "Alpha")
    assert rc == 0 and (project / "research/items/alpha.md").is_file()
    assert not (project / "research/items/beta-gamma.md").exists()
    assert vl(capsys, "import", "comparator", str(corpus), "--only", "Nope")[0] == 2


# Idempotence and revisions -------------------------------------------------------------------------------------

def test_importing_the_same_challenges_again_changes_nothing(project, corpus, capsys, monkeypatch):
    vl(capsys, "import", "comparator", str(corpus), *PROVENANCE, "--write")
    before = snapshot(project)
    monkeypatch.setattr(importer, "today", lambda: "2027-01-01")
    rc, out, _ = vl(capsys, "import", "comparator", str(corpus), *PROVENANCE, "--write", "--json")
    assert rc == 0 and counts(out) == {"unchanged": 2}
    assert snapshot(project) == before


def test_a_changed_challenge_becomes_a_new_revision_and_leaves_the_admitted_target_alone(project, corpus, capsys):
    vl(capsys, "import", "comparator", str(corpus), *PROVENANCE, "--write")
    commit_all(project, "import")                                    # on the trusted branch: admitted
    admitted = snapshot(project)
    (corpus / "Alpha.lean").write_text((corpus / "Alpha.lean").read_text().replace("n + 0 = n", "0 + n = n"))
    rc, out, _ = vl(capsys, "import", "comparator", str(corpus), *PROVENANCE, "--write", "--json")
    assert rc == 0 and counts(out) == {"revision": 1, "unchanged": 1}
    again = snapshot(project)
    assert {k: v for k, v in again.items() if k in admitted} == admitted          # nothing existing was touched
    assert set(again) - set(admitted) == {"research/items/alpha-r2.md", "research/targets/alpha-r2.lean"}
    assert git(project, "status", "--short").split() == ["??", "research/items/alpha-r2.md",
                                                         "??", "research/targets/alpha-r2.lean"]
    newer = parse_item(again["research/items/alpha-r2.md"], "research/items/alpha-r2.md")
    assert newer.supersedes == ("alpha",) and newer.lean["target"] == "research/targets/alpha-r2.lean"
    assert again["research/targets/alpha-r2.lean"] == (corpus / "Alpha.lean").read_bytes()
    # the revision is now the latest one: importing again is a no-op, and a further change makes -r3
    assert counts(vl(capsys, "import", "comparator", str(corpus), *PROVENANCE, "--json")[1]) == {"unchanged": 2}
    (corpus / "Alpha.json").write_text((corpus / "Alpha.json").read_text().replace("Fixture.AlphaProof", "Fixture.Other"))
    vl(capsys, "import", "comparator", str(corpus), *PROVENANCE, "--write")
    assert (project / "research/items/alpha-r3.md").is_file()
    assert vl(capsys, "validate")[0] == 0


def test_a_warning_reads_as_a_warning_whatever_the_status(project, corpus, capsys):
    challenge(corpus, "Alpha", solution_imports=["X"])
    vl(capsys, "import", "comparator", str(corpus), *PROVENANCE, "--write")
    for status in ("new", "unchanged"):
        rc, out, _ = vl(capsys, "import", "comparator", str(corpus), *PROVENANCE, "--only", "Alpha", "--id-prefix",
                        "x-" if status == "new" else "")
        assert rc == 0 and f"1 {status}" in out
        assert "  warning Alpha: config keys vl does not read: solution_imports" in out


def test_a_changed_config_is_a_revision_but_reordered_axioms_are_not(project, corpus, capsys):
    vl(capsys, "import", "comparator", str(corpus), *PROVENANCE, "--write")
    challenge(corpus, "Alpha", permitted_axioms=["Classical.choice", "propext", "Quot.sound"])       # control
    assert counts(vl(capsys, "import", "comparator", str(corpus), "--json")[1]) == {"unchanged": 2}
    challenge(corpus, "Alpha", solution_module="Fixture.Elsewhere")
    assert counts(vl(capsys, "import", "comparator", str(corpus), "--json")[1]) == {"revision": 1, "unchanged": 1}


# Conflicts ------------------------------------------------------------------------------------------------------

def test_an_item_of_the_same_id_that_is_not_an_import_is_never_replaced(project, corpus, capsys):
    mine = write_item(project, "alpha")
    before = mine.read_bytes()
    rc, out, _ = vl(capsys, "import", "comparator", str(corpus), *PROVENANCE, "--write")
    assert rc == 1 and "conflict Alpha" in out and "use --id-prefix" in out
    assert mine.read_bytes() == before and not (project / "research/targets/alpha.lean").exists()
    assert (project / "research/items/beta-gamma.md").is_file()                      # the others still go in
    rc, _, _ = vl(capsys, "import", "comparator", str(corpus), *PROVENANCE, "--write", "--id-prefix", "oai-")
    assert rc == 0 and (project / "research/items/oai-alpha.md").is_file()


def test_a_challenge_of_the_same_name_from_another_source_or_prefix_is_not_an_earlier_version(project, corpus,
                                                                                               capsys):
    """Planted: corpus B (another source URL) has a challenge `Alpha` too, once identical to A's and once changed.
    Neither may be reported `unchanged` under A's id nor `supersede` A's item; with its own prefix it is new,
    without one its id is taken (a conflict). Same source, another prefix: a separate import, also new. The control,
    A re-imported as it was, is unchanged."""
    vl(capsys, "import", "comparator", str(corpus), *PROVENANCE, "--write")
    commit_all(project, "import A")
    other = ["--source-url", "https://example.org/other", "--source-commit", "def5678"]

    def statuses(*args):
        rc, out, _ = vl(capsys, "import", "comparator", str(corpus), "--only", "Alpha", "--json", *args)
        row = json.loads(out)["challenges"][0]
        return rc, row["status"], row["id"], row["supersedes"]

    assert statuses(*PROVENANCE) == (0, "unchanged", "alpha", None)                                 # control
    assert statuses(*other, "--id-prefix", "b-") == (0, "new", "b-alpha", None)
    assert statuses(*PROVENANCE, "--id-prefix", "b-") == (0, "new", "b-alpha", None)
    assert statuses(*other)[:3] == (1, "conflict", "alpha")
    challenge(corpus, "Alpha", solution_module="Fixture.Elsewhere")
    assert statuses(*other, "--id-prefix", "b-") == (0, "new", "b-alpha", None)
    assert statuses(*PROVENANCE) == (0, "revision", "alpha-r2", "alpha")                            # control


def test_a_target_file_with_no_item_is_not_overwritten(project, corpus, capsys):
    stray = project / "research/targets/alpha.lean"
    stray.parent.mkdir(exist_ok=True)
    stray.write_text("-- mine\n")
    rc, out, _ = vl(capsys, "import", "comparator", str(corpus), *PROVENANCE, "--write")
    assert rc == 1 and "conflict Alpha" in out and stray.read_text() == "-- mine\n"
    assert not (project / "research/items/alpha.md").exists()


def test_two_challenges_that_make_the_same_id_do_not_both_import(project, tmp_path, capsys):
    folder = tmp_path / "c"
    challenge(folder, "FooBar")
    challenge(folder, "Foo_Bar")
    rc, out, _ = vl(capsys, "import", "comparator", str(folder), "--json")
    rows = {r["name"]: r["status"] for r in json.loads(out)["challenges"]}
    assert rc == 1 and sorted(rows.values()) == ["conflict", "new"]


def test_two_challenges_that_make_the_same_id_write_the_first_only_and_do_not_crash(project, tmp_path, capsys):
    folder = tmp_path / "c"
    challenge(folder, "FooBar")
    challenge(folder, "Foo_Bar", ("other",))
    rc, out, err = vl(capsys, "import", "comparator", str(folder), *PROVENANCE, "--write")
    assert rc == 1 and "conflict Foo_Bar" in out and "internal error" not in err
    assert (project / "research/targets/foo-bar.lean").read_bytes() == (folder / "FooBar.lean").read_bytes()
    item = parse_item((project / "research/items/foo-bar.md").read_bytes(), "research/items/foo-bar.md")
    assert "comparator:FooBar" in item.tags
    assert vl(capsys, "import", "comparator", str(folder), *PROVENANCE, "--only", "Foo_Bar", "--id-prefix", "x-",
              "--write")[0] == 0                                                                    # control


def test_an_admitted_item_missing_from_the_worktree_is_not_made_again(project, corpus, capsys):
    vl(capsys, "import", "comparator", str(corpus), *PROVENANCE, "--write")
    commit_all(project, "import")
    (project / "research/items/alpha.md").unlink()
    (project / "research/targets/alpha.lean").unlink()
    rc, out, _ = vl(capsys, "import", "comparator", str(corpus), *PROVENANCE, "--write", "--json")
    assert rc == 0 and counts(out) == {"unchanged": 2}
    assert not (project / "research/items/alpha.md").exists()                      # the trusted ref has it


def test_files_are_created_exclusively(tmp_path):
    root = tmp_path / "root"
    (root / "research").mkdir(parents=True)
    (root / "research/t.lean").write_text("admitted")
    with pytest.raises(FileExistsError):
        importer._write(root, [importer.Row("X", files={"research/t.lean": b"overwritten"})])
    assert (root / "research/t.lean").read_text() == "admitted"
    importer._write(root, [importer.Row("X", files={"research/new.lean": b"fresh"})])           # control
    assert (root / "research/new.lean").read_text() == "fresh"


def test_a_write_that_fails_part_way_leaves_nothing_of_this_run(tmp_path):
    root = tmp_path / "root"
    (root / "research").mkdir(parents=True)
    (root / "research/taken.md").write_text("admitted")
    rows = [importer.Row("A", files={"research/a.lean": b"a", "research/a.md": b"a"}),
            importer.Row("B", files={"research/b.lean": b"b", "research/taken.md": b"overwritten"})]
    with pytest.raises(FileExistsError):
        importer._write(root, rows)
    assert sorted(p.name for p in (root / "research").iterdir()) == ["taken.md"]          # not even a temporary file
    assert (root / "research/taken.md").read_text() == "admitted"
    importer._write(root, rows[:1])                                                         # control
    assert sorted(p.name for p in (root / "research").iterdir()) == ["a.lean", "a.md", "taken.md"]


def test_a_file_that_appears_after_planning_stops_the_run_cleanly_and_undoes_it(project, corpus, capsys, monkeypatch):
    finish = importer._finish

    def late(ctx, row, broken):
        finish(ctx, row, broken)
        if row.name == "BetaGamma":                       # someone else makes the item between planning and writing
            (project / "research/items/beta-gamma.md").write_text("theirs")

    monkeypatch.setattr(importer, "_finish", late)
    before = snapshot(project)
    rc, _, err = vl(capsys, "import", "comparator", str(corpus), *PROVENANCE, "--write")
    assert rc == 2 and "nothing was written" in err and "beta-gamma.md" in err and "internal error" not in err
    assert {k: v for k, v in snapshot(project).items() if k != "research/items/beta-gamma.md"} == before
    assert (project / "research/items/beta-gamma.md").read_text() == "theirs"



# What vl cannot take, and what is malformed -----------------------------------------------------------------------

def test_challenges_vl_cannot_represent_are_skipped_with_the_reason(project, tmp_path, capsys):
    folder = tmp_path / "c"
    challenge(folder, "Good")
    challenge(folder, "Holes", theorem_names=[], definition_names=["Demo.d"])                  # definitions to fill
    challenge(folder, "NoNames", theorem_names=..., definition_names=["Demo.d"])               # no key at all
    challenge(folder, "Open", lean=LEAN.format(decls="\nsection\ntheorem alpha : True := by\n  sorry\n"))
    challenge(folder, "Proved", lean=LEAN.format(decls="\ntheorem alpha : True := trivial\n"))
    rc, out, _ = vl(capsys, "import", "comparator", str(folder), *PROVENANCE, "--write")
    assert rc == 0 and "1 new, 4 skipped" in out and "skipped NoNames: no theorem_names" in out
    assert "no theorem_names" in out and "closes section" in out and "must have body `sorry`" in out
    assert sorted(p.name for p in (project / "research/items").glob("*.md")) == ["add-zero.md", "good.md"]


def test_malformed_configs_are_invalid_and_fail_the_run(project, tmp_path, capsys):
    folder = tmp_path / "c"
    challenge(folder, "Good")
    cases = {"NoSolution": {"solution_module": ...}, "WrongModule": {"challenge_module": "ComparatorChallenges.Other"},
             "NoAxioms": {"permitted_axioms": ...}, "BadNanoda": {"enable_nanoda": "yes"},
             "Reserved": {"solution_module": "VLTrusted.X"}}
    for name, bad in cases.items():
        challenge(folder, name, **bad)
    (folder / "Garbled.json").write_text("{not json")
    (folder / "Garbled.lean").write_text("theorem x : True := sorry\n")
    (folder / "NoLean.json").write_text((folder / "Good.json").read_text().replace("Good", "NoLean"))
    rc, out, _ = vl(capsys, "import", "comparator", str(folder), "--json")
    rows = {r["name"]: r for r in json.loads(out)["challenges"]}
    assert rc == 1 and rows["Good"]["status"] == "new"
    assert {n for n, r in rows.items() if r["status"] == "invalid"} == {*cases, "Garbled", "NoLean"}
    assert "solution_module" in rows["NoSolution"]["reasons"][0] and "reserved" in rows["Reserved"]["reasons"][0]


def test_names_in_a_config_that_are_not_plain_text_make_it_invalid_and_do_not_crash(project, tmp_path, capsys):
    folder = tmp_path / "c"
    challenge(folder, "Good")
    challenge(folder, "Surrogate", permitted_axioms=["\ud800"])
    challenge(folder, "Spaced", permitted_axioms=["two words\n- injected"])
    challenge(folder, "Empty", definition_names=[""])
    rc, out, err = vl(capsys, "import", "comparator", str(folder), *PROVENANCE, "--write", "--json")
    rows = {r["name"]: r["status"] for r in json.loads(out)["challenges"]}
    assert rc == 1 and "internal error" not in err
    assert rows == {"Good": "new", "Surrogate": "invalid", "Spaced": "invalid", "Empty": "invalid"}


# The project-wide contract ------------------------------------------------------------------------------------------

def set_config(project: Path, text: str) -> None:
    (project / "research/vl.toml").write_text(text)
    commit_all(project, "config")


def test_where_the_project_allows_more_than_the_config_the_limits_say_so(project, tmp_path, capsys):
    folder = tmp_path / "c"
    challenge(folder, "Strict", enable_nanoda=True, definition_names=["Demo.a", "Demo.b"])
    challenge(folder, "Plain")
    set_config(project, '[project]\nname = "fixture"\n\n[lean]\nroots = ["Fixture"]\n'
                        'permitted_axioms = ["propext", "Quot.sound", "Classical.choice", "Lean.ofReduceBool"]\n'
                        'external_kernels = false\n')
    vl(capsys, "import", "comparator", str(folder), *PROVENANCE, "--write")
    strict = parse_item((project / "research/items/strict.md").read_bytes(), "research/items/strict.md")
    text = " ".join(strict.limits)
    assert "Lean.ofReduceBool" in text and "second kernel" in text and "2 definition holes (Demo.a, Demo.b)" in text
    assert "does not compare (only their types)" in text and "bodies included" in text and "to compare" not in text
    plain = parse_item((project / "research/items/plain.md").read_bytes(), "research/items/plain.md")
    assert "second kernel" not in " ".join(plain.limits) and "definition holes" not in " ".join(plain.limits)
    assert "Lean.ofReduceBool" in " ".join(plain.limits)          # the project-wide allowance concerns every item


def test_the_run_names_the_upstream_definition_holes_it_does_not_pass_through(project, tmp_path, capsys):
    folder = tmp_path / "c"
    challenge(folder, "Holed", definition_names=["Demo.row", "Demo.phi"])
    challenge(folder, "Plain")                                                                     # control
    rc, out, _ = vl(capsys, "import", "comparator", str(folder))
    assert rc == 0 and "warning Holed: definition holes not passed through: Demo.row, Demo.phi" in out
    assert "Plain:" not in out


def test_a_project_that_is_stricter_than_the_config_and_a_solution_outside_the_roots_are_warned_about(
        project, tmp_path, capsys):
    folder = tmp_path / "c"
    challenge(folder, "Wide", permitted_axioms=[*STANDARD_AXIOMS, "Extra.axiom"], solution_module="Other.Proof",
              solver_hint="x")
    challenge(folder, "Clean")
    rc, out, _ = vl(capsys, "import", "comparator", str(folder), "--json")
    rows = {r["name"]: r for r in json.loads(out)["challenges"]}
    wide = " ".join(rows["Wide"]["warnings"])
    assert rc == 0 and "Extra.axiom" in wide and "not under [lean] roots" in wide and "solver_hint" in wide
    assert rows["Clean"]["warnings"] == []


# Sources: the catalogue and the family scope notes ---------------------------------------------------------------------

CATALOGUE = """# yaml-language-server: $schema=x
version: "v0.4"

sources:
  - title: "Paper One: on $x$"
    authors: ["Someone"]
    id: ../preprints/Paper-One-2026/paper.pdf   # a comment
    type: article

related_formalizations:
  - id: https://github.com/leanprover-community/mathlib4
    relationship: builds-on

status:
  scope: "Partial progress."
  main_results:
    - comparator_config: ComparatorChallenges/Alpha.json
      declaration: Demo.alpha_main
      file: Demo/Alpha.lean

review:
  status: unchecked
"""
NOTE_ONE = """# Family one

- [Paper One](../../preprints/Paper-One-2026/paper.pdf)
- [A second paper](../../preprints/Paper-Two-2026/second.pdf)

| Result | Statement |
|---|---|
| a | [Alpha.lean](../ComparatorChallenges/Alpha.lean) |
| b | [BetaGamma.lean](../ComparatorChallenges/BetaGamma.lean) |
"""
NOTE_TWO = "# Family two\n\n[again](../../preprints/Paper-Two-2026/second.pdf) [x](../ComparatorChallenges/Delta.lean)\n"


@pytest.fixture
def scope(tmp_path: Path) -> tuple[Path, Path]:
    notes = tmp_path / "docs"
    notes.mkdir()
    (notes / "001.md").write_text(NOTE_ONE)
    (notes / "002.md").write_text(NOTE_TWO)
    (tmp_path / "formalization.yaml").write_text(CATALOGUE)
    return tmp_path / "formalization.yaml", notes


def test_the_notes_give_a_source_item_per_paper_that_the_results_cite(project, corpus, scope, capsys):
    challenge(corpus, "Delta")
    yaml, notes = scope
    rc, out, _ = vl(capsys, "import", "comparator", str(corpus), *PROVENANCE, "--catalogue", str(yaml),
                    "--notes", str(notes), "--write")
    assert rc == 0 and "sources: 2 new" in out
    sources = [parse_item(p.read_bytes(), str(p)) for p in sorted((project / "research/items").glob("paper-*.md"))]
    by_title = {s.title: s for s in sources}
    one, two = by_title["Paper One: on $x$"], by_title["A second paper"]         # catalogue title, else the note's
    assert (one.kind, one.access, one.ref) == ("source", "citation-only", "preprints/Paper-One-2026/paper.pdf")
    assert two.ref == "preprints/Paper-Two-2026/second.pdf" and "catalogue-review:unchecked" in one.tags
    assert "catalogue-review:unchecked" not in two.tags
    alpha = parse_item((project / "research/items/alpha.md").read_bytes(), "research/items/alpha.md")
    assert alpha.cites == tuple(sorted((one.id, two.id)))
    delta = parse_item((project / "research/items/delta.md").read_bytes(), "research/items/delta.md")
    assert delta.cites == (two.id,) and "main-result" not in delta.tags
    assert "main-result" in alpha.tags and "catalogue-review:unchecked" in alpha.tags
    assert "`Demo.alpha_main` in `Demo/Alpha.lean`" in alpha.body and "001.md" in alpha.body
    assert vl(capsys, "validate")[0] == 0


def test_a_paper_folder_with_parentheses_is_read_and_a_note_without_a_paper_is_reported(tmp_path, project, capsys):
    """Planted: a preprint folder named `...-CAT(0)-...` (as OpenAI's note 337 has), whose link the reader cut at the
    first `)`, so the challenge silently got no paper; and a note that lists a challenge but links no paper. The
    control, a plain folder in the same note, is read either way."""
    notes = tmp_path / "docs"
    notes.mkdir()
    (notes / "337.md").write_text(
        "- [Fillings in $\\mathrm{CAT}(0)$ spaces](../../preprints/Fillings-in-CAT(0)-spaces/paper.pdf)\n"
        "- [Plain](../../preprints/Plain/paper.pdf)\n[x](../ComparatorChallenges/Alpha.lean)\n")
    (notes / "338.md").write_text("[y](../ComparatorChallenges/BetaGamma.lean)\n")
    read = formalization_yaml.read_notes(notes)
    assert read.cited_by["Alpha"] == ("Fillings-in-CAT(0)-spaces", "Plain")
    assert read.papers["Fillings-in-CAT(0)-spaces"].path == "preprints/Fillings-in-CAT(0)-spaces/paper.pdf"
    folder = tmp_path / "c"
    challenge(folder, "Alpha")
    challenge(folder, "BetaGamma")
    out = vl(capsys, "import", "comparator", str(folder), "--notes", str(notes))[1]
    assert "1 challenges are in scope notes that link no paper: BetaGamma" in out


def test_a_source_that_exists_is_reused(project, corpus, scope, capsys):
    _, notes = scope
    args = ["import", "comparator", str(corpus), *PROVENANCE, "--notes", str(notes), "--write"]
    vl(capsys, *args, "--only", "Alpha")
    papers = sorted((project / "research/items").glob("paper-*.md"))
    assert len(papers) == 2
    rc, out, _ = vl(capsys, *args, "--only", "BetaGamma", "--json")
    assert rc == 0 and json.loads(out)["source_counts"] == {"unchanged": 2}
    assert sorted((project / "research/items").glob("paper-*.md")) == papers
    beta = parse_item((project / "research/items/beta-gamma.md").read_bytes(), "research/items/beta-gamma.md")
    assert beta.cites == tuple(sorted(p.stem for p in papers))
    other = ["--source-url", "https://example.org/other", "--source-commit", "def5678"]          # planted
    challenge(corpus, "Delta")                                         # cites Paper-Two, as BetaGamma does
    rc, out, _ = vl(capsys, "import", "comparator", str(corpus), *other, "--notes", str(notes), "--only", "Delta",
                    "--json")
    assert json.loads(out)["source_counts"] == {"conflict": 1}            # the same folder in another source


def test_a_source_id_taken_by_another_item_is_not_replaced_or_cited(project, corpus, scope, capsys):
    _, notes = scope
    taken = write_item(project, importer._paper_id("Paper-One-2026"))
    before = taken.read_bytes()
    rc, out, _ = vl(capsys, "import", "comparator", str(corpus), *PROVENANCE, "--notes", str(notes), "--write")
    assert rc == 1 and "conflict Paper-One-2026" in out and taken.read_bytes() == before
    alpha = parse_item((project / "research/items/alpha.md").read_bytes(), "research/items/alpha.md")
    other = next(p for p in (project / "research/items").glob("paper-*.md") if p != taken)
    assert alpha.cites == (other.stem,)


def test_a_source_whose_id_would_be_invalid_is_skipped_and_not_cited(project, corpus, scope, capsys):
    _, notes = scope
    long = "x" * 40 + "-"                                    # a paper id with this prefix is over 64 characters
    rc, out, _ = vl(capsys, "import", "comparator", str(corpus), *PROVENANCE, "--notes", str(notes), "--write",
                    "--id-prefix", long)
    assert rc == 0 and "skipped Paper-One-2026" in out and not list((project / "research/items").glob("*paper-*"))
    alpha = parse_item((project / f"research/items/{long}alpha.md").read_bytes(), "x/" + long + "alpha.md")
    assert alpha.cites == () and vl(capsys, "validate")[0] == 0


def test_a_source_with_a_short_prefix_is_written(project, corpus, scope, capsys):                    # control
    _, notes = scope
    rc, out, _ = vl(capsys, "import", "comparator", str(corpus), *PROVENANCE, "--notes", str(notes), "--write",
                    "--id-prefix", "ok-")
    assert rc == 0 and "skipped" not in out and len(list((project / "research/items").glob("ok-paper-*"))) == 2


def test_a_catalogue_of_another_version_is_refused(project, corpus, tmp_path, capsys):
    bad = tmp_path / "other.yaml"
    bad.write_text(CATALOGUE.replace("v0.4", "v0.5"))
    rc, _, err = vl(capsys, "import", "comparator", str(corpus), "--catalogue", str(bad))
    assert rc == 2 and "version v0.4" in err
    good = tmp_path / "catalogue.json"
    good.write_text(json.dumps(formalization_yaml.parse_yaml(CATALOGUE)))
    assert vl(capsys, "import", "comparator", str(corpus), "--catalogue", str(good))[0] == 0      # JSON is YAML


# The YAML subset ------------------------------------------------------------------------------------------------------

def test_the_yaml_subset_reads_what_the_catalogue_uses():
    parsed = formalization_yaml.parse_yaml(CATALOGUE)
    assert parsed["version"] == "v0.4" and parsed["review"] == {"status": "unchecked"}
    assert parsed["sources"][0] == {"title": "Paper One: on $x$", "authors": ["Someone"],
                                    "id": "../preprints/Paper-One-2026/paper.pdf", "type": "article"}
    assert parsed["status"]["main_results"][0]["declaration"] == "Demo.alpha_main"
    assert formalization_yaml.parse_yaml("a:\n- x\n- y: 1\n  z: 'it''s'\nb: >-\n  folded\n  text\nc: [1, \"two, 2\", ]\nd: []\n"
                                "e: null\nf: true\ng:\n  h: ~\n") == {
        "a": ["x", {"y": "1", "z": "it's"}], "b": "folded text", "c": ["1", "two, 2"], "d": [], "e": None,
        "f": True, "g": {"h": None}}


def test_an_empty_list_item_is_empty_and_not_the_next_item():
    assert formalization_yaml.parse_yaml("-\n- b: 1\n- c\n") == [None, {"b": "1"}, "c"]
    assert formalization_yaml.parse_yaml("a:\n-\n  - x\n- y\n") == {"a": [["x"], "y"]}              # control: a nested value
    assert formalization_yaml.parse_yaml("a:\n- b\nc: >-\n  one\n  two\n") == {"a": ["b"], "c": "one two"}


@pytest.mark.parametrize("text", [
    "a: &anchor 1\n", "a: *alias\n", "a: {b: 1}\n", "a: !!str x\n", "a: [[1]]\n", "a:\n\tb: 1\n", "---\na: 1\n",
    "a: first\n  second\n", "a: 1\na: 2\n", "a: |\n  x\n    y\n", "- a\n b: 1\n", "a: \"\\x41\"\n", "a: b: c\n", "a: |x\n  t\n",
], ids=lambda t: t.splitlines()[0] + "...")
def test_the_yaml_subset_refuses_what_it_does_not_read(text):
    with pytest.raises(formalization_yaml.CatalogueError, match="line"):
        formalization_yaml.parse_yaml(text)


def test_challenge_names_become_kebab_ids():
    assert [importer.kebab(n) for n in ("AbhyankarSathaye", "WLIdentification", "C0Absorption", "Foo_Bar", "L1")] == [
        "abhyankar-sathaye", "wl-identification", "c0-absorption", "foo-bar", "l1"]

