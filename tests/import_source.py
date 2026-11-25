"""A synthetic foreign Git history for offline resolver tests. No verifier or external checkout required."""
from pathlib import Path

from conftest import commit_all, git, synthetic_receipt
from verifylab import status
from verifylab.records import question_digest, seal_review, sha256_hex, write_new_json
from verifylab.repo import Repo


def make_source(root: Path, item_id: str, module: str, theorems: list[str], *, wrapper=False):
    root.mkdir(parents=True)
    git(root, "init", "-q", "-b", "trusted")
    git(root, "config", "user.name", "Test")
    git(root, "config", "user.email", "test@example.org")
    git(root, "config", "vl.trustedRef", "trusted")
    prefix = module.split(".")[0]
    research = root / "research"
    (research / "items").mkdir(parents=True)
    (research / "vl.toml").write_text(f'[project]\nname = "source"\n[lean]\nroots = ["{prefix}"]\n')
    solution = module.replace(".", "/") + ".lean"
    path = root / solution
    path.parent.mkdir(parents=True)
    path.write_text("".join(f"theorem {name} : True := trivial\n" for name in [*theorems, f"{prefix}.transfer_same"]))
    target = "ReceiptFixture.lean"
    body = "".join(f"theorem {name} : True := sorry\n" for name in theorems)
    (root / target).write_text(body)
    binding = f'proofs = {{ "{theorems[0]}" = "trivial" }}' if wrapper else f'solution = "{module}"'
    (research / "items" / f"{item_id}.md").write_text(
        f'+++\nid = "{item_id}"\nkind = "result"\ntitle = "Synthetic source"\nauthor = "agent:test"\n'
        f'created = "2020-01-01"\nclaim = "formal"\nstatement = "For the defined two-world fixture."\n'
        f'limits = ["Known indistinguishability mechanism, no novelty claim"]\n[lean]\ntarget = "{target}"\n'
        f'theorems = {theorems!r}\n{binding}\n+++\nSynthetic control.\n')
    commit_all(root, "source declaration")
    repo = Repo.open(root)
    item = repo.load_item(item_id)
    receipt = synthetic_receipt(root, {
        "item": item_id, "item_revision": item.revision, "question_digest": question_digest(item),
        "adapter": "lean-comparator", "assurance": "protected", "verdict": "pass", "reasons": [],
        "inputs": {"files": {solution: sha256_hex(path.read_bytes())}}, "checked": {"theorems": theorems},
        "target": {"path": target, "sha256": sha256_hex(body.encode()), "theorems": theorems,
                   "source": "trusted-commit", "commit": repo.trusted_commit},
        "command": ["synthetic"], "started_at": "2020-01-01T00:00:00Z", "finished_at": "2020-01-01T00:00:01Z",
        "tool_version": "fixture", "log_tail": "", "log_sha256": sha256_hex(b""),
    })
    meaning = status.meaning(repo, item)
    review = seal_review({"kind": "fidelity", "verdict": "faithful", "author": "agent:ref",
                          "text": "Synthetic review.", "created": "2020-01-01T00:00:02Z",
                          "item": item_id, "item_revision": item.revision, "target_path": target,
                          "target_sha256": sha256_hex(body.encode()), "meaning": meaning,
                          "meaning_digest": status.meaning_digest(meaning)})
    for folder, key, record in [("evidence", "receipt_id", receipt), ("reviews", "review_id", review)]:
        write_new_json(research / folder / item_id / (record[key][:16] + ".json"), record)
    commit_all(root, "admit synthetic records")
    return item.revision, receipt["receipt_id"], review["review_id"]
