# ADR 0010 — The statement probes list what a referee must weigh

Status: accepted, 2026-10-11 (owner). Extends the statement probes (docs/ARCHITECTURE.md, "Statement probes").

Context: auditing an external corpus (416 Comparator challenges, 396 with definitions of their own) moves the cost
from proof checking to fidelity. Comparator establishes that the definitions in a statement's closure are the
trusted ones, never that they mean what the question asks, and Lean gives every operation a value everywhere:
`n - 1 + 1 = n` is false at 0, `x / 0 = 0`, a divergent `tsum` is 0, `deriv` of a non-differentiable function is 0
(docs/CHEATS.md S8, S15). A referee found these by reading; nothing named them, so an unattended review could skip
them, and a review could say `faithful` without ever answering for a definition.

Decision: the probe metaprogram, which already reads each target theorem's type on the pre-candidate copy of the
challenge build, also records per theorem in `checked.probes` (receipt format `vl.receipt/1`, optional keys):
- `definitions`: the in-project declarations its type reaches through the types and values of in-project
  declarations (modules under `[lean] roots`, the target, the renamed trusted copies), as a referee reads them;
- `totalized`: subterms of its type, of those definitions' values and of the constructors of those inductive types (the fields of a
  structure) that are totalized operations (`op`, `term`,
  `in`, `why`): ℕ subtraction, ℕ and ℤ division and modulo, division and inverse in a group with zero, real and
  integer powers, casts into `Fin`/`ZMod` or of a truncated value, and named constants (`Real.sqrt`, `Real.log`,
  `tsum`, `MeasureTheory.integral`, `deriv`, `Classical.choose`, `sSup`, `Nat.card`, …), at most 40;
- `boundary`: for each of its first two ℕ binders set to 0, 1 and 2, the values where the battery refutes the
  hypotheses, and those where it closes the instance while it closes neither at another of the values.
Cards show them; `vl validate` warns about each in-project definition no admitted fidelity review of the current
meaning names (by its full name or last component), and, until a review finds the target faithful, about the
totalized operations and boundary values. The referee reference of the `verifylab` skill maps every hypothesis and quantifier of the
informal claim to the target and answers the definition question for each definition by name.

Consequences: none of this changes a verdict or a status; the triviality and vacuity semantics are unchanged.
Boundary values run after every theorem's main probes, so a probe timeout during them leaves `boundary` absent and
`probe_run.boundary_unfinished`, never "probes incomplete". The cost is bounded by the per-attempt heartbeats (at
most 6 instances per theorem, each tried by the battery at most twice) and the probe timeout; on the fixtures it
adds under a second to a probe run of a few seconds. Naming a definition is a nudge, not proof that it was read. A totalized operation
inside a library definition, or behind a guard the probe cannot see, is not reported; a reported one may be
guarded by a hypothesis: the referee judges. Older receipts have no such keys and ask nothing.
