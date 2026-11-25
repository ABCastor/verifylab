# ADR 0008 — Reusing the untrusted solution build of the same inputs

Status: accepted, 2026-10-11 (owner). Off by default; audit projects set `[check] reuse_solution_builds = true` in
machine.toml. Extends the trusted build store (ADR 0005),
which keeps the trusted side only.

Context: a protected Lean check builds the solution side (the generated `VLSolution` and every candidate module it
imports) afresh in each check: 18% of the check time in one measured corpus. In a corpus audit the solution side is most of the
work: in OpenAI's corpus (405 challenges checked out, one Lake workspace) the in-corpus import closure of a challenge's
solution module is 77 modules at the median, 765 at the 90th percentile and 4,774 at most (counted from the import
lines, 11 October 2026). Audits re-run checks whose candidate side has not changed: a trusted change that leaves it
alone (`research/vl.toml`, nanoda turned on, a target's wording when the solution is a
module), a run that ended on memory or time after the build,
a second pass. Each re-run compiles the same modules again.

## 1. What is stored
After Comparator ran, `vl` copies Lake's products of the solution side (the modules the check compiles beyond the
challenge build: `VLSolution` and the candidate modules not shared with the trusted side) from the check project
into `$XDG_DATA_HOME/verifylab/lean-solution-builds/<key>/build/`, laid out as under `.lake/build`, with the sha256 of
each file (`SHA256SUMS`). Only when every one of those modules has its olean and trace (a build stopped part-way, or a
solution that does not compile, is not stored), only when the check did not use an entry, and only from `vl`'s own
process after the jail is gone. The candidate's build-time code wrote that folder, and `vl` copies it outside
the jail: a product reached through a link (`.lake/build` or a folder in it turned into a link to a folder of
the machine) is not stored, so no file the jail could not see reaches an entry that a later jail mounts
(`test_a_product_reached_through_a_link_is_never_stored`). Entries appear whole (written aside, then renamed)
and are never rewritten. Off by default: machine.toml `[check] reuse_solution_builds = true` turns it on, for protected checks with every guard on.
It is a store of its own, apart from the trusted one: nothing moves between them.

## 2. What a key binds
One key per check, not per module: the sha256 of the build environment (store format, `lean-toolchain`, the
`lean --version` line, the generated lakefile, which carries the trusted lakefile's `leanOptions`, the roots and the
packages folder, the generated manifest, each Lake package's checked-out revision and the trace of each dependency
module the trusted side imports), the sha256 of each tool that takes part in the build (Comparator, landrun,
lean4export; not nanoda, which only replays the export), every candidate input the check read (`inputs.files` of
the receipt: path and sha256), the sha256 of the generated `VLSolution`, and the list of solution-side modules. A
check reuses an entry only when all of it is equal: exactly the same inputs.

The trusted side is left out of the key on purpose, so that a trusted change the solution side does not depend on
keeps the entry. Lake decides, from each module's trace, which stored products still match the check's own
challenge build, and rebuilds the others in the project; build-time code that reads a file Lake does not track
(a trusted file through `IO`, the clock) can make a reused build differ from a fresh one, as two fresh builds can
differ. Either way the kernels replay what the build declares.

## 3. How a check uses an entry
Before Comparator runs, after the challenge build and the probes' copy of it, `vl` looks for the entry of the check's
key and uses it only when it is exactly what was stored: every file listed in `SHA256SUMS` with that sha256, no file
the sums do not list, no link or special file, and every file a product of one of the check's solution-side modules,
so that an entry can never hold a module of the challenge side (`VLChallenge`, a trusted module), which the check's
own challenge build provides. Comparator's jail then mounts the entry read-only, as the lower layer of an overlay on
the project's `.lake/build`, whose upper layer is that folder itself: Lake finds the solution side up to date and
replays it; anything it writes lands in the project, never in the entry; files already in the project (the challenge
build) win over the entry's. Only Comparator's run sees the overlay; the probes and the challenge build never do. An
entry that is not what was stored is not mounted, and a note names it. Each receipt records
`environment.solution_build`: the store, the key, the modules, whether the entry was reused and whether this check stored one.

