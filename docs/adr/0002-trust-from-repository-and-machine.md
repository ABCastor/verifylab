# ADR 0002 — Trust comes from the repository and the machine, never from the caller

Status: accepted, 2026-10-01. Extends ADR 0001.

Context: `vl` runs with the rights of whoever calls it, often an agent working in a worktree it controls. Every
input that decides a verdict must therefore come from a place that agent cannot redirect for one command: the
trusted ref of the repository, or the machine's own configuration.

## 1. The trusted ref is named by the repository's git config
The trusted ref is the repository-local `git config vl.trustedRef`, written by `vl init` and read with `--local`,
so neither the caller's environment nor a per-worktree value counts; `main`, with a warning, when it is unset. Only
an explicit `--trusted-ref REF` overrides it, for one command; a ref starting with `-` is refused. Every receipt
records the ref, where its name came from and the commit; every card, brief, impact view, `vl find` and `vl
validate` output whose statuses depend on an anchor other than the configured one (`--trusted-ref`, or the default
`main`) names it, so an alternate anchor never yields an unqualified `verified`. Why: a pointer in a committed file lets a branch name
itself (`trusted_ref = "HEAD"`) and admit its own receipts. `[project] trusted_ref` is accepted, ignored and warned
about.

## 2. The project configuration is read from the trusted ref
`research/vl.toml` is read as committed on the trusted ref; the worktree's copy only by exploratory checks, and
while the trusted ref has none. `vl validate` reports a worktree copy that differs. Why: a lane could otherwise widen
`permitted_axioms` or change the roots for its own checks.

## 3. The programs that judge come from the machine, pinned
A protected check takes Comparator, lean4export, landrun, nanoda and the Lean toolchain only from the machine:
`machine.toml` `[tools]` (absolute paths; `elan_home`), then the tools store
`~/.local/share/verifylab/tools/<toolchain>/` filled by `vl init --tools`, then, deprecated, the trusted `[tools]` of
the project. Never from `COMPARATOR_*`, `ELAN_HOME`, `PATH` or a relative path, and only a tool whose sha256 the
`REVISIONS` file next to it pins; any other is refused (exit 3, with a `vl init --tools` hint). Each receipt records
every tool's path, sha256, source and pin, and the machine file's path and sha256. Exploratory checks still accept
the environment variables and unpinned tools, and say which tools were not pinned. Why: a caller could point
`COMPARATOR_BIN` at a script that prints the success banner, and a relative path runs whatever the worktree holds.
A tool path inside the repository or its main worktree is refused, even pinned: candidates write there. The system
programs `vl` starts (bwrap, systemd-run, systemctl, env, stdbuf, sh, git) come from machine.toml `[launchers]` or
from system folders by absolute path, never from `PATH`, and every receipt records them. Residual: the XDG
variables are honoured, so a caller can choose another machine file; every receipt records the machine policy it
ran under (`environment.machine_policy`: file, digest, tools store, the variables that moved it), and the status of
a result such a receipt verifies says so.
What is shared by every project on the machine belongs to the machine too: the build caches (`[caches]`) and the
memory cap of all `vl` runs together (`[check] memory_total`, the cap of the systemd slice `vl.slice`). A project
value of `memory_total` is ignored and warned about: two projects would overwrite each other's cap.

## 4. A receipt binds the question it answered
A receipt records `question_digest`, the digest of the item's question fields (`[lean]` target, theorems,
witnesses, proofs, imports, solution; `[python]` evaluator, candidate, entry, files), and is stale once the item's
current fields differ; an admitted receipt is also stale while the item as committed on the trusted ref differs or
is missing, so a proof term that only a worktree holds never verifies. A receipt without that field is bound through the item blob its `item_revision` names; when
git no longer has that blob, it is stale. Why: otherwise a merged edit of the question keeps an old receipt
verified.

## 5. The configuration binds a receipt only through what decides a verdict
A Lean receipt records `research/vl.toml#verdict-rules`, the digest of `[lean]` permitted axioms, external kernels,
project and roots. Paths of one machine (`packages`, `cache`), resource limits, tool paths and lanes are left out:
editing them does not stale receipts. `lean-toolchain` and `lake-manifest.json` bind what is built, as trusted
inputs of their own. Receipts that recorded `#verdict` (the same plus the `packages` and `cache` paths) or `#check`
(also without roots; their roots are compared through the commit they checked) keep their basis.

