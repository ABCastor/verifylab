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


def test_git_disables_fsmonitor_and_hooks_from_repository_config(tmp_path):
    root = _repo(tmp_path)
    marker = tmp_path / "outside-marker"
    script = tmp_path / "callback"
    script.write_text(f"#!/bin/sh\ntouch '{marker}'\n")
    script.chmod(0o755)
    git(root, "config", "core.fsmonitor", str(script))
    assert gitref.dirty_paths(root) == set()
    assert not marker.exists()
    # Prove the planted callback can execute when the command override is absent.
    git(root, "status", "--porcelain")
    assert marker.exists()
    marker.unlink()
    git(root, "config", "core.fsmonitor", "false")
    hooks = tmp_path / "hooks"
    hooks.mkdir()
    (hooks / "pre-commit").write_bytes(script.read_bytes())
    (hooks / "pre-commit").chmod(0o755)
    git(root, "config", "core.hooksPath", str(hooks))
    gitref.git(root, "commit", "--allow-empty", "-qm", "clean control")
    assert not marker.exists()
    git(root, "commit", "--allow-empty", "-qm", "unguarded control")
    assert marker.exists()


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


def test_is_full_sha_accepts_only_a_whole_sha1_or_sha256_in_lower_case():
    assert gitref.is_full_sha("a" * 40) and gitref.is_full_sha("0123456789abcdef" * 4)
    for text in ("a" * 39, "a" * 41, "A" * 40, "a" * 40 + "\n", "a" * 39 + "g", "", "HEAD"):
        assert not gitref.is_full_sha(text), repr(text)


def test_ls_files_lists_a_folder_and_nothing_beside_it(tmp_path):
    """The folder's paths come from one sorted index of the tree: names that sort just before or after the folder
    (`items-old`, `items0`, `items.md`) are not in it, a nested folder is, and a file named like the prefix is."""
    root = _repo(tmp_path)
    for name in ("r/items/a.md", "r/items/sub/b.md", "r/items-old/c.md", "r/items0/d.md", "r/items.md", "r/item"):
        _write(root, name)
    commit_all(root, "tree")
    assert gitref.ls_files(root, "trusted", "r/items") == ["r/items/a.md", "r/items/sub/b.md"]
    assert gitref.ls_files(root, "trusted", "r/items/") == ["r/items/a.md", "r/items/sub/b.md"]
    assert gitref.ls_files(root, "trusted", "r/item") == ["r/item"]
    assert gitref.ls_files(root, "trusted", "r/nothing") == []


def test_a_blob_named_by_its_full_sha_is_read_without_a_git_process_each(tmp_path, monkeypatch):
    import subprocess
    root = _repo(tmp_path)
    for k in range(6):
        _write(root, f"f{k}.txt", f"content {k}\n")
    commit_all(root, "files")
    shas = [git(root, "rev-parse", f"trusted:f{k}.txt").strip() for k in range(6)]
    gitref.close_readers()
    started, real = [], subprocess.Popen

    def counting(args, *a, **kw):
        started.append([str(x) for x in args])
        return real(args, *a, **kw)
    monkeypatch.setattr(subprocess, "Popen", counting)
    assert [gitref.blob(root, sha) for sha in shas] == [(sha, f"content {k}\n".encode()) for k, sha in enumerate(shas)]
    assert [s[s.index("cat-file"):] for s in started] == [["cat-file", "--batch"]]
    assert gitref.blob(root, shas[0][:12]) == (shas[0], b"content 0\n")            # abbreviated: the slow path
    assert gitref.blob(root, "0" * len(shas[0])) == (None, None)                   # missing
    tree = git(root, "rev-parse", "trusted^{tree}").strip()
    assert gitref.blob(root, tree) == (None, None)                                 # not a blob


def test_a_trusted_input_and_an_item_meaning_are_read_once_per_command(research_repo, monkeypatch):
    """Receipts share most of their inputs and fidelity, correspondence and the card each ask for the meaning: on a
    corpus of hundreds of items the second and later reads must cost nothing. The trusted commit does not move
    within a command, so the first read stands; the result handed out is a copy."""
    from verifylab import status
    from verifylab.repo import Repo
    item_file = research_repo / "research/items/add-zero.md"
    item_file.write_text(item_file.read_text().replace(
        "+++\nBody", '\n[lean]\ntarget = "ReceiptFixture.lean"\ntheorems = ["receipt"]\n+++\nBody'))
    commit_all(research_repo, "a target")
    monkeypatch.chdir(research_repo)
    repo = Repo.open()
    reads, real = [], repo.read_trusted
    monkeypatch.setattr(repo, "read_trusted", lambda path: reads.append(path) or real(path))
    assert repo.trusted_digest("Fixture/Basic.lean") == repo.trusted_digest("Fixture/Basic.lean") is not None
    assert reads == ["Fixture/Basic.lean"]
    item = repo.trusted_item("add-zero")
    first = status.meaning(repo, item)
    count = len(reads)
    first["files"].clear()
    assert status.meaning(repo, item)["files"] and len(reads) == count
