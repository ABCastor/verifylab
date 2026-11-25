# ADR 0017 — Selectable research context and attributed appraisals

Status: accepted for this implementation.

## Problem

Useful research must survive lost context. Proof receipts and meaning reviews establish their own contracts;
they do not say which known lemma, speculative bridge or useful failure deserves human attention or agent effort.
An external evaluator may return only categories, and forcing invented prose would misattribute its output.

## Decision

Keep optional readable problem, map and ideas homes discoverable in `[context]`; `vl init --context` scaffolds
defaults without replacing files. Agents choose rich, sparse or fresh context and their own research method.
Scoped `vl find` searches registered notes and validated reviews live; item/Lean indexing retains its existing cache.

Reuse immutable reviews with `kind = "appraisal"` and a required structured assessment. Its supplied context,
subject revision, versioned rubric, objective when relevant and optional local file hashes form a separate
applicability basis. Novelty and goal-relative usefulness are configurable defaults. Score/category-only output
is valid without prose, with available declared model/run/source provenance and raw output preserved. A later
explanation has its own author and may reference the original contribution.

Cards, compact context, search and exports retain contributions and disagreement; no aggregate, proof verdict,
fidelity label or admission is derived from them. Local basis freshness cannot detect later literature.
Researchers can ignore ratings.

## Consequences

Proof freshness remains independent of objective/rubric changes. Historical appraisals remain readable against
their saved rubric. An unknown/abstaining rating differs from low and from invalid output. Actual input context
and raw output become part of the record and export surface; ordinary project publication/privacy rules apply.
No model service, scheduler, mandatory routine, rival pair, human gate or measured efficiency claim is added.

The exact record and retrieval contracts live in [SPEC](../SPEC.md#contextual-appraisal-format); usage lives in
[GUIDE](../GUIDE.md#context-and-appraisals). The `verifylab` skill routes research-result improvement to its improve reference and orientation
to its orient reference.