## 4. The trust argument
Comparator never trusts the solution build. Its contract is that the challenge is built and exported before any
candidate code is compiled, and that whatever the solution side holds is then exported and checked: the same statement
as the challenge's export, identical definitions in the statement's closure, permitted axioms only, and a replay of the
solution's declarations by the Lean kernel and nanoda. In a normal check the candidate's build-time code can already
write any file of the project's `.lake` (docs/CHEATS.md E2, E3): a solution olean is candidate-controlled bytes. A
reused entry is one more way for such bytes to reach the solution side, and it reaches nothing else: the challenge
export reads the challenge closure only, its products are the check's own and win over the entry, and the entry may
hold no file of a challenge-side module.

Who can write an entry:
- The check that stored it, through its candidate code: all of that code is under the key, since the key binds every
  input of that build. So an entry holds only what this exact set of candidate inputs could have produced in a check
  of its own. A candidate never writes another key: no jail mounts the store, entries are copied out by `vl` under the
  key of the check that built them, and a check sees only its own entry, read-only.
- A process of the same user outside the jail, as for the trusted store, the Lake packages and the tools store. It can
  replace an entry and rewrite its sums (the sums catch damage, not an attacker). What it cannot do: make a false
  statement pass. The kernels replay whatever the entry declares against the trusted statement and definitions, with
  the permitted axioms (`test_a_poisoned_entry_still_meets_the_kernels_and_a_damaged_one_is_not_mounted`: an olean
  declaring a false theorem with a proof the kernel never checked, planted under an honest key, is rejected by the
  kernel).
- Damage (a crash before the data reached the disk, a bad disk): the sums catch it; the entry is not mounted.

What changes is the binding of a receipt. Without reuse a pass says: these candidate sources (`inputs.files`) compiled,
in this jail, to an environment whose proofs of the trusted statements the kernels accepted. With a reused entry it
says: an artifact stored under the key of these sources held such proofs. Against a poisoned entry the theorem is
still proved, by kernel-checked terms over the trusted definitions, but not necessarily by these sources, which may
not even compile. The receipt names the key and that the entry was reused, so a replay knows which checks to rebuild.

Acceptable for audits: an audit's claim per challenge is first "this statement has a proof the kernels accept with
the permitted axioms", which no entry can fake, and second "the corpus's sources give it", which a poisoner of the
same user could fake. Such a process can already do worse in every check: rewrite the trusted build store (the
challenge side), the Lake packages, the tools. vl detects that kind of attack by replay on a clean machine, never by
the store, and the same holds here. For a result that is published, the receipts that will be cited should come from
checks with the store off, or be replayed on a clean machine (the planned deep replay), which rebuilds everything.

## 5. What it does not give: reuse across challenges
OpenAI's challenges share little of their solution side: the 405 closures add up to 128,277 modules, 107,034 distinct
(17% shared; no module is in more than 6 closures). A first pass therefore gains nothing from this ADR, and at most
17% from sharing across challenges. Per-module keys of untrusted products are refused: in one build, the build-time
code of one candidate module can rewrite the products of another that does not import it, so an entry stored under
module M's key could be shaped by code outside M's sources and reach a check of another challenge; a candidate would
then write another key. A sound way to share would be a library build: one jailed build of a declared set of modules
L, keyed by all of L, mounted by checks whose candidate files are all in L with the same bytes, the receipt saying
"artifacts of a build of L, which contains these sources". Not built.

## Measurements
11 October 2026, this laptop under other load, seconds of Comparator's `build_solution` phase (whole check in
brackets), reuse off / first check with reuse (stores) / second check (reuses):
- fixture `control-solution-module`: 1.32 / 1.31 / 0.25; fixture `d4-sorry`: 0.52 / 0.54 / 0.25
  (`VL_MEASURE=1`, `test_a_reused_solution_build_gives_the_verdict_of_a_fresh_one`);
- the same item with a candidate chain of 40 modules (50 small theorems each) under the solution: 37.2 (43.9) /
  26.6 (30.7) / 0.29 (4.2). Storing costs nothing measurable; checking the entry's sums before mounting it costs the
  time to hash it (sha256 runs at 1.4 GB/s here, from memory).
Verdicts, reasons, probes and definitions mode were the same with reuse off, cold and warm.

## Rejected
- Copying entries into the check project, as the trusted store does: an OpenAI solution side is up to thousands of
  modules; the overlay costs no copy and no disk per check.
- Per-module keys of the solution side, and storing it from a failed or partial build (above).
- Trusting an entry's products further than a fresh build: the verdict, the probes and the lints are unchanged.
