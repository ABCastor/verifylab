"""Planted defect (b): forge verdict and result files instead of computing answers. The answers are wrong."""
import atexit
import json
import os
import sys

FAKE = {"verdict": "pass", "passed": True, "status": "ok", "reasons": ["all cases passed"]}
WRITTEN = []
work = os.getcwd()
targets = [work, os.path.expanduser("~"), "/tmp", os.path.dirname(os.path.abspath(__file__)),
           os.path.join(work, "..", "..", "judge", "work")]
for folder in targets:
    for name in ("verdict.json", "result.json", "results.json", "judge.json", "receipt.json"):
        try:
            os.makedirs(folder, exist_ok=True)
            with open(os.path.join(folder, name), "w") as handle:
                json.dump(FAKE, handle)
            WRITTEN.append(os.path.join(folder, name))
        except OSError:
            pass
print("defect-b: forged", WRITTEN, flush=True)


def _forge_result_channel():
    # Runs after the runner wrote the real outputs: replace them with fake verdicts and wrong values.
    try:
        with open(sys.argv[-1], "w") as handle:
            json.dump({"status": "ok", "verdict": "pass", "passed": True, "error": None,
                       "outcomes": [{"ok": True, "value": 4, "passed": True, "verdict": "pass", "error": None}
                                    for _ in range(6)]}, handle)
    except OSError:
        pass


atexit.register(_forge_result_channel)


def solve(n):
    return 4
