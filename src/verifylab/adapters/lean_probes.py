"""Statement probes: is a target theorem closed by automation alone, or are its hypotheses contradictory?

Comparator proves that a proof matches the trusted target; it cannot tell whether the target says anything.
A target written as `P ∨ True`, with hypotheses that never hold together, or over a definition that is
constantly `True`, passes it. The probes look at the target's statement only (never at the proof), with the
Lean metaprogram in `StatementProbe.lean`: a fixed tactic battery tries to close each target theorem's type
(triviality) and to derive `False` from its top-level Prop hypotheses (premise-vacuity). The same metaprogram lists
what a referee must read and weigh, without judging it (ADR 0010): the in-project definitions the
statement depends on (Comparator checks they are the trusted ones, not that they mean what the question asks), the
totalized operations in the statement and in those definitions (`a - b` on ℕ, `x / 0`, `Real.sqrt` of a negative,
...), and the small values of its first ℕ binders at which the statement is vacuous or closed by the battery. This
does not establish the adequacy of definitions or inspect solution classes denied inside a conclusion.

Where they run: beside Comparator (ADR 0005), in the jail, on a SNAPSHOT of the challenge build taken before any candidate
code was compiled; their result is recorded only after a pass, and a check that does not pass cancels them.
Comparator's solution build may write anything under the check project's `.lake` (its
sandbox allows it), including the challenge's own `.olean` files, and the project's build directory comes first
on Lean's search path. So `vl` builds the challenge first (the same `lake build` under the same landrun sandbox
Comparator uses), copies its build products to a separate probe workspace, and runs the probes there.

What they may change: a vacuity hit makes the derived status `vacuous`, never `verified`; a triviality hit is
a warning a fidelity review must acknowledge, and so are set-theoretic foundations (`--acknowledge foundations`);
definitions, totalized operations and boundary values are card information and warnings, never a status. A probe
crash, a missing result or a timeout is a tool problem recorded in the receipt, never a verdict: the verdict is
Comparator's.
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

# Totalized operations: Lean and Mathlib give every operation a value everywhere, so a statement can hold, or say
# something else, through a conventional value outside the intended domain. Kinds the metaprogram recognizes by
# their shape (`structural?` in StatementProbe.lean), then constants whose application counts; each with why it
# may change the meaning. A finding is information for the referee, never a verdict.
TOTALIZED_KINDS = {
    "nat_sub": "subtraction in ℕ truncates: a - b = 0 when a < b",
    "nat_div": "division in ℕ rounds down, and a / 0 = 0",
    "nat_mod": "a % 0 = a in ℕ",
    "int_div": "division in ℤ rounds (toward -∞ for a positive divisor), and a / 0 = 0",
    "int_mod": "a % 0 = a in ℤ",
    "div_zero": "x / 0 = 0 in a field or group with zero",
    "inv_zero": "0⁻¹ = 0 in a field or group with zero",
    "rpow": "real power: 0 ^ 0 = 1, 0 ^ y = 0 for y ≠ 0, and a negative base gives exp (y log x) cos (yπ)",
    "zpow": "integer power: x ^ (-n) = (x ^ n)⁻¹, so 0 ^ n = 0 for n < 0",
    "cast_wrap": "a cast into Fin n or ZMod n reduces modulo n",
    "cast_truncated": "computed in ℕ or ℤ, then cast: the truncation happens before the cast",
}
TOTALIZED_CONSTANTS = {
    "Nat.log": "Nat.log b n = 0 when b ≤ 1 or n < b",
    "Nat.findGreatest": "0 when no number up to the bound satisfies the property",
    "Nat.find": "the least witness of an existence proof: check that the least one is the one meant",
    "Int.toNat": "a negative integer becomes 0",
    "Real.sqrt": "√x = 0 for x < 0",
    "Real.log": "log 0 = 0 and log x = log |x| for x < 0",
    "Real.logb": "logb b x = log x / log b: 0 when b ∈ {0, 1, -1}, and log's conventions for x ≤ 0",
    "Real.rpow": "real power: 0 ^ 0 = 1, 0 ^ y = 0 for y ≠ 0, and a negative base gives exp (y log x) cos (yπ)",
    "Real.toNNReal": "a negative real becomes 0",
    "ENNReal.toReal": "∞ becomes 0",
    "ENNReal.toNNReal": "∞ becomes 0",
    "ENat.toNat": "⊤ becomes 0",
    "tsum": "∑' is 0 when the family is not summable",
    "tprod": "∏' is 1 when the family is not multipliable",
    "MeasureTheory.integral": "∫ is 0 when the function is not integrable",
    "intervalIntegral": "∫ a..b is 0 when the function is not integrable on the interval",
    "deriv": "deriv f x = 0 where f is not differentiable",
    "derivWithin": "0 where f is not differentiable within the set",
    "fderiv": "fderiv f x = 0 where f is not differentiable",
    "fderivWithin": "0 where f is not differentiable within the set",
    "limUnder": "an arbitrary value when the limit does not exist",
    "Classical.choice": "an arbitrary element: what is stated of it holds for whichever one is chosen",
    "Classical.choose": "an arbitrary witness: what is stated of it holds for whichever one is chosen",
    "Classical.epsilon": "an arbitrary element, also when none has the property",
    "Exists.choose": "an arbitrary witness: what is stated of it holds for whichever one is chosen",
    "InfSet.sInf": "sInf of an empty or unbounded set is a conventional value (0 in ℕ and ℝ)",
    "SupSet.sSup": "sSup of an empty or unbounded set is a conventional value (0 in ℝ and ℕ)",
    "iInf": "an infimum over an empty or unbounded family is a conventional value (0 in ℕ and ℝ)",
    "iSup": "a supremum over an empty or unbounded family is a conventional value (0 in ℝ and ℕ)",
    "Nat.card": "0 for an infinite type",
    "Set.ncard": "0 for an infinite set",
    "Polynomial.natDegree": "0 for the zero polynomial",
}
# Set-theoretic foundations: constants whose name says the statement is about the foundations (`foundation?` in
# StatementProbe.lean), by kind, with why a referee must look. There the list of axioms is uninformative: Lean always
# admits `Classical.choice`, so a statement that assumes, denies or avoids Choice passes every axiom check alike.
FOUNDATIONS_KINDS = {
    "zfset": "ZFSet and PSet are sets built inside type theory, where Classical.choice holds: what is true of them is "
             "true in a model of Choice, which may not be the set theory meant",
    "cardinal": "cardinals are compared and added with Classical.choice behind them (every set is well-orderable): a "
                "statement whose truth depends on Choice, or on its failure, cannot be told from one that does not",
    "ordinal": "ordinals are well-ordered types, and with Classical.choice every set is well-orderable: what is "
               "stated of ordinals and choice principles is not the ZF statement",
    "choice": "names a choice principle: Lean admits Classical.choice whatever the statement says, so the axiom list "
              "cannot show whether it assumes, denies or avoids Choice",
    "theory": "names ZF or ZFC: check which theory it is about, and that a model or a deep embedding is not "
              "mistaken for Lean's own foundations",
    "independence": "consistency or independence vocabulary: a statement about a theory, not in it; check that the "
                    "object theory and Lean's metatheory are kept apart",
}
# Boundary values: the first BOUNDARY_BINDERS binders of type ℕ of a target theorem, each set to these values.
BOUNDARY_BINDERS = 2
BOUNDARY_VALUES = (0, 1, 2)
# At most this many totalized operations are listed per theorem (the statement first, then its definitions).
TOTALIZED_CAP = 40


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
               permitted_axioms: Iterable[str], heartbeats: int, nonce: str, *, project: Iterable[str] = (),
               trivial: bool = True, vacuity: bool = True, scan: bool = True,
               boundary_binders: int = BOUNDARY_BINDERS) -> str:
    """The probe file: imports, options, the metaprogram, one `run_elab` per theorem (each prints its own line), then
    the boundary values of each theorem (one line each). `project`: the module prefixes of in-project declarations."""
    lines = [f"import {challenge}", "import Lean", *(f"import {m}" for m in imports), "",
             "set_option maxHeartbeats 0", "set_option Elab.async false", "", SOURCE.read_text(encoding="utf-8"), ""]
    flags = " ".join("true" if f else "false" for f in (trivial, vacuity, scan))
    names = list(theorems)
    for name in names:
        lines.append(f"run_elab VLProbe.probe {_lean_string(nonce)} {_lean_string(name)} {_lean_strings(tactics)} "
                     f"{_lean_strings(permitted_axioms)} {_lean_strings(TOTALIZED_CONSTANTS)} "
                     f"{_lean_strings(project)} {int(heartbeats)} {TOTALIZED_CAP} {flags}")
    for name in names if boundary_binders > 0 else ():
        lines.append(f"run_elab VLProbe.boundary {_lean_string(nonce)} {_lean_string(name)} {_lean_strings(tactics)} "
                     f"{_lean_strings(permitted_axioms)} #[{', '.join(map(str, BOUNDARY_VALUES))}] {int(heartbeats)} "
                     f"{int(boundary_binders)}")
    return "\n".join(lines) + "\n"


EMPTY = {"trivial_by": None, "vacuous_by": None, "prop_hypotheses": None}
TERM_CHARS = 200          # a totalized subterm is recorded up to this many characters


def parse_output(stdout: str, nonce: str, theorems: Iterable[str]) -> tuple[dict[str, dict[str, Any]], list[str]]:
    """Per-theorem results from the probe's tagged lines, and the problems found. A theorem with no line, a
    duplicate line or a malformed line gets nulls: a missing probe result never flags anything. The boundary line
    of a theorem (`"phase": "boundary"`) adds `boundary` to its result; a theorem whose boundary line is missing
    (the run ended first) has no `boundary`, which is not a problem: the boundary values are information."""
    expected = list(theorems)
    tag = re.compile(rf"{re.escape(MARKER + nonce)} (\{{.*\}})\s*$")
    seen: dict[str, dict[str, Any]] = {}
    bounds: dict[str, dict[str, Any]] = {}
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
        phase = "boundary " if isinstance(row, dict) and row.get("phase") == "boundary" else ""
        lines = bounds if phase else seen
        if name not in expected:
            problems.append(f"probe line for an unexpected theorem: {line[:200]}")
        elif name in lines:
            problems.append(f"two {phase}probe lines for {name}; both ignored")
            lines[name] = {**EMPTY, "error": f"duplicate {phase}probe lines"}
        elif "error" in row:
            problems.append(f"{name}: {phase}{row['error']}")
            lines[name] = {**EMPTY, "error": str(row["error"])}
        else:
            entry = _boundary(row) if phase else _entry(row)
            if entry is None:
                problems.append(f"malformed {phase}probe line for {name}: {line[:200]}")
                lines[name] = {**EMPTY, "error": f"malformed {phase}probe line"}
            else:
                lines[name] = entry
    if unavailable:
        problems.append(f"battery tactics that did not parse in the challenge environment: {sorted(unavailable)}")
    results = {}
    for name in expected:
        if name not in seen:
            problems.append(f"{name}: no probe result")
            results[name] = {**EMPTY, "error": "no probe result"}
            continue
        results[name] = seen[name]
        bound = bounds.get(name)
        if bound is not None and "error" not in seen[name] and "error" not in bound:
            results[name]["boundary"] = bound["boundary"]
            if bound["limit"]:
                results[name].setdefault("limit_reached", {})["boundary"] = bound["limit"]
    return results, problems


def _strings(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(v, str) for v in value)


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
    reached = {k: v for k, v in limit.items() if k in ("trivial", "vacuous", "totalized") and isinstance(v, list) and v}
    if reached:
        entry["limit_reached"] = reached
    if isinstance(row.get("vacuity_skipped"), str):
        entry["vacuity_skipped"] = row["vacuity_skipped"]
    if "definitions" in row or "totalized" in row:          # the scan ran
        found = row.get("totalized")
        if not (_strings(row.get("definitions")) and isinstance(found, list) and all(
                isinstance(f, dict) and all(isinstance(f.get(k), str) for k in ("op", "term", "in")) for f in found)):
            return None
        entry["definitions"] = row["definitions"]
        entry["totalized"] = [{"op": f["op"], "term": f["term"][:TERM_CHARS], "in": f["in"], "why": why(f["op"])}
                              for f in found]
        if "foundations" in row:
            roots = row["foundations"]
            if not (isinstance(roots, list) and all(
                    isinstance(f, dict) and f.get("kind") in FOUNDATIONS_KINDS
                    and all(isinstance(f.get(k), str) for k in ("const", "in")) for f in roots)):
                return None
            entry["foundations"] = [{"kind": f["kind"], "const": f["const"], "in": f["in"],
                                     "why": FOUNDATIONS_KINDS[f["kind"]]} for f in roots]
    return entry


def _boundary(row: dict[str, Any]) -> dict[str, Any] | None:
    hits, limit = row.get("boundary"), row.get("limit_reached")
    if not (isinstance(hits, list) and _strings(limit)):
        return None
    out = []
    for hit in hits:
        if not (isinstance(hit, dict) and isinstance(hit.get("binder"), str) and type(hit.get("value")) is int):
            return None
        tactics = {k: hit.get(k) for k in ("trivial_by", "vacuous_by")}
        if not all(t is None or isinstance(t, str) for t in tactics.values()) or not any(tactics.values()):
            return None
        out.append({"binder": hit["binder"], "value": hit["value"], **{k: t for k, t in tactics.items() if t}})
    return {"boundary": out, "limit": limit}


def why(op: str) -> str:
    """Why a totalized operation may change what a statement says."""
    return TOTALIZED_KINDS.get(op) or TOTALIZED_CONSTANTS.get(op) or "a totalized operation"
