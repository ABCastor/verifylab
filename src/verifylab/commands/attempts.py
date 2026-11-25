"""`vl trial`: a patch-and-build trial at a pinned commit, kept as one immutable JSON record."""
from __future__ import annotations

import argparse
import hashlib
import math
import os
import shlex
import subprocess
import tempfile
import time
import tomllib
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from .. import attempt_files, gitref, leanmod
from ..foreign import Checkout, client_sources, library_sources
from ..output import emit
from ..records import RecordError, pretty_json, sha256_hex


def dollars(value):
    number = float(value)
    if not math.isfinite(number) or number < 0:
        raise argparse.ArgumentTypeError("cost must be finite and nonnegative")
    return number


def register(sub):
    p = sub.add_parser("trial", aliases=["simplify"], help="compare pinned and patched builds with an explicit runner")
    p.add_argument("lock", type=Path, help="lockfile written by vl pin")
    output = p.add_mutually_exclusive_group(required=True)
    output.add_argument("--out", type=Path, help="new JSON trial record")
    output.add_argument("--attempt", type=Path, metavar="FILE",
                        help="associate this run with an open Markdown attempt; write FILE's .trial.json sidecar")
    output.add_argument("--plan", action="store_true", help="preview exact --downstream targets without a runner")
    p.add_argument("--by", help="person or agent responsible (default: sole forecaster with --attempt)")
    p.add_argument("--model", help="model used, or none (default with --attempt: none)")
    p.add_argument("--cost-usd", type=dollars, help="known total monetary cost; omitted means unmeasured")
    p.add_argument("--json", action="store_true", help="print versioned JSON instead of text")
    p.add_argument("--patch", type=Path, required=True, help="unified Git diff against the locked tree")
    p.add_argument("--targets", nargs="+", required=True, help="named Lean module or Lake library targets")
    p.add_argument("--downstream", action="store_true",
                   help="rebuild affected TOML library modules and explicitly named client modules")
    p.add_argument("--runner", type=Path,
                   help="path to a trusted executable receiving DIR TARGET... and isolating foreign builds")
    p.set_defaults(handler=run)


