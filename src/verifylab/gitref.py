"""Thin read-only wrappers over git. The trusted ref is read from git objects, never from the worktree.

Every command whose output is parsed as paths runs with `-z`: NUL-separated and unquoted. Without it git
C-quotes names with non-ASCII bytes (`"research/evidence/foo/\\303\\251.json"`), and a quoted path fails
every prefix test, so a receipt with such a name would pass `validate --incoming` unnoticed.

A read of a commit is either the file's bytes, a certain "no such file" (the commit's tree, listed once with
`ls-tree -r -z`, has no blob at that path), or a GitError: a failed read is never taken for an absent file, because
an admitted retraction or failure that cannot be read must not let an older pass count. Git is started by absolute path (never
from the caller's PATH), without the caller's GIT_* variables (which redirect the repository, its objects or its
index) and with `--no-replace-objects`, so a local `git replace` cannot swap the trusted commit for another.

Blobs are read through one `git cat-file --batch` process per repository, kept for the whole command: a command
that reads a thousand records starts one git process for them, not a thousand. An object that the process reports
missing, or a process that ends or answers out of step, is a GitError, and the next read starts a fresh process.
"""

from __future__ import annotations

import atexit
import functools
import os
import re
import subprocess
import tempfile
import threading
from pathlib import Path


class GitError(RuntimeError):
    pass


_FULL_SHA = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")


def environment() -> dict[str, str]:
    """The caller's environment without GIT_* variables (GIT_DIR, GIT_OBJECT_DIRECTORY, GIT_INDEX_FILE, ...)."""
    return {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}


def program() -> str:
    """git by absolute path (machine.toml [launchers] git, else a system folder), never from the caller's PATH."""
    from . import machine
    try:
        path = machine.launcher("git")
    except machine.MachineError as exc:
        raise GitError(f"cannot locate git: {exc}") from exc
    if path is None:
        raise GitError(f"git is not installed in {', '.join(machine.SYSTEM_BIN)} (or set [launchers] git in "
                       f"{machine.config_path()})")
    return path


