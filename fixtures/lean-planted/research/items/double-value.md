+++
id = "double-value"
kind = "result"
title = "double 21 is 42"
author = "human:fixture"
created = "2026-10-01"
statement = "double 21 = 42."
claim = "formal"

[lean]
target = "research/targets/double-value.lean"
theorems = ["VL.DoubleValue.main"]
proofs = { "VL.DoubleValue.main" = "rfl" }
+++
A closed decidable statement, used for the `native_decide` defect.
