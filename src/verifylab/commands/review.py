"""`vl review ID[@rev] --kind K --text T --author A`: write one immutable review bound to an item revision.

A review is an attributed judgement with its reason, never evidence of truth. It counts once committed on the
trusted ref: an admitted retraction makes the item `retracted`; a correction is shown first on every card; a
fidelity review decides the MEANING line of the card; a correspondence review (how the formal statement relates to the
paper's: same, stronger, weaker or different) fills the last row of the verdict ladder (ADR 0013).

An author `human:NAME` says that NAME wrote or approved the text. `vl review` writes one only with
`--human-approved`, or after NAME confirms at the terminal: a speed bump against an agent signing for a person,
not a proof of who typed it.
"""

from __future__ import annotations

import fcntl
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from ..output import emit, fail, terminal_text
from .. import gitref
from ..records import (ACKNOWLEDGEABLE, CORRESPONDENCE_OUTCOMES, MEANING_KINDS, REVIEW_KINDS, STATEMENT_CHARS,
                       RecordError, bad_statement, pretty_json, review_problems, seal_review, write_new_json)
from . import open_repo
from ..repo import REVIEWS
from ..status import meaning, meaning_digest, question_file, valid_supersession
from .show import resolve

FIDELITY_VERDICTS = ("faithful", "too-weak", "vacuous", "wrong-definition", "unclear")
# What makes two reviews the same review: a renewed fidelity review of a changed target (or meaning) is a new one.
_CONTENT_KEYS = ("item", "item_revision", "kind", "author", "text", "verdict", "statement", "compare_with",
                 "acknowledges", "target_path", "target_sha256", "meaning_digest", "assessment", "supersedes_review")


def register(sub):
    p = sub.add_parser("review", help="record one immutable review of an item revision")
    p.add_argument("spec", nargs="?", metavar="ID[@rev]",
                   help="item reviewed; fidelity and correspondence require the current trusted revision; other kinds "
                        "can name an older revision")
    p.add_argument("--kind", choices=REVIEW_KINDS, help="type of judgment to record")
    p.add_argument("--text", help="the review itself, with its reason; optional for a structured appraisal")
    p.add_argument("--assessment", metavar="FILE", help="appraisal only: structured JSON contribution with actual context and ratings")
    p.add_argument("--author", help="human:NAME or agent:NAME")
    p.add_argument("--verdict", help=f"fidelity: one of {', '.join(FIDELITY_VERDICTS)}; correspondence: one of "
                   f"{', '.join(CORRESPONDENCE_OUTCOMES)}; other kinds: a short word")
    p.add_argument("--statement", help="correspondence only: which statement of the paper the verdict is about "
                   "(one line); without it the review is about the item's statement as a whole")
    p.add_argument("--compare-with", metavar="ID[@rev]", help="compare only: the other item")
    p.add_argument("--supersedes-review", help="full ID of your earlier fidelity review of the same meaning")
    p.add_argument("--acknowledge", action="append", choices=ACKNOWLEDGEABLE, metavar="FINDING",
                   help="fidelity only: a statement-probe finding the reviewer read and accepts as intended "
                        "(trivial: automation alone closes a target theorem; foundations: it judged what the statement says "
                        "about Choice and the set theory it works in); vl validate warns until one does")
    p.add_argument("--human-approved", action="store_true",
                   help="with --author human:NAME: NAME wrote or approved this text (without it, NAME is asked at "
                        "the terminal, and without a terminal nothing is written)")
    p.add_argument("--expected-meaning-digest", metavar="SHA256",
                   help="fidelity and correspondence writes require the meaning_digest read from vl show --json or "
                        "a review dry run")
    p.add_argument("--dry-run", action="store_true", help="print the review that would be written; write nothing")
    p.add_argument("--emit", metavar="FILE",
                   help="write the validated sealed review to FILE instead of the repository (no lock is opened)")
    p.add_argument("--import", dest="import_file", metavar="FILE",
                   help="import a sealed review written by --emit FILE; use without the item or review creation options")
    p.epilog = ("fidelity and correspondence reviews bind to the meaning on the trusted ref: the target (or evaluator), "
                "the in-project definitions it imports, the Lean semantic environment, the theorems and witnesses, and "
                "the item's statement, limits and assumptions (not its title or body); a change of any of them makes the review stale. Merge the lane first, then "
                "review. Suggested compare verdicts (free text, not enforced): stronger, weaker, equivalent, "
                "incomparable, clearer.")
    return p


