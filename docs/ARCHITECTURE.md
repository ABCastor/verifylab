# Architecture

## Contract

`vl` checks an answer in a worktree against a question committed on a trusted ref and records a sealed receipt.
Status is derived from admitted records and their current bindings. The integrator remains responsible for
admission. `vl replay` reruns the check from a clean clone with preserved inputs (ADR 0009). Exact replay
compares the recorded tool hashes. With separately built verifier binaries, use the recheck contract to record an
independent run with its own tools and kernel coverage.

## Non-goals

- No UI and no server: a command line over plain files in Git.
- No database of record: `.vl-cache/index.sqlite` is a search cache, rebuilt when stale; records are files.
- No automatic verification policy: reviews are attributed judgements. Optional contextual appraisals preserve
  project-defined ratings and their basis, including external score-only output. There is no score aggregation or vote-derived verification.
- No agent launcher: `vl` never starts an agent; skills tell agents when to call it.
- No model or harness rules: `vl` prescribes no model, harness or prompt.
- No large artifacts: receipts keep digests and a log tail; builds stay in their caches.

## Design policy

- A caller's role adds no trust. Every caller uses the same evidence and admission rules; no command asserts
  that a result is true.
- Keep proof, fidelity and understanding separate. A passing check establishes its formal or computational
  contract; a meaning review is an attributed judgement, including for important intermediate results. The verdict
  ladder shows them as five rows (Lean kernel, second kernel, axioms and trusted base, statement fidelity, paper
  correspondence), each `not assessed` until a record assessed it, and human-facing text offers no single
  `verified` badge (ADR 0013).
- Inherit existing tools and formats. Add a mechanism only for a demonstrated gap; repeated mechanical work
  belongs in commands, while research judgement belongs in skills.
- Leave research strategy and context selection to external agents and their harness. A living Markdown plan
  records hypotheses, alternatives, failed roads and reasons for the next step; agents may challenge that strategy while preserving
  the question and evidence boundaries. Strategic context can be rich, sparse or deliberately withheld for an
  independent approach; isolation, fixed-question checking and snapshot-bound fidelity retain their contracts.
- Keep the problem, current research map and speculative ideas in separate readable homes, optionally scaffolded
  by `vl init --context` and discoverable through `[context]`. Registered notes and validated reviews are searched
  live by `vl find`; the existing SQLite cache continues to index only items and Lean declarations.
- Compact contextual judgments can allocate human attention and agent effort. Contributors choose context,
  criteria and method; they may disregard ratings. Preserve useful failures, unassessed work and unresolved
  disagreement. A later synthesis owns its prose and cites the original contributions; no model call is built in.
- State present guarantees and their limits precisely. `vl replay` reruns one receipt on demand; the planned deep
  check (clean rebuild, conclusion mutation, version floor, replay of every receipt as a status) is not a
  prerequisite for describing the shipped checks, and must never be described as implemented. Platform and
  same-user limits remain explicit.

Apply these tests to contributions. Changes to trust, public formats or compatibility require a documented
decision and matching regression evidence; smaller details may vary without prescribing a model or harness.

## Trust model

