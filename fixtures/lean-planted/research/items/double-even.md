+++
id = "double-even"
kind = "result"
title = "Doubling gives an even number"
author = "human:fixture"
created = "2026-10-01"
statement = "For every natural number n, double n is even."
claim = "formal"
limits = ["Natural numbers only."]

[lean]
target = "research/targets/double-even.lean"
theorems = ["VL.DoubleEven.main"]
proofs = { "VL.DoubleEven.main" = "Fixture.double_mod_two n" }
imports = ["Fixture.Proofs"]
+++
The genuine result of the planted-defect fixture. Each case in `cases/` replaces this `[lean]` table
and overlays files on the candidate worktree.
