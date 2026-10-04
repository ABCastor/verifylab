"""`vl lane`: one git worktree per subagent, with a copy-on-write view of the shared build cache.

A lane never gets its own Mathlib: `vl lane exec` mounts the shared build cache (machine.toml `[caches]` for the
main checkout, else `[lean] cache`, else the main checkout's `<lean project>/.lake`) read-only as the lower layer of
an overlay whose upper layer belongs to the lane. Rebuilt modules cost megabytes, the shared cache cannot be
written, and the command runs in the jail without network.

A lane belongs to one repository: it records git's common directory when it is made, and lives in a folder of
`[lanes] dir` that is that repository's own, so `list`, `exec` and `close` never reach another repository's lane,
also when sibling repositories share the default `../.vl-lanes`.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import posixpath
import shutil
import tempfile
import stat
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .. import gitref, jail, machine
from ..config import Config
from ..output import emit, fail
from ..records import ID_RE
from . import TRUSTED_REF_HELP, open_repo, seconds
from ..repo import Repo

# `vl lane exec` hands the command's output and exit code to the caller as they are: vl's JSON never mixes with them.
EXEC_JSON = ("refused: `vl lane exec` passes the command's own output and exit code through unchanged and never wraps "
             "them in vl's JSON")


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
    close = actions.add_parser("close", help="remove the lane worktree and its overlay")
    close.add_argument("name")
    close.add_argument("--force", action="store_true", help="remove even with uncommitted changes")
    close.add_argument("--discard-ignored", action="store_true",
                       help="also delete the lane's gitignored files (they are listed); without it they refuse the close")
    for name, action in actions.choices.items():
        # SUPPRESS: an action that is not given --json leaves the value of `vl lane --json` as it is, so the flag means
        # the same before or after the action.
        action.add_argument("--json", action="store_true", default=argparse.SUPPRESS,
                            help=EXEC_JSON if name == "exec" else "print versioned JSON instead of text")
        action.add_argument("--trusted-ref", metavar="REF", help=TRUSTED_REF_HELP)
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

    def overlay(self) -> tuple[Path, Path]:
        return overlay_dirs(self.folder, self.name)


def owner_problem(config: Config, meta: dict, strict: bool = True) -> str | None:
    """Why the lane `meta` describes is not this repository's, or None. A lane records git's common directory of the
    repository that made it (`repository`), and must name this one; one made before lanes recorded it counts as this
    repository's only while git lists its path among this repository's worktrees. `strict` (exec and close): a lane
    folder that exists must be such a worktree in either case."""
    name, path, recorded = meta.get("name"), Path(str(meta.get("path"))).resolve(), meta.get("repository")
    if recorded is not None and recorded != repository(config):
        return f"lane '{name}' belongs to the repository {recorded}, not to this one ({repository(config)})"
    if recorded is None or (strict and path.exists()):
        if path not in gitref.worktrees(config.root):
            return (f"lane '{name}': git does not list {path} among this repository's worktrees, so it may belong to "
                    "another repository that shares this lanes folder; it is left as it is")
    return None


def _read(folder: Path, name: str) -> dict | None:
    path = meta_path(folder, name)
    return json.loads(path.read_text()) if path.is_file() else None


def owned_lanes(config: Config) -> list[Lane]:
    """The open lanes of this repository: those in its own folder that record it, and older ones of the shared folder
    whose path git lists among its worktrees. Another repository's lanes are never among them."""
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
    """Names of this repository's open lanes (created and not yet closed): what `[lanes] max_parallel` counts."""
    return sorted(lane.name for lane in owned_lanes(config))


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


