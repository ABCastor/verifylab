"""`vl show ID[@rev] [ID…]`: the result card, its impact, or a compact brief for a subagent.

Reads records directly and derives status; never uses the index.
"""

from __future__ import annotations

import posixpath
import re
from dataclasses import dataclass

from .. import gitref, leanmod
from ..output import emit, fail
from ..records import ID_RE, Item, RecordError, git_blob_sha, parse_item
from ..render import Context, card_data, impact_data, render_brief, render_card, render_impact, short, split_ref
from ..status import meaning, meaning_digest, question_file
from . import open_repo
from ..repo import Repo

REV_RE = re.compile(r"^[0-9a-fA-F]{4,40}$")
DEFAULT_BUDGET = 8000


@dataclass(frozen=True)
class Resolved:
    current: Item               # the item as it is in the worktree now
    shown: Item                 # the revision to show: current, or the older blob that was asked for
    requested: str | None       # the revision prefix given after '@', if any
    note: str | None            # loud message when the shown revision is not what was asked or not current


def parse_spec(spec: str) -> tuple[str, str | None]:
    item_id, rev = split_ref(spec)
    if not ID_RE.match(item_id):
        raise RecordError(f"'{spec}': not an item id (expected ID or ID@rev)")
    if rev is not None and not REV_RE.match(rev):
        raise RecordError(f"'{spec}': revision must be 4 to 40 hex characters (an item blob sha)")
    return item_id, rev.lower() if rev else None


def resolve(repo: Repo, spec: str, strict: bool = False) -> Resolved:
    """Resolve ID or ID@rev. With `strict`, a revision that cannot be found is an error (for records
    that bind to it); otherwise the current revision is shown with a loud note."""
    item_id, rev = parse_spec(spec)
    current = repo.load_item(item_id)
    if rev is None or current.revision.startswith(rev):
        return Resolved(current, current, rev, None)
    full, data = gitref.blob(repo.root, rev)
    problem = None
    if data is None:
        problem = f"revision {rev} of '{item_id}' is not in the git object store"
    else:
        try:
            old = parse_item(data, current.path)
        except RecordError as exc:
            problem = f"blob {full[:12]} is not a revision of '{item_id}' ({exc})"
        else:
            if git_blob_sha(data) != full:
                problem = f"blob {full[:12]} does not hash to itself"
    if problem:
        if strict:
            raise RecordError(problem)
        return Resolved(current, current, rev,
                        f"{problem}; showing the CURRENT revision {current.revision[:12]} instead")
    return Resolved(current, old, rev,
                    f"showing OLD revision {full[:12]} as asked; the current revision is {current.revision[:12]}. "
                    "Status, receipts and reviews are those of the item now.")


def register(sub):
    p = sub.add_parser("show", help="result card: corrections, limits and statement first, then the verdict ladder, status, evidence")
    p.add_argument("ids", nargs="+", metavar="ID[@rev]", help="item id, optionally at a revision (blob sha prefix)")
    p.add_argument("--impact", action="store_true",
                   help="items that use it (transitively) and explanations that cite it, with their status")
    p.add_argument("--brief", action="store_true",
                   help="compact context pack for a subagent; a source's file is named, never read")
    p.add_argument("--no-appraisals", action="store_true",
                   help="omit contextual appraisals for a deliberately fresh reading; keep proof and corrections")
    p.add_argument("--budget", type=int, default=None,
                   help=f"characters for --brief across all ids (default {DEFAULT_BUDGET}); cuts are marked loudly")
    p.add_argument("--snapshot", action="store_true",
                   help="source snapshot: trusted target and in-project definitions with their original comments, "
                        "meaning digest and trusted commit (item narrative and earlier reviews omitted)")
    return p


