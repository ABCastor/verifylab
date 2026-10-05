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
- Consider a cheap discriminating calculation (algebra, a ten-line exact script, a tiny instance) when it
  addresses the question. Some investigations first need method development or study.
- For a confirmatory comparison, pre-register the prediction, refuting outcome, comparison rule and budget
  before running. For exploration, record purpose and conditions and distinguish hypotheses formed after
  observing results; later confirmation needs fresh evidence.
- Validate the measurer before trusting a gain: gold against gold scores perfect, a known-bad
  input scores bad, the metric moves the right way on a planted change. An optimizer exploits its
  evaluator. For a mathematically certified numerical claim, acceptance needs exact or certified interval
  arithmetic and conservative bounds; a search loss alone is not certification. For empirical claims, use the
  stated statistical comparison, uncertainty and held-out confirmation; do not call that an exact proof.

## While running
- Choose an experimental design that separates the effects you need to estimate (one variable at a time is one
  option, not a universal rule); re-tune nuisance parameters per arm; match baselines on compute
  and tuning; keep development and confirmation data apart; watch for leakage.
- Preserve all trials and tuning outcomes relevant to the reported comparison, including failures; omitting
  them can create selection bias. Routine tool invocations need not each become a research item.
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
- Preserve a short reusable account of setup, observation, limits and next question, linked to artifacts;
  speculative interpretations remain explicit.
- Spending money, GPUs or remote services follows the project's own approval rules.
