---
schema: vl.attempt/2
id: 2026-11-22-remove-unused-helper
purpose: shorten
base: 1d7b3e5
claim: Remove the unused sumTo_zero_add helper to shorten the file without changing main_theorem.
forecasts:
  - by: agent:codex
    at: 2026-11-22T23:39+01:00
    p_accepted: 0.85
    value: {physical_lines: [-5, -3]}
    cost: {agent_hours: [0.01, 0.12], build_minutes: [0, 0]}
    basis: The helper has a docstring and a short proof and has no visible use in the file; no build is available.
outcome:
  status: abandoned
  at: 2026-11-22T23:39+01:00
  commit: a629f19
  value: {physical_lines: -4}
  cost: {agent_hours: null, build_minutes: null}
  contract: {statements: unchanged, axioms: null, comparator: null}
  evidence: Toy/Basic.lean
---

## What was tried
Removed `Toy.sumTo_zero_add` from `Toy/Basic.lean`.

## Decisive evidence
`wc -l` measured 35 lines before and 31 after in `Toy/Basic.lean`; `Toy.main_theorem` is unchanged. No Lean build or Comparator was available. The edit remains for review, but verification was abandoned.

## What would justify trying again
A Lean build and downstream check would establish whether removing `Toy.sumTo_zero_add` is safe.