def publish(path, data):
    """A completed attempt becomes visible once. Concurrent writers cannot replace one another."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, mode="w", encoding="utf-8") as tmp:
        tmp.write(pretty_json(data))
        tmp.flush()
        try:
            os.link(tmp.name, path)
        except FileExistsError:
            raise RecordError(f"attempt already exists: {path}") from None


BUILD_CONFIG = ("lakefile.toml", "lakefile.lean", "lake-manifest.json", "lean-toolchain")


def build_context(project, path):
    """Lean sources, Lake configuration and anything under a non-root library source directory."""
    if path.suffix == ".lean" or path.as_posix() in BUILD_CONFIG or path.parts[:1] == (".lake",):
        return True
    config = gitref.show(project.root, project.commit, "lakefile.toml")
    try:
        libraries = leanmod.lean_libraries(config) if config is not None else []
    except leanmod.LeanModError:
        return True
    return any(Path(lib.src_dir).parts and Path(lib.src_dir) != Path(".")
               and path.parts[:len(Path(lib.src_dir).parts)] == Path(lib.src_dir).parts for lib in libraries)


def resolve_link(path, links, files, dirs):
    """Follow a pinned link component by component. None when it escapes, dangles or loops."""
    pending = list(Path(links[path]).parts) if not Path(links[path]).is_absolute() else None
    if pending is None:
        return None
    current = list(path.parent.parts)
    hops = 0
    while pending:
        part = pending.pop(0)
        if part in ("", "."):
            continue
        if part == "..":
            if not current:
                return None
            current.pop()
            continue
        candidate = Path(*current, part)
        if candidate in links:
            hops += 1
            target = Path(links[candidate])
            if hops > 40 or target.is_absolute():
                return None
            pending = [*target.parts, *pending]
            continue
        current.append(part)
    final = Path(*current) if current else Path(".")
    return final if final in files or final in dirs or final == Path(".") else None


def materialize(project, dest):
    """Copy the literal Git blobs. Ignored files, host filters and worktree edits are outside the base.

    A symbolic link that resolves inside the pinned tree is recreated as the same relative link. An escaping,
    dangling or looping link is left out only when it sits outside the build context; otherwise the trial refuses."""
    dest.mkdir()
    tree = gitref.git(project.root, "ls-tree", "-rz", "--full-tree", project.commit).stdout
    files, links = {}, {}
    for entry in tree.split(b"\0"):
        if not entry:
            continue
        meta, name = entry.split(b"\t", 1)
        mode = meta.split()[0]
        path = Path(os.fsdecode(name))
        if (mode not in (b"100644", b"100755", b"120000") or path.is_absolute()
                or any(part.lower() in (".git", "..") for part in path.parts)):
            raise RecordError(f"{path}: patch trials require regular files or internal links in the pinned tree")
        if mode == b"120000":
            links[path] = os.fsdecode(project.read(path.as_posix()))
        else:
            files[path] = mode
    dirs = {parent for path in [*files, *links] for parent in path.parents if parent != Path(".")}
    kept, omitted = [], []
    for path in sorted(links):
        if resolve_link(path, links, files, dirs) is not None:
            kept.append(path)
        elif build_context(project, path):
            raise RecordError(f"{path}: link escapes or does not resolve inside the pinned tree")
        else:
            omitted.append({"path": path.as_posix(), "target": links[path],
                            "reason": "unresolved or escaping link outside the build context; left out"})
    for path, mode in files.items():
        target = dest / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(project.read(path.as_posix()))
        if mode == b"100755":
            target.chmod(0o755)
    for path in kept:
        (dest / path).parent.mkdir(parents=True, exist_ok=True)
        os.symlink(links[path], dest / path)
    return {"internal_links": [p.as_posix() for p in kept], "omitted_links": omitted}


def build(runner, directory, targets, *, exact_modules=False):
    # A backend must wait for completion and use the child's exit code. Keep large remote logs off the Python heap.
    with tempfile.TemporaryFile() as log:
        started = time.monotonic()
        env = {**os.environ, "VL_TRIAL_EXACT_MODULES": "1"} if exact_modules else None
        try:
            code = subprocess.run([runner, directory, *targets], stdout=log, stderr=subprocess.STDOUT,
                                  check=False, env=env).returncode
        except OSError as exc:
            code = 125
            log.write(f"Could not start the runner: {exc}\n".encode())
        elapsed = time.monotonic() - started
        size = log.tell()
        log.seek(0)
        digest = hashlib.file_digest(log, "sha256").hexdigest()
        log.seek(max(0, size - 8192))
        return {"exit": code, "seconds": round(elapsed, 4), "log_bytes": size,
                "log_sha256": digest, "log_tail": log.read().decode(errors="replace")}


def downstream(project, base, changed, requested, changes):
    """Plan from both sets of source import headers, independently of the worker's artifact inventory."""
    if (base / "lakefile.lean").exists() or not (base / "lakefile.toml").is_file():
        raise RecordError("--downstream needs a static lakefile.toml; for a dynamic layout, pass exact --targets")
    config = (base / "lakefile.toml").read_bytes()
    try:
        libraries = leanmod.lean_libraries(config)
    except leanmod.LeanModError as exc:
        raise RecordError(f"--downstream cannot resolve the Lake layout: {exc}") from exc
    if not libraries:
        raise RecordError("--downstream needs at least one declared TOML library")
    paths = set(gitref.ls_files(project.root, project.commit, ""))
    modules = {}
    for module, path in library_sources(libraries, paths):
        if module in modules and modules[module] != path:
            raise RecordError(f"--downstream: ambiguous source paths for {module}")
        modules[module] = path
    source_dir = tomllib.loads(config.decode()).get("srcDir", ".")
    modules.update(client_sources(source_dir, paths, (name for name in requested if name not in modules)))
    changed_paths = {entry["file"] for entry in changes}
    outside = changed_paths - set(modules.values())
    if outside:
        raise RecordError(f"--downstream expects edits to existing library or named client sources: {', '.join(sorted(outside))}. "
                          "For other edits, pass exact --targets without --downstream")

    graphs = [defaultdict(set), defaultdict(set)]
    edited = set()
    for module, path in sorted(modules.items()):
        original, patched = base / path, changed / path
        if any(file.is_symlink() or not file.is_file() for file in (original, patched)):
            raise RecordError(f"--downstream needs regular source files on both sides: {path}")
        before, after = original.read_bytes(), patched.read_bytes()
        if before != after:
            edited.add(module)
        for reverse, source in zip(graphs, (before, after)):
            try:
                imports = leanmod.parse_header(source.decode(), check_body=False).modules
            except (leanmod.LeanModError, UnicodeError) as exc:
                raise RecordError(f"{path}: cannot read the import header for --downstream: {exc}") from exc
            for dep in imports:
                if dep not in modules and any(lib.is_local(dep) for lib in libraries):
                    raise RecordError(f"{path}: local import {dep} is missing from the trial source inventory")
                reverse[dep].add(module)
    if not edited:
        raise RecordError("--downstream needs an edit to a covered Lean module")
    planned = set(requested)
    for reverse in graphs:
        reached, pending = set(edited), list(edited)
        while pending:
            for importer in reverse[pending.pop()] - reached:
                reached.add(importer)
                pending.append(importer)
        planned.update(reached)
    planned = sorted(planned)
    return {"scope": "pinned TOML libraries and explicitly named clients",
            "requested": requested, "edited": sorted(edited), "planned": planned,
            "base_passed": [], "patched_passed": [], "missing": planned, "complete": False}


