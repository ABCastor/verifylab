"""`vl lane`: one git worktree per subagent, with a copy-on-write view of the shared build cache.

A lane never gets its own Mathlib: `vl lane exec` mounts the shared build cache (machine.toml `[caches]` for the
main checkout, else `[lean] cache`, else the main checkout's `<lean project>/.lake`) read-only as the lower layer of
an overlay whose upper layer belongs to the lane. Rebuilt modules cost megabytes, the shared cache cannot be
written, and the command runs in the jail without network.

A lane belongs to one repository: it records git's common directory when it is made, and lives in a folder of
`[lanes] dir` that is that repository's own, so `list`, `exec`, `done` and `close` never reach another repository's
lane, also when sibling repositories share the default `../.vl-lanes`.

A lane counts toward `[lanes] max_parallel` from `new` until `done` (or `close`): a finished lane frees its slot at
once, while its worktree, branch and overlay stay for the integrator until `close`, and it runs nothing more.
"""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import hashlib
import io
import json
import os
import posixpath
import shutil
import tempfile
import stat
import subprocess
import sys
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .. import fsio, gitref, jail, machine, packages
from ..config import Config
from ..output import emit, fail
from ..records import ID_RE
from . import JSON_HELP, action_flags, open_repo, seconds
from .queue import size
from ..repo import Repo

# `vl lane exec` hands the command's output and exit code to the caller as they are: vl's JSON never mixes with them.
EXEC_JSON = ("refused: `vl lane exec` passes the command's own output and exit code through unchanged and never wraps "
             "them in vl's JSON")


def register_integrate(sub):
    integrate = sub.add_parser(
        "integrate",
        help="validate --incoming, merge the lane branch, and list items needing check or fidelity",
    )
    integrate.add_argument("name", help="lane name (integrates branch lane/NAME)")
    integrate.add_argument("--accept-trusted-change", action="store_true",
                           help="allow integrating a branch that changes trusted inputs (configuration, evaluators, "
                                "Lean build files, or the question of an existing item)")
    integrate.add_argument("--no-merge", action="store_true",
                           help="run incoming validation and report what would need checking, without merging")
    integrate.set_defaults(action="integrate", handler=run)
    return integrate


def register(sub):
    p = sub.add_parser("lane", help="worktrees for parallel subagents with a shared read-only build cache")
    actions = p.add_subparsers(dest="action", required=True)
    new = actions.add_parser("new", help="create a lane: worktree + branch lane/NAME")
    new.add_argument("name")
    new.add_argument("--from", dest="from_ref", default=None, help="start point (default: the trusted ref)")
    actions.add_parser("list", help="list lanes")
    run_ = actions.add_parser("exec", help="run a command inside the lane: no network, shared cache read-only; its "
                                           "output and exit code pass through unchanged")
    run_.add_argument("name")
    run_.add_argument("cmd", nargs="+", help="command after --, e.g. -- lake build Foo")
    run_.add_argument("--timeout", type=seconds, default=3600.0, help="seconds (default 3600)")
    run_.add_argument("--reserve", type=size, metavar="SIZE",
                      help="memory the machine's queue reserves for this command (default: [queue] "
                           "default_reservation); its cap stays memory_max")
    run_.add_argument("--max-wait", type=seconds, help="give up (exit 2) after waiting this many seconds in the "
                                                       "queue (default: --timeout, or [queue] max_wait when shorter)")
    done = actions.add_parser("done", help="mark a finished lane: it stops counting toward [lanes] max_parallel and "
                                           "runs nothing more; its worktree, branch and overlay stay until close")
    done.add_argument("name")
    done.add_argument("--undo", action="store_true", help="count the lane again, only while a slot is free")
    close = actions.add_parser("close", help="remove the lane worktree and its overlay")
    close.add_argument("name")
    close.add_argument("--force", action="store_true", help="remove even with uncommitted changes")
    close.add_argument("--discard-ignored", action="store_true",
                       help="also delete the lane's gitignored files (they are listed); without it they refuse the close")
    register_integrate(actions)
    for name, action in actions.choices.items():
        action_flags(action, EXEC_JSON if name == "exec" else JSON_HELP)
    return p



