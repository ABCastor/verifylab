# Replaying a receipt from a clean clone

A result is `verified` because the integrator ran a protected check and committed its receipt on the trusted ref.
`vl replay` lets anybody else run that check again, from the repository as published, and see whether it gives the
same answer (ADR 0009). This page is the runbook for a stranger on a clean Linux machine.

## What you need

- Linux with Landlock (5.13 or later), unprivileged user namespaces, and a systemd user manager: the same jail as a
  protected check (docs/INSTALL.md, "Linux isolation").
- git, Python 3.11 or later, uv, bubblewrap; for Lean also elan, Go 1.24 or later, Rust with Cargo, a C compiler.
- Disk for the research repository's Lake packages (a built Mathlib is about 7 GB) and network for the downloads in
  steps 1 to 4. The replay itself downloads nothing.

## Steps

1. Install `vl` from a checkout of VerifyLab (docs/INSTALL.md, "Install the command"):

   ```sh
   git clone <verifylab-url> verifylab && cd verifylab && uv tool install . && cd ..
   ```

2. Clone the research repository and name its trusted branch (the publisher says which; `main` here):

   ```sh
   git clone <repository-url> /srv/research && cd /srv/research
   git config vl.trustedRef main
   vl show ID                # the status, and the receipt that decides it
   ```

3. Install the Lean toolchain the trusted ref pins, and build and pin the verifier tools. `scripts/install-tools.sh`
   builds Comparator, lean4export, landrun and nanoda at the source revisions docs/INSTALL.md lists and runs
   `vl init --tools`; `--toolchain SPEC` picks the toolchain (v4.34.0-rc2 and v4.34.1 are reviewed), and another
   toolchain needs reviewed source pins first. For a Python result, `/usr/bin/python3` is enough.

   ```sh
   elan toolchain install "$(git show main:lean-toolchain)"
   ../verifylab/scripts/install-tools.sh "$HOME/.cache/verifylab/tool-build"
   ```

4. Build the Lake packages the trusted ref pins (Lean results with dependencies). For a Mathlib project,
   `lake exe cache get` downloads Mathlib's built files; then tell `vl` where they are, in
   `~/.config/verifylab/machine.toml` (absolute paths):

   ```sh
   lake exe cache get        # in the clone, at the trusted ref
   ```

   ```toml
   [caches]
   "/srv/research" = "/srv/research/.lake"
   ```

5. Replay, one command per receipt:

   ```sh
   vl replay ID                         # the receipt that decides the item's status: the one vl show names
   vl replay ID --receipt 3f2a9c1d      # a given admitted receipt, by a prefix of its receipt_id
   vl replay ID --contract auto|exact|compat|recheck
   ```

`vl replay` keeps three replay contracts separate: `exact` (the default for current receipts: reproduces the recorded
tool and kernel generation over the full basis), `compat` (the default via `auto` only for recognized historical
receipt profiles: projects `item_revision` questions, legacy config bindings and `VLTrusted/` paths, and explicitly
records omitted `environment.packages.identity` without inventing coverage or rewriting the receipt), and `recheck`
(an independent rerun under the current policy and tools, recorded separately as `RECHECKED`). The relation
reports any added kernels and changed tools. An unchanged kernel set and tools establish repeatability of the
recorded result. Only exact and compatibility matches can promote a foreign receipt.

`vl replay` makes its own fresh clone of your clone (git's transport: only what branches, remote-tracking branches and
tags reach). It looks for the answer at the receipt's `candidate_head`, the commit on the trusted first-parent
history that first contains the admitted receipt, then the current trusted commit. Use
`vl replay ID --answer-rev REV` to select another published revision (for example, a commit that archived the
answer later). This revision must resolve in the fresh clone. Every answer location has to match the candidate
input digests and the recorded answer binding when available. Replay reads the question from the receipt's
`trusted_commit`, runs the protected check with your pinned tools,
builds both sides anew (not from the trusted build store, ADR 0005, nor from a stored solution build, ADR 0008), and
compares. It prints `MATCH`, `RECHECKED` (for `--contract recheck`), or `MISMATCH` and every difference, and keeps a record under `.vl-cache/replay/ID/`. Exit 0 on a match (or `RECHECKED`), 1 on a
mismatch, 3 when it could not compare, 2 for usage. `--json` gives the same as data. A foreign receipt that
`vl receipts import` quarantined replays the same way; on an `exact` or `compatibility` match the replay's own receipt goes to `research/evidence/`
for the integrator to admit (ADR 0015, proposed).

## Reading the result

- `verdict`, `question_digest`, `inputs.*`: the check of the bytes git holds says something other than the receipt.
  The receipt was edited, the answer or the question it names is not what git holds, or the verifier behaves
  otherwise here. This is what the replay is for.
- "git does not hold the checked candidate files and answer at one commit": the integrator checked bytes or an item
  answer binding (`question_digest`) absent from the revisions listed in the diagnostic. Nothing ran. If another published revision preserves those
  inputs, select it with `--answer-rev`.
- `environment.tools[...]`: your binaries have other hashes. Tools built from the same source revisions by other
  compilers differ; the verdict and inputs lines say whether the check itself agreed.
- `environment.packages.identity...`: your Lake packages are at another revision, or Lake traced their build
  otherwise (a different Mathlib build, a different toolchain).
- Exit 3: the receipt names no trusted commit in full (only a pass must), the clone lacks a commit the receipt names
  (history rewritten, a branch not published), or the check was inconclusive here (a missing tool, toolchain or
  package, a timeout). Exit 2, as for every command: a usage or setup problem, or the queue wait ran out
  (`--max-wait`); nothing ran and no record is kept.

## What a match proves, and what it does not

A match proves that the repository's history holds the question and the answer the receipt names, and that the
same protected check, run here by you with tools of the recorded hashes, gives the recorded verdict over the
recorded input digests, with the recorded kernels.

It does not prove:

- that the integrator ran the check: admission still trusts the integrator, and a replay only shows the receipt
  reproduces;
- anything on the integrator's own machine: a replay there shares its tools, caches and account, and a process of
  the same user can write all of them (the same-user hole). Replay on another machine or account to close it;
- that the verifier tools, Lean's kernel or nanoda are sound, or that a tool with the recorded hash is the one its
  source revision describes;
- that anyone signed it: the audit is AI checking AI, with the kernel doubled; no human signature or human review is
  claimed, and one added by a person is optional input, not a guarantee;
- that the target says what the item claims: that is the fidelity review's question (an agent's attributed
  judgement). The statement probes are compared in one direction: a `vacuous_by` or `trivial_by` hit the replay finds and the receipt lacks is a difference
  (`checked.probes[THEOREM].vacuous_by: receipt absent, replay TACTIC`); a hit only the receipt has, or another tactic
  for the same hit, is not, and the lists of definitions, totalized operations and boundary values are not compared;
- that the result is current: the replay checks the receipt against the commits it names. `vl show ID` says
  whether the trusted ref still holds those inputs (`verified`) or has moved on (`verified-stale`).

An inconclusive rerun exits 3 even when both verdicts agree. Compatibility replay of a recorded historical single-kernel profile reproduces that coverage. Its result stays in the replay log. New protected Lean passes require nanoda.
