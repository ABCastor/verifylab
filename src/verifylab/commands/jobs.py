"""`vl jobs export | missing | shard`: a job list of protected checks, what is still undone, and one ids file per machine.

No daemon and no network: a machine runs its ids file with `vl check --ids FILE` at the trusted commit the list names,
and its receipts return as files to be admitted. The list and the receipts are read from the trusted commit alone
(TrustedRepo), so a worktree's proposals change neither which jobs exist nor which are done.
"""

from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

from .. import fsio, gitref, jobs
from ..output import emit, fail, listed
from ..repo import TrustedRepo
from . import action_flags, open_repo
from . import batch
from .batch import checkable, read_ids, stale
from .check import load_adapters


def weights(text: str) -> dict[str, float]:
    """An argparse type: `worker-a=3,worker-b=2`, machine names (they name files) with finite positive weights."""
    found: dict[str, float] = {}
    for part in text.split(","):
        name, _, number = part.partition("=")
        try:
            weight = float(number)
        except ValueError:
            raise argparse.ArgumentTypeError(f"not NAME=WEIGHT: {part!r}") from None
        if not jobs.MACHINE.fullmatch(name):
            raise argparse.ArgumentTypeError(f"not a machine name (letters, digits, . _ -): {name!r}")
        if not math.isfinite(weight) or weight <= 0:
            raise argparse.ArgumentTypeError(f"the weight of {name} must be finite and positive, not {number!r}")
        if name in found:
            raise argparse.ArgumentTypeError(f"{name} is given twice")
        found[name] = weight
    return found


def register(sub):
    p = sub.add_parser("jobs", help="a list of protected checks for several machines: export, missing, shard")
    actions = p.add_subparsers(dest="action", required=True)

    def action(name, help_, arg, what, trusted_ref=True):
        a = actions.add_parser(name, help=help_)
        a.add_argument("file", metavar=arg, help=what)
        action_flags(a, trusted_ref=trusted_ref)
        return a

    exp = action("export", "write the job list: item, trusted commit, question digest and expected cost of each check",
                 "OUT.json", "the job list to write")
    which = exp.add_mutually_exclusive_group(required=True)
    which.add_argument("--all", action="store_true", help="every item a protected check can ask (as vl check --all)")
    which.add_argument("--stale", action="store_true", help="the items a check could improve (as vl check --stale)")
    which.add_argument("--ids", metavar="FILE", help="the items listed in FILE, one id per line (# comments), in order")
    mis = action("missing", "list the jobs of a job list that no admitted receipt answers yet", "JOBS.json",
                 "a job list from `vl jobs export`")
    mis.add_argument("--out", metavar="FILE", help="also write those jobs as a job list, to shard again")
    sh = action("shard", "write one ids file per machine, balanced by expected cost", "JOBS.json",
                "a job list from `vl jobs export`", trusted_ref=False)
    sh.add_argument("--weights", type=weights, required=True, metavar="NAME=W,...",
                    help="machines and their relative speed, e.g. worker-a=3,worker-b=2,worker-c=4")
    sh.add_argument("--by", choices=jobs.BY, default="reservation", help="the cost to balance: the memory a check "
                    "reserves (default) or the seconds its newest receipts took; a job without one costs the median")
    sh.add_argument("--out", metavar="DIR", help="where NAME.txt files go (default: beside the job list)")
    return p


