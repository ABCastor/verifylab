"""One module per command. Each exposes `register(subparsers)` and `run(args) -> int`."""

from __future__ import annotations

import argparse
import math
import sys

COMMANDS = ("init", "show", "find", "check", "review", "validate", "lane")
TRUSTED_REF_HELP = "trust REF for this command instead of the repository's git config vl.trustedRef"


def seconds(text: str) -> float:
    """An argparse type: a finite, positive number of seconds (`nan` or `inf` would switch a deadline off)."""
    try:
        value = float(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not a number of seconds: {text!r}") from None
    if not math.isfinite(value) or value <= 0:
        raise argparse.ArgumentTypeError(f"must be a finite, positive number of seconds, not {text!r}")
    return value


def open_repo(args, warn: bool = True):
    """The repository, with the trusted ref of `--trusted-ref` or git config; its configuration warnings go to
    stderr unless the command reports them itself (`warn=False`)."""
    from ..repo import Repo

    repo = Repo.open(trusted_ref=getattr(args, "trusted_ref", None))
    if warn:
        for _, message in repo.config.warnings:
            sys.stderr.write(f"vl: warning: {message}\n")
    return repo
