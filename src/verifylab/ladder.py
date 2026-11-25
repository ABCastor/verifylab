"""The verdict ladder of a result (ADR 0013): five separate facts, each `not assessed` until a record assessed it.

    Lean kernel, second kernel, axioms and trusted base   what a protected check recorded (the receipt)
    statement fidelity                                    what a fidelity review judged (the meaning of the target)
    paper correspondence                                  what a correspondence review judged (target against paper)

Nothing here is stored or decides anything: a row is read from the receipt and the reviews the card already holds,
and no row implies another (a passing kernel says nothing of fidelity). Human-facing text shows the ladder and not
the derived status label as the verdict; `--json` keeps the label for compatibility.
"""

from __future__ import annotations

from typing import Any

NOT_ASSESSED = "not assessed"
ROWS = (("lean_kernel", "Lean kernel", "Lean kernel"),
        ("second_kernel", "second kernel", "second kernel"),
        ("axioms", "axioms and trusted base", "axioms"),
        ("statement_fidelity", "statement fidelity", "fidelity"),
        ("paper_correspondence", "paper correspondence", "paper"))
# The derived labels that read as a verdict when shown alone. Text for people names what the check did; the label
# itself stays in `--json`, the catalog's data and the filters.
HUMAN_LABEL = {"verified": "check passed", "verified-stale": "check passed, stale"}


def human_label(label: str) -> str:
    return HUMAN_LABEL.get(label, label)


def _row(key: str, state: str, detail: str | None = None, record: str | None = None, **more: Any) -> dict[str, Any]:
    title = next(t for k, t, _ in ROWS if k == key)
    return {"row": key, "title": title, "state": state, "detail": detail, "record": record, **more}


def _check_rows(proof: dict[str, Any] | None, receipt: dict[str, Any] | None) -> list[dict[str, Any]]:
    """The first three rows, from the receipt the card's proof rests on (`receipt`: its entry, with `admitted` and
    `stale_inputs`)."""
    keys = [k for k, _, _ in ROWS[:3]]

    def none(why: str) -> list[dict[str, Any]]:
        return [_row(k, NOT_ASSESSED, why) for k in keys]
    if proof is None or receipt is None:
        return none("no receipt")
    if proof["adapter"] != "lean-comparator":
        return none(f"a {proof['adapter']} check; no Lean kernel ran")
    if proof["assurance"] != "protected":
        return none("an exploratory receipt never counts")
    if not receipt["admitted"]:
        return none("the receipt is not on the trusted ref")
    if proof["verdict"] == "fail":
        observed = proof.get("kernel_results", {})
        rows = []
        for key, kernel in zip(keys[:2], ("lean", "nanoda")):
            outcome = observed.get(kernel)
            if outcome in ("passed", "failed"):
                rows.append(_row(key, outcome, f"{kernel} {outcome} during replay", proof["receipt"]))
            else:
                rows.append(_row(key, NOT_ASSESSED, "the receipt records no outcome for this kernel"))
        return [*rows, _row(keys[2], NOT_ASSESSED, "the protected check did not pass")]
    if proof["verdict"] != "pass":
        return none(f"the check ended with {proof['verdict']}")
    state, note = ("stale", "inputs changed since the check") if receipt["stale_inputs"] else ("passed", None)
    kernels = proof["kernels"] if isinstance(proof["kernels"], list) else []
    axioms = proof["permitted_axioms"]
    return [
        _row(keys[0], state if "lean" in kernels else NOT_ASSESSED,
             note if "lean" in kernels else "the receipt records no kernels", proof["receipt"]),
        _row(keys[1], state if "nanoda" in kernels else NOT_ASSESSED,
             note if "nanoda" in kernels else "nanoda did not replay it", proof["receipt"]),
        _row(keys[2], state if isinstance(axioms, list) else NOT_ASSESSED,
             f"axioms within {', '.join(map(str, axioms)) or 'none'}" + (f"; {note}" if note else "")
             if isinstance(axioms, list) else "the receipt records no axiom list", proof["receipt"])]


def _fidelity_row(fidelity: dict[str, Any] | None) -> dict[str, Any]:
    key = ROWS[3][0]
    if fidelity is None:
        return _row(key, NOT_ASSESSED, "no target or evaluator to review")
    label = fidelity["label"]
    if label == "faithful" or label.startswith("disputed"):
        older = "; an older review: it binds less, review again" if fidelity["legacy"] or fidelity["unbound"] else ""
        return _row(key, "faithful" if label == "faithful" else "disputed",
                    (label[len("disputed: "):] + ", " if label != "faithful" else "") + f"by {fidelity['by']}"
                    + ("; self-review: not independent" if fidelity["self_review"] else "") + older,
                    fidelity["review"], judgments=fidelity.get("judgments", []))
    return _row(key, "stale" if label.startswith("review stale") else NOT_ASSESSED,
                label if label != "not reviewed" else None)


def _correspondence_row(correspondence: dict[str, Any]) -> dict[str, Any]:
    rows = correspondence["statements"]
    detail = correspondence["note"]
    if rows:
        detail = "; ".join((f"{r['statement']}: " if r["statement"] else "") + f"{r['outcome']} by {r['by']}"
                           + (" (self-review: not independent)" if r["self_review"] else "") for r in rows)
    return _row(ROWS[4][0], correspondence["state"], detail, rows[0]["review"] if len(rows) == 1 else None,
                statements=[{k: r[k] for k in ("statement", "outcome", "review", "by", "created", "self_review")}
                            for r in rows])


UNINFORMATIVE = ("uninformative: the target names set-theoretic foundations and Lean always admits "
                 "Classical.choice, so the axiom list says little about what the statement assumes")


def rows(proof: dict[str, Any] | None, receipt: dict[str, Any] | None, fidelity: dict[str, Any] | None,
         correspondence: dict[str, Any], foundations: bool = False) -> list[dict[str, Any]]:
    """The five rows, in order. `proof` and `fidelity` are the card's entries of those names, `receipt` the entry of
    the receipt the proof rests on (or None), `correspondence` the result of `status.correspondence`. With
    `foundations` (the probes found set-theoretic foundations in the target), a passed axioms row says first that
    it is uninformative (`uninformative`: true); its state is unchanged."""
    out = [*_check_rows(proof, receipt), _fidelity_row(fidelity), _correspondence_row(correspondence)]
    axioms = out[2]
    if foundations and axioms["state"] in ("passed", "stale"):
        axioms.update(detail=UNINFORMATIVE + (f"; {axioms['detail']}" if axioms["detail"] else ""), uninformative=True)
    return out


def summary(ladder: list[dict[str, Any]]) -> str:
    """One line for a header: `Lean kernel passed · second kernel not assessed · ...`."""
    return " · ".join(f"{short} {row['state']}" + (" (uninformative)" if row.get("uninformative") else "")
                      for (_, _, short), row in zip(ROWS, ladder))


def lines(ladder: list[dict[str, Any]]) -> list[str]:
    out = []
    for row in ladder:
        record = f" [{row['record'].rsplit('/', 1)[-1]}]" if row["record"] else ""
        out.append(f"{row['title']}: {row['state']}" + (f" ({row['detail']})" if row["detail"] else "") + record)
    return out
