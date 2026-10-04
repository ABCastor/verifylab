+++
id = "sorted-list"
kind = "result"
title = "solve(xs) sorts a list of integers"
author = "agent:fixture"
created = "2026-10-01"
statement = "experiments/sorting/solve.py:solve(xs) returns xs sorted ascending on the evaluator's cases."
claim = "computation"
limits = ["Only the cases listed in research/evaluators/sorted_list.py."]

[python]
evaluator = "research/evaluators/sorted_list.py"
candidate = "experiments/sorting/solve.py"
entry = "solve"
+++
Second genuine control; its evaluator is also the lenient target of planted defect (i).
