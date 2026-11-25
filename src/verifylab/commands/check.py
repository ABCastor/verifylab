"""`vl check ID`: run the item's checker and write an immutable receipt.

Protected mode takes what defines the question (target path, theorem list and witnesses; evaluator path) from
the item as committed on the trusted ref, and only the candidate's answer (proofs, imports, solution, candidate
code) from the worktree. Exploratory mode takes everything from the worktree and never counts as verified.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from .. import __version__, gitref, jail, jobs, machine, queue
from ..adapters.base import CheckOutcome, CheckRequest
from ..adapters.lean_comparator import LeanComparatorAdapter
from ..adapters.python_eval import PythonEvalAdapter
from ..output import EXIT_FAIL, EXIT_PASS, EXIT_TOOL, emit, fail
from ..records import (Item, canonical_json, question_digest, receipt_problems, seal_receipt, sha256_hex,
                       write_new_json)
from . import open_repo, seconds
from .queue import size
from ..repo import EVIDENCE, EXPLORE_DIR, Repo

# The checkers, imported directly: a packaging problem is an error of `vl` itself, never a silently missing checker.
ADAPTERS = (LeanComparatorAdapter, PythonEvalAdapter)
# Fields that define the question come from the trusted item; the rest is the candidate's answer.
TRUSTED_FIELDS = {"lean": ("target", "theorems", "witnesses"), "python": ("evaluator",)}
LOG_TAIL = 4000


def register(sub):
    p = sub.add_parser("check", help="run the item's checker and write a receipt")
    p.add_argument("id", nargs="?", help="item id (or one of --all, --stale, --ids, --selftest)")
    batch = p.add_mutually_exclusive_group()
    batch.add_argument("--all", action="store_true", help="check every item a protected check can ask: a checker "
                                                          "applies and it is on the trusted ref")
    batch.add_argument("--stale", action="store_true", help="check the items a protected check could improve: "
                                                            "unverified, verified-stale, explored, inconclusive, or "
                                                            "failed on an answer that has since changed")
    batch.add_argument("--ids", metavar="FILE", help="check the items listed in FILE, one id per line (# comments)")
    batch.add_argument("--selftest", action="store_true",
                       help="plant known cheats and a clean control against the project's trusted Lean config")
    p.add_argument("-j", "--jobs", type=int, metavar="N", help="with --all, --stale or --ids: start N checks at a "
                   "time (default: the queue's slots); the machine's queue still decides when each runs")
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--protected", dest="assurance", action="store_const", const="protected",
                      help="question (target, theorems, witnesses, evaluator) from the trusted ref (default)")
    mode.add_argument("--explore", dest="assurance", action="store_const", const="exploratory",
                      help="everything from the worktree, in the same jail: never counts as verified")
    p.set_defaults(assurance="protected")
    p.add_argument("--timeout", type=seconds, default=1800.0, help="seconds for the whole check (default 1800)")
    p.add_argument("--keep-scratch", action="store_true", help="keep the scratch directory for debugging")
    p.add_argument("--reserve", type=size, metavar="SIZE",
                   help="memory the machine's queue reserves for this check (default: the largest peak of the item's "
                        "5 newest admitted or exploratory receipts, else the largest of its checker's in the "
                        "repository, else [queue] default_reservation); its cap stays memory_max")
    p.add_argument("--max-wait", type=seconds, help="give up (exit 2, no receipt) after waiting this many seconds in "
                                                    "the queue")
    p.epilog = ("Protected receipts go to research/evidence/<id>/. To count toward status, a receipt must be "
                "admitted on the trusted ref and still match the current item and inputs. "
                "Exploratory receipts go to .vl-cache/explore/<id>/ (gitignored) and never count. "
                "Each check builds in a fresh scratch directory under $XDG_CACHE_HOME/verifylab/scratch, "
                "normally created after queue admission (vl queue status) and deleted unless --keep-scratch is set. "
                "Exit: 0 pass, 1 fail, 3 when no conclusive result can be reported, 2 usage or a problem found before a "
                "check ran (no such item, no checker, a bad configuration, --max-wait reached). --all, --stale and "
                "--ids run one protected `vl check --json` per item, print one table (item, verdict, seconds, "
                "queue wait) and exit 0 when every check passed, else 1 if any failed, else 3. "
                "--explore, --keep-scratch and --reserve apply to single-item checks.")
    return p


def load_adapters():
    return [adapter() for adapter in ADAPTERS]


def question_item(repo: Repo, item: Item, assurance: str, trusted_commit: str) -> tuple[Item | None, str | None]:
    """The item the adapter sees. In protected mode the question-defining fields come from the trusted commit."""
    if assurance == "exploratory":
        return item, None
    trusted = repo.trusted_item(item.id, trusted_commit)
    if trusted is None:
        return None, (f"item '{item.id}' is not on the trusted ref '{repo.config.trusted_ref}': "
                      "its target must be integrated and reviewed before a protected check")
    merged = {}
    for table, fields in TRUSTED_FIELDS.items():
        current = dict(getattr(item, table))
        trusted_table = getattr(trusted, table)
        if not current and not trusted_table:
            merged[table] = current
            continue
        for key in fields:
            if key in trusted_table:
                current[key] = trusted_table[key]
            else:
                current.pop(key, None)
        merged[table] = current
    merged["imports"] = tuple(dict(entry) for entry in trusted.imports)
    return replace(item, **merged), None


def _peaks(repo: Repo, item_id: str) -> list[tuple[str, str, int]]:
    """(finished_at, adapter, memory peak) of the item's admitted or exploratory receipts that record a peak."""
    return [(r.data["finished_at"], r.data["adapter"], peak) for r in repo.receipts(item_id)
            if not r.problems and (r.admitted or r.data["assurance"] == "exploratory")
            and type(peak := r.data.get("extra", {}).get("memory_peak_bytes")) is int and peak > 0]


