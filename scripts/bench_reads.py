"""Time `vl show` and `vl validate` on a synthetic repository: `python3 scripts/bench_reads.py [--items N] [DIR]`.

Builds, in DIR (default: a new temporary folder), a git repository with N result items of about 1.6 KB, each with
one admitted receipt and every tenth with a review, all committed on the trusted ref, then runs each command a few
times in a fresh process and prints the best wall time. The machine file and tools store point into DIR, so the
account's own set-up is neither read nor written. Run it from the checkout whose `vl` is measured (it imports
`verifylab` from `src/`).

Measured with 1,000 items on one laptop while a Lean check ran beside it, best of 3: one `git cat-file` process per
object read, then one `git cat-file --batch` per command:

    vl show ID            2.75 s -> 0.24 s
    vl show ID --brief    2.78 s -> 0.26 s
    vl validate          16.66 s -> 1.19 s
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from verifylab.records import seal_receipt, seal_review, sha256_hex, write_new_json  # noqa: E402

BODY = "Synthetic body text for a timing run. " * 30
ITEM = """+++
id = "{id}"
kind = "result"
title = "Synthetic result {n}"
author = "agent:bench"
created = "2026-10-02"
statement = "The value of case {n} is what the evaluator expects."
claim = "computation"
limits = ["Synthetic."]
uses = {uses}
[python]
evaluator = "research/evaluators/judge.py"
candidate = "solution.py"
entry = "solve"
+++
{body}
"""


def git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)


def build(root: Path, n: int) -> list[str]:
    root.mkdir(parents=True)
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "vl.trustedRef", "main")
    git(root, "config", "user.name", "Bench")
    git(root, "config", "user.email", "bench@example.com")
    (root / "research" / "evaluators").mkdir(parents=True)
    (root / "research" / "items").mkdir()
    (root / "research" / "vl.toml").write_text('[project]\nname = "bench"\n')
    (root / "research" / "evaluators" / "judge.py").write_text("CASES = [1]\ndef judge(case, output):\n    return True\n")
    (root / "solution.py").write_text("def solve(case):\n    return case\n")
    files = {"solution.py": sha256_hex((root / "solution.py").read_bytes())}
    ids = [f"r{k:05d}" for k in range(n)]
    from verifylab.records import parse_item, question_digest
    for k, item_id in enumerate(ids):
        uses = f'["{ids[k - 1]}"]' if k else "[]"
        path = root / "research" / "items" / f"{item_id}.md"
        path.write_text(ITEM.format(id=item_id, n=k, uses=uses, body=BODY))
        item = parse_item(path.read_bytes(), f"research/items/{item_id}.md")
        receipt = seal_receipt(dict(
            item=item_id, item_revision=item.revision, question_digest=question_digest(item), adapter="python-eval",
            assurance="protected", verdict="pass", reasons=[], inputs={"digest": "d", "files": files},
            environment={}, checked={}, command=["vl"], started_at="2026-10-02T10:00:00+00:00",
            finished_at="2026-10-02T10:00:01+00:00", tool_version="vl bench"))
        write_new_json(root / "research" / "evidence" / item_id / f"{receipt['receipt_id'][:16]}.json", receipt)
        if k % 10 == 0:
            review = seal_review(dict(item=item_id, item_revision=item.revision, kind="understanding",
                                      author="agent:bench", text="read it", created="2026-10-02T11:00:00+00:00"))
            write_new_json(root / "research" / "reviews" / item_id / f"{review['review_id'][:16]}.json", review)
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "synthetic records")
    return ids


def best(root: Path, env: dict[str, str], args: list[str], runs: int) -> float:
    code = "import sys; from verifylab.cli import main; sys.exit(main(sys.argv[1:]))"
    times = []
    for _ in range(runs):
        start = time.perf_counter()
        proc = subprocess.run([sys.executable, "-c", code, *args], cwd=root, env=env, capture_output=True)
        times.append(time.perf_counter() - start)
        if proc.returncode not in (0, 1):
            raise SystemExit(f"vl {' '.join(args)} exited {proc.returncode}: {proc.stderr.decode()[-500:]}")
    return min(times)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("dir", nargs="?", help="where to build the repository (default: a temporary folder)")
    parser.add_argument("--items", type=int, default=1000)
    parser.add_argument("--runs", type=int, default=3)
    args = parser.parse_args()
    base = Path(args.dir) if args.dir else Path(tempfile.mkdtemp(prefix="vl-bench-"))
    root = base / "repo"
    ids = build(root, args.items)
    env = {**os.environ, "PYTHONPATH": str(SRC), "XDG_CONFIG_HOME": str(base / "xdg-config"),
           "XDG_DATA_HOME": str(base / "xdg-data")}
    print(f"{args.items} items, {args.items} receipts, {len(range(0, args.items, 10))} reviews in {root}")
    for label, cmd in (("vl show ID", ["show", ids[-1]]), ("vl show ID --brief", ["show", ids[-1], "--brief"]),
                       ("vl validate", ["validate"])):
        print(f"{label:<20} {best(root, env, cmd, args.runs):7.2f} s (best of {args.runs})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