def _import_conflict_flags(args) -> list[str]:
    checks = (("ID[@rev]", args.spec is not None), ("--kind", args.kind is not None),
              ("--text", args.text is not None), ("--assessment", args.assessment is not None),
              ("--author", args.author is not None), ("--verdict", args.verdict is not None),
              ("--statement", args.statement is not None), ("--compare-with", args.compare_with is not None),
              ("--acknowledge", bool(args.acknowledge)), ("--supersedes-review", bool(args.supersedes_review)),
              ("--expected-meaning-digest", args.expected_meaning_digest is not None),
              ("--emit", args.emit is not None))
    return [name for name, used in checks if used]


def _usage_problems(args) -> list[str]:
    if args.import_file is not None:
        bad = _import_conflict_flags(args)
        return [f"--import cannot be combined with {', '.join(bad)}"] if bad else []
    problems = []
    if not args.spec:
        problems.append("a review needs ID[@rev]")
    if not args.kind:
        problems.append("a review needs --kind")
    if not args.author:
        problems.append("a review needs --author")
    if args.kind == "appraisal":
        if not args.assessment:
            problems.append("an appraisal needs --assessment FILE")
        if args.verdict is not None:
            problems.append("--verdict is not supported for appraisals; use configured ratings")
    elif args.assessment:
        problems.append("--assessment only applies to --kind appraisal")
    if not args.text and args.kind != "appraisal":
        problems.append("this review needs --text with its reason")
    if args.kind == "fidelity":
        if args.verdict not in FIDELITY_VERDICTS:
            problems.append(f"a fidelity review needs --verdict in {FIDELITY_VERDICTS}")
    elif args.kind == "correspondence":
        if args.verdict not in CORRESPONDENCE_OUTCOMES:
            problems.append(f"a correspondence review needs --verdict in {CORRESPONDENCE_OUTCOMES}")
    elif args.verdict is not None and (not args.verdict.strip() or len(args.verdict) > 64 or "\n" in args.verdict):
        problems.append("--verdict must be a short single-line word or phrase (at most 64 characters)")
    if args.kind == "compare" and not args.compare_with:
        problems.append("a compare review needs --compare-with ID[@rev]")
    if args.kind != "compare" and args.compare_with:
        problems.append("--compare-with only applies to --kind compare")
    if args.expected_meaning_digest is not None and args.kind not in MEANING_KINDS:
        problems.append("--expected-meaning-digest only applies to --kind fidelity or correspondence")
    if args.statement is not None and bad_statement(args.kind, args.statement):
        problems.append(f"--statement is for --kind correspondence: one non-empty line of at most "
                        f"{STATEMENT_CHARS} characters")
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
        sys.stderr.write(terminal_text(f"vl review: the author {args.author} says that {name} wrote or approved this text:\n"
                         f"  {args.text or '(structured contribution)'}\n"
                         + (f"{args.approval_assessment}\n" if getattr(args, "approval_assessment", None) else "")
                         + f"Did {name} write or approve it? [y/N] "))
        sys.stderr.flush()
        if sys.stdin.readline().strip().lower() in ("y", "yes"):
            return None
        return f"{name} did not confirm the text; nothing was written"
    return (f"--author {args.author} says that {name} wrote or approved this text: add --human-approved only when "
            f"{name} did, or sign it agent:<you>; there is no terminal to ask {name}, so nothing was written")


def fidelity_revision_problem(target, basis_item, kind: str = "fidelity") -> str | None:
    if target.requested is not None and target.shown.revision != basis_item.revision:
        return (f"{kind} reviews require the current trusted item revision; the requested revision is different. "
                "Read the admitted target and text, then review the item without @rev or with its trusted revision; "
                "use a correction or understanding review for historical commentary")
    return None


def expected_meaning_problem(args, current_digest: str) -> str | None:
    """The caller supplies the context read; equality does not establish understanding or independence."""
    expected = args.expected_meaning_digest
    if expected is None and not args.dry_run:
        return (f"a {args.kind} write requires --expected-meaning-digest from vl show --json or a review "
                "dry run; read the admitted meaning before recording the judgement")
    if expected is not None and expected != current_digest:
        return ("the expected meaning digest differs from the current trusted meaning; nothing was "
                "written. Read it again and renew the judgement")
    return None


