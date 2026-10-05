---
name: vl-explore
description: Find literature, distant analogies, new hypotheses, conjectures and new roads for a VerifyLab research question, and preserve useful leads with their reasons and limits. Use when an attack is missing, stuck, or needs ideas from another field, older papers, or other people's lemmas.
---

# vl-explore — sources, analogies, hypotheses, roads

Explore within the current VerifyLab research project. Choose how much prior strategy to read: broad context,
a few supports or a deliberately independent start. Sources, analogies and hypotheses remain leads until the
appropriate evidence supports a result; a conjecture need not already have a test.

## Sources
- Record sources you rely on or expect to reuse as items (`kind = "source"`) with an exact `ref` (DOI, arXiv id, URL,
  file path, Lean declaration) and an honest `access`: `full-text-read`, `abstract-only`,
  `citation-only` or `secondary`. Citing a paper is not verifying its proposition. `vl find` searches
  titles, statements, limits, assumptions, bodies and `ref`; no duplicate identifier in the prose is needed.
- Resolve identifiers mechanically before you rely on them. Fabricated citations happen; a
  plausible theorem from a paper you have not read is a hypothesis.
- Use existing work when it helps, and check it before claiming novelty: the project (`vl find`: its items and the Lean
  declarations under `[lean] roots`), the proof library (search its local sources yourself, for
  example in `.lake/packages/`; `vl find` does not index dependencies), then the literature with the
  tools and permissions your harness gives you. Never send private research content to an external
  search service without the owner's permission.

## Distant analogies (a physics paper for a tokenizer, a geometry for a bound)
Develop a consequential analogy so its gaps can be examined; an initial note may contain only part of this:
1. State the structure without the domain's names.
2. Give the exact source and the object-to-object map.
3. Say where the map can break (which assumption has no counterpart).
4. Derive one consequence that is checkable in the target domain.
5. Check that it opens real work here. Distance is not merit.

## Walls and roads
- When useful, map the walls: barriers, near-counterexamples, strategy classes that fail, each with its reason. A
  near-miss model that keeps the known properties and breaks the conclusion says what any proof must use.
- Ways to a new road: restate in another space; generalize until tractable; make the parameter that extremal
  examples need unboundedly the variable; import one distant idea into an otherwise standard attack.
- A developed road names its endpoint, a useful next step and reasons to reconsider it when known. A new
  lead may first need study, definitions or a microgoal. Roads forward and
  backward meet only at a B with B ⇒ T, not merely T ⇒ B (`vl-prove` records reductions and obligations).
- Test whether a new definition makes useful consequences easier or unlocks an obligation. Two short corollaries
  can be a useful heuristic; an exploratory definition need not already have them.
- Assess bounded contributions against the owner's goal even when reproducing the whole result is too costly:
  reusable lemmas, simpler certificates, weaker assumptions or application bridges may have their own value.
  Give reasons specific to the proposed contribution when closing it; this does not require pursuing every lead.

## Hypotheses and conjectures
- Keep provisional hypotheses in a notebook; promote consequential or reusable ones to items with the idea, assumptions, possible contribution and gaps.
  Add refuting evidence, a next investigation and cost when known; a lead need not have a cheap test.
- Choose priorities for the task; expected information per cost is one useful heuristic. A distant lead may
  justify substantial study before a discriminating test exists. State the reason and uncertainty.
- Optional usefulness/promise appraisals may include numbers in the body/notebook, with their meaning, goal
  and reason. Treat self-ratings and repeated endorsements as clues to inspect, not independent support.
- Broad idea generation and focused development serve different purposes; no fixed number of ideas is required.
- Before a strong novelty claim, search the literature and seek an independent judgment. Storing a lead does
  not require that ceremony.

## Output
Consequential `source`, `conjecture` or `question` items with citations or body links to what motivated them.
Use `uses` for actual dependencies and `answers` only when its question-binding contract is met; a short notebook account of provisional and discarded leads and their limits. Preserve the reusable
connection or method, its prerequisites, the missing bridge and source pointers for later sessions; reading
these notes remains task-dependent.
Keep condensed observations scoped, with their conditions, unresolved bridges, dated or pinned provenance
and evidence that can be reopened. Body links are pointers, not checks of their contents;
before relying on a saved interpretation, reread its load-bearing evidence and derive current status with `vl show`.
