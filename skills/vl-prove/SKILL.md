---
name: vl-prove
description: Prove or refute a precise statement in a VerifyLab project — Lean formalization, counterexamples, exact certificates, two-road attacks — without fooling yourself or the checker. Use whenever a statement is precise enough to be proved, disproved or computed exactly.
---

# vl-prove — prove, refute, certify

## Freeze the question first
- The statement lives in `research/targets/<id>.lean`, committed on the trusted ref, with every
  target theorem ending in `:= sorry`. Domain, dimension, premises and definitions are part of it.
- You never edit a target or the definitions it uses to make a proof go through. If the target is
  wrong, propose a new one and ask for a `fidelity` review; that is a different act from proving.
- Before proving, test non-vacuity: can the premises hold together? Is there a degenerate case
  (empty set, zero, trivial type) that makes the statement empty or trivial? A target theorem with
  hypotheses comes with a witness in the same target: a theorem that instantiates them on a concrete
  object (`theorem witness : ∃ n, 0 < n ∧ …`), listed in `[lean] theorems` and `[lean] witnesses`.
  `vl validate` warns without one; `vl check` probes whether automation alone closes a target theorem
  or refutes its hypotheses (`vacuous`).

## Attack from both sides
- Guess, refute, then prove the survivors: small instances, edge cases, a random model, guessed answers
  checked exactly. Build an instance with X but not Y before banking "X implies Y"; ask whether your method
  also proves something known to be false.
- Backward, by records: a reduction is a result of its own, target `theorem red : B₁ → … → Bₖ → T`, each `Bᵢ`
  a `def … : Prop` in the definitions-only module that the reduction's and the obligations' targets both import
  (the kernel then sees one constant on both sides). Each `Bᵢ` is an obligation: a `result` whose target is
  `theorem ob : Bᵢ`, with no proof yet (`unverified`). The reduction `uses` its obligations; say why each `Bᵢ`
  is easier than T, and give toy cases where each holds instead of a witness, since its hypotheses are the
  open problem (`vl validate` warns; expected). A `vacuous` reduction has jointly inconsistent obligations.
- Forward: verified results and library lemmas. The roads meet when an obligation's protected check passes
  with a forward term (`proofs = { ob = "Lib.lemma …" }`): B ⇒ T, not T ⇒ B. A gap `M → B` is a new
  obligation, not a meeting. An auxiliary problem says first whether its result or its method will be used.
- A dead road is a refuted obligation, a `vacuous` reduction, or a barrier model (an object with the known
  properties where the conclusion fails) whose `limits` say what it rules out and what not; it `refutes` only a
  road that claims exactly what it negates. Before closing a road, record what it proved and where it broke.
- A counterexample needs an exact certificate (exact arithmetic, a Lean term, a checker run); a near miss, a
  floating-point coincidence or a timeout is not one. Classify it: premises hold and the conclusion fails (find
  the hidden lemma), or a premise fails (replace it). Never patch a definition only to exclude it.
- A failed proof is not a refutation; a failed refutation is not a proof. A proof much easier than expected,
  stronger than asked, or that never uses a hypothesis indicts the target first. If a proof and a refutation of
  the same statement both look accepted, stop: a target, a definition or a checker is wrong; report an incident.

## Lean discipline
- Say exactly which declaration proves which target. A file that compiles, a proved helper and the
  requested theorem are different objects. `def claim : Prop := …` compiles and proves nothing.
- Fill the item's `[lean]` table: `proofs = { "<target theorem>" = "<term>" }`, where the term is
  written in scope of the target theorem's binders (e.g. `"Lib.lemma k n"`), plus `imports`; or
  `solution = "<module>"` that declares the target theorems itself.
- A new result needs its own target: write `research/targets/<id>.lean` in your lane (theorems
  ending in `:= sorry`, plus a witness theorem when they have hypotheses). It is a proposal until the coordinator
  admits it and a fidelity review accepts it. Never modify an existing target. Definitions the
  target needs must live in an in-project module that the target imports (keep it definitions-only,
  separate from the proofs), so the reviewer can read exactly what the statement means.
- Put new proofs in new modules when you can. Editing a shared module makes every receipt that
  depends on it stale until it is re-checked; that is correct, but say so in your report.
- In a lane, type-check a file with `vl lane exec <lane> -- lake lean <file>` (Lake builds the file's imports
  first, then elaborates it) or a module with `vl lane exec <lane> -- lake build <Module>`. Run
  `vl check <id> --explore` directly in the lane while iterating: it reads the target from your worktree,
  so it is weak and never counts. Only a protected check on the trusted state counts. Do not grep for
  `sorry` as a proof of soundness: the checker verifies statement equality, permitted axioms and kernel
  replay.
- No `sorry`, no new `axiom`, no `native_decide`: each use of `native_decide` adds an axiom of its own, which
  a check rejects unless `[lean] permitted_axioms` names it.
- A shortened or improved proof keeps the statement, or makes it stronger: the same hypotheses or weaker ones
  (stronger hypotheses make a weaker theorem) and the same conclusion or a stronger one. Say which implication
  between the old and the new statement holds, and record any change of the statement as a change.

## Record
A `result` item (`claim = "formal"` or `"computation"`) with statement, assumptions, limits and the
declaration names; every hypothesis of the target theorems stated in `assumptions` in plain words
(`vl validate` warns when the target has some and `assumptions` is empty); no process state in them
("exploratory check only", "not yet admitted": the status is derived, and `vl validate` warns); a
`refutes = [...]` link for a counterexample; `answers = ["<question>"]` only with the question's own
`[lean]` target and theorems, or its own `[python]` evaluator and entry (`vl validate` reports any other
as an error); failed attempts that rule out a route as a short note on the question or conjecture. Then
hand over to the coordinator for the protected check and admission.