| Source | What `vl` takes from it | Who can change it |
|---|---|---|
| repository git config `vl.trustedRef` (`--local`) | the name of the trusted ref | whoever administers the checkout |
| the trusted ref (one commit per command, read from git objects) | `research/vl.toml`, with `[lean] trusted_packages`; the item's target, theorems, witnesses and evaluator; target files and their in-project definitions; evaluators; `lean-toolchain`, `lake-manifest.json` and the lakefile's `leanOptions`; the admitted receipts and reviews, listed from its tree; the kind, claim and relations of items, for status; their statement, limits and assumptions, for the meaning and the card | the integrator; protect the branch on the forge |
| the worktree | the answer: proof terms, imports, solution module, candidate modules and files; the title and body of items; proposals (records, relations and a statement, limits or assumptions that differ from the trusted ref's, labelled "uncommitted edit, not reviewed"), shown and never counted | the agent |
| `machine.toml`, the tools store and the trusted build store (ADR 0005) | tools (pinned by sha256, outside the repository), `elan_home`, build caches, the cap of all runs, the job queue's limits, the sandbox class (`[sandbox] class`, ADR 0012, proposed), the machine's name and trust class (ADR 0015, proposed), the system programs `vl` starts (`[launchers]`, else system folders); the challenge-side build products of earlier protected checks; with `[check] reuse_solution_builds`, the solution-side build products of earlier protected checks of the same inputs, untrusted (ADR 0008) | the owner of the machine |
| the caller's environment | XDG locations (a receipt records a machine policy they move, and the status it verifies names it); `--trusted-ref` (named on every status that depends on it); for exploratory checks only, `COMPARATOR_*`, `ELAN_HOME` and `PATH` for tools | the caller |

What holds the boundary:

- **Receipts.** A receipt is sealed (`receipt_id` is the sha256 of its content), bound to the digest of every input
  it read and of the question it answered, and admitted only when committed on the trusted ref. Any later change of
  an input makes it stale. Validation checks the aggregate digest and protected-pass coverage. The self-hash detects
  changes without resealing; it is not authentication or execution attestation. Admission trusts the integrator,
  who runs the protected check rather than merging a candidate-written receipt.
- **Git reads.** Admitted records, relations and trusted files are read from the trusted commit's objects, never
  from the worktree's copy, so deleting or never checking out a record hides nothing: the commit's tree is listed
  once (`ls-tree -r -z`) and blobs come through one `git cat-file --batch` process per command. A failed read is
  an error, never an absence; git runs without the caller's `GIT_*` variables and with `--no-replace-objects`.
  An older Lean review is dated by the commits of the trusted commit's history that added it (ADR 0004).
- **Launchers.** bubblewrap, systemd-run, systemctl, env, stdbuf, sh and git are started by absolute path
  (`machine.toml` `[launchers]`, else `/usr/bin`, `/bin`, `/usr/sbin`, `/sbin`), never looked up in the caller's
  `PATH`, which could put a wrapper in front of the jail or of git; each receipt records them.
- **Jail.** Every check and `vl lane exec` runs under bubblewrap: new namespaces (no network), cleared environment,
  system folders read-only, only the listed folders mounted. Protected Lean commands run in an outer systemd
  service enforcing `RestrictAddressFamilies=~AF_UNIX`; a trusted helper observes socket denial before exec of
  bubblewrap. Missing enforcement stops the check. Other runs use optional resource scopes. Both unit types use
  `vl.slice` with per-run memory/task caps and the slice's machine `memory_total`; the units of a queued job go in
  its own slice inside it (`vl-<job>.slice`), whose anonymous memory the job queue reads. A check's slice is capped
  at `[queue] threads` CPUs (CPUQuota), and Lean and Lake in its jail run that many worker threads
  (LEAN_NUM_THREADS); Comparator does not pass the variable to the builds it sandboxes, which only the quota bounds.
  A lane serializes overlay mounts and gives each command a fresh overlay work directory. It can retry an
  overlay setup failure in a short window while the kernel releases the previous mount. Bubblewrap signals
  when it reaches the end of mount setup, so the retry cannot run the lane command a second time.
- **Third-party packages** (ADR 0012, proposed). A Lake package's code runs when its cache is built, outside the jail,
  and whenever an importing module is elaborated. A package of the trusted ref's manifest outside `[lean]
  trusted_packages` (by default Mathlib and its official dependencies, by name and GitHub owner) makes `vl check`,
  `vl lane new` and `vl import` `unsupported` before anything is read, built or written, unless machine.toml
  declares `[sandbox] class = "disposable"`, the owner's word for a purpose-built VM destroyed afterwards. Receipts
  record the class and the packages. The gate decides where such code may run; it does not make it safe.
- **Comparator** decides a Lean verdict: same statement, identical definitions in the statement's closure,
  permitted axioms, replay by the Lean kernel and nanoda. Its landrun (Landlock) sandbox runs nested in the jail,
  after a probe proves that Landlock denies a write the jail allows.
- **Trusted build store.** Lake's products of the challenge build of a protected check (the target and its
  in-project closure, compiled before any candidate code) are copied to `$XDG_DATA_HOME/verifylab/lean-builds/`,
  keyed per module by a digest of its source, the keys of its in-project imports, the toolchain, the generated
  Lake files and the dependency packages' revisions and traces, and copied back into later checks' fresh projects.
  Only `vl` writes it, outside every jail; no jail mounts it; an entry whose files no longer match their stored
  sha256 is not used; receipts name what came from it (ADR 0005).
- **Reused solution builds**, off by default (`[check] reuse_solution_builds`, ADR 0008). What Comparator's
  build of the solution compiled is copied by `vl`, after the check, to `$XDG_DATA_HOME/verifylab/lean-solution-builds/`
  under one key binding every candidate input, the generated solution module, the build environment and the tools of the build;
  a later check of exactly those inputs mounts that entry read-only, under an overlay, in Comparator's jail only. It
  stays untrusted: the kernels replay it as any solution build. An entry that is not exactly what was stored, or that
  holds a file of a challenge-side module, is not mounted. A same-user process that replaces an entry cannot make a
  false statement pass, but the receipt then binds "an artifact under this key", not "these sources compiled"; it
  records the key and that the entry was reused.
- **Statement probes** look at the target's meaning mechanically: a tactic battery tries to close each target
  theorem alone and to refute its top-level Prop hypotheses, on a copy of the challenge built before any candidate
  code ran. This does not inspect classes denied inside a conclusion or establish definition adequacy; cards
  show intentional per-theorem skip reasons separately from incomplete probe runs. The same run lists what a
  referee must weigh (ADR 0010): the in-project definitions each statement depends on, its totalized
  operations (in the statement and in those definitions), and the values 0, 1, 2 of its first ℕ binders where its
  hypotheses are refuted or it degenerates; cards show them, `vl validate` warns, no status changes.
  A fourth finding is the foundations profile: constants in the statement and those definitions whose name says it is
  about set-theoretic foundations (`ZFSet`, `PSet`, `Cardinal`, `Ordinal`, choice principles, ZF, ZFC,
  consistency and independence words). Lean always admits `Classical.choice`, so on such a statement the list of
  permitted axioms cannot tell a statement that assumes, denies or avoids Choice from one that does not: the card
  says so and `vl validate` asks for a fidelity review of the current target that acknowledges it
  (`--acknowledge foundations`), whatever its verdict. It is a name match, a nudge for the referee: a finite count
  through `Cardinal` flags too, and a statement about Choice that never names it does not. `Classical.choice`
  itself, which a definition uses to pick an element, is a totalized constant, not a foundations finding.
- **Imports.** `vl import comparator` writes only into the worktree and creates files exclusively, all or none, so
  it never replaces a target the integrator admitted: a changed challenge becomes a new item that supersedes the old
  one. What it writes counts only once the integrator commits it on the trusted ref. The source corpus is not
  trusted: its definitions become the target unreviewed, and the item's first limit says so (ADR 0007).
- **Provenance and origin** (ADR 0014) are tables of the item that its author declares: where the statement comes
  from, which agent made the item, with which model, context and tokens, from which records and ideas. They are
  shown on the card and in the catalog and checked for shape; no status, digest or review reads them, so a claim
  of `upstream-reviewed` adds no trust. `vl validate` asks for them only on items created after 2026-10-11.
- **Reviews** carry what no machine checks: a fidelity review binds to the meaning it read on the trusted ref (the
  target and the in-project definitions it imports, the theorems and witnesses, the item's claim text, limits and
  assumptions, plus the stable Lean semantic environment) and goes stale when any of it changes. A Lean review
  recorded before the environment was bound takes the environment of the commit, in the trusted commit's history,
  that added it (ADR 0004). New fidelity writes require the expected meaning digest obtained before reading; each
  command reads one pinned trusted commit.
  A clean-context reader can perform literal read-back before seeing intent and earlier verdicts; independence
  remains a fallible procedure. A
  `human:` author is written only with `--human-approved` or after the person confirms at the terminal.
  Fidelity records name the current trusted item revision; explicit older revisions are refused. A resolvable
  recorded item revision that disagrees with the review's bound meaning is rejected, including records created
  by earlier versions. Self-review compares the reviewer with the trusted author, not a worktree edit.
  A `correspondence` review (the formal statement against the paper's: same, stronger, weaker or different, per paper
  statement) binds the meaning in the same way, fills one row of the ladder and changes no status (ADR 0013).
- **Appraisals** ([ADR 0017](adr/0017-contextual-appraisals.md)) are immutable attributed reviews with a separate local applicability basis: item revision,
  versioned criterion/options, supplied context, goal when relevant and optional context-file digests. Changes
  to this basis can stale the appraisal without staling a proof receipt. Cards and search preserve individual
  contributions, their actual output and objections. Later literature cannot be detected from local hashes;
  authors declare their source access and comparison scope. No rating changes verification, fidelity or admission.
- **The catalog** (`vl export catalog`, ADR 0011) is a view, not a record: it reads through `TrustedRepo`, which
  counts only admitted receipts and reviews and compares candidate inputs with the trusted ref, never the worktree,
  so what it publishes is what a fresh clone of the trusted commit derives; it writes no time and no machine path,
  and its page is escaped text under a Content-Security-Policy that allows only the page's own inline style and
  script. It restates the card's statuses and never decides one.
- **`vl validate --incoming BRANCH`** lists what a merge would change on the trusted side: configuration,
  evaluators, Lean build files, targets, question fields, added reviews, deletions, receipts it would stale (also
  through a changed question). The items and reviews the branch brings are validated as they are on the branch: one
  that does not parse, a relation to an item the branch lacks, or an `answers` to a question with another `[lean]`
  target or theorems, or another `[python]` evaluator or entry, is an error.
- **Receipts from other machines** (`vl receipts`; ADR 0015, proposed). A receipt is a file, so another machine's can
  be put in the repository; whether it counts is the integrator's declaration at import. `vl receipts export` bundles
  the protected receipts the trusted ref lacks with a manifest of their sha256 and the machine's name
  (`[machine] name`); `import --from NAME --trust own|foreign` reads the bundle without extracting it and checks
  each receipt as `vl validate` does. `own`: the receipt is written to `research/evidence/` like a local one and counts
  once `vl admit` commits it, provided every verifier tool it names has the sha256 this machine pins and the commit
  it asked its question of is here (else it would go stale). `foreign`: it is written to
  `.vl-cache/foreign/NAME/`, shown on the card ("foreign, not counted until vl replay") and read by no status;
  `vl replay` that reproduces it writes the replay's own receipt to `research/evidence/` in its place. A receipt's machine policy (machine file, tools
  store, launchers, paths) is recorded and decides nothing, so a receipt made on another machine is as valid here as
  there. Nothing authenticates the machine: `own` is the owner's word, as admission is.
- **Batch and `vl admit`.** `vl check --all|--stale|--ids` starts one ordinary protected `vl check --json` process
  per item, each through the job queue, and only reads their verdicts; it adds no way to write a record.
  `vl admit` is the integrator's commit of what the checks and reviews wrote: it runs only with the trusted branch
  checked out, refuses unless `vl validate` passes and the only changes of the worktree are new
  `research/evidence/<id>/*.json` and `research/reviews/<id>/*.json` files (any other file, a changed or deleted
  record, a new item refuses), commits exactly those and never pushes. It authenticates nothing: admission is
  still the integrator committing on the trusted ref, here by one command run by that integrator, and a record
  a candidate wrote is admitted by it exactly as by a hand-made commit; the integrator runs the protected checks
  on the trusted checkout and does not merge candidate-written receipts (`vl validate --incoming`).
- **Several machines** need no mechanism of their own. `vl jobs export` names each protected check by item, trusted
  commit and question digest, `vl jobs shard` splits the list into ids files, each machine runs `vl check --ids` on
  its file and the receipts come back as files for the integrator's `vl validate` and `vl admit`; `vl jobs missing`
  says what is left. It reads through `TrustedRepo`, like the catalog, so a worktree's proposals change neither the
  list nor what counts as done, and no command here writes a record or contacts a machine.

The boundary has one hole by design: a process of the same user can write any file `vl` reads. `vl` makes that
detectable (`vl replay` of a receipt from a clean clone on another machine; a protected trusted branch; a
clean-context reviewer), never impossible. docs/CHEATS.md lists every known attack and what catches it.
Incoming validation already rejects new candidate receipts and edits/deletions of evidence and existing reviews.
Status enumerates the current trusted tree, not all historical commits: bypassing that integration policy can
hide admitted evidence. Asserted `answers`, `refutes` and `uses` relations are not checked semantic implications.

## The protected check, step by step

`vl check ID` (commands/check.py) resolves the trusted ref and reads the configuration from it, loads the item
from the worktree, takes its question fields (target, theorems, witnesses; evaluator) from the item on the
trusted commit, records the memory cap the run gets, waits until the machine's job queue admits it (queue.py), and
gives the adapter a fresh scratch folder under `$XDG_CACHE_HOME/verifylab/scratch/`. An item not on the trusted ref
ends here, without waiting: `unsupported`, no receipt.

Lean (adapters/lean_comparator.py), with the phase names the receipt records in `extra.phases`:

1. **prepare.** Read `research/vl.toml` from the trusted commit (its rules digest becomes an input). Before reading
   anything else, stop with `unsupported` when the project's manifest lists a package outside `[lean]
   trusted_packages` and the machine is not `disposable` (ADR 0012, proposed). Read the
   target from the trusted commit and check it (header, one `sorry` theorem per listed name). Collect the
   in-project modules it imports, transitively, from the trusted commit. If the worktree holds every one of them
   unchanged, use them under their own names (shared mode); else write the trusted copies under `VLTrusted.*`
   (renamed mode), so that Comparator sees an altered definition as a mismatch. Collect the candidate's modules
   from the worktree, generate `VLSolution` from the target and the proof terms (or import the `solution`
   module), and lint the Lean files the candidate changed. Take `lean-toolchain`, `lake-manifest.json` and the
   lakefile's `leanOptions` from the trusted commit; the toolchain from the machine's elan home; the tools from
   the machine, pinned (including nanoda for new protected Lean checks: without it the check stops here,
   `unsupported`); the Lake packages from the build cache (machine.toml `[caches]`, else the project's),
   mounted read-only, recording each package's checked-out revision and Lake's trace of every dependency module
   the trusted side imports (what a replay compares; status does not). Write a fresh Lake project.
2. **preflight.** `lake build --no-build` of the dependency modules the trusted side imports, in a workspace with
   only the generated lakefile, in the jail, beside steps 3 and 4 (ADR 0005): a shared cache that would need a rebuild stops
   the check (`error`), with this reason first, and ends the challenge build early.
3. **isolation_probe.** landrun, inside the jail, must deny a write that the jail allows; else `unsupported`.
4. **prebuild_challenge.** `lake build VLChallenge` under the same landrun sandbox Comparator uses, before any
   candidate code is compiled; its build products are copied to a separate probe workspace. With every guard on
   and `[check] build_cache` not false, the modules this build compiles are first copied in from the trusted build
   store (ADR 0005) when their key is there (Lake replays them), and those it lacks are copied out after
   the build.
5. **Comparator.** `lake env comparator config.json` in the jail, read line by line: `build_challenge`,
   `export_challenge`, `build_solution`, `export_solution_and_compare`, `kernel_lean`, `kernel_nanoda`. Its output
   is classified: `fail` only when the failure is certainly the candidate's (a different statement, an altered
   definition, a forbidden axiom, a rejected or missing proof) and Comparator exited normally; trouble on the trusted
   side or with a tool, and a Comparator ended by a signal or a limit, are `error`; a pass needs every kernel's own
   acceptance line, and a kernel's rejection counts only from Comparator's lines after the solution's export, and
   not when the kernel was killed by a signal (Comparator reports any non-zero exit of nanoda as a rejection).
   With `[check] reuse_solution_builds` (ADR 0008), Comparator's jail mounts the stored solution build of
   exactly this check's inputs, when there is an intact one, read-only under the project's `.lake/build`; otherwise,
   once Comparator is done, the solution side it compiled is stored under that key.
6. **probes.** Beside Comparator (ADR 0005), from the moment the probe workspace exists: the statement probes run on it
   under `probe_timeout`, and are recorded after a pass only (a check that does not pass stops them); whatever
   happens to them, the verdict stays Comparator's. They share the check's slice (its `[queue] threads` CPUs and
   `memory_max`) with Comparator and give way there: the kernel kills them first when that memory runs out. After a
   pass, probes that did not finish beside Comparator (a timeout before every theorem had a result, a crash, their
   unit ended by systemd) run again alone (`probes_alone`), and that run is recorded, with why (`rerun_alone`). The
   receipt's `preflight` and `probes` phases overlap the others. Its `memory_peak_bytes` adds up the peaks of the runs
   that overlap: the largest of preflight plus challenge build, Comparator plus probes, and probes alone; its
   `cpu_seconds` adds up the CPU time of every run, overlapping
   or not (cpu.stat `usage_usec` of each unit, polled like the peak, so a lower bound).

Every step from preflight on runs in the jail under the same memory cap, with bounded output, and within what
remains of the check's `--timeout`; a command that closes its output and keeps running is still killed at the
deadline. Protected Lean deadlines stop the whole service, including descendants that closed output;
`RuntimeMaxSec` bounds the service if its client disappears. Services kill remaining children immediately
with SIGKILL and allow one second for reaping, preserving the main command's exit status without a zero-timeout race.
systemd-run exits with the main process's status only when the service's result is `success` or `exit-code`; for
any other (`oom-kill` for the memory cap, `timeout`, `signal`) it exits 1 (255 for `signal`), whatever the command
returned; `vl` reads the report systemd-run prints after the unit is gone, takes it off the command's stderr, and
names that result: such a run is never a pass or a fail.

Python (adapters/python_eval.py): read the candidate files from the worktree (no symbolic link on the way) and
copy them; take the evaluator from the trusted commit, after checking that the trusted item binds this
evaluator; ask a judge process, in its own jail, for `CASES` and `TIMEOUT_S`; run the candidate in another jail,
one call per case, outputs as JSON; give the outputs to the judge; `pass` only when every case passes. A result
counts only from a process that exits 0; a candidate process ended by a signal or a limit is `error`, never `fail`.

`vl check --all|--stale|--ids FILE` (commands/batch.py) runs that sequence once per selected item, each in its own
`vl check --json` process, `-j N` at a time (default: the queue's slots); every process waits in the queue like a
single check, so the queue bounds the load. A batch refuses to run under `vl queue run`, whose one admission its
checks would otherwise all share. `--all` selects the items that have a checker and are on the trusted
ref; `--stale` those of them whose status a protected check could change (`unverified`, `verified-stale`, `explored`,
`check-error`, `check-unsupported`, or `failed` on inputs that changed since or on an exploratory receipt); `--ids`
exactly the listed items. A coordinator pipe (`VL_COORDINATOR_FD`) and SIGTERM handler stop running child checks and
clean only the systemd units `vl` owns if the batch is cancelled or the coordinator dies. A verdict counts only when it agrees with the process's exit code; a process that printed
none is an `error`. The summary lists item, verdict, seconds and queue wait; the exit code is 1 if any check failed,
else 3 if any ended `error` or `unsupported`, else 0.

Last, `vl check` seals the receipt and writes it to `research/evidence/<id>/` (protected) or
`.vl-cache/explore/<id>/` (exploratory), and exits 0 on pass, 1 on fail, 3 on error or unsupported. A check that
stopped before it read any candidate input writes no receipt, and `vl check` never writes one that `vl validate`
would reject. An exploratory
check runs the same steps with the question, the configuration and the build files read from the worktree, and
also accepts unpinned tools and the caller's `COMPARATOR_*`, `ELAN_HOME` and `PATH`.

`vl replay ID` (commands/replay.py; ADR 0009) runs these steps again for one admitted protected receipt: it
clones the repository with `git clone --no-local` (only what branches, remote-tracking branches and tags reach) into a
scratch folder, checks out the receipt's `candidate_head`, else the trusted commit that admits it, wherever the clone
holds every checked candidate file with its recorded digest and the item's answer binding (when none does, nothing
runs and the replay is a mismatch), opens the clone with the receipt's `trusted_commit` as trusted ref, and calls
`check.execute` there under a machine policy that maps the repository's build cache to the clone and turns the trusted
build store off (ADR 0005). The new receipt is compared field by field with the admitted one
(`replay.COMPARED`; and the statement-probe hits `vacuous_by` and `trivial_by`, in one direction: a hit only the replay
finds is a difference) and kept, with the result, in `.vl-cache/replay/<id>/`; nothing else is written, except that when a foreign
receipt (imported by `vl receipts import`, ADR 0015, proposed) matches, the new receipt goes to `research/evidence/`
and the foreign one leaves quarantine: the fields `COMPARED` leaves out are then this machine's.

## Modules

```
cli.py                        argument parsing; maps vl's own errors to exit 2
output.py                     text or versioned JSON (vl.output/1) on stdout; failures on stderr, exit 2 (`vl lane exec`
                              prints only its command's output); the exit codes of the commands that judge
fsio.py                       files written whole or not at all (staged beside their place, then renamed)
commands/init.py              vl init: research/ layout, vl.toml, git config vl.trustedRef; --tools pins tools
commands/show.py              vl show: cards, --impact, --brief; ID@rev resolution
commands/find.py              vl find: indexed items/Lean plus live scoped notes/reviews, statuses from records
context.py                    optional context homes, safe registered Markdown discovery and live review retrieval
commands/check.py             vl check: question from the trusted ref, adapter, receipt, exit code
commands/batch.py             vl check --all|--stale|--ids: selection, one `vl check --json` process per item under
                              -j, the summary table and exit code
commands/admit.py             vl admit: validate, only new records changed, one commit with counts, no push
commands/replay.py            vl replay: a fresh clone (git's transport) at the receipt's commits, the protected check
                              run there through check.execute with no trusted build store, the comparison
commands/receipts.py          vl receipts export|import (bundles of protected receipts between machines, own: a file of
                              research/evidence/, or foreign: .vl-cache/foreign/; ADR 0015, proposed)
commands/review.py            vl review: sealed reviews; fidelity binds to the admitted target
commands/validate.py          vl validate: records, references, targets, probes, modules a library build leaves out,
                              --incoming classification
commands/lane.py              vl lane: worktrees made whole or not at all, bound to their repository by git's common
                              directory, a lock on the lane count, jailed exec over an overlay, done lanes that free
                              their slot and run nothing, a close that waits for no running command and follows no
                              link
commands/queue.py             vl queue: run any command through the job queue in vl.slice; status
commands/jobs.py              vl jobs: export, missing and shard a job list of protected checks, from the trusted commit
                              alone
jobs.py                       the job list (`vl.jobs/1`): content-addressed jobs, the check that a list is as written,
                              expected costs from admitted receipts, the greedy weighted split
foreign.py                    pins the foreign Git tree and Lake inputs, indexes supported source signatures
reading_order.py              source or supplied declaration edges, deterministic demand and reading packets
removal.py                    opt-in multi-root removal view: dominator-exclusive lines over supplied kernel rows
elaboration.py                pre-prune nominations of elaboration-only dependencies a kernel graph misses
commands/attempts.py          vl trial: immutable build comparisons at a pinned commit
attempt_files.py              the attempt files of docs/PURPOSE-AND-ATTEMPTS.md (`vl.attempt/2`), in any repository: the
                              rules of the convention, the order of forecast and outcome read from Git, the table of
                              forecast against outcome per forecaster
commands/attempt_files.py     vl attempts new|check|table (prints what attempt_files.py finds; opens no VerifyLab project)
commands/importer.py          vl import comparator: a folder of Comparator challenges into items and targets in the
                              worktree, dry run by default, never replacing a file
formalization_yaml.py         the YAML subset of a source's formalization.yaml (v0.4) and the links of its family
                              scope notes; `typed=True` adds numbers and nested flow collections for the front
                              matter of attempt files
commands/export.py            vl export catalog: OUT/catalog.json and OUT/index.html, from the trusted commit alone,
                              each written in one step (ADR 0011)
catalog.py                    the catalog's data (`vl.catalog/1`): one entry per item, built from the cards of `vl show`
                              over a TrustedRepo, probe states per theorem and item (ADR 0011)
catalog_page.py               the catalog's page: inline style and script under a Content-Security-Policy of their own
                              hashes, record text escaped, every item in the HTML (ADR 0011)
config.py                     research/vl.toml from the trusted ref; the trusted ref; configuration digests
machine.py                    machine.toml (read once per process), the tools store, REVISIONS pins, the cap of
                              all runs, the sandbox class, the host class a receipt records
packages.py                   the third-party package gate: which manifest packages are trusted, what a workstation
                              and a disposable machine do with the others (ADR 0012, proposed)
gitref.py                     read-only git: one tree listing per commit, blobs through one cat-file --batch,
                              diff, status; path lists NUL-separated
repo.py                       items, receipts and reviews on disk; admission; staleness of a receipt; TrustedRepo, the
                              same read from the trusted commit alone (the catalog export)
records.py                    item parsing, question digest, receipt and review shapes and seals
status.py                     derived status, fidelity and paper correspondence
appraisals.py                  contextual rubric and applicability
ladder.py                     the verdict ladder: five rows read from the card's receipt and reviews (ADR 0013)
render.py                     cards, briefs and impact views
imports.py                    pinned cross-repo `[[imports]]` parsing/resolution, offline whole-snapshot status and fidelity checks, and local locator harvesting
index.py                      SQLite search cache of items, Lean declaration names and imported locator metadata
jail.py                       bubblewrap jail, guarded systemd services/scopes and vl.slice cap, bounded streaming,
                              the memory peak and CPU seconds of each unit
queue.py                      the machine's job queue: admission by CPU slots (the CPUs over the threads one check
                              may use), memory_total and a MemAvailable floor
                              less what each job reserved beyond what its own slice uses; places held as flock'd
                              files, kept by the job and released when it dies; where each limit came from
candidate_exec.py             runs untrusted Python in a separate, sandboxed child process
_candidate_child.py           the child: imports the candidate, calls it, writes outputs (stdlib only)
leanmod.py                    Lean headers, module paths, import closure and rewriting, target theorems; Lake
                              libraries and the modules their build leaves out
leanlint.py                   command lints over candidate Lean files
adapters/base.py              CheckRequest, CheckOutcome, the Adapter protocol
adapters/lean_comparator.py   the Lean check: check project, tools, jail, Comparator, classification
adapters/lean_build_cache.py  the trusted build store: per-module keys, entries copied in and out; reused solution
                              builds: one key per check's inputs, entries checked before an overlay mounts them
adapters/lean_probes.py       probe file generation, the totalized operations and the foundations kinds it reports and
                              why, parsing of its results
adapters/StatementProbe.lean  the probe metaprogram
adapters/python_eval.py       the Python check: candidate jail, judge jail, aggregation
```

## What comes from which tool

| Tool | What `vl` relies on it for | What `vl` adds |
|---|---|---|
| Lake | building, packages, `lake build --no-build`, `lake env` | a generated lakefile with the packages read-only; never downloads |
| Comparator | statement equality, identical definitions, axiom allowlist, kernel replay, external kernels | a fresh project whose trusted side comes from the trusted ref; renamed mode; failure classification; phase timings |
| lean4export | exporting both environments for Comparator | pinned by sha256 |
| nanoda | a second kernel, through Comparator's `external_kernels` | pinned; named in the receipt's `checked.kernels`; required for newly issued protected Lean checks |
| landrun | the Landlock sandbox of Comparator's builds and of the challenge pre-build | a probe that proves it enforces |
| git | the trusted ref, its bytes, admission, worktrees for lanes and the common directory that owns them | `vl.trustedRef`; incoming classification; question and input digests; a lane's binding to its repository |
| bubblewrap | namespaces, no network, cleared environment, mounts, overlays | the jail's mounts; lanes' copy-on-write build cache |
| systemd | protected Lean services with AF_UNIX denied; resource scopes for other runs; slice memory/task limits and a low CPU weight for `vl.slice`; a check's CPU quota on its job's slice | actual denial tested before protected Lean exec; caps recorded; other uncapped runs say so; slice cap is session-only (`systemctl --user set-property --runtime`) |
| flock | places in the job queue, held by the job itself (`vl queue run` and `vl lane exec` pass the lock to their command) and released by the kernel when it ends or dies (a job slice still populated after that counts as running until it empties); one admission decision at a time | the admission rule: slots, reservations within `memory_total`, MemAvailable above `[queue] keep_free` |

What `vl` adds overall: the split between question and answer, receipts and derived status, statement probes and
lints, a judge that never loads candidate code, lanes, incoming validation and fidelity reviews.

## Configuration layers

1. `git config vl.trustedRef`, repository-local; `--trusted-ref REF` overrides it for one command.
2. `research/vl.toml` as committed on the trusted ref (`templates/vl.toml`); the worktree's copy only for
   exploratory checks, and while the trusted ref has none.
3. `$XDG_CONFIG_HOME/verifylab/machine.toml` (`templates/machine.toml`) and the tools store
   `$XDG_DATA_HOME/verifylab/tools/<toolchain>/` with its `REVISIONS`.
4. Command-line flags.
5. The environment: XDG folders (also `$XDG_CACHE_HOME` for scratch, `$XDG_STATE_HOME` for the job queue's
   places; a machine file or tools store they move is
   recorded in receipts and named on statuses); exploratory checks also read `COMPARATOR_*`, `ELAN_HOME` and
   `PATH` for tools; `vl lane exec` takes `ELAN_HOME` of its call. `PATH` never selects a launcher or git.

## Extension points

A new kind of checker is an adapter: a class with `name`, `applies(item)` and `check(request) -> CheckOutcome`
(adapters/base.py), listed in `commands/check.ADAPTERS`. It never raises (a problem is `error` or `unsupported`),
records candidate-side inputs in `files` and trusted ones in `trusted_files`, and names test-only guards for each
defence, each with a planted case. Its item table goes in `records._ITEM_KEYS`, its question fields in
`records.QUESTION_FIELDS`, the trusted subset in `commands/check.TRUSTED_FIELDS`, and the file a fidelity review
binds to in `status.question_file`. Smaller points: the probe battery (`lean_probes.CORE_BATTERY`,
`OPTIONAL_TACTICS`), the totalized constants the probes report (`lean_probes.TOTALIZED_CONSTANTS`), the foundations kinds
(`lean_probes.FOUNDATIONS_KINDS`, matched in `foundation?` of StatementProbe.lean), the lint rules
(`leanlint.RULES`), Comparator's failure classes (`lean_comparator.EXCEPTIONS`).

Protected checks compare package HEAD and tracked changes with the manifest and look for extra Lean or native sources before using the build store. The cache key includes the Lean and Lake executable hashes. These checks assume the build-store owner supplies artifacts built from the pinned sources. A kernel panic makes the check inconclusive.
