"""Planted defect (f): kills its own process in the middle of the run, before any result is written."""
import os


def solve(n):
    os._exit(0)
