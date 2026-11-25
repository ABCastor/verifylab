"""One module per command. Each exposes `register(subparsers)` and `run(args) -> int`."""

from __future__ import annotations

import argparse
import math
import sys

COMMANDS = ("init", "status", "new", "show", "find", "check", "replay", "review", "validate", "admit", "lane",
            "queue", "importer", "export", "receipts", "jobs", "inspect")
TRUSTED_REF_HELP = "trust REF for this command instead of the repository's git config vl.trustedRef"
JSON_HELP = "print versioned JSON instead of text"


def action_flags(action: argparse.ArgumentParser, json_help: str = JSON_HELP, trusted_ref: bool = True) -> None:
    """`--json` and `--trusted-ref` for an action of a command that has actions. SUPPRESS: an action that is not given a
    flag leaves the value of the command's own (`vl lane --json`), so the flag means the same before or after the
    action."""
    action.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help=json_help)
    if trusted_ref:
        action.add_argument("--trusted-ref", metavar="REF", default=argparse.SUPPRESS, help=TRUSTED_REF_HELP)


def seconds(text: str) -> float:
    """An argparse type: a finite, positive number of seconds (`nan` or `inf` would switch a deadline off)."""
    try:
        value = float(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not a number of seconds: {text!r}") from None
    if not math.isfinite(value) or value <= 0:
        raise argparse.ArgumentTypeError(f"must be a finite, positive number of seconds, not {text!r}")
    return value


def open_repo(args, warn: bool = True, cls=None):
    """The repository (a `Repo`, or the `cls` given), with the trusted ref of `--trusted-ref` or git config; its
    configuration warnings go to stderr unless the command reports them itself (`warn=False`)."""
    from ..repo import Repo

    repo = (cls or Repo).open(trusted_ref=getattr(args, "trusted_ref", None))
    if warn:
        for _, message in repo.config.warnings:
            sys.stderr.write(f"vl: warning: {message}\n")
    return repo
