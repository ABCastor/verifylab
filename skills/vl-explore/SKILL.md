---
name: vl-explore
description: Find literature, distant analogies, new hypotheses, conjectures and new roads for a VerifyLab research question, and rank them by what a cheap test could teach. Use when an attack is missing, stuck, or needs ideas from another field, older papers, or other people's lemmas.
---

# vl-explore — sources, analogies, hypotheses, roads

Exploration produces leads. A lead becomes a result only through `vl-prove` or `vl-experiment`.

## Sources
- Record sources you rely on or expect to reuse as items (`kind = "source"`) with an exact `ref` (DOI, arXiv id, URL,
  file path, Lean declaration) and an honest `access`: `full-text-read`, `abstract-only`,
  `citation-only` or `secondary`. Citing a paper is not verifying its proposition. `vl find` searches
  titles, statements, limits, assumptions, bodies and `ref`; no duplicate identifier in the prose is needed.
- Resolve identifiers mechanically before you rely on them. Fabricated citations happen; a
  plausible theorem from a paper you have not read is a hypothesis.
- Search what already exists before rebuilding it: the project (`vl find`: its items and the Lean
  declarations under `[lean] roots`), the proof library (search its local sources yourself, for
  example in `.lake/packages/`; `vl find` does not index dependencies), then the literature with the
  tools and permissions your harness gives you. Never send private research content to an external
  search service without the owner's permission.

## Distant analogies (a physics paper for a tokenizer, a geometry for a bound)
Write the analogy so it can fail:
1. State the structure without the domain's names.
2. Give the exact source and the object-to-object map.
3. Say where the map can break (which assumption has no counterpart).
4. Derive one consequence that is checkable in the target domain.
5. Check that it opens real work here. Distance is not merit.

## Walls and roads
- List the walls first: barriers, near-counterexamples, strategy classes that fail, each with its reason. A
  near-miss model that keeps the known properties and breaks the conclusion says what any proof must use.
- Ways to a new road: restate in another space; generalize until tractable; make the parameter that extremal
  examples need unboundedly the variable; import one distant idea into an otherwise standard attack.
- A road names its endpoint, its first checkable step and what would make you abandon it. Roads forward and
  backward meet only at a B with B ⇒ T, not merely T ⇒ B (`vl-prove` records reductions and obligations).
- Test whether a new definition makes useful consequences easier or unlocks an obligation. Two short corollaries
  can be a useful heuristic; an exploratory definition need not already have them.

## Hypotheses and conjectures
- Keep provisional hypotheses in a notebook; promote consequential or reusable ones to items with: the prediction, the assumptions, the evidence
  that would refute it, the cheapest informative test, and its cost.
- Rank by expected information per cost, not by excitement. A rank is a calibratable prediction,
  never a verdict; if you want it kept, write it with its reason in the item's body.
- Prefer the hypothesis that a small exact computation or a short Lean statement can test today.
- Many vague ideas with no discriminating test is a failure mode. Three sharp ones beat twenty.
- Novelty is judged by an agent that did not produce the result, after a literature search.

## Output
Consequential `source`, `conjecture` or `question` items, each linked (`cites`, `uses`, `answers`) to what
motivated it; a short notebook account of provisional and discarded leads and why they were discarded.