def _write_review_to_repo(args, repo, review: dict, target, reviewed_item) -> int:
    item_id = target.current.id
    kind = review["kind"]
    author = review["author"]
    content = {k: review.get(k) for k in _CONTENT_KEYS}
    path = repo.path(REVIEWS, item_id, f"{review['review_id'][:16]}.json")
    self_review = author == reviewed_item.author
    lock = gitref.common_dir(repo.root) / "vl-review-write.lock"
    with lock.open("a") as guard:
        fcntl.flock(guard, fcntl.LOCK_EX)
        for existing in repo.reviews(item_id):
            if {k: existing.data.get(k) for k in _CONTENT_KEYS} == content:
                return fail(f"an identical review already exists: {existing.path} (reviews are immutable; "
                            "write a new review with different text if your judgement changed)")
        try:
            write_new_json(path, review)
        except FileExistsError:
            return fail(f"{path.relative_to(repo.root)} already exists; reviews are immutable")
    rel = str(path.relative_to(repo.root))
    rev = review["item_revision"]
    revision_note = (" (current trusted revision)" if kind in MEANING_KINDS else
                     " (current revision)" if rev == target.current.revision else " (NOT the current revision)")
    lines = [f"wrote {rel}",
             f"{kind} by {author} on {item_id}@{rev[:12]}" + revision_note,
             f"not admitted until committed on the trusted ref '{repo.config.trusted_ref}'"]
    if self_review:
        lines.append("note: the reviewer is the item's author; this is not an independent review")
    if kind == "retraction":
        lines.append("once admitted, the item's derived status becomes 'retracted'")
    elif kind == "correction":
        lines.append("shown first on every card of this item; it does not change derived status by itself")
    emit(args, "\n".join(lines), {"path": rel, "review": review, "admitted": False})
    return 0


