from __future__ import annotations

import shutil

import pytest

from verifylab import index
from verifylab.cli import main
from verifylab.repo import Repo

from conftest import commit_all, write_item


def vl(capsys, *args):
    rc = main(list(args))
    captured = capsys.readouterr()
    return rc, captured.out, captured.err


def result_lines(out: str) -> list[str]:
    return [line for line in out.splitlines() if line.startswith("  ")]


def test_index_rebuilds_after_cache_deletion_without_loss(research_repo, monkeypatch, capsys):
    root = research_repo
    monkeypatch.chdir(root)
    rc, first, _ = vl(capsys, "find", "zero")
    assert rc == 0 and "index rebuilt (missing)" in first
    assert (root / ".vl-cache" / "index.sqlite").is_file()
    rc, second, _ = vl(capsys, "find", "zero")
    assert "index rebuilt" not in second and result_lines(second) == result_lines(first)
    shutil.rmtree(root / ".vl-cache")
    rc, third, _ = vl(capsys, "find", "zero")
    assert "index rebuilt (missing)" in third and result_lines(third) == result_lines(first)


def test_corrupt_index_is_rebuilt(research_repo, monkeypatch, capsys):
    root = research_repo
    monkeypatch.chdir(root)
    rc, first, _ = vl(capsys, "find", "zero")
    db = root / ".vl-cache" / "index.sqlite"
    db.write_bytes(b"this is not a sqlite database" * 100)
    rc, out, _ = vl(capsys, "find", "zero")
    assert rc == 0 and "index rebuilt (corrupt" in out and result_lines(out) == result_lines(first)


def test_damaged_pages_are_rebuilt(research_repo, monkeypatch, capsys):
    root = research_repo
    for i in range(40):
        write_item(root, f"filler-{i:02d}")
    monkeypatch.chdir(root)
    rc, first, _ = vl(capsys, "find", "zero", "--limit", "100")
    db = root / ".vl-cache" / "index.sqlite"
    data = bytearray(db.read_bytes())
    assert len(data) > 3 * 4096
    data[4096:len(data)] = b"\xff" * (len(data) - 4096)  # keep the header page, destroy every other page
    db.write_bytes(bytes(data))
    rc, out, _ = vl(capsys, "find", "zero", "--limit", "100")
    assert rc == 0 and "index rebuilt (corrupt" in out and result_lines(out) == result_lines(first)


def test_index_goes_stale_when_items_or_lean_files_change(research_repo, monkeypatch, capsys):
    root = research_repo
    monkeypatch.chdir(root)
    vl(capsys, "find", "zero")
    write_item(root, "brand-new")
    rc, out, _ = vl(capsys, "find", "brand")
    assert "index rebuilt (stale)" in out and "brand-new@" in out
    with (root / "Fixture" / "Basic.lean").open("a") as handle:
        handle.write("theorem fresh_decl : True := trivial\n")
    rc, out, _ = vl(capsys, "find", "fresh_decl")
    assert "index rebuilt (stale)" in out and "fresh_decl  theorem  Fixture/Basic.lean:2" in out


def test_like_fallback_matches_fts(research_repo, monkeypatch):
    root = research_repo
    write_item(root, "other-result")
    repo = Repo.open(root)
    if not index.fts5_available():
        pytest.skip("SQLite without FTS5: only the LIKE path exists here")
    fts = index.open_index(repo, fts=True)
    like = index.open_index(repo, fts=False)
    try:
        assert fts.info.fts and not like.info.fts and like.info.rebuilt
        for query in ("natural", "small result", "add-zero", "every n"):
            assert set(fts.search_items(query)) == set(like.search_items(query)), query
        assert like.search_items("nothing-like-this") == []
    finally:
        fts.close()
        like.close()


def test_scan_lean_tracks_namespaces_and_skips_comments():
    text = """namespace A.B
/-- doc -/
theorem t1 : True := trivial
/- nested /- comment -/ theorem hidden : True := trivial -/
section S
lemma l1 : True := trivial
end S
namespace C
@[simp, reducible] protected def d1 := 1
instance : Inhabited Nat := ⟨0⟩
instance namedInst : Inhabited Bool := ⟨true⟩
class inductive K | a
theorem _root_.rooted : True := trivial
end C
end A.B
structure Top where
  x : Nat
"""
    names = [(n, k, line) for n, k, line, _ in index.scan_lean(text)]
    assert names == [
        ("A.B.t1", "theorem", 3), ("A.B.l1", "lemma", 6), ("A.B.C.d1", "def", 9),
        ("A.B.C.namedInst", "instance", 11), ("A.B.C.K", "class inductive", 12), ("rooted", "theorem", 13),
        ("Top", "structure", 16),
    ]


def test_the_index_never_reads_lean_files_outside_the_repository(research_repo, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "Leak.lean").write_text("theorem leaked : True := trivial\n")
    (research_repo / "Fixture" / "Linked.lean").symlink_to(outside / "Leak.lean")
    cfg = research_repo / "research" / "vl.toml"
    cfg.write_text(cfg.read_text().replace('roots = ["Fixture"]', 'roots = ["Fixture", "../outside"]'))
    commit_all(research_repo, "a root outside the repository")
    files = index.lean_files(Repo.open(research_repo))
    assert [f.name for f in files] == ["Basic.lean"], files
    cfg.write_text(cfg.read_text().replace("[lean]\n", '[lean]\nproject = "../outside"\n'))
    commit_all(research_repo, "a project outside the repository")
    assert index.lean_files(Repo.open(research_repo)) == []


def test_a_failed_build_leaves_no_half_built_index(research_repo, monkeypatch):
    repo = Repo.open(research_repo)
    target = research_repo / ".vl-cache" / "index.sqlite"
    target.parent.mkdir()

    def broken(text):
        raise RuntimeError("scanner bug")
    monkeypatch.setattr(index, "scan_lean", broken)
    with pytest.raises(RuntimeError, match="scanner bug"):
        index._build(repo, target, "sig", fts=False)
    assert list(target.parent.iterdir()) == []


def test_a_root_is_a_module_name_its_file_and_its_folder(research_repo):
    """`Fixture` is the umbrella file Fixture.lean and the folder Fixture/; `Fixture.Sub` is Fixture/Sub.lean and the
    folder Fixture/Sub/, never a folder literally named `Fixture.Sub`."""
    root = research_repo
    (root / "Fixture.lean").write_text("import Fixture.Basic\n")
    sub = root / "Fixture" / "Sub"
    sub.mkdir()
    (root / "Fixture" / "Sub.lean").write_text("theorem s : True := trivial\n")
    (sub / "Deep.lean").write_text("theorem d : True := trivial\n")
    names = lambda: sorted(str(f.relative_to(root)) for f in index.lean_files(Repo.open(root)))
    commit_all(root, "an umbrella module and a submodule")
    assert names() == ["Fixture.lean", "Fixture/Basic.lean", "Fixture/Sub.lean", "Fixture/Sub/Deep.lean"]
    cfg = root / "research" / "vl.toml"
    cfg.write_text(cfg.read_text().replace('roots = ["Fixture"]', 'roots = ["Fixture.Sub"]'))
    commit_all(root, "only the submodule")
    assert names() == ["Fixture/Sub.lean", "Fixture/Sub/Deep.lean"]
