"""Planted defect (e'): disarm the per-case timer inside the call, then never return."""
import signal


def solve(n):
    signal.setitimer(signal.ITIMER_REAL, 0)
    signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGALRM})
    while True:
        pass
