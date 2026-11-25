---
schema: vl.attempt/2
id: 2026-11-22-remove-redundant-hypothesis
purpose: shorten
base: 1d7b3e5
claim: Remove the redundant natural-number hypothesis and its induction-hypothesis application in main_theorem.
forecasts:
  - by: agent:codex
    at: 2026-11-22T23:39+01:00
    p_accepted: 0.65
    value: {physical_lines: [-2, -1]}
    cost: {agent_hours: [0.02, 0.16], build_minutes: [0, 0]}
    basis: The source describes the hypothesis as unnecessary and the induction hypothesis should no longer require an argument; Lean cannot be run here.
outcome:
  status: not-run
  at: 2026-11-22T23:39+01:00
  commit: a629f19
  value: {physical_lines: null}
  cost: {agent_hours: null, build_minutes: null}
  contract: {statements: null, axioms: null, comparator: null}
  evidence: Toy/Basic.lean
---

## What was tried
The candidate for `Toy.main_theorem` in `Toy/Basic.lean` was not run.

## Decisive evidence
The other candidate shortened `Toy/Basic.lean` without changing `Toy.main_theorem`.

## What would justify trying again
A Lean build of `Toy.main_theorem` would resolve whether this edit elaborates.
