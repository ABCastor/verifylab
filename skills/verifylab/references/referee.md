# Find traps

Reference for the `verifylab` skill ([SKILL.md](../SKILL.md)). Find mathematical traps and judge whether a pinned formal statement matches the intended claim.

Start with `vl pin PROJECT --at REV`, then `vl inspect vl.lock NAME --json`. Read the signature before reading a summary. Test whether the conclusion is supplied as a hypothesis, whether the domain has an inhabitant, and which totalized operations change boundary cases. Use the output to locate the statement, dependency declarations and source indicators. Use an elaborated check for the axioms used by the proof and a protected receipt for recorded kernel outcomes.

For an admitted VerifyLab item start with `vl show ITEM --snapshot`, then `vl show ITEM --json`. The snapshot contains pinned source, including comments. It omits earlier reviews. Read back the claim independently, compare it to the intended statement and name any quantifier or definition that changed. Bind fidelity and correspondence reviews to the current trusted meaning with `--expected-meaning-digest`. A correspondence review records the paper statement and the direction of any difference. Current adverse fidelity reviews remain visible until their author explicitly supersedes them.

Use the checklist in docs/CHEATS.md and docs/SPEC.md (`vl docs CHEATS`, `vl docs SPEC`).

- The proof is not a proof: `sorry` or `admit`, an added `axiom`, `native_decide`, the theorem never declared (P1, P3, P5, P7). The permitted axioms are `propext`, `Classical.choice` and `Quot.sound`: a list of what is allowed, never of what is forbidden.
- Another statement under the pinned name, or the same text with another meaning through a local notation, instance or definition (S1, S2).
- A weak or trivial target: `P ∨ True`, a definition that is constantly `True`, an answer slot filled with the question (S4, S5).
- Hypotheses that cannot hold, or hold only at 0 or on an empty range: ask for a witness that exercises the claim (S6). A hypothesis that already carries the conclusion (S7).
- Junk values of total functions: `-` on ℕ, `x / 0`, `sqrt` and `log` outside their domain, a divergent `tsum` (S8). Coercions and `Fin` wraparound (S9).
- A free name captured as an implicit variable; quantifier order; 0- against 1-indexing and precedence (S10, S11, S12).
- Hypotheses missing, added or changed against the source, and a Lean statement narrower than the prose: one point of a family, a lemma shown as the headline (S13, S14). A definition detached from the object it names (S15).
- The statement written by whoever wrote the proof (R8); the target, the checker or its configuration edited by the candidate (R2); an exploratory pass read as verification (R9); a success reported and never re-run (R10); a pipe that hides a failing exit code (R15).

Finish with the counterexample, the exact differing premise, or a meaning-bound review explaining the correspondence and its limits.
