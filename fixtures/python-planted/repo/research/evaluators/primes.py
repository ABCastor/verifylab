"""Trusted evaluator for nth-prime: solve(n) must return the n-th prime, with solve(1) == 2."""

CASES = [1, 2, 3, 6, 25, 100]
TIMEOUT_S = 0.5


def _nth_prime(n):
    count, k = 0, 1
    while count < n:
        k += 1
        if all(k % d for d in range(2, int(k ** 0.5) + 1)):
            count += 1
    return k


def judge(case, output):
    expected = _nth_prime(case)
    if type(output) is not int or output != expected:
        return False, f"solve({case}) returned {output!r:.60}, expected {expected}"
    return True, "ok"
