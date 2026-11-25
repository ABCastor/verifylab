---
schema: vl.attempt/2
id: 2026-11-22-direct-dependencies
purpose: understand
base: 1d7b3e5
claim: Remove the unused hypothesis and unused helper, retaining the existing proof with direct visible dependencies.
forecasts:
  - by: codex/unknown
    at: 2026-11-22T23:40+01:00
    p_accepted: 0.75
    value: {identifiable_dependencies: [1, 1]}
    cost: {agent_hours: [0.02, 0.3], build_minutes: [0, 0]}
    basis: The source explicitly identifies an unused hypothesis and helper; retaining the existing proof limits changes. No build is possible.
outcome:
  status: accepted
  at: 2026-11-22T23:40+01:00
  commit: 1dd94f8
  value: {identifiable_dependencies: null, physical_lines: -4}
  cost: {agent_hours: null, build_minutes: null}
  contract: {statements: unverified, axioms: null, comparator: not-run}
  evidence: Toy/Basic.lean
---

## What was tried
The forecast timestamp was entered approximately and is later than the actual forecast commit; Git commit order, rather than that timestamp, establishes the sequence.

Removed the unused hypothesis and helper in `Toy/Basic.lean`, `Toy.main_theorem` and `Toy.sumTo_zero_add`.

## Decisive evidence
In `Toy/Basic.lean`, `Toy.main_theorem` uses `Toy.sumTo_succ'`, which uses `Toy.sumTo_succ`; the Git diff measured 4 fewer physical lines; no student trial or Lean build was possible. Acceptance means the change was kept, not that it was verified.

## What would justify trying again
Build `Toy/Basic.lean`, `Toy.main_theorem` and ask a first-course student to trace its dependencies.
