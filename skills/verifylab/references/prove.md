# Dismantle a claim

Reference for the `verifylab` skill ([SKILL.md](../SKILL.md)). Dismantle a claim into obligations, prove a frozen target, or construct a counterexample.

Start with `vl pin PROJECT --at REV`, then `vl inspect vl.lock NAME`. Identify the weakest conclusion and strongest premise. Use `vl graph vl.lock` to find a possible dependency and `vl inspect vl.lock NAME` to locate its source. Check the source when notation, namespaces or generated proofs make name matching ambiguous.

Write each obligation as a statement with its assumptions. Look for a concrete case that satisfies the assumptions and defeats the conclusion. Mark any unproved intermediate step explicitly. On a frozen VerifyLab item, start with `vl show ITEM --snapshot`, then `vl check ITEM --explore`. Run the checker directly. Integrate the frozen target onto the trusted ref before running `vl check ITEM` or submitting a meaning-bound review. Admit the resulting records on the trusted ref. Fill in `[origin]` when creating an item.

Finish with a protected receipt, a witnessed counterexample, or the smallest unproved obligation and its downstream use. See docs/FOREIGN.md and docs/SPEC.md (`vl docs FOREIGN`, `vl docs SPEC`).