def last_peak(repo: Repo, item_id: str, adapter: str) -> int | None:
    """What the queue reserves for a check (jobs.reservation): the largest memory peak of the item's jobs.RECENT
    newest admitted or exploratory receipts; for an item with none, the largest of the repository's receipts from the
    same checker; else None ([queue] default_reservation)."""
    def others():
        explored = repo.root / EXPLORE_DIR
        for other in sorted({*repo.record_folders(EVIDENCE), *(d.name for d in explored.glob("*/"))} - {item_id}):
            yield from (peak for _, name, peak in _peaks(repo, other) if name == adapter)

    return jobs.reservation([(finished, peak) for finished, _, peak in _peaks(repo, item_id)], others())


def scratch_root() -> Path:
    base = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "verifylab" / "scratch"
    base.mkdir(parents=True, exist_ok=True)
    return base


def build_receipt(repo: Repo, item: Item, asked: Item, adapter_name: str, assurance: str, trusted_commit: str,
                  outcome: CheckOutcome, started: str, finished: str, memory_cap: dict | None = None,
                  policy: dict | None = None, resolved_imports: list | tuple = ()) -> dict:
    """`item` is the worktree item; `asked` the item the adapter checked (question fields from the trusted commit in
    protected mode), whose question the receipt binds to; `memory_cap` what `jail.cap_record` says of its runs;
    `policy` the machine policy it ran under (machine.policy_record)."""
    head = gitref.head(repo.root)
    dirty = sorted(gitref.dirty_paths(repo.root) & set(outcome.files))
    imp_entries = [r.receipt_entry() for r in resolved_imports]
    digest_basis: dict = {"files": outcome.files, "trusted_files": outcome.trusted_files, "target": outcome.target}
    if imp_entries:
        digest_basis["imports"] = imp_entries
    return seal_receipt({
        "item": item.id,
        "item_revision": item.revision,
        "question_digest": question_digest(asked),
        "adapter": adapter_name,
        "assurance": assurance,
        "verdict": outcome.verdict,
        "reasons": outcome.reasons,
        "inputs": {
            "digest": "sha256:" + sha256_hex(canonical_json(digest_basis).encode()),
            "files": outcome.files,
            "trusted_files": outcome.trusted_files,
            **({"imports": imp_entries} if imp_entries else {}),
            "trusted_ref": repo.config.trusted_ref,
            "trusted_ref_source": repo.config.trusted_ref_source,
            "trusted_commit": trusted_commit,
            "candidate_head": head,
            "candidate_uncommitted": dirty,
        },
        "target": outcome.target,
        "environment": {**outcome.environment, **(memory_cap or {}), **({"machine_policy": policy} if policy else {}),
                        "host": machine.host_class(),
                        "environment_class": machine.environment_class(adapter_name, assurance, outcome.environment,
                                                                       memory_cap)},
        "checked": outcome.checked,
        "command": outcome.command,
        "log_sha256": sha256_hex(outcome.log.encode()),
        "log_tail": outcome.log[-LOG_TAIL:],
        "started_at": started,
        "finished_at": finished,
        "tool_version": f"vl {__version__}",
        **({"extra": outcome.extra} if outcome.extra else {}),
    })


