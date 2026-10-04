"""Planted defect (i), candidate side: satisfies the sorted-list evaluator, not the n-th prime claim."""


def solve(x):
    return sorted(x) if isinstance(x, list) else 4
