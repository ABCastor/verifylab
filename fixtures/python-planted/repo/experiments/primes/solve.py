"""Genuine candidate: the n-th prime by trial division (uses a helper module listed in `files`)."""

from helpers import is_prime


def solve(n):
    count, k = 0, 1
    while count < n:
        k += 1
        if is_prime(k):
            count += 1
    return k