class Refused(Exception):
    """A problem found before any check ran: no checker, no trusted commit, a wait past `--max-wait`."""


def execute(repo: Repo, item: Item, assurance: str, timeout: float, reserve: int | None = None,
            max_wait: float | None = None, keep_scratch: bool = False,
            guards: frozenset[str] = frozenset(),
            require_external_kernels: bool = False, historical_single_kernel: bool = False,
            package_stores: tuple[Path, ...] = ()) -> tuple[CheckOutcome, dict | None]:
    """Run the checker of the worktree item `item` once the machine's queue admits it: (outcome, the sealed receipt,
    None when guards are disabled or the check stopped before reading any candidate input). Writes nothing."""
    from .. import imports as vl_imports

    import_guards = guards & vl_imports.GUARDS
    adapter_guards = guards - vl_imports.GUARDS
    adapters = [a for a in load_adapters() if a.applies(item)]
    if len(adapters) != 1:
        found = ", ".join(a.name for a in adapters) or "none"
        raise Refused(f"item '{item.id}' needs exactly one checker ([lean] or [python] table); found: {found}")
    adapter = adapters[0]

    loaded = machine.load()
    memory_total = loaded.total_memory_cap()
    policy = machine.policy_record(loaded)
    for variable, value in policy["overridden_by"].items():
        sys.stderr.write(f"vl: warning: {variable}={value} moves the machine policy (machine.toml, tools store) away "
                         "from the account's default; the receipt records it and a status it verifies says so\n")
    memory_cap = jail.cap_record(repo.config.memory_max, memory_total)
    if memory_cap["memory_cap"] == "none" and repo.config.memory_max:
        sys.stderr.write(f"vl: warning: this check runs without a memory cap ({memory_cap['memory_cap_why']}); "
                         "the receipt records memory_cap: none\n")
    trusted_commit = repo.trusted_commit
    if trusted_commit is None:
        raise Refused(f"the trusted ref '{repo.config.trusted_ref}' (from {repo.config.trusted_ref_source}) does not "
                      "resolve to a commit")
    asked, problem = question_item(repo, item, assurance, trusted_commit)
    resolved_imports = []
    if not problem and asked is not None and asked.imports:
        imp_commit = trusted_commit if assurance == "protected" else None
        resolved_imports, imp_errors = vl_imports.resolve_imports(
            repo, asked, commit=imp_commit, store_dirs=package_stores, guards=import_guards
        )
        if imp_errors:
            problem = "; ".join(imp_errors)
    try:     # a check that runs nothing (the problem below) does not wait for memory
        ticket = queue.Ticket(None, None, None) if problem else queue.admit(
            f"vl check {item.id}", reserve or last_peak(repo, item.id, adapter.name), max_wait)
    except queue.QueueError as exc:
        raise Refused(str(exc)) from None
    started = datetime.now(timezone.utc).isoformat(timespec="microseconds")
    scratch = Path(tempfile.mkdtemp(prefix=f"{item.id}-", dir=scratch_root()))
    try:
        if problem:
            outcome = CheckOutcome("unsupported", [problem], {}, {}, {}, {}, {}, [], "")
        else:
            request = CheckRequest(repo=repo, item=asked, assurance=assurance, trusted_commit=trusted_commit,
                                   scratch=scratch, timeout=timeout, memory_max=repo.config.memory_max,
                                   memory_total=memory_total, threads=loaded.threads(), guards=adapter_guards,
                                   require_external_kernels=require_external_kernels,
                                   historical_single_kernel=historical_single_kernel)
            try:
                with queue.inside(ticket, loaded.threads(), repo.config.memory_max):
                    outcome = adapter.check(request)
            except Exception as exc:  # an adapter bug is a tool failure, never a verdict
                outcome = CheckOutcome("error", [f"adapter crashed: {type(exc).__name__}: {exc}"],
                                       {}, {}, {}, {}, {}, [], "")
    finally:
        ticket.release()
        if not keep_scratch:
            shutil.rmtree(scratch, ignore_errors=True)
    if ticket.waited is not None:
        outcome.extra["queue_wait_seconds"] = ticket.waited
    finished = datetime.now(timezone.utc).isoformat(timespec="microseconds")
    if guards or not outcome.files:
        return outcome, None
    return outcome, build_receipt(repo, item, asked or item, adapter.name, assurance, trusted_commit, outcome,
                                  started, finished, memory_cap, policy, resolved_imports=resolved_imports)


