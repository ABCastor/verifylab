+++
id = "replay-example"
kind = "result"
title = "Replay fixture"
author = "agent:fixture"
created = "2026-10-01"
statement = "Four sample Lean declarations exercise the replay input bindings."
claim = "formal"

[lean]
target = "research/targets/replay-example.lean"
theorems = ["VL.Replay.first", "VL.Replay.second", "VL.Replay.third", "VL.Replay.fourth"]
proofs = { "VL.Replay.first" = "Nat.add_zero n", "VL.Replay.second" = "rfl", "VL.Replay.third" = "h", "VL.Replay.fourth" = "fun h => h" }
imports = ["ReplayFixture.Support"]
+++
Sample checked-input bindings for replay tests.