def trial(project, args):
    if any(not leanmod.is_valid_module_name(name) for name in args.targets):
        raise RecordError("expected named Lake targets, for example Tiny or Tiny.Step")
    patch = args.patch.read_bytes()
    result = {"targets": args.targets, "patch_sha256": sha256_hex(patch)}
    if not args.plan:
        runner = args.runner.resolve(strict=True)
        if not runner.is_file() or not os.access(runner, os.X_OK):
            raise RecordError("runner must be an executable file")
        result.update(runner=str(runner), runner_sha256=sha256_hex(runner.read_bytes()), patch=patch.decode(),
                      base_build=None, patched_build=None)
    with tempfile.TemporaryDirectory(prefix="vl-simplify-") as tmp:
        base, changed = Path(tmp) / "base", Path(tmp) / "changed"
        result["materialized"] = materialize(project, changed)
        # Anchor git apply to this scratch root even when TMPDIR is inside another repository.
        gitref.git(changed, "init", "-q")
        diff = Path(tmp) / "change.diff"
        diff.write_bytes(patch)
        applied = gitref.git(changed, "apply", "--whitespace=nowarn", "--", str(diff), check=False)
        if applied.returncode:
            diagnostic = applied.stderr.decode(errors="replace").strip()
            if args.plan:
                raise RecordError(f"patch did not apply: {diagnostic}")
            result.update(outcome="dead-road", evidence="patch did not apply", diagnostic=diagnostic)
            return result
        stats = gitref.git(changed, "apply", "--numstat", "-z", "--", str(diff)).stdout
        result["changed_files"] = []
        for line in stats.rstrip(b"\0").split(b"\0"):
            if line:
                added, removed, name = line.split(b"\t", 2)
                result["changed_files"].append({"file": os.fsdecode(name), "added": added.decode(), "removed": removed.decode()})
        materialize(project, base)
        gitref.git(base, "init", "-q")
        coverage = None
        if args.downstream:
            if gitref.git(changed, "apply", "--summary", "--", str(diff)).stdout:
                raise RecordError("--downstream requires existing modules with stable paths and file modes; "
                                  "for structural edits, pass exact --targets without --downstream")
            coverage = result["downstream"] = downstream(project, base, changed, args.targets, result["changed_files"])
            result["targets"] = coverage["planned"]
        if args.plan:
            return result
        result["base_build"] = build(runner, base, result["targets"], exact_modules=args.downstream)
        if result["base_build"]["exit"]:
            return {**result, "outcome": "open", "evidence": "base build failed; patch comparison unavailable"}
        if coverage is not None:
            coverage["base_passed"] = coverage["planned"]
        result["patched_build"] = build(runner, changed, result["targets"], exact_modules=args.downstream)
        if coverage is not None and result["patched_build"]["exit"] == 0:
            coverage.update(patched_passed=coverage["planned"], missing=[], complete=True)
        if result["patched_build"]["exit"] == 125 or result["patched_build"]["exit"] < 0:
            return {**result, "outcome": "open", "evidence": "patched build interrupted or backend unavailable"}
    ok = result["patched_build"]["exit"] == 0
    evidence = "both named builds succeeded" if ok else "patched build failed"
    if ok and args.downstream:
        evidence = "both builds covered the downstream source plan"
    return {**result, "outcome": "result" if ok else "dead-road",
            "evidence": evidence}