def run(args, guards: frozenset[str] = frozenset()) -> int:
    chosen = [name for name, used in (("ID", args.id), ("--all", args.all), ("--stale", args.stale),
                                      ("--ids", args.ids), ("--selftest", args.selftest)) if used]
    if len(chosen) != 1:
        return fail("name one item ID, or one of --all, --stale, --ids FILE, --selftest"
                    + (f" (got {' and '.join(chosen)})" if chosen else ""))
    if args.jobs is not None and chosen[0] not in ("--all", "--stale", "--ids"):
        return fail("-j: only with --all, --stale or --ids")
    if getattr(args, "_batch_anchor", None):
        if not args.id or args.assurance != "protected" or args.trusted_ref:
            return fail("the internal batch anchor requires one protected item check")
        from ..config import load_config
        repo = Repo(load_config(Path.cwd(), batch_anchor=tuple(args._batch_anchor)))
        for _, message in repo.config.warnings:
            sys.stderr.write(f"vl: warning: {message}\n")
    else:
        repo = open_repo(args)
    if args.selftest:
        if args.assurance != "protected":
            return fail("--selftest runs only in protected mode (without --explore)")
        from . import selftest
        return selftest.run_selftest(args, repo, sys.modules[__name__], guards)
    if not args.id:
        from . import batch      # imported here: it calls back into this module
        return batch.run(args, load_adapters, repo)
    item = repo.load_item(args.id)
    try:
        with jail.terminate_on_sigterm(), jail.watch_coordinator():
            outcome, receipt = execute(repo, item, args.assurance, args.timeout, args.reserve, args.max_wait,
                                       args.keep_scratch, guards)
    except Refused as exc:
        return fail(str(exc))

    if guards:  # test-only runs with guards disabled must never leave a receipt behind
        emit(args, f"{item.id}: {outcome.verdict} (guards disabled: {', '.join(sorted(guards))}; no receipt)",
             {"item": item.id, "verdict": outcome.verdict, "reasons": outcome.reasons, "receipt": None,
              "queue_wait_seconds": outcome.extra.get("queue_wait_seconds")})
        return EXIT_TOOL

    if receipt is None:
        # The check stopped before it read any candidate input: there is no answer to bind a receipt to.
        emit(args, f"{item.id}: {outcome.verdict} (no receipt: the check stopped before reading the candidate)\n" +
             "\n".join(f"  - {r}" for r in outcome.reasons),
             {"item": item.id, "verdict": outcome.verdict, "reasons": outcome.reasons, "receipt": None,
              "queue_wait_seconds": outcome.extra.get("queue_wait_seconds")})
        return EXIT_TOOL

    problems = receipt_problems(receipt)
    if problems:     # vl never writes a receipt that vl validate would reject
        return fail(f"internal error: the receipt of this check would be malformed ({'; '.join(problems)}); none "
                    "was written", EXIT_TOOL)
    name = f"{receipt['receipt_id'][:16]}.json"
    # Exploratory receipts are signal for the worker, never records: they stay in the gitignored cache.
    path = repo.path(EVIDENCE, item.id, name) if args.assurance == "protected" else repo.explore_path(item.id, name)
    write_new_json(path, receipt)
    rel = str(path.relative_to(repo.root))

    if outcome.verdict == "pass" and args.assurance == "protected":
        meaning = "protected pass: commit this receipt on the trusted ref to admit it"
    elif outcome.verdict == "pass":
        meaning = "exploratory pass: useful signal, never counts as verified"
    else:
        meaning = "not verified"
    anchor = None if repo.config.trusted_ref_source == "git config vl.trustedRef" else (
        f"question read from trusted ref {repo.config.trusted_ref} at {repo.trusted_commit[:12]} "
        f"({repo.config.trusted_ref_source}), not the repository's git config vl.trustedRef")
    text = "\n".join([
        f"{item.id}: {outcome.verdict} ({args.assurance}, {receipt['adapter']})",
        *[f"  - {r}" for r in outcome.reasons],
        f"receipt: {rel}",
        meaning,
        *([anchor] if anchor and args.assurance == "protected" else []),
    ])
    emit(args, text, {"item": item.id, "verdict": outcome.verdict, "assurance": args.assurance,
                      "reasons": outcome.reasons, "receipt": rel, "receipt_id": receipt["receipt_id"],
                      "queue_wait_seconds": outcome.extra.get("queue_wait_seconds")})
    if outcome.verdict == "pass":
        return EXIT_PASS
    return EXIT_FAIL if outcome.verdict == "fail" else EXIT_TOOL