def _new(args, repo: Repo) -> int:
    if not ID_RE.match(args.name):
        return fail(f"lane name must match {ID_RE.pattern}")
    folder = lanes_dir(repo.config)
    folder.mkdir(parents=True, exist_ok=True)
    # Count the open lanes and create the new one under one lock: two agents asking for the last free lane at
    # once must not both get it (the count is the lane metadata, written last).
    with open(folder / ".lanes.lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        return _create(args, repo)


def _create(args, repo: Repo) -> int:
    config = repo.config
    folder = lanes_dir(config)
    open_ = open_lanes(config)
    if args.name in open_ or meta_path(folder, args.name).exists():
        return fail(f"lane '{args.name}' already exists")
    if len(open_) >= config.max_parallel:
        return fail(f"{len(open_)} lanes are open ({', '.join(open_)}) and [lanes] max_parallel is "
                    f"{config.max_parallel}: close one with `vl lane close NAME` before opening another")
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
        meta_path(folder, args.name).write_text(json.dumps(meta, indent=2) + "\n")
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
        rows.append({**meta, "exists": exists, "head": head, "uncommitted": dirty, "overlay_bytes": size})
    text = "\n".join(
        f"{r['name']}: {r['branch']} head {str(r['head'])[:12]} "
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
    meta = entry.meta
    lane = Path(meta["path"])
    upper, default_work = entry.overlay()
    work = work or default_work
    elan = elan_home()
    read_only = [elan] if elan.is_dir() else []
    overlays = ()
    canonical = meta.get("canonical_lake")
    if canonical and Path(canonical).is_dir():
        overlays = ((Path(canonical), upper, work, lane / meta["lean_project"] / ".lake"),)
    env = jail.filter_env(os.environ, jail.DEFAULT_ENV)
    env["PATH"] = f"{elan / 'bin'}:{env.get('PATH', '/usr/bin:/bin')}"
    env["ELAN_HOME"] = str(elan)
    return jail.Jail(workdir=lane, read_write=(upper.parent,), read_only=tuple(read_only),
                     env=env, overlays=overlays)


def _exec(args, repo: Repo) -> int:
    if getattr(args, "json", False):
        return fail(f"--json: {EXEC_JSON}; run it without --json (the command may print JSON of its own)")
    entry = load_lane(repo.config, args.name)
    if not entry.meta.get("canonical_lake") and repo.config.lean.roots:
        return fail("no build cache in the main checkout: build there first (lake exe cache get && lake build)")
    command = args.cmd[1:] if args.cmd and args.cmd[0] == "--" else args.cmd
    upper, _ = entry.overlay()
    # One overlay mount per lane at a time: two mounts sharing an upper layer are undefined, and a
    # work directory still held by a dying mount makes the next one fail with "busy". Serialize on a
    # lock and give every run a fresh, empty work directory on the same filesystem as the upper layer.
    with open(upper.parent / "exec.lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        work = Path(tempfile.mkdtemp(prefix="work-", dir=upper.parent))
        try:
            box = lane_jail(entry, work)
            uncapped = jail.cap_problem(repo.config.memory_max)
            if uncapped and repo.config.memory_max:
                sys.stderr.write(f"vl: warning: this command runs without a memory cap ({uncapped})\n")
            try:
                argv = jail.memory_capped(box.argv(command), repo.config.memory_max,
                                          machine.load().total_memory_cap())
            except RuntimeError as exc:
                return fail(str(exc))
            try:
                proc = subprocess.run(argv, timeout=args.timeout)
            except subprocess.TimeoutExpired:
                return fail(f"lane command timed out after {args.timeout:.0f}s")
            return proc.returncode
        finally:
            _make_writable(work)
            shutil.rmtree(work, ignore_errors=True)


def ignored_files(meta: dict) -> list[str]:
    """Gitignored files in the lane, except the build-cache mount point the lane machinery owns."""
    lake = posixpath.normpath(posixpath.join(meta["lean_project"], ".lake"))
    return [p for p in gitref.ignored_paths(Path(meta["path"])) if p != lake and not p.startswith(lake + "/")]


def _close(args, repo: Repo) -> int:
    """Under the lanes lock (as `lane new`), and only while no command runs in the lane (its exec lock)."""
    config = repo.config
    entry = load_lane(config, args.name)
    folder = lanes_dir(config)
    folder.mkdir(parents=True, exist_ok=True)
    with open(folder / ".lanes.lock", "w") as lanes_lock:
        fcntl.flock(lanes_lock, fcntl.LOCK_EX)
        overlay = entry.overlay()[0].parent
        if not overlay.is_dir():
            return _close_locked(args, repo)
        with open(overlay / "exec.lock", "w") as exec_lock:
            try:
                fcntl.flock(exec_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return fail(f"a command is running in lane '{args.name}' (vl lane exec); wait for it to end, then "
                            "close the lane")
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


def run(args) -> int:
    repo = open_repo(args)
    try:
        return {"new": _new, "list": _list, "exec": _exec, "close": _close}[args.action](args, repo)
    except (LaneError, FileNotFoundError) as exc:
        return fail(str(exc))
