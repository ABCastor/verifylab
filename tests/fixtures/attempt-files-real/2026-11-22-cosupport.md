---
schema: vl.attempt/2
id: 2026-11-22-cosupport
purpose: reuse
base: sample-baseline
claim: Restrict the comparison to the keys present in the input list and reuse
  the existing lookup lemma.
forecasts:
- by: model-fixture/high
  at: '2026-11-22T21:38:42+00:00'
  p_accepted: 0.2
  value:
    reached_lines:
    - -2500
    - 0
  cost:
    agent_hours:
    - 0.6
    - 3
    build_minutes:
    - 0
    - 26
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
  value: 3
  by: model-fixture/high
  at: '2026-11-22T21:38:42+00:00'
  basis: The toy proof uses the same equality as the example text.
---
Restrict the comparison to the keys present in the input list and reuse the existing lookup lemma.