## 6. Statement probes after every pass
Comparator proves that a proof matches the trusted target; it cannot tell whether the target says anything. After a
pass, a fixed tactic battery tries to close each target theorem alone (triviality) and to derive `False` from its
hypotheses (vacuity), on a copy of the challenge build taken before any candidate code was compiled. A vacuity hit
makes the status `vacuous`, never `verified`, and a later run of the same inputs whose probes did not finish never
erases it; a triviality hit is a `vl validate` warning until a fidelity review of
the current target acknowledges it (`--acknowledge trivial`); probes that crash, time out or are skipped are named
on the card and in `vl validate`, never a verdict. A target theorem with Prop hypotheses needs a witness theorem in
the same target, listed in `[lean] witnesses`. Why: weak, trivial and vacuous targets pass Comparator, also when
the trusted side wrote them.

## 7. The exploratory store is never admitted
A receipt under `.vl-cache/explore/` never counts, even when a file there is force-committed on the trusted ref
(`git add -f`); a receipt there that claims `protected` is rejected, and `vl validate` (and `--incoming`) report
any file git tracks under `.vl-cache/` as an error. Why: admission must not depend on a `.gitignore` line.

## 8. One CLI for every caller
There is one `vl`, with the same commands and the same rules for an agent, a coordinator, a reviewer and a human;
no role flag or caller identity changes what counts. Trust depends only on the trusted ref, the machine
configuration and the bytes checked. Why: a role is a claim the caller makes about itself, and what an
integrator can run, an agent can run too. Consequence: the limit of the design is explicit. A process of the same
user can write any file `vl` reads; the defences make that detectable rather than impossible (re-running a check
on a clean clone, a protected branch on the forge for the trusted ref, a reviewer started with a clean context).

## 9. Status reads the trusted commit, never the worktree's copy of it
The trusted ref is resolved to one commit per command. Admitted receipts and reviews are listed from that commit's
tree and read from its objects; a worktree record not there is a proposal, and one that differs from the admitted
record at its path is rejected. The kind, claim and `refutes`/`answers` relations that decide a status are those of
the items as committed; worktree-only relations are shown as proposals, and a pinned relation (`id@rev`) applies
only while the trusted item is at that revision. A git read that fails raises an error instead of returning
"absent", and git runs without the caller's `GIT_*` variables and with `--no-replace-objects`. Why: a worktree
that deletes a newer admitted fail or retraction, or never checked it out, made an older pass `verified` again;
adding `refutes` to a worktree item refuted a verified result; and a read that failed looked like a missing
retraction.

## 10. A fidelity review binds the meaning it read

An explicit historical item revision cannot authorize a review of current meaning. New fidelity reviews require
the current trusted item revision and record it, including when worktree prose differs. Existing records whose
resolvable item revision contradicts their saved meaning are rejected without rewriting them. The self-review
warning compares the reviewer with the trusted author; editing an author in the worktree does not remove it.
A fidelity review records, from the trusted ref, the digest of what decides the meaning of a result: the target (or
evaluator) and, for Lean, every in-project module of its import closure; the selected theorems and witnesses; the
item's claim text, its `limits` and its `assumptions`. It counts only while that digest is the one now, and the card
says which part changed when it is not. The title and the body explain and are not bound. A review written before
this records only `target_sha256`, and one written before limits and assumptions were bound lacks their digests;
each keeps its basis, marked on the card with what it does not bind, and `vl validate` asks for a new one. The
second counts only until a review that binds limits and assumptions is admitted: otherwise, when that newer review
went stale through an edited limit, the older one would count again. Two
reviews with the same words of different meanings are different reviews. Why: a `faithful` review of one file
survived a change of the definitions the statement uses, of the theorem list, and of the claim it was judged
against; and removing a limit ("not a claim about all corpora") widens the claim as much as editing the statement,
while the review that read the limit stayed valid.