def read_list(path: str) -> dict:
    try:
        return jobs.load(Path(path).read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError(f"{path}: cannot read: {exc.strerror}") from exc
    except (ValueError, UnicodeDecodeError) as exc:
        raise ValueError(f"{path}: {exc}") from exc


def select(args, repo: TrustedRepo) -> list[str]:
    """The ids to export: the checkable ones, the stale ones among them, or exactly the listed ones (each must be)."""
    items = repo.trusted_items()[0]
    ids = checkable(repo, items, load_adapters)
    if args.ids:
        wanted = read_ids(args.ids)
        unknown = [i for i in wanted if i not in ids]
        if unknown:
            raise ValueError(f"--ids: not on the trusted ref with a checker: {listed(unknown)}")
        return wanted
    return [i for i in ids if stale(repo, items[i], items)] if args.stale else ids


def export(args) -> int:
    repo = open_repo(args, cls=TrustedRepo)
    if repo.trusted_commit is None:
        return fail(f"the trusted ref '{repo.config.trusted_ref}' does not resolve to a commit: nothing to export")
    out = Path(args.file)
    if out.is_dir():
        return fail(f"{out}: a folder")
    try:
        ids = select(args, repo)
    except ValueError as exc:
        return fail(str(exc))
    items, checkers = repo.trusted_items()[0], load_adapters()
    adapters = {i: next(a.name for a in checkers if a.applies(items[i])) for i in ids}
    data = jobs.build(repo, ids, adapters)
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
        fsio.replace_files(out.parent, {out.name: jobs.dumps(data)})
    except OSError as exc:
        return fail(f"cannot write {out}: {exc}")
    unknown = sum(j["reservation_bytes"] is None for j in data["jobs"])
    emit(args, f"wrote {out}: {len(ids)} jobs at {data['trust']['commit'][:12]} ({data['digest'][:19]}); "
               f"{unknown} without an expected reservation", {"out": str(out), "digest": data["digest"],
                                                              "trust": data["trust"], "jobs": len(ids)})
    return 0


def missing(args) -> int:
    try:
        data = read_list(args.file)
    except ValueError as exc:
        return fail(str(exc))
    repo = open_repo(args, cls=TrustedRepo)
    todo = jobs.missing(repo, data)
    if args.out:
        try:
            Path(args.out).parent.mkdir(parents=True, exist_ok=True)
            fsio.replace_files(Path(args.out).parent, {Path(args.out).name: jobs.dumps(
                jobs.manifest(data["project"], data["trust"], todo))})
        except OSError as exc:
            return fail(f"cannot write {args.out}: {exc}")
    sys.stderr.write(f"vl: {len(todo)} of {len(data['jobs'])} jobs have no admitted receipt\n")
    emit(args, "\n".join(j["item"] for j in todo),
         {"total": len(data["jobs"]), "missing": [j["item"] for j in todo], "out": args.out})
    return 0


def share(part: float, whole: float) -> str:
    return f"{100 * part / whole:.1f}%" if whole else "-"


def shard(args) -> int:
    try:
        data = read_list(args.file)
    except ValueError as exc:
        return fail(str(exc))
    shards = jobs.shard(data["jobs"], args.weights, args.by)
    cost = jobs.cost_of(data["jobs"], args.by)
    folder = Path(args.out) if args.out else Path(args.file).parent
    total_cost, total_weight = sum(cost.values()), sum(args.weights.values())
    commit = data["trust"].get("commit")
    pin = f"{batch.PIN_PREFIX}{commit}\n" if isinstance(commit, str) and gitref.is_full_sha(commit) else ""
    rows, texts = [], {}
    for name, mine in shards.items():
        load = sum(cost[j["job"]] for j in mine)
        texts[f"{name}.txt"] = (f"# vl jobs shard of {data['digest']}: {name}, {len(mine)} of {len(data['jobs'])} jobs, "
                                f"cost {load:g} by {args.by}\n" + pin + "".join(f"{j['item']}\n" for j in mine))
        rows.append({"machine": name, "weight": args.weights[name], "jobs": len(mine), "cost": load,
                     "file": str(folder / f"{name}.txt")})
    try:
        folder.mkdir(parents=True, exist_ok=True)
        fsio.replace_files(folder, texts)
    except OSError as exc:
        return fail(f"cannot write into {folder}: {exc}")
    lines = [f"{len(data['jobs'])} jobs from {data['digest'][:19]}, balanced by {args.by}:"]
    lines += [f"  {r['machine']:<12} {r['jobs']:>6} jobs  cost {r['cost']:>14g}  {share(r['cost'], total_cost):>6} of the "
              f"cost for {share(r['weight'], total_weight)} of the weight  {r['file']}" for r in rows]
    emit(args, "\n".join(lines), {"digest": data["digest"], "by": args.by, "shards": rows})
    return 0


def run(args) -> int:
    return {"export": export, "missing": missing, "shard": shard}[args.action](args)