def _import_review(args, repo) -> int:
    src = Path(args.import_file)
    try:
        review = json.loads(src.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return fail(f"cannot read emitted review {src}: {exc}")
    if not isinstance(review, dict):
        return fail(f"emitted review {src} must be a JSON object")
    problems = review_problems(review)
    if not problems and "supersedes_review" in review:
        if not any(r.admitted and not r.problems and r.data.get("review_id") == review["supersedes_review"]
                   and valid_supersession(review, r.data) for r in repo.reviews(review["item"])):
            problems.append("supersession needs your earlier admitted fidelity review of this meaning")
    if problems:
        return fail("review rejected: " + "; ".join(problems))
    target = resolve(repo, f"{review['item']}@{review['item_revision']}", strict=True)
    reviewed_item = target.shown
    if "compare_with" in review:
        other = resolve(repo, review["compare_with"], strict=True)
        if other.current.id == target.current.id and other.shown.revision == target.shown.revision:
            return fail("--compare-with names the same item revision as the one reviewed")
    kind = review["kind"]
    if kind in MEANING_KINDS:
        trusted = repo.trusted_item(target.current.id)
        basis_item = trusted if trusted is not None and question_file(trusted) else target.current
        if review["item_revision"] != basis_item.revision:
            return fail(f"{kind} review is for revision {review['item_revision'][:12]}, not current trusted revision "
                        f"{basis_item.revision[:12]}; renew the judgement")
        path = question_file(basis_item)
        if path is None:
            return fail(f"a {kind} review needs an item with a [lean] target or a [python] evaluator")
        recorded = meaning(repo, basis_item)
        if recorded is None:
            return fail(f"{path} is not on the trusted ref '{repo.config.trusted_ref}' yet: merge the lane first, "
                        "then review the target that was actually admitted")
        if (review.get("target_path") != path
                or review.get("target_sha256") != recorded["files"].get(path)
                or review.get("meaning_digest") != meaning_digest(recorded)):
            return fail("the trusted meaning differs from the emitted review; nothing was written. Read it again and "
                        "renew the judgement")
        reviewed_item = basis_item
    content = {k: review.get(k) for k in _CONTENT_KEYS}
    for existing in repo.reviews(target.current.id):
        if {k: existing.data.get(k) for k in _CONTENT_KEYS} == content:
            return fail(f"an identical review already exists: {existing.path} (reviews are immutable; "
                        "write a new review with different text if your judgement changed)")
    self_review = review["author"] == reviewed_item.author
    if args.dry_run:
        emit(args, "dry run, nothing written:\n" + "\n".join(f"  {k}: {v}" for k, v in sorted(review.items()))
             + ("\n  note: the reviewer is the item's author (not an independent review)" if self_review else ""),
             {"dry_run": True, "review": review, "self_review": self_review})
        return 0
    approval = SimpleNamespace(
        author=review["author"],
        human_approved=args.human_approved,
        text=review.get("text", ""),
        approval_assessment=pretty_json(review["assessment"]) if "assessment" in review else None,
    )
    problem = human_approval_problem(approval)
    if problem:
        return fail(problem)
    return _write_review_to_repo(args, repo, review, target, reviewed_item)


def run(args) -> int:
    problems = _usage_problems(args)
    if problems:
        return fail("; ".join(problems))
    repo = open_repo(args)
    if args.import_file is not None:
        return _import_review(args, repo)
    target = resolve(repo, args.spec, strict=True)
    reviewed_item = target.shown
    fields = {
        "item": target.current.id,
        "item_revision": target.shown.revision,
        "kind": args.kind,
        "author": args.author,
        "text": args.text or "",
        "created": datetime.now(timezone.utc).isoformat(timespec="microseconds"),
    }
    if args.kind == "appraisal":
        from ..appraisals import load_assessment
        try:
            fields["assessment"] = load_assessment(args.assessment, repo, target.shown)
        except (RecordError, OSError, UnicodeError) as exc:
            return fail(str(exc))
        from ..records import pretty_json
        args.approval_assessment = pretty_json(fields["assessment"])
    if args.verdict is not None:
        fields["verdict"] = args.verdict
    if args.statement is not None:
        fields["statement"] = args.statement
    if args.acknowledge:
        fields["acknowledges"] = sorted(set(args.acknowledge))
    if args.kind in MEANING_KINDS:
        trusted = repo.trusted_item(target.current.id)
        basis_item = trusted if trusted is not None and question_file(trusted) else target.current
        problem = fidelity_revision_problem(target, basis_item, args.kind)
        if problem:
            return fail(problem)
        path = question_file(basis_item)
        if path is None:
            return fail(f"a {args.kind} review needs an item with a [lean] target or a [python] evaluator")
        recorded = meaning(repo, basis_item)
        if recorded is None:
            return fail(f"{path} is not on the trusted ref '{repo.config.trusted_ref}' yet: merge the lane first, "
                        "then review the target that was actually admitted")
        fields["target_path"] = path
        fields["target_sha256"] = recorded["files"][path]
        fields["meaning"] = recorded
        fields["meaning_digest"] = meaning_digest(recorded)
        problem = expected_meaning_problem(args, fields["meaning_digest"])
        if problem:
            return fail(problem)
        fields["trusted_ref"] = repo.config.trusted_ref
        fields["trusted_commit"] = repo.trusted_commit
        fields["item_revision"] = basis_item.revision
        reviewed_item = basis_item
    if args.compare_with:
        other = resolve(repo, args.compare_with, strict=True)
        if other.current.id == target.current.id and other.shown.revision == target.shown.revision:
            return fail("--compare-with names the same item revision as the one reviewed")
        fields["compare_with"] = f"{other.current.id}@{other.shown.revision}"
    if args.supersedes_review:
        fields["supersedes_review"] = args.supersedes_review
    review = seal_review(fields)
    problems = review_problems(review)
    if not problems and "supersedes_review" in review:
        if not any(r.admitted and not r.problems and r.data.get("review_id") == review["supersedes_review"]
                   and valid_supersession(review, r.data) for r in repo.reviews(review["item"])):
            problems.append("supersession needs your earlier admitted fidelity review of this meaning")
    if problems:
        return fail("review rejected: " + "; ".join(problems))

    content = {k: review.get(k) for k in _CONTENT_KEYS}
    for existing in repo.reviews(target.current.id):
        if {k: existing.data.get(k) for k in _CONTENT_KEYS} == content:
            return fail(f"an identical review already exists: {existing.path} (reviews are immutable; "
                        "write a new review with different text if your judgement changed)")
    self_review = args.author == reviewed_item.author
    if args.dry_run:
        emit(args, "dry run, nothing written:\n" + "\n".join(f"  {k}: {v}" for k, v in sorted(review.items()))
             + ("\n  note: the reviewer is the item's author (not an independent review)" if self_review else ""),
             {"dry_run": True, "review": review, "self_review": self_review})
        return 0
    problem = human_approval_problem(args)
    if problem:
        return fail(problem)
    if args.emit is not None:
        out_path = Path(args.emit)
        try:
            write_new_json(out_path, review)
        except FileExistsError:
            return fail(f"{out_path} already exists; refusing to overwrite an emitted review")
        lines = [f"emitted {out_path}",
                 f"{args.kind} by {args.author} on {target.current.id}@{fields['item_revision'][:12]}",
                 f"not recorded in the repository; pass to an integrator via: vl review --import {out_path}"]
        if self_review:
            lines.append("note: the reviewer is the item's author; this is not an independent review")
        emit(args, "\n".join(lines), {"path": str(out_path), "review": review, "admitted": False, "emitted": True})
        return 0
    return _write_review_to_repo(args, repo, review, target, reviewed_item)
