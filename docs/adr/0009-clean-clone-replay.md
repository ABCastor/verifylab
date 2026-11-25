# ADR 0009 — Integrator admission, and a clean-clone replay as the public guarantee

Status: accepted, 2026-10-11 (owner). Extends
ADR 0001 (trust boundary) and ADR 0002 (trust from the repository and the machine).

Context: D1, execution assurance, asked who vouches that a check ran. A receipt is sealed and bound to the digests
of everything it read, but a seal is not a signature: anybody can write a well-formed receipt, and a process of the
same user can write any file `vl` reads. Admission (the integrator commits the receipt on the trusted ref after
running the protected check) is the boundary today. The alternatives were an independent observer of every run (a
separate account or machine that signs receipts) and a replay anybody can run. An observer adds infrastructure and
still has to be trusted; it does not let a reader of a published repository check anything themselves. Until now
the replay was a recipe in the README, done by hand.

## Decision
1. Admission stays as it is: a receipt counts when the integrator commits it on the trusted ref. Nothing about
   status, staleness or validation changes; a replay never changes a status (the one receipt it can write, the replay's
   own check of a foreign receipt, comes with ADR 0015, proposed).
2. A stranger can rerun an admitted receipt from a clean clone with its published inputs and required tools:
   `vl replay ID [--receipt RID]` clones the repository with git's own transport (`--no-local`: only what its
   branches, remote-tracking branches and tags reach, nothing of the caller's worktree, index or loose objects) into a
   scratch directory. It selects the first eligible revision that holds all recorded candidate digests and the
   item's answer binding. See [replay answer selection](../REPLAY.md#steps). It runs the protected check
   there with the question from the receipt's `trusted_commit` and compares the new receipt with the admitted one. Exact matching compares binary hashes. An independent recheck
   can record the verdict with different locally built verifier binaries.
3. Compared: verdict, `question_digest`, `inputs.digest` and every candidate and trusted input digest, the
   toolchain and Lean version, the sha256 of each verifier tool, the Lake packages' revisions and dependency
   `depHash`es, the Python interpreter and version, and `checked.kernels`, and the statement probes in one direction: a
   `vacuous_by` or `trivial_by` hit the replay finds and the receipt lacks is a difference, the reverse (the battery
   did not reach it) is not. Not compared: times, paths, the trusted ref's name, the machine policy, memory, logs,
   the other probe findings. Without `--receipt` the receipt replayed is the one that decides the item's status
   (the one `vl show` names), else the newest pass. Exit 0 on a match, 1 with the list of differences
   (also when no examined revision holds the checked files and answer binding), 3 when it could not compare
   (no clone, a commit no branch reaches, an inconclusive check), 2 for usage, setup or a queue wait past `--max-wait`
   (as for every command).
4. The replay uses this machine's pinned tools and the Lake packages its build cache holds for the repository, but
   not the trusted build store (ADR 0005) nor a stored solution build (ADR 0008): both sides are built anew. Besides
   that receipt it writes only a record, `.vl-cache/replay/<id>/<receipt>-<time>.json` (schema `vl.replay/1`), never
   admitted.

## Consequences
A match shows that the bytes git holds, checked by the same protected check with tools of the same hashes, give
the recorded verdict and digests: a receipt edited after the check, a pass that was really a fail, or an answer
that only the integrator's worktree held does not match. It does not show that the stranger's tools are sound, that
the integrator ran the check (only that it reproduces), or anything about meaning (fidelity reviews) or probes beyond
the hits above. A
replay on the integrator's own machine shares that machine's tools and Lake packages: the same-user hole is closed
only by a replay on another machine or account. Tool binaries built from the same sources by other compilers have
other hashes, and the replay names them as differences; whether such a replay should count as a match with a note
is left open. `docs/REPLAY.md` is the runbook for a stranger.
