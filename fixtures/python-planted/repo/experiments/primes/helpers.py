def is_prime(k):
    return k >= 2 and all(k % d for d in range(2, int(k ** 0.5) + 1))
