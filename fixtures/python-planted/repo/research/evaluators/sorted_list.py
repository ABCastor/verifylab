"""Trusted evaluator for sorted-list: solve(xs) must return xs sorted ascending."""

CASES = [[3, 1, 2], [], [5, 5, -1], [9, 8, 7, 6, 5, 4, 3, 2, 1, 0]]


def judge(case, output):
    expected = sorted(case)
    if type(output) is not list or not all(type(v) is int for v in output) or output != expected:
        return False, f"solve({case}) returned {output!r:.60}, expected {expected}"
    return True, "ok"
