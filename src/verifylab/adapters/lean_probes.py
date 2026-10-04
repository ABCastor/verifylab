"""Statement probes: is a target theorem closed by automation alone, or are its hypotheses contradictory?

Comparator proves that a proof matches the trusted target; it cannot tell whether the target says anything.
A target written as `P ∨ True`, with hypotheses that never hold together, or over a definition that is
constantly `True`, passes it. The probes look at the target's statement only (never at the proof), with the
Lean metaprogram in `StatementProbe.lean`: a fixed tactic battery tries to close each target theorem's type
(triviality) and to derive `False` from its hypotheses (vacuity).

Where they run: after a pass, in the jail, on a SNAPSHOT of the challenge build taken before any candidate
code was compiled. Comparator's solution build may write anything under the check project's `.lake` (its
sandbox allows it), including the challenge's own `.olean` files, and the project's build directory comes first
on Lean's search path. So `vl` builds the challenge first (the same `lake build` under the same landrun sandbox
Comparator uses), copies its build products to a separate probe workspace, and runs the probes there.

What they may change: a vacuity hit makes the derived status `vacuous`, never `verified`; a triviality hit is
a warning a fidelity review must acknowledge. A probe crash, a missing result or a timeout is a tool problem
recorded in the receipt, never a verdict: the verdict is Comparator's.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Iterable

SOURCE = Path(__file__).with_name("StatementProbe.lean")
MARKER = "VLPROBE-"
# Each entry is a tactic sequence run on the goal; recorded verbatim as `trivial_by` / `vacuous_by`.
CORE_BATTERY = ("intros; trivial", "decide", "intros; decide", "simp", "intros; simp_all", "intros; omega")
# (module to import, Lake package that provides it, battery entry): used only when the package is a dependency
# of the project and the module is built in its packages directory.
OPTIONAL_TACTICS = (("Mathlib.Tactic.Tauto", "mathlib", "intros; tauto"), ("Aesop", "aesop", "intros; aesop"))


def battery(packages_dir: Path | None, package_names: Iterable[str]) -> tuple[list[str], list[str]]:
    """(battery, extra imports) for a project: the core tactics, plus tauto and aesop when importable."""
    tactics, imports = list(CORE_BATTERY), []
    names = set(package_names)
    for module, package, tactic in OPTIONAL_TACTICS:
        if packages_dir is None or package not in names:
            continue
        olean = packages_dir / package / ".lake" / "build" / "lib" / "lean" / (module.replace(".", "/") + ".olean")
        if olean.is_file():
            tactics.append(tactic)
            imports.append(module)
    return tactics, imports


def _lean_string(text: str) -> str:
    if any(ch in text for ch in "\n\r\\\"") or not text.isprintable():
        raise ValueError(f"unsupported text in a probe argument: {text!r}")
    return f'"{text}"'


def _lean_strings(values: Iterable[str]) -> str:
    return "#[" + ", ".join(_lean_string(v) for v in values) + "]"


def probe_file(challenge: str, theorems: Iterable[str], tactics: list[str], imports: list[str],
               permitted_axioms: Iterable[str], heartbeats: int, nonce: str, *, trivial: bool = True,
               vacuity: bool = True) -> str:
    """The probe file: imports, options, the metaprogram, one `run_elab` per theorem (each prints its own line)."""
    lines = [f"import {challenge}", "import Lean", *(f"import {m}" for m in imports), "",
             "set_option maxHeartbeats 0", "set_option Elab.async false", "", SOURCE.read_text(encoding="utf-8"), ""]
    flags = f"{'true' if trivial else 'false'} {'true' if vacuity else 'false'}"
    for name in theorems:
        lines.append(f"run_elab VLProbe.probe {_lean_string(nonce)} {_lean_string(name)} {_lean_strings(tactics)} "
                     f"{_lean_strings(permitted_axioms)} {int(heartbeats)} {flags}")
    return "\n".join(lines) + "\n"


EMPTY = {"trivial_by": None, "vacuous_by": None, "prop_hypotheses": None}


def parse_output(stdout: str, nonce: str, theorems: Iterable[str]) -> tuple[dict[str, dict[str, Any]], list[str]]:
    """Per-theorem results from the probe's tagged lines, and the problems found. A theorem with no line, a
    duplicate line or a malformed line gets nulls: a missing probe result never flags anything."""
    expected = list(theorems)
    tag = re.compile(rf"{re.escape(MARKER + nonce)} (\{{.*\}})\s*$")
    seen: dict[str, dict[str, Any]] = {}
    problems: list[str] = []
    unavailable: set[str] = set()
    for line in stdout.splitlines():
        match = tag.search(line)
        if not match:
            continue
        try:
            row = json.loads(match.group(1))
        except json.JSONDecodeError:
            problems.append(f"unreadable probe line: {line[:200]}")
            continue
        name = row.get("theorem") if isinstance(row, dict) else None
        if isinstance(name, str) and isinstance(row.get("unavailable"), list):
            unavailable.update(str(t) for t in row["unavailable"])
        if name not in expected:
            problems.append(f"probe line for an unexpected theorem: {line[:200]}")
        elif name in seen:
            problems.append(f"two probe lines for {name}; both ignored")
            seen[name] = {**EMPTY, "error": "duplicate probe lines"}
        elif "error" in row:
            problems.append(f"{name}: {row['error']}")
            seen[name] = {**EMPTY, "error": str(row["error"])}
        else:
            entry = _entry(row)
            if entry is None:
                problems.append(f"malformed probe line for {name}: {line[:200]}")
                seen[name] = {**EMPTY, "error": "malformed probe line"}
            else:
                seen[name] = entry
    if unavailable:
        problems.append(f"battery tactics that did not parse in the challenge environment: {sorted(unavailable)}")
    results = {}
    for name in expected:
        if name not in seen:
            problems.append(f"{name}: no probe result")
            results[name] = {**EMPTY, "error": "no probe result"}
        else:
            results[name] = seen[name]
    return results, problems


def _entry(row: dict[str, Any]) -> dict[str, Any] | None:
    def tactic(value: Any) -> bool:
        return value is None or isinstance(value, str)
    hyps = row.get("prop_hypotheses")
    limit = row.get("limit_reached", {})
    if not (tactic(row.get("trivial_by")) and tactic(row.get("vacuous_by")) and isinstance(hyps, int)
            and not isinstance(hyps, bool) and hyps >= 0 and isinstance(limit, dict)):
        return None
    entry: dict[str, Any] = {"trivial_by": row.get("trivial_by"), "vacuous_by": row.get("vacuous_by"),
                             "prop_hypotheses": hyps}
    reached = {k: v for k, v in limit.items() if k in ("trivial", "vacuous") and isinstance(v, list) and v}
    if reached:
        entry["limit_reached"] = reached
    if isinstance(row.get("vacuity_skipped"), str):
        entry["vacuity_skipped"] = row["vacuity_skipped"]
    return entry
