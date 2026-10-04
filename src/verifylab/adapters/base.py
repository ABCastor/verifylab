"""The contract every checker adapter implements. `vl check` turns a CheckOutcome into a receipt."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from ..records import Item
from ..repo import Repo


@dataclass(frozen=True)
class CheckRequest:
    repo: Repo
    item: Item
    assurance: str                # "protected": target, definitions and evaluator come from the trusted ref
                                  # "exploratory": everything comes from the worktree (fast, weak)
    trusted_commit: str           # commit the trusted ref resolved to when the check started
    scratch: Path                 # empty directory owned by this check; deleted afterwards
    timeout: float
    memory_max: str | None
    memory_total: str | None = None        # aggregate cap of all vl runs (the shared systemd slice)
    guards: frozenset[str] = frozenset()   # test-only: names of guards to DISABLE, to prove each one matters


@dataclass
class CheckOutcome:
    verdict: str                                   # pass | fail | error | unsupported
    reasons: list[str]
    files: dict[str, str]                          # candidate-side inputs: repo-relative path -> sha256 of worktree content
    trusted_files: dict[str, str]                  # trusted-side inputs: repo-relative path -> sha256 at trusted_commit
    target: dict[str, Any]                         # what was checked against (path, sha256, theorem names or evaluator)
    environment: dict[str, Any]                    # toolchain, tool paths and versions, isolation used
    checked: dict[str, Any]                        # declarations, axioms, kernels, cases: what the verdict covers
    command: list[str]
    log: str = ""                                  # full tool output; the receipt keeps its sha256 and its tail
    extra: dict[str, Any] = field(default_factory=dict)


class Adapter(Protocol):
    name: str

    def applies(self, item: Item) -> bool: ...

    def check(self, request: CheckRequest) -> CheckOutcome: ...