def link_attempt(args, project):
    repo = attempt_files.toplevel(args.attempt.parent)
    source = args.attempt.resolve()
    if repo != project.root or not source.is_relative_to(project.root) or source.suffix != ".md":
        raise RecordError(f"{args.attempt}: expected a Markdown attempt inside the pinned checkout")
    commit = gitref.head(repo)
    relative = source.relative_to(repo).as_posix()
    snapshot = gitref.show(repo, commit, relative) if commit else None
    if snapshot is None or source.stat().st_size > attempt_files.MAX_BYTES or snapshot != source.read_bytes():
        raise RecordError(f"{args.attempt}: commit the complete open attempt before running a linked trial")
    record = attempt_files.parse(source, snapshot.decode("utf-8"))
    if record is None or not record.valid or record.outcome is not None:
        check = shlex.join(["vl", "attempts", "check", str(args.attempt.parent)])
        raise RecordError(f"{args.attempt}: expected a valid open Markdown attempt. Run {check}", has_recovery=True)
    base = gitref.rev_parse(project.root, record.data["base"])
    if base != project.commit:
        pin = shlex.join(["vl", "pin", str(repo), "--at", base, "--lock", "base.lock"])
        raise RecordError(f"{args.attempt}: attempt and lock must name the same checkout and base commit. Run {pin}", has_recovery=True)
    if any(entry["basis"] == "TODO: write the basis before committing" for entry in record.data["forecasts"]):
        raise RecordError(f"{args.attempt}: replace the scaffold forecast basis and commit it before the trial")
    authors = {entry["by"] for entry in record.data["forecasts"]}
    if args.by is None and len(authors) == 1:
        args.by = authors.pop()
    if args.model is None:
        args.model = "none"
    args.out = attempt_files.trial_output(source)
    return {"id": record.data["id"], "path": relative, "forecast_commit": commit,
            "forecast_sha256": attempt_files.forecast_fingerprint(record.entries)}


def run(args):
    if args.plan and (not args.downstream or args.runner is not None):
        raise RecordError("--plan requires --downstream and runs without --runner")
    if not args.plan and args.runner is None:
        raise RecordError("supply --runner for a trial, or use --downstream --plan to preview its targets")
    project = Checkout.locked(args.lock)
    if args.plan:
        result = trial(project, args)
        plan = {key: result["downstream"][key] for key in ("scope", "requested", "edited", "planned")}
        emit(args, "\n".join([f"Downstream plan ({plan['scope']}, {len(plan['planned'])} modules):", *plan["planned"]]),
             {"commit": project.commit, "patch_sha256": result["patch_sha256"], "downstream": plan})
        return 0
    linked = link_attempt(args, project) if args.attempt is not None else None
    if args.by is None or args.model is None:
        raise RecordError("supply --by and --model, or link an attempt with a sole forecaster")
    if args.out.exists():
        raise RecordError(f"attempt already exists: {args.out}")
    stamp = datetime.now(timezone.utc).isoformat()
    attempt = {"schema": "vl.attempt/1", "commit": project.commit, "by": args.by, "model": args.model,
               "started_at": stamp, "cost_usd": args.cost_usd, "aim": "simplify", "action": "patch-and-build"}
    if linked is not None:
        attempt["attempt_file"] = linked
    try:
        attempt.update(trial(project, args))
    except (OSError, UnicodeError) as exc:
        attempt.update(outcome="open", targets=args.targets, evidence=f"patch trial could not run: {exc}")
    attempt["finished_at"] = datetime.now(timezone.utc).isoformat()
    publish(args.out, attempt)
    followup = "Review the named builds and check the original contract."
    if attempt["outcome"] != "result":
        failed = attempt.get("patched_build") or attempt.get("base_build") or {}
        followup = (failed.get("log_tail") or attempt.get("diagnostic") or
                    "Inspect the trial record for the patch, targets and runner details.").strip()
    text = [f"{attempt['outcome']}: {attempt['evidence']}", f"Attempt: {args.out}"]
    if (coverage := attempt.get("downstream")) is not None:
        text.append(f"Downstream: {len(coverage['planned'])} planned, {len(coverage['patched_passed'])} "
                    f"confirmed on the patch, {len(coverage['missing'])} without two passing builds.")
    emit(args, "\n".join([*text, followup]), {"attempt": attempt, "path": str(args.out)})
    return {"result": 0, "dead-road": 1, "open": 3}[attempt["outcome"]]
