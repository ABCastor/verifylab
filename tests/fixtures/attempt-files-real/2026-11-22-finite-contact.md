---
schema: vl.attempt/2
id: 2026-11-22-finite-contact
purpose: new-route
base: sample-baseline
claim: Use a finite counter to bound the work in the recursive function.
  Compare the two branches for each value up to the bound
  and remove the unused loop.
forecasts:
- by: model-fixture/high
  at: '2026-11-22T22:03:32+00:00'
  p_accepted: 0.12
  value:
    reached_lines:
    - -2900
    - -45
  cost:
    agent_hours:
    - 1
    - 3
    build_minutes:
    - 2
    - 50
    peak_ram_gb:
    - 1
    - 10
    usd:
    - 0
    - 0
  basis: The toy proof has one path for each count; the source is small enough
    to read by hand; no paid calls planned.
outcome:
  status: not-run
scores:
- what: novelty
  value: 4
  by: model-fixture/high
  at: '2026-11-22T22:03:32+00:00'
  basis: The toy proof uses the same equality as the example text.
---
Use a finite counter to bound the work in the recursive function. Compare the two branches for each value up to the bound and remove the unused loop.
