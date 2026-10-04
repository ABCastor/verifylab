"""`vl review ID[@rev] --kind K --text T --author A`: write one immutable review bound to an item revision.

A review is an attributed judgement with its reason, never evidence of truth. It counts once committed on the
trusted ref: an admitted retraction makes the item `retracted`; a correction is shown first on every card; a
fidelity review decides the MEANING line of the card.

An author `human:NAME` says that NAME wrote or approved the text. `vl review` writes one only with
`--human-approved`, or after NAME confirms at the terminal: a speed bump against an agent signing for a person,
not a proof of who typed it.
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone

from ..output import emit, fail
from ..records import ACKNOWLEDGEABLE, REVIEW_KINDS, review_problems, seal_review, write_new_json
from . import open_repo
from ..repo import REVIEWS, Repo
from ..status import meaning, meaning_digest, question_file
from .show import resolve

FIDELITY_VERDICTS = ("faithful", "too-weak", "vacuous", "wrong-definition", "unclear")
# What makes two reviews the same review: a renewed fidelity review of a changed target (or meaning) is a new one.
_CONTENT_KEYS = ("item", "item_revision", "kind", "author", "text", "verdict", "compare_with", "acknowledges",
                 "target_path", "target_sha256", "meaning_digest")


def register(sub):
    p = sub.add_parser("review", help="record one immutable review of an item revision")
    p.add_argument("spec", metavar="ID[@rev]", help="item reviewed; fidelity requires the current trusted revision; "
                   "other kinds can name an older revision")
    p.add_argument("--kind", required=True, choices=REVIEW_KINDS)
    p.add_argument("--text", required=True, help="the review itself, with its reason")
    p.add_argument("--author", required=True, help="human:NAME or agent:NAME")
    p.add_argument("--verdict", help=f"fidelity: one of {', '.join(FIDELITY_VERDICTS)}; other kinds: a short word")
    p.add_argument("--compare-with", metavar="ID[@rev]", help="compare only: the other item")
    p.add_argument("--acknowledge", action="append", choices=ACKNOWLEDGEABLE, metavar="FINDING",
                   help="fidelity only: a statement-probe finding the reviewer read and accepts as intended "
                        "(trivial: automation alone closes a target theorem); vl validate warns until one does")
    p.add_argument("--human-approved", action="store_true",
                   help="with --author human:NAME: NAME wrote or approved this text (without it, NAME is asked at "
                        "the terminal, and without a terminal nothing is written)")
    p.add_argument("--dry-run", action="store_true", help="print the review that would be written; write nothing")
    p.epilog = ("fidelity reviews bind to the meaning on the trusted ref: the target (or evaluator), the in-project "
                "definitions it imports, the theorems and witnesses, and the item's statement, limits and assumptions "
                "(not its title or body); a change of any of them makes the review stale. Merge the lane first, then "
                "review. Suggested compare verdicts (free text, not enforced): stronger, weaker, equivalent, "
                "incomparable, clearer.")
    return p


def _usage_problems(args) -> list[str]:
    problems = []
    if args.kind == "fidelity":
        if args.verdict not in FIDELITY_VERDICTS:
            problems.append(f"a fidelity review needs --verdict in {FIDELITY_VERDICTS}")
    elif args.verdict is not None and (not args.verdict.strip() or len(args.verdict) > 64 or "\n" in args.verdict):
        problems.append("--verdict must be a short single-line word or phrase (at most 64 characters)")
    if args.kind == "compare" and not args.compare_with:
        problems.append("a compare review needs --compare-with ID[@rev]")
    if args.kind != "compare" and args.compare_with:
        problems.append("--compare-with only applies to --kind compare")
    if args.acknowledge and args.kind != "fidelity":
        problems.append("--acknowledge only applies to --kind fidelity")
    return problems


def _interactive() -> bool:
    """Whether a person at a terminal can be asked: standard input and standard error are terminals."""
    return sys.stdin.isatty() and sys.stderr.isatty()


def human_approval_problem(args) -> str | None:
    """Why a review signed `human:NAME` cannot be written, or None. The signature says that NAME wrote or approved
    the text: the caller states it with --human-approved, or NAME confirms at the terminal."""
    if not args.author.startswith("human:") or args.human_approved:
        return None
    name = args.author.split(":", 1)[1]
    if _interactive():
        sys.stderr.write(f"vl review: the author {args.author} says that {name} wrote or approved this text:\n"
                         f"  {args.text}\nDid {name} write or approve it? [y/N] ")
        sys.stderr.flush()
        if sys.stdin.readline().strip().lower() in ("y", "yes"):
            return None
        return f"{name} did not confirm the text; nothing was written"
    return (f"--author {args.author} says that {name} wrote or approved this text: add --human-approved only when "
            f"{name} did, or sign it agent:<you>; there is no terminal to ask {name}, so nothing was written")


def fidelity_revision_problem(target, basis_item) -> str | None:
    if target.requested is not None and target.shown.revision != basis_item.revision:
        return ("fidelity reviews require the current trusted item revision; the requested revision is different. "
                "Read the admitted target and text, then review the item without @rev or with its trusted revision; "
                "use a correction or understanding review for historical commentary")
    return None


def run(args) -> int:
    problems = _usage_problems(args)
    if problems:
        return fail("; ".join(problems))
    repo = open_repo(args)
    target = resolve(repo, args.spec, strict=True)
    reviewed_item = target.shown
    fields = {
        "item": target.current.id,
        "item_revision": target.shown.revision,
        "kind": args.kind,
        "author": args.author,
        "text": args.text,
        "created": datetime.now(timezone.utc).isoformat(timespec="microseconds"),
    }
    if args.verdict is not None:
        fields["verdict"] = args.verdict
    if args.acknowledge:
        fields["acknowledges"] = sorted(set(args.acknowledge))
    if args.kind == "fidelity":
        trusted = repo.trusted_item(target.current.id)
        basis_item = trusted if trusted is not None and question_file(trusted) else target.current
        problem = fidelity_revision_problem(target, basis_item)
        if problem:
            return fail(problem)
        path = question_file(basis_item)
        if path is None:
            return fail("a fidelity review needs an item with a [lean] target or a [python] evaluator")
        recorded = meaning(repo, basis_item)
        if recorded is None:
            return fail(f"{path} is not on the trusted ref '{repo.config.trusted_ref}' yet: merge the lane first, "
                        "then review the target that was actually admitted")
        fields["target_path"] = path
        fields["target_sha256"] = recorded["files"][path]
        fields["meaning"] = recorded
        fields["meaning_digest"] = meaning_digest(recorded)
        fields["item_revision"] = basis_item.revision
        reviewed_item = basis_item
    if args.compare_with:
        other = resolve(repo, args.compare_with, strict=True)
        if other.current.id == target.current.id and other.shown.revision == target.shown.revision:
            return fail("--compare-with names the same item revision as the one reviewed")
        fields["compare_with"] = f"{other.current.id}@{other.shown.revision}"
    review = seal_review(fields)
    problems = review_problems(review)
    if problems:
        return fail("review rejected: " + "; ".join(problems))

    content = {k: review.get(k) for k in _CONTENT_KEYS}
    for existing in repo.reviews(target.current.id):
        if {k: existing.data.get(k) for k in _CONTENT_KEYS} == content:
            return fail(f"an identical review already exists: {existing.path} (reviews are immutable; "
                        "write a new review with different text if your judgement changed)")
    path = repo.path(REVIEWS, target.current.id, f"{review['review_id'][:16]}.json")
    self_review = args.author == reviewed_item.author
    if args.dry_run:
        emit(args, "dry run, nothing written:\n" + "\n".join(f"  {k}: {v}" for k, v in sorted(review.items()))
             + ("\n  note: the reviewer is the item's author (not an independent review)" if self_review else ""),
             {"dry_run": True, "review": review, "self_review": self_review})
        return 0
    problem = human_approval_problem(args)
    if problem:
        return fail(problem)
    try:
        write_new_json(path, review)
    except FileExistsError:
        return fail(f"{path.relative_to(repo.root)} already exists; reviews are immutable")
    rel = str(path.relative_to(repo.root))

    rev = fields["item_revision"]
    revision_note = (" (current trusted revision)" if args.kind == "fidelity" else
                     " (current revision)" if rev == target.current.revision else " (NOT the current revision)")
    lines = [f"wrote {rel}",
             f"{args.kind} by {args.author} on {target.current.id}@{rev[:12]}"
             + revision_note]
    lines.append(f"not admitted until committed on the trusted ref '{repo.config.trusted_ref}'")
    if self_review:
        lines.append("note: the reviewer is the item's author; this is not an independent review")
    if args.kind == "retraction":
        lines.append("once admitted, the item's derived status becomes 'retracted'")
    elif args.kind == "correction":
        lines.append("shown first on every card of this item; it does not change derived status by itself")
    emit(args, "\n".join(lines), {"path": rel, "review": review, "admitted": False})
    return 0
