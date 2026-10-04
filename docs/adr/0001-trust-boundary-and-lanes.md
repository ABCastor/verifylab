# ADR 0001 — Trust boundary, fidelity and lanes

Status: accepted, 2026-10-01. Where the trusted ref, the configuration and the tools come from, and what a receipt
binds: ADR 0002.

## 1. What defines the question comes from the trusted ref
`vl check` takes the target path, the theorem list, the witnesses and the evaluator path from the item as
committed on the trusted ref; only the answer (proof terms, imports, solution module, candidate code) comes from
the worktree. Why: a candidate could otherwise drop the hard theorem from the list or repoint the claim at an
easier target or a lenient evaluator. Planted cases d11 and Python defect i fail without it.

## 2. Lean: shared mode by default, renamed mode only when definitions changed
Comparator assumes the challenge's import closure is trusted. When the candidate has not changed any
module in the target's closure, the check imports those modules under their own names (shared mode).
When it has, the trusted copies are rebuilt under `VLTrusted.*` so Comparator's declaration comparison
catches an altered definition (planted case d7). Why not always rename: Lean gives private helpers and
generated lemmas module-dependent names, so renaming makes genuine results that use them fail. Renamed
mode still fails closed in that situation, with a hint to integrate the definition change first.

## 3. Fidelity is a separate, content-bound judgement
A protected pass says the proof proves the target; it says nothing about whether the target means the
question. A `fidelity` review binds to the admitted target (or evaluator), its in-project definition closure,
selected theorems and witnesses, and the item's statement, limits and assumptions (ADR 0002 §10). `vl validate`
warns on a verified result whose target has no faithful review, flags self-review, and treats a review of
an older target as stale. Why: otherwise a new target can be admitted and verified without anyone having read
what it means, and nothing says so.

## 4. Exploratory receipts are not records
Exploratory receipts go to the gitignored `.vl-cache/explore/`; only protected receipts live in
`research/evidence/`, and they count only once committed on the trusted ref. Why: an exploratory receipt
written among the records is one `git add -A` away from being admitted.

## 5. Lanes share the build cache through an overlay, never a copy or a symlink
`vl lane exec` mounts the shared build cache (machine.toml `[caches]` for the main checkout, else `[lean] cache`,
else the main checkout's `.lake`) read-only as the lower layer of an overlay whose upper layer belongs to the
lane, inside the no-network jail. Why: a lane with its own build holds its own Mathlib (about 8 GB), and a
symlinked `.lake` lets a lane write into the shared cache. Measured on a research project's Mathlib build:
replaying a built module costs 6 s and 4 KB, rebuilding an edited module 23 s and 8 MB, with zero writes to the
shared cache.

## 6. A lane belongs to the repository that made it
A lane records git's common directory (`repository`) when it is made and lives in a folder of `[lanes] dir` named
after the main checkout and a digest of that directory; `list`, `exec` and `close` refuse a lane that names another
repository, and a lane folder that git does not list among the repository's worktrees. A lane of earlier versions
(metadata directly in `[lanes] dir`, no `repository`) counts as the repository's only while git lists its path
there, so it still closes from its own repository and from no other. Why: sibling repositories keep the default
`../.vl-lanes`, and keyed by name alone one repository listed, ran commands in and could close another's lane, and
shared its lane names, count and lock.

## 7. `vl lane exec` passes its command through
The command's output and exit code reach the caller unchanged, and `--json` is refused for `exec`, before or after
the action alike. Why: an envelope would have to capture the output of builds that run for an hour, and it would put
a child's output, JSON or not, inside vl's own JSON; passing it through is simpler and never mixes the two.
