---
schema: vl.attempt/2
id: 2026-11-22-inline-helper
purpose: understand
base: 1d7b3e5
claim: Inline the swapped successor helper and remove unused declarations so the main proof has fewer named dependencies.
forecasts:
  - by: codex/unknown
    at: 2026-11-22T23:40+01:00
    p_accepted: 0.6
    value: {identifiable_dependencies: [1, 1]}
    cost: {agent_hours: [0.03, 0.3], build_minutes: [0, 0]}
    basis: Fewer helpers could ease navigation, but changing the rewrite sequence risks a Lean error that cannot be tested here.
outcome:
  status: not-run
  at: 2026-11-22T23:40+01:00
  commit: 1dd94f8
  value: {identifiable_dependencies: null}
  cost: {agent_hours: null, build_minutes: null}
  contract: {statements: unverified, axioms: null, comparator: not-run}
  evidence: Toy/Basic.lean
---

## What was tried
The forecast timestamp was entered approximately and is later than the actual forecast commit; Git commit order, rather than that timestamp, establishes the sequence.

Not run: `Toy/Basic.lean`, `Toy.main_theorem` retains `Toy.sumTo_succ'`.

## Decisive evidence
No measurement: `Toy/Basic.lean`, `Toy.sumTo_succ'` was not inlined.

## What would justify trying again
Try if a student finds `Toy/Basic.lean`, `Toy.sumTo_succ'` an obstacle and a Lean build is available.
