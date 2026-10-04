"""`vl show ID[@rev] [ID…]`: the result card, its impact, or a compact brief for a subagent.

Reads records directly and derives status; never uses the index.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .. import gitref
from ..output import emit, fail
from ..records import ID_RE, Item, RecordError, git_blob_sha, parse_item
from ..render import Context, card_data, impact_data, render_brief, render_card, render_impact, split_ref
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
    p = sub.add_parser("show", help="result card: corrections and limits first, then statement, status, evidence")
    p.add_argument("ids", nargs="+", metavar="ID[@rev]", help="item id, optionally at a revision (blob sha prefix)")
    p.add_argument("--impact", action="store_true",
                   help="items that use it (transitively) and explanations that cite it, with their status")
    p.add_argument("--brief", action="store_true",
                   help="compact context pack for a subagent; a source's file is named, never read")
    p.add_argument("--budget", type=int, default=None,
                   help=f"characters for --brief across all ids (default {DEFAULT_BUDGET}); cuts are marked loudly")
    return p


def run(args) -> int:
    if args.budget is not None and not args.brief:
        return fail("--budget only applies with --brief")
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