def _snapshot_packet(repo: Repo, trusted_items: dict[str, Item], spec: str) -> dict:
    """Read trusted source including its comments. Omit the item narrative and prior reviews."""
    item_id, rev = split_ref(spec)
    if rev is not None:
        raise RecordError("--snapshot reads from the trusted commit; omit @rev")
    if item_id not in trusted_items:
        raise RecordError(f"'{item_id}' is not on trusted ref '{repo.config.trusted_ref}'")
    item = trusted_items[item_id]
    path = question_file(item)
    if not path:
        raise RecordError(f"'{item_id}' has no [lean] target or [python] evaluator")
    content = repo.read_trusted(path)
    if content is None:
        raise RecordError(f"'{item_id}': trusted target '{path}' not found at {repo.config.trusted_ref}")
    basis = meaning(repo, item)
    if basis is None:
        raise RecordError(f"'{item_id}' has no computable meaning on {repo.config.trusted_ref}")
    digest = meaning_digest(basis)
    target_text = content.decode("utf-8", "replace")
    definitions: list[dict[str, str]] = []
    if item.lean and isinstance(item.lean.get("target"), str):
        roots = repo.config.lean.roots
        project = posixpath.normpath(repo.config.lean.project.strip() or ".")
        prel = (lambda p: p) if project == "." else (lambda p: f"{project}/{p}")
        header = leanmod.parse_header(leanmod.decode(content, path))
        start = [m for m in header.modules if leanmod.in_roots(m, roots)]
        closure = leanmod.closure(start, roots, lambda p: repo.read_trusted(prel(p)))
        definitions = [
            {"module": mod, "path": prel(leanmod.module_to_path(mod)), "text": leanmod.decode(raw, mod)}
            for mod, raw in closure.modules.items()
        ]
        target_info: dict = {
            "path": path,
            "text": target_text,
            "theorems": [str(t) for t in item.lean.get("theorems") or []],
            "witnesses": [str(w) for w in item.lean.get("witnesses") or []],
        }
    else:
        target_info = {
            "path": path,
            "text": target_text,
            "entry": item.python.get("entry"),
        }
    return {
        "id": item.id,
        "revision": item.revision,
        "trusted_ref": repo.config.trusted_ref,
        "trusted_commit": repo.trusted_commit,
        "meaning_digest": digest,
        "representation": "committed source including comments; no elaboration",
        "target": target_info,
        "definitions": definitions,
    }


def _render_snapshot(packet: dict) -> str:
    t = packet["target"]
    lines = [
        f"SNAPSHOT {packet['id']}@{short(packet['revision'])}",
        f"trusted_commit: {packet['trusted_commit']} (ref: {packet['trusted_ref']})",
        f"meaning_digest: {packet['meaning_digest']}",
        packet["representation"],
    ]
    if "theorems" in t:
        lines.append("theorems: " + (", ".join(t["theorems"]) or "(none)"))
        if t.get("witnesses"):
            lines.append("witnesses: " + ", ".join(t["witnesses"]))
    elif t.get("entry"):
        lines.append(f"entry: {t['entry']}")
    lines += ["", f"--- TARGET: {t['path']} ---", t["text"].rstrip("\n")]
    for d in packet["definitions"]:
        lines += ["", f"--- DEFINITION: {d['module']} ({d['path']}) ---", d["text"].rstrip("\n")]
    return "\n".join(lines)


def _run_snapshot(args, repo: Repo) -> int:
    if not repo.trusted_commit:
        return fail(f"trusted ref '{repo.config.trusted_ref}' does not resolve to a commit")
    trusted_items, _ = repo.trusted_items()
    try:
        packets = [_snapshot_packet(repo, trusted_items, spec) for spec in args.ids]
    except (RecordError, leanmod.LeanModError) as exc:
        return fail(str(exc))
    text = ("\n\n" + "=" * 72 + "\n\n").join(_render_snapshot(p) for p in packets)
    emit(args, text, {"items": packets})
    return 0


def run(args) -> int:
    if args.snapshot:
        conflicts = [name for name, flag in (("--impact", args.impact), ("--brief", args.brief),
                                             ("--budget", args.budget is not None),
                                             ("--no-appraisals", args.no_appraisals)) if flag]
        if conflicts:
            return fail(f"--snapshot cannot be combined with {', '.join(conflicts)}")
        repo = open_repo(args)
        return _run_snapshot(args, repo)
    if args.budget is not None and not args.brief:
        return fail("--budget only applies with --brief")
    if args.no_appraisals and args.impact:
        return fail("--no-appraisals applies to cards or briefs, not --impact")
    if args.brief and args.impact:
        return fail("--brief and --impact are separate views; run them one at a time")
    if args.budget is not None and args.budget < 1:
        return fail("--budget must be a positive number of characters")
    repo = open_repo(args)
    ctx = Context.load(repo)
    resolved = [resolve(repo, spec) for spec in args.ids]

    if args.impact:
        impacts = [impact_data(ctx, r.current.id) for r in resolved]
        emit(args, "\n\n".join(render_impact(d) for d in impacts), {"impact": impacts})
        return 0

    cards = [card_data(ctx, r.shown, r.note) for r in resolved]
    for card, r in zip(cards, resolved):
        card["requested_revision"] = r.requested
        if args.no_appraisals:
            card["appraisals_omitted"] = sum(e["kind"] == "appraisal" for e in card["reviews"])
            card["reviews"] = [e for e in card["reviews"] if e["kind"] != "appraisal"]
    if args.brief:
        budget = args.budget or DEFAULT_BUDGET
        text, meta = render_brief(cards, budget)
        if len(text) > budget:
            text += (f"\n[OVER BUDGET: {len(text)} chars for a budget of {budget}; every requested item keeps "
                     "its header and its truncation marker]")
        emit(args, text, {"brief": text, "budget": budget, "chars": len(text), "items": meta})
        return 0
    emit(args, ("\n\n" + "=" * 72 + "\n\n").join(render_card(c) for c in cards), {"items": cards})
    return 0
