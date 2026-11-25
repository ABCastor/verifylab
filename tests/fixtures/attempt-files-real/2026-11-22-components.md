---
schema: vl.attempt/2
id: 2026-11-22-components
purpose: reuse
base: sample-baseline
claim: Replace successive append steps by a single concatenation of the list of parts
  and reuse its length lemma.
forecasts:
- by: model-fixture/high
  at: '2026-11-22T21:38:42+00:00'
  p_accepted: 0.3
  value:
    reached_lines:
    - -780
    - 35
  cost:
    agent_hours:
    - 0.5
    - 3
    build_minutes:
    - 0
    - 30
    peak_ram_gb:
    - 0
    - 9
    usd:
    - 0
    - 0
  basis: 'The toy proof has one path and the other branch already reuses the helper;
    no paid calls planned.'
outcome:
  status: not-run
scores:
- what: novelty
  value: 2
  by: model-fixture/high
  at: '2026-11-22T21:38:42+00:00'
  basis: The toy proof uses the same equality as the example text.
---
Replace successive append steps by a single concatenation of the list of parts and reuse its length lemma.
