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
from .output import error_help, fail
from .records import RecordError


PURPOSES = {
    "Understand and explain": ("pin", "inspect", "graph", "value", "order", "show", "find"),
    "Improve and create": ("attempts", "trial", "new"),
    "Check and review": ("check", "replay", "review", "status", "validate", "admit"),
    "Set up and coordinate": ("init", "lane", "queue", "jobs", "receipts", "export", "import", "docs"),
}


# Compatibility spellings use the same parsers and handlers as the canonical verbs.
PROJECT_ROUTES = {
    ("inspect", "--item"): "show",
    ("inspect", "--search"): "find",
    ("check", "--replay"): "replay",
}


class CommandParser(argparse.ArgumentParser):
    def error(self, message):
        super().error(f"{message}. Next, run {self.prog} --help.")


def build_parser(*, _batch_worker: bool = False) -> argparse.ArgumentParser:
    parser = CommandParser(
        prog="vl",
        description="Read, evaluate and improve pinned mathematics.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Run vl docs GUIDE for project records and administration.",
    )
    parser.add_argument("--version", action="version", version=f"vl {__version__}")
    sub = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")
    for name in COMMANDS:
        module = importlib.import_module(f"verifylab.commands.{name}")
        cmd = module.register(sub)
        if _batch_worker and name == "check":
            cmd.add_argument("--_batch-anchor", nargs=3, help=argparse.SUPPRESS)
        cmd.add_argument("--json", action="store_true", help="print versioned JSON instead of text")
        if name not in ("init", "lane", "queue", "export", "receipts", "jobs", "inspect"):  # init writes the trusted ref
            cmd.add_argument("--trusted-ref", metavar="REF", help=TRUSTED_REF_HELP)
        cmd.set_defaults(handler=module.run)
    from .commands import attempt_files, attempts, docs, importer, reading
    importer.register_pin(sub)
    reading.register(sub)
    attempts.register(sub)
    attempt_files.register(sub)
    docs.register(sub)
    for (verb, selector), original in PROJECT_ROUTES.items():
        target = sub.choices[verb]
        hint = f"Use 'vl {verb} {selector} --help' for the 'vl {original}' interface."
        target.epilog = f"{target.epilog} {hint}" if target.epilog else hint
    summaries = {action.dest: action.help for action in sub._get_subactions()}
    lines = [parser.description]
    for purpose, verbs in PURPOSES.items():
        lines += ["", purpose, *(f"  {verb:<10}{summaries[verb]}" for verb in verbs)]
    parser.description = "\n".join(lines)
    # Preserve the parser choices while showing the shorter, purpose-grouped list above.
    sub._choices_actions.clear()
    return parser


def main(argv: list[str] | None = None, *, _batch_worker: bool = False) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    option_end = argv.index("--") if "--" in argv else len(argv)
    routes = [(selector, original) for (verb, selector), original in PROJECT_ROUTES.items()
              if argv[:1] == [verb] and selector in argv[1:option_end]]
    help_words = None
    if len(routes) == 1:
        selector, original = routes[0]
        help_words = [argv[0], selector]
        argv.remove(selector)
        argv[0] = original
    elif argv[:1] == ["simplify"]:
        argv[0] = "trial"
        sys.stderr.write("vl: 'simplify' is deprecated. Use 'vl trial'.\n")
    elif len(argv) >= 2 and argv[0] == "import" and argv[1] not in ("lake", "comparator") and not argv[1].startswith("-"):
        argv.insert(1, "lake")
    args = build_parser(_batch_worker=_batch_worker).parse_args(argv)
    if args.command == "import" and args.action == "lake":
        help_words = ["pin"]
        sys.stderr.write("vl: 'import lake' is deprecated. Use 'vl pin'.\n")
    if help_words is None:
        help_words = [args.command]
        if getattr(args, "action", None) and not args.action.startswith("-"):
            help_words.append(args.action)
    token = error_help.set("vl " + " ".join(help_words))
    try:
        return int(args.handler(args) or 0)
    except RecordError as exc:
        return fail(str(exc), show_help=not exc.has_recovery)
    except (ConfigError, GitError, MachineError, OSError) as exc:
        return fail(str(exc))
    except KeyboardInterrupt:
        return fail("interrupted", 130)
    except Exception as exc:  # a crash is a tool failure, never a verdict or a validation result
        return fail(f"internal error: {type(exc).__name__}: {exc}")
    finally:
        error_help.reset(token)


if __name__ == "__main__":
    sys.exit(main())
