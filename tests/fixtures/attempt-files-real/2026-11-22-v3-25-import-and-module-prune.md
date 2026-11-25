---
schema: vl.attempt/2
id: 2026-11-22-v3-25-import-and-module-prune
purpose: shorten
base: sample-baseline
claim: "Remove private imports from the toy module."
forecasts: []
outcome:
  status: accepted
  at: null
  commit: sample-revision
  value: {reached_lines: null, physical_lines: null, reached_declarations: null}
  cost: {agent_hours: null, human_hours: null, build_minutes: null, peak_ram_gb: null, disk_gb: null, usd: null}
  contract: {statements: same, axioms: [propext, Classical.choice, Quot.sound], comparator: accepted}
  evidence: logs/example.txt
scores: []
---

Remove private imports from the toy module.
