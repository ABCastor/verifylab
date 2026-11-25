---
schema: vl.attempt/2
id: 2026-11-22-return-certificate
purpose: shorten
base: tiny-fixture-baseline
claim: Return the certificate directly and remove the unused helper.
forecasts:
  - by: example-model
    at: 2026-11-22T12:00Z
    p_accepted: 0.8
    value: {physical_lines: [-4, -2]}
    cost: {build_minutes: [0, 1]}
    basis: Tiny.conclusion supplies the proof needed by the helper.
---
Open candidate. Check the source in `Tiny.lean` before making the edit.
