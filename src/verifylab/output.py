"""One way to print: readable text for agents and humans, or versioned JSON with --json."""

from __future__ import annotations

import json
import sys
from contextvars import ContextVar
from typing import Any

error_help: ContextVar[str | None] = ContextVar("error_help", default=None)

JSON_SCHEMA = "vl.output/1"
# Exit codes of the commands that judge: 0 pass, 1 fail, 2 usage or a problem found before anything ran (`fail`), 3 the
# check or replay ran and is inconclusive.
EXIT_PASS, EXIT_FAIL, EXIT_TOOL = 0, 1, 3


def terminal_text(text: str) -> str:
    """Show record-controlled terminal bytes literally; JSON keeps the original data."""
    return "".join(f"\\x{ord(ch):02x}" if (ord(ch) < 32 and ch not in "\n\t") or 127 <= ord(ch) <= 159
                   else ch for ch in text)


def emit(args: Any, text: str, data: dict[str, Any]) -> None:
    if getattr(args, "json", False):
        sys.stdout.write(json.dumps({"schema": JSON_SCHEMA, **data}, indent=2, ensure_ascii=False) + "\n")
    else:
        sys.stdout.write(terminal_text(text).rstrip("\n") + "\n")


def listed(names: list[str], shown: int = 10) -> str:
    """The first `shown` names, then how many more there are."""
    return ", ".join(names[:shown]) + (f" and {len(names) - shown} more" if len(names) > shown else "")


def fail(message: str, code: int = 2, *, show_help: bool = True) -> int:
    """Tool failures (bad input, missing tool) exit 2; they are never a scientific verdict."""
    if show_help and code == 2 and (command := error_help.get()):
        separator = "\n" if "\n" in message else ". "
        message += f"{separator}Next, run {command} --help."
    sys.stderr.write(terminal_text(f"vl: {message}") + "\n")
    return code
