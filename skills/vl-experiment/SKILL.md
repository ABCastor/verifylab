---
name: vl-experiment
description: Run exact computations, numerics or ML experiments for a VerifyLab research question so that the number means what it claims. Use when a hypothesis needs calculation, simulation, a benchmark or training runs rather than a proof.
---

# vl-experiment — numbers that mean what they claim

## Choose the right kind of claim
- **Exact computation**: a finite procedure with a checker independent of the code that produced
  the answer. It proves a universal claim only over a finite domain it covers exhaustively, every case
  certified by the checker; sampling a larger or an infinite domain can refute a universal claim, never
  prove it.
- **Numerics**: method, precision, tolerance and inputs stated; use certified intervals when
  available. Floating-point agreement is not an exact proof.
- **Empirical / ML**: protocol, data and splits, model, metric, compute, number of attempts. A run
  that finished is not evidence of a general advantage.
Keep an empirical result separate from any theorem it is meant to illustrate, and vice versa.

## Before spending
- Do the cheapest discriminating calculation first (algebra, a ten-line exact script, a tiny
  instance). Many expensive experiments are answered by it.
- Pre-register in the item body: the prediction, what result would refute it, the comparison
  rule, the budget. Then run.
- Validate the measurer before trusting a gain: gold against gold scores perfect, a known-bad
  input scores bad, the metric moves the right way on a planted change. An optimizer exploits its
  evaluator: accept only with exact or interval arithmetic and conservative worst cases; a
  continuous loss may guide a search, never decide acceptance.

## While running
- Change one variable at a time; re-tune nuisance parameters per arm; match baselines on compute
  and tuning; keep development and confirmation data apart; watch for leakage.
- Log every attempt, including the ones you would rather forget. A failure is a result.
- Tie every reported number to an artifact (file, commit, run id). No number from memory.
- Ask: could this number look perfect while the thing is wrong?

## Conjectures from data
- Tabulate small cases, find which features matter, then conjecture. Optima a search finds at small
  n show small n: say so before calling a construction general.
- State the range a "no counterexample found" search covered, next to the count.

## Recording
- For a computation with a trusted evaluator, record a `result` with `claim = "computation"` (only
  `formal` and `computation` results are checked; others are `recorded-<claim>`) and a `[python]` table:
  `evaluator` committed under `research/evaluators/` on the trusted ref (it defines `CASES` and
  `judge(case, output)`), `candidate`, `entry` (called once per case as `entry(case)`, returning JSON),
  optional `files`. Then run `vl check`. The candidate never sees or edits the evaluator.
- For ML runs, record a `result` with `claim = "empirical"` that points to the runner's own
  artifacts and states conditions and limits. VerifyLab does not run training; your project does.
- Spending money, GPUs or remote services follows the project's own approval rules.
