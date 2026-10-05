# Architecture

## Contract

`vl` checks an answer in a worktree against a question committed on a trusted ref and records a sealed receipt.
Status is derived from admitted records and their current bindings. Replay requires preserved checked inputs and
a reconstructible tool environment; the integrator remains responsible for admission.

## Non-goals

- No UI and no server: a command line over plain files in Git.
- No database of record: `.vl-cache/index.sqlite` is a search cache, rebuilt when stale; records are files.
- No automatic scoring policy: reviews are attributed judgements. Optional contextual appraisals may be
  recorded in prose; there is no dedicated numeric interface, score aggregation or vote-derived verification.
- No agent launcher: `vl` never starts an agent; skills tell agents when to call it.
- No model or harness rules: `vl` prescribes no model, harness or prompt.
- No large artifacts: receipts keep digests and a log tail; builds stay in their caches.

## Design policy

- A caller's role adds no trust. Every caller uses the same evidence and admission rules; no command asserts
  that a result is true.
- Keep proof, fidelity and understanding separate. A passing check establishes its formal or computational
  contract; a meaning review is an attributed judgement, including for important intermediate results.
- Inherit existing tools and formats. Add a mechanism only for a demonstrated gap; repeated mechanical work
  belongs in commands, while research judgement belongs in skills.
- Leave research strategy and context selection to external agents and their harness. A living Markdown plan
  records hypotheses, alternatives, failed roads and reasons for the next step; agents may challenge that strategy while preserving
  the question and evidence boundaries. Strategic context can be rich, sparse or deliberately withheld for an
  independent approach; isolation, fixed-question checking and snapshot-bound fidelity retain their contracts.
- State present guarantees and their limits precisely. Planned deep replay is not a prerequisite for describing
  the shipped checks, and must never be described as implemented. Platform and same-user limits remain explicit.

Apply these tests to contributions. Changes to trust, public formats or compatibility require a documented
decision and matching regression evidence; smaller details may vary without prescribing a model or harness.

## Trust model