class LaneError(Exception):
    """A lane that does not exist here, or that belongs to another repository."""


def lanes_root(config: Config) -> Path:
    """`[lanes] dir`, next to the main checkout (also when vl runs from inside a lane). Sibling repositories that
    keep the default `../.vl-lanes` share it, so it only holds one folder per repository (`lanes_dir`)."""
    return (gitref.main_worktree(config.root) / config.lanes_dir).resolve()


def repository(config: Config) -> str:
    """The repository a lane belongs to: git's common directory, the same from the main checkout and its lanes."""
    return str(gitref.common_dir(config.root))


def lanes_dir(config: Config) -> Path:
    """This repository's lanes, metadata and overlays: a folder of `[lanes] dir` named after the main checkout and a
    digest of git's common directory, so two repositories never share a lane name, a count or a lock."""
    common = gitref.common_dir(config.root)
    main = common.parent if common.name == ".git" else common
    return lanes_root(config) / f"{main.name}_{hashlib.sha256(os.fsencode(common)).hexdigest()[:8]}"


def meta_path(folder: Path, name: str) -> Path:
    return folder / f"{name}.vl-lane.json"


def overlay_dirs(folder: Path, name: str) -> tuple[Path, Path]:
    base = folder / ".overlay" / name
    return base / "upper", base / "work"


@dataclass(frozen=True)
class Lane:
    """A lane's metadata and the folder that holds it and its overlay: `lanes_dir`, or the shared `lanes_root` for a
    lane made before lanes recorded their repository."""

    meta: dict
    folder: Path

    @property
    def name(self) -> str:
        return self.meta["name"]

    @property
    def done(self) -> str | None:
        """When the lane was marked done (`vl lane done`), or None while it is open."""
        return self.meta.get("done")

    def overlay(self) -> tuple[Path, Path]:
        return overlay_dirs(self.folder, self.name)


def owner_problem(config: Config, meta: dict, strict: bool = True) -> str | None:
    """Why the lane `meta` describes is not this repository's, or None. A lane records git's common directory of the
    repository that made it (`repository`), and must name this one; one made before lanes recorded it counts as this
    repository's only while git lists its path among this repository's worktrees. `strict` (exec, done, close): a lane
    folder that exists must be such a worktree in either case."""
    name, path, recorded = meta.get("name"), Path(str(meta.get("path"))).resolve(), meta.get("repository")
    if recorded is not None and recorded != repository(config):
        return f"lane '{name}' belongs to the repository {recorded}, not to this one ({repository(config)})"
    if recorded is None or (strict and path.exists()):
        if path not in gitref.worktrees(config.root):
            return (f"lane '{name}': git does not list {path} among this repository's worktrees, so it may belong to "
                    "another repository that shares this lanes folder; it is left as it is")
    if path.exists() and (problem := git_pointer_problem(config, path)):
        # A damaged owned lane is an error, never an omitted lane that silently frees a slot.
        raise LaneError(f"lane '{name}': {problem}")
    return None


def git_pointer_problem(config: Config, path: Path) -> str | None:
    """Check the lane's pointer against the owning repository's administrative backlink without running Git in
    the lane. Neither a replacement .git directory nor a pointer to a candidate-created repository is accepted."""
    pointer = path / ".git"
    try:
        if not stat.S_ISREG(pointer.lstat().st_mode):
            raise ValueError("not a regular file")
        raw = pointer.read_text()
        if not raw.startswith("gitdir: ") or "\n" in raw.rstrip("\n"):
            raise ValueError("invalid pointer")
        target = Path(raw[len("gitdir: "):].strip())
        target = (path / target).resolve()
        registry = gitref.common_dir(config.root) / "worktrees"
        if target.parent != registry:
            raise ValueError("not the owning repository's worktree directory")
        backlink = target / "gitdir"
        if Path(backlink.read_text().strip()).resolve() != pointer:
            raise ValueError("administrative backlink differs")
    except (OSError, ValueError, RuntimeError) as exc:
        return f"git pointer for {path} does not match its registered worktree ({exc}); left untouched"
    return None


