"""gitref returns paths exactly as they are on disk: git's NUL-separated output, never its C-quoted form."""

from __future__ import annotations

from pathlib import Path

import pytest

from verifylab import gitref
from conftest import commit_all, git

NAMES = ["dir with space/é ñ.json", "plain.txt", "ü.lean"]


def _repo(tmp_path: Path) -> Path:
    root = tmp_path / "r"
    root.mkdir()
    git(root, "init", "-q", "-b", "trusted")
    git(root, "config", "user.name", "T")
    git(root, "config", "user.email", "t@e")
    (root / "base.txt").write_text("base\n")
    commit_all(root, "base")
    return root


def _write(root: Path, name: str, text: str = "x\n") -> None:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def test_changed_files_and_ls_files_return_unquoted_paths(tmp_path):
    root = _repo(tmp_path)
    git(root, "checkout", "-q", "-b", "side")
    for name in NAMES:
        _write(root, name)
    git(root, "mv", "base.txt", "basé.txt")
    commit_all(root, "side")
    rows = gitref.changed_files(root, "trusted", "side")
    assert sorted(rows) == sorted([("A", n) for n in NAMES] + [("A", "basé.txt"), ("D", "base.txt")])
    assert gitref.ls_files(root, "side", "dir with space") == ["dir with space/é ñ.json"]


def test_dirty_and_ignored_paths_return_unquoted_paths(tmp_path):
    root = _repo(tmp_path)
    (root / ".gitignore").write_text("*.olean\n")
    commit_all(root, "ignore")
    for name in NAMES:
        _write(root, name)
    _write(root, "build/ä b.olean")
    git(root, "mv", "base.txt", "basé.txt")
    assert gitref.dirty_paths(root) == {*NAMES, "basé.txt", "base.txt"}
    assert gitref.ignored_paths(root) == ["build/ä b.olean"]


def test_a_revision_starting_with_a_dash_is_refused_never_read_as_an_option(tmp_path):
    import pytest
    root = _repo(tmp_path)
    planted = tmp_path / "written-by-git"
    for call in (lambda ref: gitref.show(root, ref, "base.txt"), lambda ref: gitref.rev_parse(root, ref),
                 lambda ref: gitref.changed_files(root, "trusted", ref), lambda ref: gitref.ls_files(root, ref, ".")):
        with pytest.raises(gitref.GitError, match="option"):
            call(f"--output={planted}")
    assert not list(tmp_path.glob("written-by-git*"))
    assert gitref.blob(root, "--output=x") == (None, None)


# One `git cat-file --batch` per command, however many records ----------------------------------------------------

def _records_repo(root: Path, n: int, start: int = 0) -> Path:
    """Results `start` to `n - 1`, each with an admitted receipt and a review, all committed on the trusted ref."""
    from conftest import write_item
    from test_trust_boundary import receipt
    from verifylab.records import seal_review, write_new_json
    for k in range(start, n):
        write_item(root, f"item-{k:03d}")
        receipt(root, f"item-{k:03d}")
        review = seal_review(dict(item=f"item-{k:03d}", item_revision="a" * 40, kind="understanding",
                                  author="agent:x", text="read", created="2026-10-02T10:00:00+00:00"))
        write_new_json(root / "research" / "reviews" / f"item-{k:03d}" / f"{review['review_id'][:16]}.json", review)
    commit_all(root, "records")
    return root


def _git_starts(monkeypatch, root: Path, argv: list[str]) -> list[list[str]]:
    """The git processes a `vl` command starts, by their arguments."""
    import subprocess
    from verifylab.cli import main
    started, real = [], subprocess.Popen

    def counting(args, *a, **kw):
        if isinstance(args, (list, tuple)) and args and str(args[0]).endswith("/git"):
            started.append([str(x) for x in args])
        return real(args, *a, **kw)
    gitref.close_readers()
    with monkeypatch.context() as patched:
        patched.setattr(subprocess, "Popen", counting)
        patched.chdir(root)
        main(argv)
    return started


@pytest.mark.parametrize("argv", [["validate"], ["show", "item-000", "item-001"]])
def test_records_are_read_through_one_cat_file_batch_whatever_their_number(research_repo, monkeypatch, capsys, argv):
    root = _records_repo(research_repo, 12)
    starts = _git_starts(monkeypatch, root, argv)
    assert [s[s.index("cat-file"):] for s in starts if "cat-file" in s] == [["cat-file", "--batch"]]
    assert sum("ls-tree" in s for s in starts) == 1
    _records_repo(root, 36, start=12)                     # three times the records: no more git processes
    assert len(_git_starts(monkeypatch, root, argv)) == len(starts)


def test_a_failed_batch_read_is_an_error_and_the_next_read_starts_afresh(tmp_path):
    root = _repo(tmp_path)
    _write(root, "a.txt", "first\n")
    _write(root, "b.txt", "second\n")
    commit_all(root, "two files")
    assert gitref.show(root, "trusted", "a.txt") == b"first\n"
    sha = git(root, "rev-parse", "trusted:b.txt").strip()
    loose = root / ".git" / "objects" / sha[:2] / sha[2:]
    loose.chmod(0o644)
    loose.write_bytes(b"not a zlib stream")                     # a corrupt object: git cannot inflate it
    with pytest.raises(gitref.GitError, match="b.txt|" + sha):
        gitref.show(root, "trusted", "b.txt")
    assert gitref.show(root, "trusted", "a.txt") == b"first\n"
    loose.unlink()
    with pytest.raises(gitref.GitError):
        gitref.show(root, "trusted", "b.txt")                   # missing: an error, never an absent file