def git(root: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[bytes]:
    """`git -C root ARGS` by absolute path, with the sanitized environment and replacement objects ignored."""
    proc = subprocess.run([program(), "--no-replace-objects", "-C", str(root), *args], capture_output=True,
                          env=environment())
    if check and proc.returncode != 0:
        raise GitError(f"git {' '.join(args)}: {proc.stderr.decode(errors='replace').strip()}")
    return proc


_git = git


def _rev(ref: str) -> str:
    """A ref or revision taken as data: one starting with '-' would be read by git as an option (`--output=...`)."""
    if not isinstance(ref, str) or not ref or ref.startswith("-"):
        raise GitError(f"refusing the revision {ref!r}: git would read it as an option")
    return ref


def toplevel(path: Path) -> Path:
    return Path(_git(path, "rev-parse", "--show-toplevel").stdout.decode().strip())


def rev_parse(root: Path, ref: str) -> str:
    return _git(root, "rev-parse", "--verify", f"{_rev(ref)}^{{commit}}").stdout.decode().strip()


def ref_exists(root: Path, ref: str) -> bool:
    _rev(ref)
    return _git(root, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}", check=False).returncode == 0


def head(root: Path) -> str | None:
    proc = _git(root, "rev-parse", "--verify", "--quiet", "HEAD", check=False)
    return proc.stdout.decode().strip() if proc.returncode == 0 else None


def _commit(root: Path, ref: str) -> str:
    return ref if _FULL_SHA.match(ref) else rev_parse(root, ref)


@functools.lru_cache(maxsize=64)
def _tree(root: str, commit: str) -> dict[str, tuple[str, str]]:
    """path -> (object type, sha) of every entry of `commit`'s tree, listed once (a commit never changes)."""
    out = _git(Path(root), "ls-tree", "-r", "-z", "--full-tree", f"{_rev(commit)}^{{tree}}").stdout
    entries = {}
    for field in _fields(out):
        meta, _, path = field.partition(b"\t")
        _mode, kind, sha = meta.decode("ascii").split()
        entries[os.fsdecode(path)] = (kind, sha)
    return entries


class _Reader:
    """`git cat-file --batch` in one repository: each read writes an object id to its input and takes back
    "<id> <type> <size>", the content and a newline. Any other answer ends the process and raises GitError."""

    def __init__(self, root: str):
        self.errors = tempfile.TemporaryFile()
        self.lock = threading.Lock()
        self.proc = subprocess.Popen([program(), "--no-replace-objects", "-C", root, "cat-file", "--batch"],
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.errors,
                                     env=environment())

    def read(self, sha: str) -> bytes:
        with self.lock:
            try:
                self.proc.stdin.write(sha.encode("ascii") + b"\n")
                self.proc.stdin.flush()
                header = self.proc.stdout.readline()
                fields = header.split()
                if len(fields) != 3 or fields[0] != sha.encode("ascii") or not fields[2].isdigit():
                    raise GitError(header.decode("utf-8", "replace").strip() or "no answer")
                data = self.proc.stdout.read(int(fields[2]))
                if len(data) != int(fields[2]) or self.proc.stdout.read(1) != b"\n":
                    raise GitError("the content ended early")
            except (GitError, OSError, ValueError) as exc:
                self._end()
                self.errors.seek(0)
                detail = self.errors.read().decode("utf-8", "replace").strip()
                self.errors.close()
                raise GitError(f"git cat-file --batch cannot read {sha}: {exc}" + (f" ({detail})" if detail else ""))
        if fields[1] != b"blob":
            raise GitError(f"{sha} is a {fields[1].decode('ascii', 'replace')}, not a blob")
        return data

    def close(self) -> None:
        self._end()
        self.errors.close()

    def _end(self) -> None:
        if self.proc.poll() is None:
            try:
                self.proc.stdin.close()
                self.proc.wait(timeout=5)
            except (OSError, subprocess.TimeoutExpired):
                self.proc.kill()
                self.proc.wait()
        for stream in (self.proc.stdin, self.proc.stdout):
            stream.close()


_READERS: dict[str, _Reader] = {}
_MAX_READERS = 2      # repositories read in one command: the checkout, sometimes a lane


def _reader(root: str) -> _Reader:
    reader = _READERS.get(root)
    if reader is not None and reader.proc.poll() is not None:     # ended after a failed read: start afresh
        _READERS.pop(root).close()
        reader = None
    if reader is None:
        while len(_READERS) >= _MAX_READERS:
            _READERS.pop(next(iter(_READERS))).close()
        reader = _READERS[root] = _Reader(root)
    return reader


def close_readers() -> None:
    """End the `cat-file --batch` processes (at exit, and in tests that count the processes a command starts)."""
    while _READERS:
        _READERS.popitem()[1].close()


atexit.register(close_readers)


def show(root: Path, ref: str, path: str) -> bytes | None:
    """Content of `path` at `ref`. None only when `ref` names a commit whose tree has no file at `path`; a ref that
    does not resolve, or an object that cannot be read, raises GitError."""
    entry = _tree(str(root), _commit(root, _rev(ref))).get(path)
    if entry is None or entry[0] != "blob":
        return None
    return _reader(str(root)).read(entry[1])


def _fields(out: bytes) -> list[bytes]:
    """The NUL-terminated fields of `-z` output (the empty piece after the last NUL is dropped)."""
    fields = out.split(b"\0")
    return fields[:-1] if fields and not fields[-1] else fields


def ls_files(root: Path, ref: str, prefix: str) -> list[str]:
    """Paths at `ref` under the folder `prefix` (or equal to it); GitError when `ref` cannot be read."""
    folder = prefix.rstrip("/") + "/"
    return sorted(p for p in _tree(str(root), _commit(root, _rev(ref))) if p.startswith(folder) or p == prefix)


def tracked_files(root: Path, prefix: str) -> list[str]:
    """Paths under `prefix` that the worktree's index tracks."""
    return [os.fsdecode(p) for p in _fields(_git(root, "ls-files", "-z", "--", prefix).stdout) if p]


def changed_files(root: Path, base: str, head_ref: str) -> list[tuple[str, str]]:
    """(status, path) for files that differ between the merge base of `base` and `head_ref`. A rename is
    reported as a deletion plus an addition (`--no-renames`), so every record is STATUS NUL PATH NUL."""
    fields = _fields(_git(root, "diff", "--name-status", "--no-renames", "-z", f"{_rev(base)}...{_rev(head_ref)}").stdout)
    return [(fields[k].decode("ascii", "replace"), os.fsdecode(fields[k + 1])) for k in range(0, len(fields) - 1, 2)]


def dirty_paths(root: Path) -> set[str]:
    """Modified, staged and untracked paths; a staged rename counts as both of its paths (`--no-renames` reports
    it as a deletion plus an addition, so every record is "XY PATH" NUL)."""
    fields = _fields(_git(root, "status", "--porcelain", "-z", "--no-renames", "--untracked-files=all").stdout)
    return {os.fsdecode(entry[3:]) for entry in fields}


def ignored_paths(root: Path) -> list[str]:
    """Gitignored untracked files, one by one, also inside ignored directories: what `git worktree remove`
    deletes without asking."""
    out = _git(root, "ls-files", "--others", "--ignored", "--exclude-standard", "-z").stdout
    return sorted(os.fsdecode(p) for p in _fields(out) if p)


def local_config(root: Path, key: str) -> str | None:
    """`key` from the repository's own .git/config (`--local`): environment-injected, global, system and
    per-worktree values are not read, so neither a caller's environment nor a lane can set it for one command."""
    proc = _git(root, "config", "--local", "--get", key, check=False)
    return (proc.stdout.decode().strip() or None) if proc.returncode == 0 else None


def set_local_config(root: Path, key: str, value: str) -> None:
    _git(root, "config", "--local", key, value)


def current_branch(root: Path) -> str | None:
    proc = _git(root, "symbolic-ref", "--quiet", "--short", "HEAD", check=False)
    return (proc.stdout.decode().strip() or None) if proc.returncode == 0 else None


def blob(root: Path, prefix: str) -> tuple[str | None, bytes | None]:
    """Full sha and content of the blob a (possibly abbreviated) sha names, if it is unique."""
    if not prefix or prefix.startswith("-"):
        return None, None
    proc = _git(root, "rev-parse", "--verify", "--quiet", f"{prefix}^{{blob}}", check=False)
    if proc.returncode != 0:
        return None, None
    full = proc.stdout.decode().strip()
    data = _git(root, "cat-file", "blob", full, check=False)
    return (full, data.stdout) if data.returncode == 0 else (full, None)


def common_dir(root: Path) -> Path:
    """Git's common directory of the repository `root` belongs to, absolute and resolved: the same from the main
    checkout and from each of its linked worktrees, and a different one for every repository."""
    return Path(_git(root, "rev-parse", "--path-format=absolute", "--git-common-dir").stdout.decode().strip()).resolve()


def worktrees(root: Path) -> set[Path]:
    """The resolved paths of every worktree git records for the repository `root` belongs to (`git worktree list`):
    the main checkout and each linked worktree, also one whose folder is gone until it is pruned."""
    fields = _fields(_git(root, "worktree", "list", "--porcelain", "-z").stdout)
    return {Path(os.fsdecode(f[len(b"worktree "):])).resolve() for f in fields if f.startswith(b"worktree ")}


def main_worktree(root: Path) -> Path:
    """The main checkout of the repository `root` belongs to (itself, unless `root` is a linked worktree)."""
    common = _git(root, "rev-parse", "--path-format=absolute", "--git-common-dir").stdout.decode().strip()
    path = Path(common)
    return path.parent if path.name == ".git" else path