def _read(folder: Path, name: str) -> dict | None:
    path = meta_path(folder, name)
    return json.loads(path.read_text()) if path.is_file() else None


def _write(folder: Path, meta: dict) -> None:
    """Write a lane's metadata whole (under the lanes lock): `list` and `exec` read it without the lock."""
    path = meta_path(folder, meta["name"])
    fsio.replace_files(path.parent, {path.name: json.dumps(meta, indent=2) + "\n"})


def owned_lanes(config: Config) -> list[Lane]:
    """The lanes of this repository, open or done: those in its own folder that record it, and older ones of the
    shared folder whose path git lists among its worktrees. Another repository's lanes are never among them."""
    lanes = []
    for folder in (lanes_dir(config), lanes_root(config)):
        for file in sorted(folder.glob("*.vl-lane.json")) if folder.is_dir() else []:
            try:
                meta = json.loads(file.read_text())
            except (OSError, ValueError):
                continue                    # unreadable: it cannot be shown to be this repository's
            if (isinstance(meta, dict) and isinstance(meta.get("name"), str) and isinstance(meta.get("path"), str)
                    and not owner_problem(config, meta, False)):
                lanes.append(Lane(meta, folder))
    return lanes


def open_lanes(config: Config) -> list[str]:
    """Names of this repository's open lanes (created, not marked done, not closed): what `[lanes] max_parallel`
    counts."""
    return sorted(lane.name for lane in owned_lanes(config) if not lane.done)


def load_lane(config: Config, name: str) -> Lane:
    """The lane `name` of this repository; LaneError when there is none, or when it belongs to another repository."""
    for folder in (lanes_dir(config), lanes_root(config)):
        meta = _read(folder, name)
        if meta is not None:
            problem = owner_problem(config, meta)
            if problem:
                raise LaneError(problem)
            return Lane(meta, folder)
    raise LaneError(f"no lane '{name}' in this repository (expected {meta_path(lanes_dir(config), name)})")


def _make_writable(path: Path) -> None:
    """Give the owner rwx on `path` and every directory below it, so that it can be removed. A symbolic link is never
    followed: a link in a lane's cache or overlay must not change the permissions of what it points to."""
    def own(folder: Path) -> bool:
        info = os.lstat(folder)
        if not stat.S_ISDIR(info.st_mode):          # a link (or anything else) is left as it is
            return False
        os.chmod(folder, info.st_mode | stat.S_IRWXU)
        return True

    if not os.path.lexists(path) or not own(path):
        return
    for dirpath, dirnames, _ in os.walk(path):      # never descends into a linked directory
        for d in dirnames:
            own(Path(dirpath) / d)