| Source | What `vl` takes from it | Who can change it |
|---|---|---|
| repository git config `vl.trustedRef` (`--local`) | the name of the trusted ref | whoever administers the checkout |
| the trusted ref (one commit per command, read from git objects) | `research/vl.toml`; the item's target, theorems, witnesses and evaluator; target files and their in-project definitions; evaluators; `lean-toolchain`, `lake-manifest.json` and the lakefile's `leanOptions`; the admitted receipts and reviews, listed from its tree; the kind, claim and relations of items, for status; their statement, limits and assumptions, for the meaning and the card | the integrator; protect the branch on the forge |
| the worktree | the answer: proof terms, imports, solution module, candidate modules and files; the title and body of items; proposals (records, relations and a statement, limits or assumptions that differ from the trusted ref's, labelled "uncommitted edit, not reviewed"), shown and never counted | the agent |
| `machine.toml` and the tools store | tools (pinned by sha256, outside the repository), `elan_home`, build caches, the cap of all runs, the system programs `vl` starts (`[launchers]`, else system folders) | the owner of the machine |
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
- **Launchers.** bubblewrap, systemd-run, systemctl, env, stdbuf, sh and git are started by absolute path
  (`machine.toml` `[launchers]`, else `/usr/bin`, `/bin`, `/usr/sbin`, `/sbin`), never looked up in the caller's
  `PATH`, which could put a wrapper in front of the jail or of git; each receipt records them.
- **Jail.** Every check and `vl lane exec` runs under bubblewrap: new namespaces (no network), cleared environment,
  system folders read-only, only the listed folders mounted. Protected Lean commands run in an outer systemd
  service enforcing `RestrictAddressFamilies=~AF_UNIX`; a trusted helper observes socket denial before exec of
  bubblewrap. Missing enforcement stops the check. Other runs use optional resource scopes. Both unit types use
  `vl.slice` with per-run memory/task caps and the slice's machine `memory_total`.
- **Comparator** decides a Lean verdict: same statement, identical definitions in the statement's closure,
  permitted axioms, replay by the Lean kernel and nanoda. Its landrun (Landlock) sandbox runs nested in the jail,
  after a probe proves that Landlock denies a write the jail allows.
- **Statement probes** look at the target's meaning mechanically: a tactic battery tries to close each target
  theorem alone and to refute its top-level Prop hypotheses, on a copy of the challenge built before any candidate
  code ran. This does not inspect classes denied inside a conclusion or establish definition adequacy; cards
  show intentional per-theorem skip reasons separately from incomplete probe runs.
- **Reviews** carry what no machine checks: a fidelity review binds to the meaning it read on the trusted ref (the
  target and the in-project definitions it imports, the theorems and witnesses, the item's claim text, limits and
  assumptions, plus the stable Lean semantic environment) and goes stale when any of it changes. New fidelity writes
  require the expected meaning digest obtained before reading; each command reads one pinned trusted commit.
  A clean-context reader can perform literal read-back before seeing intent and earlier verdicts; independence
  remains a fallible procedure. A
  `human:` author is written only with `--human-approved` or after the person confirms at the terminal.
  Fidelity records name the current trusted item revision; explicit older revisions are refused. A resolvable
  recorded item revision that disagrees with the review's bound meaning is rejected, including records created
  by earlier versions. Self-review compares the reviewer with the trusted author, not a worktree edit.
- **`vl validate --incoming BRANCH`** lists what a merge would change on the trusted side: configuration,
  evaluators, Lean build files, targets, question fields, added reviews, deletions, receipts it would stale (also
  through a changed question). The items and reviews the branch brings are validated as they are on the branch: one
  that does not parse, a relation to an item the branch lacks, or an `answers` to a question with another `[lean]`
  target or theorems, or another `[python]` evaluator or entry, is an error.

The boundary has one hole by design: a process of the same user can write any file `vl` reads. `vl` makes that
detectable (reproduce a receipt from a clean clone; a protected trusted branch; a clean-context reviewer), never
impossible. docs/CHEATS.md lists every known attack and what catches it.
Incoming validation already rejects new candidate receipts and edits/deletions of evidence and existing reviews.
Status enumerates the current trusted tree, not all historical commits: bypassing that integration policy can
hide admitted evidence. Asserted `answers`, `refutes` and `uses` relations are not checked semantic implications.

## The protected check, step by step

`vl check ID` (commands/check.py) resolves the trusted ref and reads the configuration from it, loads the item
from the worktree, takes its question fields (target, theorems, witnesses; evaluator) from the item on the
trusted commit, records the memory cap the run gets, and gives the adapter a fresh scratch folder under
`$XDG_CACHE_HOME/verifylab/scratch/`. An item not on the trusted ref ends here: `unsupported`, no receipt.

Lean (adapters/lean_comparator.py), with the phase names the receipt records in `extra.phases`:

1. **prepare.** Read `research/vl.toml` from the trusted commit (its rules digest becomes an input). Read the
   target from the trusted commit and check it (header, one `sorry` theorem per listed name). Collect the
   in-project modules it imports, transitively, from the trusted commit. If the worktree holds every one of them
   unchanged, use them under their own names (shared mode); else write the trusted copies under `VLTrusted.*`
   (renamed mode), so that Comparator sees an altered definition as a mismatch. Collect the candidate's modules
   from the worktree, generate `VLSolution` from the target and the proof terms (or import the `solution`
   module), and lint the Lean files the candidate changed. Take `lean-toolchain`, `lake-manifest.json` and the
   lakefile's `leanOptions` from the trusted commit; the toolchain from the machine's elan home; the tools from
   the machine, pinned (nanoda too while `[lean] external_kernels` is on: without it the check stops here,
   `unsupported`); the Lake packages from the build cache (machine.toml `[caches]`, else the project's),
   mounted read-only, recording each package's checked-out revision and Lake's trace of every dependency module
   the trusted side imports (what a replay compares; status does not). Write a fresh Lake project.
2. **preflight.** `lake build --no-build` of the dependency modules the trusted side imports, in a workspace with
   only the generated lakefile, in the jail: a shared cache that would need a rebuild stops the check (`error`).
3. **isolation_probe.** landrun, inside the jail, must deny a write that the jail allows; else `unsupported`.
4. **prebuild_challenge.** `lake build VLChallenge` under the same landrun sandbox Comparator uses, before any
   candidate code is compiled; its build products are copied to a separate probe workspace.
5. **Comparator.** `lake env comparator config.json` in the jail, read line by line: `build_challenge`,
   `export_challenge`, `build_solution`, `export_solution_and_compare`, `kernel_lean`, `kernel_nanoda`. Its output
   is classified: `fail` only when the failure is certainly the candidate's (a different statement, an altered
   definition, a forbidden axiom, a rejected or missing proof) and Comparator exited normally; trouble on the trusted
   side or with a tool, and a Comparator ended by a signal or a limit, are `error`; a pass needs every kernel's own
   acceptance line, and a kernel's rejection counts only from Comparator's lines after the solution's export.
6. **probes.** After a pass only: the statement probes run on the probe workspace under `probe_timeout`; whatever
   happens to them, the verdict stays Comparator's.

Every step from preflight on runs in the jail under the same memory cap, with bounded output, and within what
remains of the check's `--timeout`; a command that closes its output and keeps running is still killed at the
deadline. Protected Lean deadlines stop the whole service, including descendants that closed output;
`RuntimeMaxSec` bounds the service if its client disappears.

Python (adapters/python_eval.py): read the candidate files from the worktree (no symbolic link on the way) and
copy them; take the evaluator from the trusted commit, after checking that the trusted item binds this
evaluator; ask a judge process, in its own jail, for `CASES` and `TIMEOUT_S`; run the candidate in another jail,
one call per case, outputs as JSON; give the outputs to the judge; `pass` only when every case passes. A result
counts only from a process that exits 0; a candidate process ended by a signal or a limit is `error`, never `fail`.

Last, `vl check` seals the receipt and writes it to `research/evidence/<id>/` (protected) or
`.vl-cache/explore/<id>/` (exploratory), and exits 0 on pass, 1 on fail, 3 on error or unsupported. A check that
stopped before it read any candidate input writes no receipt, and `vl check` never writes one that `vl validate`
would reject. An exploratory
check runs the same steps with the question, the configuration and the build files read from the worktree, and
also accepts unpinned tools and the caller's `COMPARATOR_*`, `ELAN_HOME` and `PATH`.

## Modules

```
cli.py                        argument parsing; maps vl's own errors to exit 2
output.py                     text or versioned JSON (vl.output/1) on stdout; failures on stderr (`vl lane exec`
                              prints only its command's output)
commands/init.py              vl init: research/ layout, vl.toml, git config vl.trustedRef; --tools pins tools
commands/show.py              vl show: cards, --impact, --brief; ID@rev resolution
commands/find.py              vl find: search through the index, statuses from the records
commands/check.py             vl check: question from the trusted ref, adapter, receipt, exit code
commands/review.py            vl review: sealed reviews; fidelity binds to the admitted target
commands/validate.py          vl validate: records, references, targets, probes, --incoming classification
commands/lane.py              vl lane: worktrees made whole or not at all, bound to their repository by git's common
                              directory, a lock on the lane count, jailed exec over an overlay, a close that waits
                              for no running command and follows no link
config.py                     research/vl.toml from the trusted ref; the trusted ref; configuration digests
machine.py                    machine.toml (read once per process), the tools store, REVISIONS pins, the cap of
                              all runs
gitref.py                     read-only git: one tree listing per commit, blobs through one cat-file --batch,
                              diff, status; path lists NUL-separated
repo.py                       items, receipts and reviews on disk; admission; staleness of a receipt
records.py                    item parsing, question digest, receipt and review shapes and seals
status.py                     derived status and fidelity
render.py                     cards, briefs and impact views
index.py                      SQLite search cache of items and Lean declaration names
jail.py                       bubblewrap jail, guarded systemd services/scopes and vl.slice cap, bounded streaming
candidate_exec.py             runs untrusted Python in a separate, sandboxed child process
_candidate_child.py           the child: imports the candidate, calls it, writes outputs (stdlib only)
leanmod.py                    Lean headers, module paths, import closure and rewriting, target theorems
leanlint.py                   command lints over candidate Lean files
adapters/base.py              CheckRequest, CheckOutcome, the Adapter protocol
adapters/lean_comparator.py   the Lean check: check project, tools, jail, Comparator, classification
adapters/lean_probes.py       probe file generation and parsing of its results
adapters/StatementProbe.lean  the probe metaprogram
adapters/python_eval.py       the Python check: candidate jail, judge jail, aggregation
```

## What comes from which tool

| Tool | What `vl` relies on it for | What `vl` adds |
|---|---|---|
| Lake | building, packages, `lake build --no-build`, `lake env` | a generated lakefile with the packages read-only; never downloads |
| Comparator | statement equality, identical definitions, axiom allowlist, kernel replay, external kernels | a fresh project whose trusted side comes from the trusted ref; renamed mode; failure classification; phase timings |
| lean4export | exporting both environments for Comparator | pinned by sha256 |
| nanoda | a second kernel, through Comparator's `external_kernels` | pinned; named in the receipt's `checked.kernels`; required while `[lean] external_kernels` is on |
| landrun | the Landlock sandbox of Comparator's builds and of the challenge pre-build | a probe that proves it enforces |
| git | the trusted ref, its bytes, admission, worktrees for lanes and the common directory that owns them | `vl.trustedRef`; incoming classification; question and input digests; a lane's binding to its repository |
| bubblewrap | namespaces, no network, cleared environment, mounts, overlays | the jail's mounts; lanes' copy-on-write build cache |
| systemd | protected Lean services with AF_UNIX denied; resource scopes for other runs; slice memory/task limits | actual denial tested before protected Lean exec; caps recorded; other uncapped runs say so; slice cap is session-only (`systemctl --user set-property --runtime`) |

What `vl` adds overall: the split between question and answer, receipts and derived status, statement probes and
lints, a judge that never loads candidate code, lanes, incoming validation and fidelity reviews.

## Configuration layers

1. `git config vl.trustedRef`, repository-local; `--trusted-ref REF` overrides it for one command.
2. `research/vl.toml` as committed on the trusted ref (`templates/vl.toml`); the worktree's copy only for
   exploratory checks, and while the trusted ref has none.
3. `$XDG_CONFIG_HOME/verifylab/machine.toml` (`templates/machine.toml`) and the tools store
   `$XDG_DATA_HOME/verifylab/tools/<toolchain>/` with its `REVISIONS`.
4. Command-line flags.
5. The environment: XDG folders (also `$XDG_CACHE_HOME` for scratch; a machine file or tools store they move is
   recorded in receipts and named on statuses); exploratory checks also read `COMPARATOR_*`, `ELAN_HOME` and
   `PATH` for tools; `vl lane exec` takes `ELAN_HOME` of its call. `PATH` never selects a launcher or git.

## Extension points

A new kind of checker is an adapter: a class with `name`, `applies(item)` and `check(request) -> CheckOutcome`
(adapters/base.py), listed in `commands/check.ADAPTERS`. It never raises (a problem is `error` or `unsupported`),
records candidate-side inputs in `files` and trusted ones in `trusted_files`, and names test-only guards for each
defence, each with a planted case. Its item table goes in `records._ITEM_KEYS`, its question fields in
`records.QUESTION_FIELDS`, the trusted subset in `commands/check.TRUSTED_FIELDS`, and the file a fidelity review
binds to in `status.question_file`. Smaller points: the probe battery (`lean_probes.CORE_BATTERY`,
`OPTIONAL_TACTICS`), the lint rules (`leanlint.RULES`), Comparator's failure classes (`lean_comparator.EXCEPTIONS`).
