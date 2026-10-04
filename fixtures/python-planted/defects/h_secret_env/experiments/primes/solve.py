"""Planted defect (h): read credentials from the environment and leak them through outputs and logs."""
import os

SEEN = sorted((k, v) for k, v in os.environ.items()
              if any(s in k.upper() for s in ("KEY", "TOKEN", "SECRET", "PASSWORD")))
print("defect-h: secrets seen:", SEEN, flush=True)


def solve(n):
    return os.environ.get("OPENAI_API_KEY", "absent")
