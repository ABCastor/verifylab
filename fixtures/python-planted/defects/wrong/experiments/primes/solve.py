"""Honest but wrong: the trial-division bound misses perfect squares, so 4, 9, 25... count as primes."""


def solve(n):
    count, k = 0, 1
    while count < n:
        k += 1
        if k >= 2 and all(k % d for d in range(2, int(k ** 0.5))):
            count += 1
    return k