@contextmanager
def lanes_lock(config: Config):
    """The repository's lanes lock: counting the open lanes and changing what counts (new, done, close) happen
    under it, so two agents asking for the last free slot at once never both get it."""
    folder = lanes_dir(config)
    folder.mkdir(parents=True, exist_ok=True)
    with open(folder / ".lanes.lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


@contextmanager
def idle(entry: Lane):
    """Hold the lane's exec lock without waiting for it: yields False while a command runs in the lane."""
    overlay = entry.overlay()[0].parent
    if not overlay.is_dir():
        yield True
        return
    with open(overlay / "exec.lock", "w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        yield True


def full(config: Config, before: str) -> str | None:
    """Why one more lane cannot count now (every slot of `[lanes] max_parallel` is taken), or None."""
    open_ = open_lanes(config)
    if len(open_) < config.max_parallel:
        return None
    return (f"{len(open_)} lanes are open ({', '.join(open_)}) and [lanes] max_parallel is {config.max_parallel}: "
            f"mark a finished one with `vl lane done NAME` or close one with `vl lane close NAME` before {before}")


def _new(args, repo: Repo) -> int:
    if not ID_RE.match(args.name):
        return fail(f"lane name must match {ID_RE.pattern}")
    unsupported = packages.project_refusal(repo)     # before anything is made, the lanes folder included
    if unsupported:
        return fail(f"unsupported: {unsupported}")
    with lanes_lock(repo.config):           # the count is the lane metadata, written last
        return _create(args, repo)


def _create(args, repo: Repo) -> int:
    config = repo.config
    folder = lanes_dir(config)
    if any(lane.name == args.name for lane in owned_lanes(config)) or meta_path(folder, args.name).exists():
        return fail(f"lane '{args.name}' already exists")
    problem = full(config, "opening another")
    if problem:
        return fail(problem)
    from_ref = args.from_ref or config.trusted_ref
    commit = gitref.rev_parse(repo.root, from_ref)
    lane = folder / args.name
    branch = f"lane/{args.name}"
    # Everything that can fail without touching anything comes first; after the worktree exists, a failure removes
    # the worktree, its branch and its overlay again, so no lane is left half made and uncounted.
    main = gitref.main_worktree(repo.root)
    machine_cache = machine.load().cache_for(main)
    configured = machine_cache or config.lean.cache
    cache = Path(configured).expanduser() if configured else Path(config.lean.project) / ".lake"
    canonical_lake = (cache if cache.is_absolute() else main / cache).resolve()
    proc = gitref.git(repo.root, "worktree", "add", "-q", "-b", branch, str(lane), commit, check=False)
    if proc.returncode != 0:
        return fail(f"git worktree add failed: {proc.stderr.decode(errors='replace').strip()}")
    try:
        lane_lake = lane / config.lean.project / ".lake"
        if config.lean.roots:
            # Guard: a bare `lake` in the lane cannot create .lake (and so cannot download Mathlib into it).
            lane_lake.mkdir(parents=True, exist_ok=True)
            lane_lake.chmod(0o555)
        upper, work = overlay_dirs(folder, args.name)
        upper.mkdir(parents=True, exist_ok=True)
        work.mkdir(parents=True, exist_ok=True)
        meta = {
            "name": args.name, "path": str(lane), "repository": repository(config), "branch": branch,
            "from_ref": from_ref, "from_commit": commit,
            "canonical_lake": str(canonical_lake) if canonical_lake.is_dir() else None,
            "lean_project": config.lean.project,
            "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        _write(folder, meta)
    except BaseException:
        _unwind(repo, folder, args.name, lane, branch)
        raise
    text = "\n".join([
        f"lane {args.name}: {lane}",
        f"branch {branch} at {commit[:12]} ({from_ref})",
        "build and run inside the lane only through:",
        f"  vl lane exec {args.name} -- lake lean <file.lean>   (builds the file's imports, then elaborates it)",
        f"  vl lane exec {args.name} -- lake build <Module>",
        "git (add, commit) runs normally in the lane directory, outside exec.",
    ])
    emit(args, text, {"lane": meta})
    return 0


def _unwind(repo: Repo, folder: Path, name: str, lane: Path, branch: str) -> None:
    """Remove what a failed `lane new` made: its metadata, overlay, worktree and branch."""
    meta_path(folder, name).unlink(missing_ok=True)
    overlay = folder / ".overlay" / name
    _make_writable(overlay)
    shutil.rmtree(overlay, ignore_errors=True)
    _make_writable(lane)
    gitref.git(repo.root, "worktree", "remove", "--force", str(lane), check=False)
    gitref.git(repo.root, "branch", "-D", branch, check=False)


def _list(args, repo: Repo) -> int:
    rows = []
    for entry in owned_lanes(repo.config):
        meta = entry.meta
        lane = Path(meta["path"])
        exists = lane.is_dir()
        head = gitref.head(lane) if exists else None
        dirty = sorted(gitref.dirty_paths(lane)) if exists else []
        upper, _ = entry.overlay()
        size = sum(f.stat().st_size for f in upper.rglob("*") if f.is_file()) if upper.is_dir() else 0
        rows.append({**meta, "done": entry.done, "exists": exists, "head": head, "uncommitted": dirty,
                     "overlay_bytes": size})
    text = "\n".join(
        f"{r['name']}: {r['branch']} head {str(r['head'])[:12]} {'done' if r['done'] else 'open'} "
        f"{'missing' if not r['exists'] else (str(len(r['uncommitted'])) + ' uncommitted') } "
        f"overlay {r['overlay_bytes'] // 1024} KiB  {r['path']}"
        for r in rows
    ) or "no lanes"
    emit(args, text, {"lanes": rows})
    return 0


def elan_home() -> Path:
    """Where the lane's Lean toolchains are: $ELAN_HOME of this call, else ~/.elan."""
    return Path(os.environ.get("ELAN_HOME") or Path.home() / ".elan")


def lane_jail(entry: Lane, work: Path | None = None) -> jail.Jail:
    """Attach the build overlay when the caller supplies its fresh work directory under the lane lock."""
    meta = entry.meta
    lane = Path(meta["path"])
    upper, _ = entry.overlay()
    elan = elan_home()
    read_only = [elan] if elan.is_dir() else []
    overlays = ()
    canonical = meta.get("canonical_lake")
    if work is not None and canonical and Path(canonical).is_dir():
        overlays = ((Path(canonical), upper, work, lane / meta["lean_project"] / ".lake"),)
    env = jail.filter_env(os.environ, jail.DEFAULT_ENV)
    env["PATH"] = f"{elan / 'bin'}:{env.get('PATH', '/usr/bin:/bin')}"
    env["ELAN_HOME"] = str(elan)
    return jail.Jail(workdir=lane, read_write=(upper.parent,), read_only=tuple(read_only), protected=(lane / ".git",),
                     env=env, overlays=overlays)


def _runs_nothing(name: str, done: str) -> str:
    return (f"lane '{name}' is done (since {done}): it no longer counts toward [lanes] max_parallel and runs nothing "
            f"more; `vl lane done {name} --undo` counts it again while a slot is free")


def _run_in_lane(ticket, box, command, memory_max, memory_total, timeout):
    if not box.overlays:
        return ticket.run(jail.memory_capped(box.argv(command), memory_max, memory_total), timeout=timeout)
    # The previous namespace can still hold the upper layer after its launcher exits.
    # Bubblewrap drains the pipe after mounting the overlay and before it can start the command.
    deadline = time.monotonic() + timeout
    for delay in (0, 0.02, 0.1, 0.3, 1, 2):
        if delay:
            if time.monotonic() + delay >= deadline:
                break
            time.sleep(delay)
        read_fd, write_fd = os.pipe()
        with os.fdopen(read_fd, "rb", buffering=0) as setup:
            with os.fdopen(write_fd, "wb") as marker:
                marker.write(b"x")
            argv = jail.memory_capped(box.argv(command, setup_fd=setup.fileno()), memory_max, memory_total)
            proc = ticket.run(argv, timeout=max(0, deadline - time.monotonic()), pass_fds=(setup.fileno(),))
            if proc.returncode != 1 or not setup.read(1):
                return proc
    return proc


def _exec(args, repo: Repo) -> int:
    if getattr(args, "json", False):
        return fail(f"--json: {EXEC_JSON}; run it without --json (the command may print JSON of its own)")
    if not jail.available():
        return fail(f"unsupported: {jail.NO_BWRAP}; `vl lane exec` runs only inside the jail")
    entry = load_lane(repo.config, args.name)
    if entry.done:
        return fail(_runs_nothing(args.name, entry.done))
    if not entry.meta.get("canonical_lake") and repo.config.lean.roots:
        return fail("no build cache in the main checkout: build there first (lake exe cache get && lake build)")
    command = args.cmd[1:] if args.cmd and args.cmd[0] == "--" else args.cmd
    upper, _ = entry.overlay()
    # One overlay mount per lane at a time: two mounts sharing an upper layer are undefined, and a
    # work directory still held by a dying mount makes the next one fail with "busy". Serialize on a
    # lock and give every run a fresh, empty work directory on the same filesystem as the upper layer.
    with open(upper.parent / "exec.lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        done = load_lane(repo.config, args.name).done      # marked while this run waited behind another one
        if done:
            return fail(_runs_nothing(args.name, done))
        work = Path(tempfile.mkdtemp(prefix="work-", dir=upper.parent))
        try:
            box = lane_jail(entry, work)
            uncapped = jail.cap_problem(repo.config.memory_max)
            if uncapped and repo.config.memory_max:
                sys.stderr.write(f"vl: warning: this command runs without a memory cap ({uncapped})\n")
            from ..queue import Queue, admit, inside
            queue = Queue.from_machine()
            max_wait = args.max_wait if args.max_wait is not None else min(args.timeout, queue.max_wait or args.timeout)
            try:
                with admit(f"vl lane exec {args.name}", args.reserve, max_wait, queue) as ticket, inside(ticket):
                    proc = _run_in_lane(ticket, box, command, repo.config.memory_max, machine.load().total_memory_cap(),
                                        args.timeout)
            except subprocess.TimeoutExpired:
                return fail(f"lane command timed out after {args.timeout:.0f}s")
            except RuntimeError as exc:        # QueueError, a slice that cannot be capped
                return fail(str(exc))
            return proc.returncode
        finally:
            _make_writable(work)
            shutil.rmtree(work, ignore_errors=True)


def ignored_files(meta: dict) -> list[str]:
    """Gitignored files in the lane, except the build-cache mount point the lane machinery owns."""
    lake = posixpath.normpath(posixpath.join(meta["lean_project"], ".lake"))
    return [p for p in gitref.ignored_paths(Path(meta["path"])) if p != lake and not p.startswith(lake + "/")]


def _done(args, repo: Repo) -> int:
    """Under the lanes lock (as `lane new`), and only while no command runs in the lane. Marking twice, or undoing an
    open lane, changes nothing."""
    config = repo.config
    with lanes_lock(config):
        entry = load_lane(config, args.name)
        if bool(entry.done) != args.undo:
            state = f"done since {entry.done}" if entry.done else "open"
            emit(args, f"lane {args.name} is already {state}", {"lane": entry.meta})
            return 0
        problem = args.undo and full(config, f"counting lane '{args.name}' again")
        if problem:
            return fail(problem)
        meta = {k: v for k, v in entry.meta.items() if k != "done"}
        if not args.undo:
            meta["done"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with idle(entry) as free:
            if not free:
                return fail(f"a command is queued or running in lane '{args.name}' (vl lane exec); wait for it to "
                            "end, then run this again")
            _write(entry.folder, meta)
        counted = len(open_lanes(config))
    text = (f"lane {args.name} is open again ({counted} of {config.max_parallel} lanes counted)" if args.undo else
            f"lane {args.name} done: {counted} of {config.max_parallel} lanes counted; its worktree, branch "
            f"{meta['branch']} and overlay stay until `vl lane close {args.name}`")
    emit(args, text, {"lane": meta})
    return 0


def _close(args, repo: Repo) -> int:
    """Under the lanes lock (as `lane new`), and only while no command runs in the lane (its exec lock)."""
    config = repo.config
    entry = load_lane(config, args.name)
    with lanes_lock(config), idle(entry) as free:
        if not free:
            return fail(f"a command is queued or running in lane '{args.name}' (vl lane exec); wait for it to end, "
                        "then close the lane")
        return _close_locked(args, repo)


def _close_locked(args, repo: Repo) -> int:
    entry = load_lane(repo.config, args.name)
    meta = entry.meta
    lane = Path(meta["path"])
    ignored: list[str] = []
    if lane.is_dir():
        dirty = gitref.dirty_paths(lane)
        if dirty and not args.force:
            evidence = repo.rel("evidence") + "/"
            receipts = sorted(p for p in dirty if p.startswith(evidence))
            hint = (f"; {len(receipts)} of them are receipts written in the lane, which are never admitted: "
                    "discard them" if receipts else "")
            return fail(f"lane '{args.name}' has uncommitted changes ({len(dirty)} files){hint}:\n"
                        + "\n".join(f"  {p}" for p in sorted(dirty))
                        + "\ncommit what belongs to the research or use --force")
        # `git worktree remove` deletes ignored files silently (exploratory receipts, scratch proofs).
        ignored = ignored_files(meta)
        if ignored and not args.discard_ignored:
            return fail(f"lane '{args.name}' holds {len(ignored)} gitignored file(s) that closing would delete:\n"
                        + "\n".join(f"  {p}" for p in ignored)
                        + "\nmove what you need out of the lane, or pass --discard-ignored")
        _make_writable(lane / meta["lean_project"] / ".lake")
        proc = gitref.git(repo.root, "worktree", "remove", str(lane), *(["--force"] if args.force else []), check=False)
        if proc.returncode != 0:
            return fail(f"git worktree remove failed: {proc.stderr.decode(errors='replace').strip()}")
    overlay_base = entry.overlay()[0].parent
    if overlay_base.exists():
        _make_writable(overlay_base)
        shutil.rmtree(overlay_base)
    meta_path(entry.folder, args.name).unlink()
    text = f"lane {args.name} closed; branch {meta['branch']} kept"
    if ignored:
        text += f"\ndiscarded {len(ignored)} gitignored file(s):\n" + "\n".join(f"  {p}" for p in ignored)
    emit(args, text, {"closed": args.name, "branch": meta["branch"], "discarded_ignored": ignored})
    return 0


def _run_validate_json(branch: str | None, trusted_ref: str | None) -> tuple[int, dict]:
    from . import validate as validate_cmd

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = validate_cmd.run(argparse.Namespace(incoming=branch, json=True, trusted_ref=trusted_ref))
    return rc, json.loads(buf.getvalue())


@contextmanager
def _preview(root, base, tip):
    """Read the prospective merge. Divergent branches need a temporary object store for the merged tree."""
    from ..imports import SnapshotRepo
    for ancestor, descendant in ((base, tip), (tip, base)):
        if gitref.git(root, "merge-base", "--is-ancestor", ancestor, descendant, check=False).returncode == 0:
            yield SnapshotRepo.open_at(root, descendant)
            return
    with tempfile.TemporaryDirectory(prefix="vl-preview-") as folder:
        scratch = Path(folder)
        gitref.git(root, "clone", "--quiet", "--shared", "--bare", "--", str(root), folder)
        merged = gitref.git(scratch, "merge-tree", "--write-tree", base, tip, check=False)
        if merged.returncode:
            raise LaneError("could not preview a clean merge: " + (merged.stderr or merged.stdout).decode(errors="replace"))
        tree = merged.stdout.decode().strip()
        commit = gitref.git(scratch, "-c", "user.name=VerifyLab", "-c", "user.email=preview@localhost",
                            "commit-tree", tree, "-p", base, "-p", tip, "-m", "Preview").stdout.decode().strip()
        yield SnapshotRepo.open_at(scratch, commit)


def _merge_actions(repo, changed):
    from .. import render
    from ..status import meaning
    from .status import follow_up

    ctx = render.Context(repo, repo.trusted_items()[0])
    checks, reviews = [], []
    for name, item in sorted(ctx.items.items()):
        check, review = follow_up(ctx, name)
        if not (check or review):
            continue
        basis = meaning(repo, item) or {}
        affected = item.path in changed or not changed.isdisjoint(basis.get("files", {}))
        if check and (affected or ctx.label(name) == "verified-stale"):
            checks.append(name)
        if review and (affected or ctx.label(name) in ("verified", "verified-stale")):
            reviews.append(name)
    return checks, reviews


def _integrate(args, repo: Repo) -> int:
    if not ID_RE.match(args.name):
        return fail(f"lane name '{args.name}' must match {ID_RE.pattern}")
    folder = lanes_dir(repo.config)
    legacy = lanes_root(repo.config)
    if meta_path(folder, args.name).is_file() or meta_path(legacy, args.name).is_file() or (folder / args.name).exists():
        entry = load_lane(repo.config, args.name)
        branch = entry.meta.get("branch") or f"lane/{args.name}"
    else:
        branch = f"lane/{args.name}"
    if not gitref.ref_exists(repo.root, branch):
        return fail(f"lane '{args.name}' has no branch '{branch}'")
    if not repo.trusted_commit:
        return fail(f"trusted ref '{repo.config.trusted_ref}' does not resolve to a commit")

    head_branch = gitref.current_branch(repo.root)
    if not args.no_merge:
        if not gitref.is_branch(repo.root, repo.config.trusted_ref, head_branch):
            return fail(f"must be on the trusted branch '{repo.config.trusted_ref}' to merge (currently on "
                        f"'{head_branch or 'HEAD'}')")
        dirty = sorted(gitref.dirty_paths(repo.root))
        if dirty:
            return fail(f"working tree has uncommitted changes; commit or stash before integrating: {', '.join(dirty)}")

    tip = gitref.rev_parse(repo.root, branch)
    v_rc, report = _run_validate_json(tip, repo.config.trusted_ref)
    if not report["ok"] or v_rc != 0:
        msgs = "; ".join(f"{e['where']}: {e['message']}" for e in report["errors"])
        return fail(f"refusing to integrate '{branch}' (validation errors): {msgs}")

    trusted_changes = report.get("trusted_changes", [])
    if trusted_changes and not args.accept_trusted_change:
        details = "; ".join(f"{c['path']} ({c['change']}: {c['why']})" for c in trusted_changes)
        return fail(f"refusing to integrate '{branch}': trusted inputs changed ({details}). Inspect the diff and "
                    "re-run with --accept-trusted-change if intended")

    changed = {path for _, path in gitref.changed_files(repo.root, repo.trusted_commit, tip)}
    stale_after_merge = report.get("stale_after_merge", [])
    merged = False
    warnings = list(report.get("warnings", []))
    if not args.no_merge:
        res = gitref.git(repo.root, "merge", "--no-ff", "-m", f"Integrate lane {args.name}", tip, check=False)
        if res.returncode != 0:
            merge_head = gitref.git(repo.root, "rev-parse", "--git-path", "MERGE_HEAD").stdout.decode().strip()
            if (repo.root / merge_head).exists():
                gitref.git(repo.root, "merge", "--abort", check=False)
            err = res.stderr.decode("utf-8", "replace").strip() or res.stdout.decode("utf-8", "replace").strip()
            return fail(f"git merge failed for '{branch}': {err}")
        merged = True
        repo = open_repo(args)
        _, post_report = _run_validate_json(None, None)
        warnings = post_report.get("warnings", warnings)
        from ..imports import SnapshotRepo
        needs_check, needs_fidelity = _merge_actions(SnapshotRepo.open_at(repo.root, repo.trusted_commit), changed)
    else:
        with _preview(repo.root, repo.trusted_commit, tip) as preview:
            needs_check, needs_fidelity = _merge_actions(preview, changed)

    unimported = [w for w in warnings if "no library imports" in w.get("message", "") or "unbuilt" in w.get("message", "").lower()]
    incoming_notes = [i["message"] for i in report.get("incoming", [])]
    lines = [
        f"{'Integrated' if merged else 'Validated (dry run, --no-merge)'} {branch} into {repo.config.trusted_ref}."
    ]
    if trusted_changes:
        lines.append("Accepted trusted changes (--accept-trusted-change): "
                     + ", ".join(f"{c['path']} ({c['change']})" for c in trusted_changes))
    for note in incoming_notes:
        lines.append(f"Incoming: {note}")
    for w in unimported:
        lines.append(f"WARNING {w['where']}: {w['message']}")
    lines.append("Needs check: " + (", ".join(needs_check) if needs_check else "none"))
    lines.append("Needs fidelity: " + (", ".join(needs_fidelity) if needs_fidelity else "none"))
    data = {
        "lane": args.name,
        "branch": branch,
        "trusted_ref": repo.config.trusted_ref,
        "merged": merged,
        "accepted_trusted_change": bool(args.accept_trusted_change),
        "trusted_changes": trusted_changes,
        "incoming": report.get("incoming", []),
        "stale_after_merge": stale_after_merge,
        "needs_check": needs_check,
        "needs_fidelity": needs_fidelity,
        "warnings": warnings,
    }
    emit(args, "\n".join(lines), data)
    return 0


def run(args) -> int:
    repo = open_repo(args)
    try:
        return {
            "new": _new,
            "list": _list,
            "exec": _exec,
            "done": _done,
            "close": _close,
            "integrate": _integrate,
        }[args.action](args, repo)
    except (LaneError, FileNotFoundError) as exc:
        return fail(str(exc))
