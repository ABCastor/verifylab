"""One way to print: readable text for agents and humans, or versioned JSON with --json."""

from __future__ import annotations

import json
import sys
from typing import Any

JSON_SCHEMA = "vl.output/1"


def emit(args: Any, text: str, data: dict[str, Any]) -> None:
    if getattr(args, "json", False):
        sys.stdout.write(json.dumps({"schema": JSON_SCHEMA, **data}, indent=2, ensure_ascii=False) + "\n")
    else:
        sys.stdout.write(text.rstrip("\n") + "\n")


def fail(message: str, code: int = 2) -> int:
    """Tool failures (bad input, missing tool) exit 2; they are never a scientific verdict."""
    sys.stderr.write(f"vl: {message}\n")
    return code
