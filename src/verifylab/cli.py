"""Entry point of the `vl` command."""

from __future__ import annotations

import argparse
import importlib
import sys

from . import __version__
from .commands import COMMANDS, TRUSTED_REF_HELP
from .config import ConfigError
from .gitref import GitError
from .machine import MachineError
from .output import fail
from .records import RecordError


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="vl",
        description="VerifyLab: trusted targets, receipts, reviews and lanes for research agents.",
    )
    parser.add_argument("--version", action="version", version=f"vl {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in COMMANDS:
        module = importlib.import_module(f"verifylab.commands.{name}")
        cmd = module.register(sub)
        cmd.add_argument("--json", action="store_true", help="print versioned JSON instead of text")
        if name not in ("init", "lane"):      # init writes the trusted ref; lane takes it per action
            cmd.add_argument("--trusted-ref", metavar="REF", help=TRUSTED_REF_HELP)
        cmd.set_defaults(handler=module.run)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.handler(args) or 0)
    except (ConfigError, RecordError, GitError, MachineError) as exc:
        return fail(str(exc))
    except Exception as exc:  # a crash is a tool failure, never a verdict or a validation result
        return fail(f"internal error: {type(exc).__name__}: {exc}")


if __name__ == "__main__":
    sys.exit(main())
