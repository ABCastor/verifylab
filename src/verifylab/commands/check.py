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

from .. import __version__, gitref, jail, machine
from ..adapters.base import CheckOutcome, CheckRequest
from ..adapters.lean_comparator import LeanComparatorAdapter
from ..adapters.python_eval import PythonEvalAdapter
from ..output import emit, fail
from ..records import (Item, canonical_json, question_digest, receipt_problems, seal_receipt, sha256_hex,
                       write_new_json)
from . import open_repo, seconds
from ..repo import EVIDENCE, Repo

# The checkers, imported directly: a packaging problem is an error of `vl` itself, never a silently missing checker.
ADAPTERS = (LeanComparatorAdapter, PythonEvalAdapter)
# Fields that define the question come from the trusted item; the rest is the candidate's answer.
TRUSTED_FIELDS = {"lean": ("target", "theorems", "witnesses"), "python": ("evaluator",)}
LOG_TAIL = 4000
EXIT_PASS, EXIT_FAIL, EXIT_TOOL = 0, 1, 3


def register(sub):
    p = sub.add_parser("check", help="run the item's checker and write a receipt")
    p.add_argument("id", help="item id")
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--protected", dest="assurance", action="store_const", const="protected",
                      help="question (target, theorems, witnesses, evaluator) from the trusted ref (default)")
    mode.add_argument("--explore", dest="assurance", action="store_const", const="exploratory",
                      help="everything from the worktree, in the same jail: never counts as verified")
    p.set_defaults(assurance="protected")
    p.add_argument("--timeout", type=seconds, default=1800.0, help="seconds for the whole check (default 1800)")
    p.add_argument("--keep-scratch", action="store_true", help="keep the scratch directory for debugging")
    p.epilog = ("Protected receipts go to research/evidence/<id>/ and count once committed on the trusted ref. "
                "Exploratory receipts go to .vl-cache/explore/<id>/ (gitignored) and never count. "
                "Each check builds in a fresh scratch directory under $XDG_CACHE_HOME/verifylab/scratch, "
                "deleted afterwards. Exit: 0 pass, 1 fail, 3 error or unsupported (the check ran and is inconclusive), "
                "2 usage or a problem found before any check ran (no such item, no checker, a bad configuration).")
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
    return replace(item, **merged), None


def scratch_root() -> Path:
    base = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "verifylab" / "scratch"
    base.mkdir(parents=True, exist_ok=True)
    return base


def build_receipt(repo: Repo, item: Item, asked: Item, adapter_name: str, assurance: str, trusted_commit: str,
                  outcome: CheckOutcome, started: str, finished: str, memory_cap: dict | None = None,
                  policy: dict | None = None) -> dict:
    """`item` is the worktree item; `asked` the item the adapter checked (question fields from the trusted commit in
    protected mode), whose question the receipt binds to; `memory_cap` what `jail.cap_record` says of its runs;
    `policy` the machine policy it ran under (machine.policy_record)."""
    head = gitref.head(repo.root)
    dirty = sorted(gitref.dirty_paths(repo.root) & set(outcome.files))
    digest_basis = {"files": outcome.files, "trusted_files": outcome.trusted_files, "target": outcome.target}
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
            "trusted_ref": repo.config.trusted_ref,
            "trusted_ref_source": repo.config.trusted_ref_source,
            "trusted_commit": trusted_commit,
            "candidate_head": head,
            "candidate_uncommitted": dirty,
        },
        "target": outcome.target,
        "environment": {**outcome.environment, **(memory_cap or {}), **({"machine_policy": policy} if policy else {})},
        "checked": outcome.checked,
        "command": outcome.command,
        "log_sha256": sha256_hex(outcome.log.encode()),
        "log_tail": outcome.log[-LOG_TAIL:],
        "started_at": started,
        "finished_at": finished,
        "tool_version": f"vl {__version__}",
        **({"extra": outcome.extra} if outcome.extra else {}),
    })


def run(args, guards: frozenset[str] = frozenset()) -> int:
    repo = open_repo(args)
    item = repo.load_item(args.id)
    adapters = [a for a in load_adapters() if a.applies(item)]
    if len(adapters) != 1:
        found = ", ".join(a.name for a in adapters) or "none"
        return fail(f"item '{item.id}' needs exactly one checker ([lean] or [python] table); found: {found}")
    adapter = adapters[0]

    started = datetime.now(timezone.utc).isoformat(timespec="microseconds")
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
        return fail(f"the trusted ref '{repo.config.trusted_ref}' (from {repo.config.trusted_ref_source}) does not "
                    "resolve to a commit")
    asked, problem = question_item(repo, item, args.assurance, trusted_commit)
    scratch = Path(tempfile.mkdtemp(prefix=f"{item.id}-", dir=scratch_root()))
    try:
        if problem:
            outcome = CheckOutcome("unsupported", [problem], {}, {}, {}, {}, {}, [], "")
        else:
            request = CheckRequest(repo=repo, item=asked, assurance=args.assurance, trusted_commit=trusted_commit,
                                   scratch=scratch, timeout=args.timeout, memory_max=repo.config.memory_max,
                                   memory_total=memory_total, guards=guards)
            try:
                outcome = adapter.check(request)
            except Exception as exc:  # an adapter bug is a tool failure, never a verdict
                outcome = CheckOutcome("error", [f"adapter crashed: {type(exc).__name__}: {exc}"],
                                       {}, {}, {}, {}, {}, [], "")
    finally:
        if not args.keep_scratch:
            shutil.rmtree(scratch, ignore_errors=True)
    finished = datetime.now(timezone.utc).isoformat(timespec="microseconds")

    if guards:  # test-only runs with guards disabled must never leave a receipt behind
        emit(args, f"{item.id}: {outcome.verdict} (guards disabled: {', '.join(sorted(guards))}; no receipt)",
             {"item": item.id, "verdict": outcome.verdict, "reasons": outcome.reasons, "receipt": None})
        return EXIT_TOOL

    if not outcome.files:
        # The check stopped before it read any candidate input: there is no answer to bind a receipt to.
        emit(args, f"{item.id}: {outcome.verdict} (no receipt: the check stopped before reading the candidate)\n" +
             "\n".join(f"  - {r}" for r in outcome.reasons),
             {"item": item.id, "verdict": outcome.verdict, "reasons": outcome.reasons, "receipt": None})
        return EXIT_TOOL

    receipt = build_receipt(repo, item, asked or item, adapter.name, args.assurance, trusted_commit, outcome,
                            started, finished, memory_cap, policy)
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
        f"question read from trusted ref {repo.config.trusted_ref} at {trusted_commit[:12]} "
        f"({repo.config.trusted_ref_source}), not the repository's git config vl.trustedRef")
    text = "\n".join([
        f"{item.id}: {outcome.verdict} ({args.assurance}, {adapter.name})",
        *[f"  - {r}" for r in outcome.reasons],
        f"receipt: {rel}",
        meaning,
        *([anchor] if anchor and args.assurance == "protected" else []),
    ])
    emit(args, text, {"item": item.id, "verdict": outcome.verdict, "assurance": args.assurance,
                      "reasons": outcome.reasons, "receipt": rel, "receipt_id": receipt["receipt_id"]})
    if outcome.verdict == "pass":
        return EXIT_PASS
    return EXIT_FAIL if outcome.verdict == "fail" else EXIT_TOOL
