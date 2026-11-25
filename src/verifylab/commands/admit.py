"""`vl admit`: the integrator's mechanical step after checks and reviews. Commit the new receipts and reviews.

Run on the trusted branch, by the integrator. It refuses unless `vl validate` passes and the only changes in the
worktree are new records under research/evidence/ and research/reviews/ (an existing record changed or deleted, or
any other file, refuses), stages exactly those, commits them with a title that names the counts, and never pushes.
It authenticates nothing: committing a record on the trusted ref is the admission, and `vl admit` is that commit made
by the person who ran the checks (docs/ARCHITECTURE.md, Trust model).
"""

from __future__ import annotations

import argparse
import contextlib
import io
import os
import re
import sys

from .. import gitref
from ..output import emit, fail, listed
from ..repo import EVIDENCE, REVIEWS, Repo
from . import open_repo, validate

NEW = ("??", "A ")      # untracked, or already staged as new: the only states of a record `vl admit` takes


def register(sub):
    p = sub.add_parser("admit", help="commit the new receipts and reviews after `vl validate` passes")
    p.epilog = ("Run on the trusted branch (checked out, and the trusted ref), after the checks and reviews are written and "
                "while nothing else is changed. Refuses when `vl validate` reports an error, when anything but a new "
                "file under research/evidence/<id>/ or research/reviews/<id>/ differs from the trusted commit, or "
                "when nothing is new. Commits only those files, titled with their counts; never pushes. Exit: 0 "
                "committed (or nothing to admit), 2 refused.")
    return p


def changes(root) -> list[tuple[str, str]]:
    """(XY, path) of every path `git status` reports: staged, modified, deleted and untracked, renames split."""
    out = gitref.git(root, "status", "--porcelain", "-z", "--no-renames", "--untracked-files=all").stdout
    return [(os.fsdecode(entry[:2]), os.fsdecode(entry[3:])) for entry in out.split(b"\0") if entry]


def split(repo: Repo, found: list[tuple[str, str]]) -> tuple[list[str], list[str], list[str]]:
    """(new receipts, new reviews, everything else) among the changed paths. A new record is an untracked (or newly
    staged) `<folder>/<item>/<name>.json` of research/evidence or research/reviews."""
    patterns = {folder: re.compile(re.escape(repo.rel(folder)) + r"/[^/]+/[^/]+\.json") for folder in (EVIDENCE, REVIEWS)}
    receipts, reviews, other = [], [], []
    for state, path in found:
        if state in NEW and patterns[EVIDENCE].fullmatch(path):
            receipts.append(path)
        elif state in NEW and patterns[REVIEWS].fullmatch(path):
            reviews.append(path)
        else:
            other.append(f"{path} ({'new' if state in NEW else 'changed'})")
    return sorted(receipts), sorted(reviews), sorted(other)


def title(receipts: int, reviews: int) -> str:
    parts = [f"{n} {word}{'' if n == 1 else 's'}" for n, word in ((receipts, "receipt"), (reviews, "review")) if n]
    return "Admit " + " and ".join(parts)


def run(args) -> int:
    repo = open_repo(args)
    commit = repo.trusted_commit
    if commit is None:
        return fail(f"the trusted ref '{repo.config.trusted_ref}' (from {repo.config.trusted_ref_source}) does not "
                    "resolve to a commit")
    ref = repo.config.trusted_ref
    branch = gitref.current_branch(repo.root)
    if not gitref.is_branch(repo.root, ref, branch):
        return fail(f"HEAD is not the trusted branch '{ref}': admission is the commit of records onto the trusted "
                    "branch, made by the integrator; check it out first")

    receipts, reviews, other = split(repo, changes(repo.root))
    if other:
        return fail(f"not only new receipts and reviews differ from the trusted commit; nothing was staged: "
                    f"{listed(other)}")
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        invalid = validate.run(argparse.Namespace(incoming=None, json=False, trusted_ref=args.trusted_ref))
    if invalid:
        sys.stderr.write(output.getvalue())
        return fail("vl validate reports errors: nothing was staged")
    if not receipts and not reviews:
        emit(args, "nothing to admit: no new receipt or review", {"admitted": {"receipts": [], "reviews": []},
                                                                  "commit": None})
        return 0
    message = title(len(receipts), len(reviews))
    paths = [*receipts, *reviews]
    try:
        gitref.git(repo.root, "--literal-pathspecs", "add", "--", *paths)
        gitref.git(repo.root, "commit", "--quiet", "-m", message)
    except gitref.GitError as exc:
        gitref.git(repo.root, "--literal-pathspecs", "reset", "--quiet", "--", *paths, check=False)
        return fail(f"{exc}; the records stay in the worktree, unstaged")
    new = gitref.head(repo.root)
    emit(args, f"{message} ({new[:12]} on {branch}; not pushed)\n{output.getvalue().splitlines()[-1]}",
         {"admitted": {"receipts": receipts, "reviews": reviews}, "commit": new, "title": message, "branch": branch})
    return 0
