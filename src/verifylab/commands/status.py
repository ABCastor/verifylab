"""`vl status`: repository-wide actionable triage across items, fidelity, lanes, and worktree state."""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

from .. import gitref, render
from ..ladder import human_label
from ..output import emit, fail
from ..records import RecordError
from ..status import fidelity, question_file
from . import lane, open_repo

NEEDS_CHECK = ("unverified", "explored", "failed", "check-error", "check-unsupported", "verified-stale")
FAILED_LABELS = ("failed", "vacuous", "check-error", "check-unsupported")


def register(sub):
    p = sub.add_parser(
        "status",
        help="repo-wide actionable triage: pending admission, stale, failed, missing fidelity, worktree edits",
    )
    p.epilog = ("Summarizes counts by derived status and kind, open lanes vs [lanes] max_parallel, commits ahead of "
                "trusted ref and upstream, worktree edits vs the trusted commit, and items needing admission, check, "
                "or fidelity review.")
    return p


def _rev_count(root: Path, rev_range: str) -> int | None:
    res = gitref.git(root, "rev-list", "--count", rev_range, check=False)
    if res.returncode != 0:
        return None
    text = res.stdout.decode("utf-8", "replace").strip()
    return int(text) if text.isdigit() else None


def _worktree_edits(root: Path, trusted_commit: str | None) -> tuple[list[str], list[str]]:
    """Return `(edits_vs_trusted, dirty)` sorted posix path lists."""
    dirty = sorted(gitref.dirty_paths(root))
    edits = set(dirty)
    if trusted_commit:
        res = gitref.git(root, "diff", "--name-only", "-z", trusted_commit, "--", check=False)
        if res.returncode == 0:
            for raw in res.stdout.split(b"\0"):
                if raw:
                    edits.add(raw.decode("utf-8", "replace"))
    return sorted(edits), dirty


def follow_up(ctx, item_id):
    """A check needs an executable binding. A referee can start as soon as the target exists."""
    item = ctx.items[item_id]
    label = ctx.label(item_id)
    if not question_file(item) or label in ("retracted", "refuted"):
        return False, None
    fid = fidelity(ctx.repo, item)
    return label in NEEDS_CHECK, fid.label if fid and fid.label != "faithful" else None


def _triage(ctx: render.Context) -> tuple[dict[str, Any], dict[str, str]]:
    """Collect actionable item lists and a per-item label map for failed/fidelity hints."""
    pending, stale, failed, missing_fidelity, superseded = [], [], [], [], []
    needs_check = []
    hints: dict[str, str] = {}
    for item_id in sorted(ctx.items):
        label = ctx.label(item_id)
        if label == "pending-admission":
            pending.append(item_id)
        elif label == "verified-stale":
            stale.append(item_id)
        elif label in FAILED_LABELS:
            failed.append(item_id)
            hints[item_id] = human_label(label)
        check, review = follow_up(ctx, item_id)
        if check:
            needs_check.append(item_id)
        if review:
            missing_fidelity.append(item_id)
            hints[f"fid:{item_id}"] = review
        successors = ctx.reverse["supersedes"].get(item_id, ())
        if any(ctx.label(s) == "verified" for s in successors):
            superseded.append(item_id)
    return {
        "needs_check": needs_check,
        "pending_admission": pending,
        "stale": stale,
        "failed": failed,
        "missing_fidelity": missing_fidelity,
        "superseded": superseded,
    }, hints


def _format_summary(data: dict[str, Any], hints: dict[str, str]) -> str:
    trust = data["trust"]
    git_info = data["git"]
    lanes_info = data["lanes"]
    counts = data["counts"]
    triage = data["triage"]
    edits = data["worktree"]["edits"]
    problems = data["worktree"]["problems"]

    lines = [
        f"Repository status: {counts['total']} items "
        f"(branch {git_info['branch']}, trusted ref {trust['ref']}"
        + (f", {render.short(trust['commit'])}" if trust["commit"] else "")
        + ")"
    ]
    anchor = render.trust_text(trust)
    if anchor:
        lines.append(f"NOTE: {anchor}")
    ahead_parts = []
    if git_info["ahead_of_trusted"]:
        ahead_parts.append(f"{git_info['ahead_of_trusted']} commit(s) ahead of {trust['ref']}")
    if git_info["ahead_of_upstream"]:
        ahead_parts.append(f"{git_info['ahead_of_upstream']} ahead of upstream")
    if ahead_parts:
        lines.append("Commits: " + "; ".join(ahead_parts))

    if counts["by_status"]:
        status_text = ", ".join(f"{n} {human_label(k)}" for k, n in sorted(counts["by_status"].items()))
        kind_text = ", ".join(f"{n} {k}" for k, n in sorted(counts["by_kind"].items()))
        lines.append(f"By status: {status_text}")
        lines.append(f"By kind: {kind_text}")
    lines.append(
        f"Lanes: {len(lanes_info['open'])}/{lanes_info['max_parallel']} open"
        + (f" ({', '.join(lanes_info['open'])})" if lanes_info["open"] else "")
    )

    sections = [
        ("Pending admission", triage["pending_admission"], lambda i: i),
        ("Needs check", triage["needs_check"], lambda i: i),
        ("Stale", triage["stale"], lambda i: i),
        ("Failed", triage["failed"], lambda i: f"{i} ({hints[i]})"),
        ("Missing fidelity", triage["missing_fidelity"], lambda i: f"{i} ({hints[f'fid:{i}']})"),
        ("Superseded", triage["superseded"], lambda i: i),
        ("Worktree edits", edits, lambda p: p),
        ("Parse problems", problems, lambda p: p),
    ]
    any_action = False
    for heading, entries, fmt in sections:
        if entries:
            any_action = True
            lines.append(f"{heading} ({len(entries)}): " + ", ".join(fmt(e) for e in entries))
    if not any_action and not ahead_parts:
        lines.append("Actionable: clean (nothing pending admission, stale, failed, unreviewed, or edited).")
    return "\n".join(lines)


def run(args) -> int:
    try:
        repo = open_repo(args)
        items, problems = repo.load_items()
        ctx = render.Context(repo, items)
        all_lanes = lane.owned_lanes(repo.config)
        open_names = lane.open_lanes(repo.config)
    except (FileNotFoundError, RecordError, lane.LaneError) as exc:
        return fail(str(exc))

    by_status = Counter(ctx.label(item_id) for item_id in items)
    by_kind = Counter(item.kind for item in items.values())
    triage, hints = _triage(ctx)
    edits, dirty = _worktree_edits(repo.root, repo.trusted_commit)
    branch = gitref.current_branch(repo.root) or "HEAD"
    data = {
        "counts": {"total": len(items), "by_status": dict(sorted(by_status.items())),
                   "by_kind": dict(sorted(by_kind.items()))},
        "triage": triage,
        "lanes": {"open": open_names, "total": len(all_lanes), "max_parallel": repo.config.max_parallel},
        "git": {
            "branch": branch,
            "ahead_of_trusted": _rev_count(repo.root, f"{repo.trusted_commit}..HEAD") if repo.trusted_commit else None,
            "ahead_of_upstream": _rev_count(repo.root, "@{u}..HEAD"),
        },
        "worktree": {"edits": edits, "dirty": dirty, "problems": problems},
        "trust": render.trust_data(repo),
    }
    emit(args, _format_summary(data, hints), data)
    return 0
