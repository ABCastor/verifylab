+++
id = "nth-prime"
kind = "result"
title = "solve(n) returns the n-th prime"
author = "agent:fixture"
created = "2026-10-01"
statement = "experiments/primes/solve.py:solve(n) returns the n-th prime (solve(1) == 2) on the evaluator's cases."
claim = "computation"
limits = ["Only the cases listed in research/evaluators/primes.py."]

[python]
evaluator = "research/evaluators/primes.py"
candidate = "experiments/primes/solve.py"
entry = "solve"
files = ["experiments/primes/helpers.py"]
+++
Planted-defect fixture item. The genuine candidate passes; every planted defect must not.
