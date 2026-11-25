# VerifyLab guide

The [README](../README.md) says what VerifyLab is and how to start. This guide holds the detail: what the trust
rests on, setting up a machine and a project, running many checks and several machines, replay, how agents use
`vl`, every command and every status. The record format is [SPEC.md](SPEC.md); installation of the verifier tools is
[INSTALL.md](INSTALL.md); the reasons behind each rule are in [ARCHITECTURE.md](ARCHITECTURE.md) and [adr/](adr/).

## The promise and the limit

**The promise: protected checks compare an answer with the committed question, and status distinguishes
admitted evidence from exploratory or stale results.** What a result
must prove is committed on a trusted ref; a check reads the question from there and only the answer from the
worktree, runs in a jail, and writes a receipt bound to the digests of everything it read. Status is derived from
receipts and reviews, never written. A receipt counts only once the integrator commits it on the trusted ref, and
goes stale when any input changes.

**The limit: a process of the same user can write any file `vl` reads**, the repository and its records included.
The integrator must run `vl validate --incoming BRANCH` and generate protected receipts with `vl check`:
incoming validation rejects candidate-written receipts and changes or deletions of existing evidence and reviews.
The receipt's self-hash checks content integrity; it does not authenticate who ran a checker or prove execution.
`vl replay ID` reruns an admitted receipt from a clean clone with its checked inputs preserved in Git. An exact
match also requires the recorded tool hashes. Use `vl replay ID --contract recheck` for a separately recorded
rerun with tools built locally whose hashes differ. The trusted ref can be a protected branch on the forge, and
a reader in a fresh context can review the target's meaning. Every derived `verified` uses admitted evidence.
An alternate trust anchor (`--trusted-ref`, a
machine policy moved by `XDG_*`) is named on the status it produces.

## Audit position: AI checks AI

A check records the tools and evidence of the run. A human review is attributed when one is recorded. The proof is
checked by Lean's own kernel and by nanoda on newly issued protected checks. The meaning of
a target is read by an agent started with a clean context (another model family than the statement's author helps;
`vl` requires none); its fidelity review is an attributed judgement, never a certificate. Agreement between agents is a
clue, not proof. A person may add a review, an intuition or a correction as optional input: it is recorded and
attributed like any other, and no status depends on it.

The records exist to put results in order. Provenance (who or which agent wrote a statement, from which idea),
statuses, probes and reviews let a coordinator find the results that matter most, improve them with other agents,
vary them, connect them to each other and finally explain them to people (the `understand.md` reference of the skill). That order helps humans
choose what to read first; it never sets a status. Provenance is declared on items (`[provenance]`, `[origin]`, ADR 0014).

## What a check proves

**The fast check** (`vl check ID`, protected): for Lean, Comparator, in the jail, on a fresh project whose
target and in-project definitions come from the trusted ref: same statement, identical definitions, permitted axioms
only, and replay by Lean's kernel and nanoda. A new protected Lean check without nanoda or with
`[lean] external_kernels = false` is `unsupported`. The false setting can preserve historic single-kernel
coverage for recognized receipts. See [historical replay](REPLAY.md). Statement probes run
beside Comparator (ADR 0005) and are recorded after a pass; they look for targets that automation alone
closes or whose hypotheses it refutes, and list the definitions, totalized operations and boundary values a referee
must weigh (ADR 0010); command lints flag what can change meaning in the files the candidate changed. For
Python, the candidate and the trusted evaluator's judge run in two separate jails; every case must pass.

## A weakened statement is not a solution

Suppose the trusted target asks for a proof of `P`. A candidate instead proves `P ∨ True`, which is true
regardless of `P`: the protected Lean check rejects the changed statement. If `P ∨ True` was already the
trusted target, a proof can pass; statement probes warn about triviality, and a fidelity review must judge
whether the target actually represents the intended problem.

Statement probes can expose easy targets and contradictory premises. They do not establish whether a
solution class inside a non-existence claim captures the intended problem. Cards show per-theorem
premise-vacuity skip reasons; meaning review and suitably scoped test lemmas still matter. They also list what
a referee must weigh (ADR 0010): the in-project definitions the statement depends on, which Comparator
checks to be the trusted ones but nothing checks to mean what the question asks; the totalized operations in the
statement and in those definitions (`n - 1` on ℕ, `x / y`, `Real.sqrt`, `tsum`, `deriv`, ...); and the small values
of its ℕ binders where it is vacuous or degenerates. `vl validate` warns until a fidelity review names each
definition; these never change a status.

## Requirements

- Linux, Python ≥ 3.11 (`vl` uses the standard library only), git.
- bubblewrap (`bwrap`): every check, protected or exploratory, and every `vl lane exec` runs in its jail.
- A working systemd user manager is required for protected Lean checks: an outer service enforces
  Comparator's `RestrictAddressFamilies=~AF_UNIX`, with actual socket denial tested before each jailed command.
  Without enforcement, the check stops with `error` or `unsupported`, never `pass`. For Python, exploratory checks and lane execution,
  systemd supplies optional memory/task caps; without it those runs are uncapped, with a warning recorded.
- For Lean: a toolchain installed with elan, and [Comparator](https://github.com/leanprover/comparator),
  lean4export, landrun (Landlock, Linux ≥ 5.13), and nanoda, the independent kernel required for new protected checks.
- For Python checks: `/usr/bin/python3` (or another interpreter under a system folder).

`vl` never downloads a toolchain, Lake package or verifier tool. A missing required tool or toolchain makes a
check `unsupported`; an incomplete dependency build can instead produce `error`. Prepare the dependencies before
checking.

## Install

See [INSTALL.md](INSTALL.md) for the CLI installation, verifier build recipe and machine setup. The CLI and
skills can be installed from a local checkout:

```sh
uv tool install .
export PATH="$(uv tool dir --bin):$PATH"
vl init --skills claude                # install the verifylab skill for Claude Code
```

Run `vl` from the mathematics repository. Without a tool install, run
`uv run --project /absolute/path/to/verifylab vl attempts check` there.

## Set up a machine

Protected checks take their tools from this machine only, never from the caller's `PATH` or `COMPARATOR_*`;
the system programs `vl` starts (bubblewrap, systemd-run, env, git, ...) come from system folders by absolute path,
or from `machine.toml` `[launchers]`:

```sh
vl init --tools --comparator PATH --lean4export PATH --landrun PATH [--nanoda PATH] [--toolchain TOOLCHAIN]
```

copies the binaries to `~/.local/share/verifylab/tools/<toolchain>/` and pins their sha256 in a `REVISIONS` file
there (the toolchain defaults to the current repository's `lean-toolchain`). A protected check refuses any tool
that is not pinned, or that changed since. Use `~/.config/verifylab/machine.toml` for the settings in
`templates/machine.toml`.

| Setting | Role |
|---|---|
| Tool paths and `elan_home` | Locate the pinned verifier tools and Lean installation. |
| `[caches]` | Name each project's built Lake directory. |
| `[check] memory_total` | Cap all resource-scoped VerifyLab runs together. Default is 70% of RAM. |
| `[check] build_cache` | Reuse trusted challenge build products. Set it to false to disable reuse (ADR 0005). |
| `[check] reuse_solution_builds` | Reuse candidate builds with identical inputs. Off by default. The kernels still replay the proof (ADR 0008). |
| `[sandbox] class` | Set `workstation` (default) or `disposable`, described below. |
| `[queue]` | Set admission limits for heavy local runs. |
| `[machine] name` | Identify receipts transferred between machines (ADR 0015, proposed). |

`XDG_CONFIG_HOME` and `XDG_DATA_HOME` can select another machine file or tool store for a call. Receipts
record these overrides and status views disclose them.

### The job queue

Heavy runs wait in the machine's job queue instead of failing. `vl check` and `vl lane exec` start only while fewer
than `[queue] slots` jobs run (default: the CPUs divided by `[queue] threads`, the CPUs one check may use, default 4),
the jobs' reservations fit `memory_total`, and MemAvailable stays above `keep_free` (default 3G); a check's runs
together get `threads` CPUs (the CPU quota of its slice), and Lean and Lake in them that many worker threads
(LEAN_NUM_THREADS; Comparator does not pass it on to the builds it sandboxes, which only the quota bounds); a check reserves the largest memory peak of its item's five newest admitted or exploratory receipts,
for a first check the largest of its checker's in the repository (`--reserve SIZE` overrides it).
`vl queue run --mem 4G -- python train.py` uses the same queue. The size reserves memory for admission. If a
usable systemd user manager can apply the scope, the size also sets MemoryMax with no swap. Without that
manager the generic runner warns and starts uncapped. Protected Lean uses its own required enforcement.
`--max-wait SECONDS` gives up with exit 2; `vl lane exec` takes `--reserve SIZE` and `--max-wait` too (default: its
`--timeout`, or `[queue] max_wait` when shorter), and a lane whose command waits there cannot be marked done or
closed. The oldest waiting job's memory reservation and one slot count against younger ones, so a big check is not starved by small
runs; a reservation that with `keep_free` exceeds the machine's RAM could never start, and is refused at once (exit 2). Reservations govern admission. Per-run caps are enforced only by a resource scope.
`vl queue status --explain` also prints each limit (threads, slots, `memory_total`, `keep_free`, the default reservation, the
maximum wait), its value and where it came from: a flag, `machine.toml` or the default (`--mem` and `--max-wait`
show what a run with those flags would get). A Lean receipt records `extra.cpu_seconds`, the CPU time of its runs
added up, and `environment.host`: the sandbox class, kernel, CPU model, cores and RAM of the machine, so that
receipts of different machines can be compared.

## Third-party Lake packages

A Lake package's code runs when its cache is built, outside the jail, and when a module that imports it is
elaborated. `[lean] trusted_packages` in `research/vl.toml` on the trusted ref names the packages whose code may
run on a workstation; by default Mathlib and its official dependencies (`mathlib`, `batteries`, `aesop`, `Qq`,
`plausible`, `proofwidgets`, `importGraph`, `LeanSearchClient`, `Cli`, from `leanprover` or `leanprover-community`).
If `lake-manifest.json` lists any other package, `vl check` (protected or exploratory), `vl lane new` and `vl import`
return `unsupported` before anything builds, naming the packages, unless `machine.toml` says `[sandbox] class =
"disposable"`: a purpose-built virtual machine, fresh for each batch, without credentials, destroyed afterwards. The
class is the machine owner's word; a receipt made on a disposable machine records it, and the packages it let in
(docs/adr/0012-third-party-package-gate.md, proposed).

## Set up a project

In a git repository, on the integrator's branch: `vl init` creates `research/` (items, targets, evidence,
reviews, evaluators) and `research/vl.toml`, records the branch as the trusted ref in the repository's own
`git config vl.trustedRef`, adds `.vl-cache/` to `.gitignore`, and prints a section for `AGENTS.md`
(`--write-agents` appends it). Commit `research/`: `vl` reads `research/vl.toml` as committed on the trusted ref.

## First run (five minutes, no Lean needed)

The README's quickstart, continued with a fidelity review and a wrong candidate. On the Python fixture of this
repository: an evaluator with the n-th prime as the question, and a candidate.

```sh
cd .. && cp -r verifylab/fixtures/python-planted/repo demo && cd demo   # next to the clone, not inside it
rm research/vl.toml && git init -q -b main && vl init
git add -A && git commit -qm "question, evaluator, candidate"
vl check nth-prime        # protected pass, exit 0; writes research/evidence/nth-prime/<receipt>.json
vl show nth-prime         # status pending-admission: the receipt is not on the trusted ref yet
git add research && git commit -qm "admit the receipt"
vl show nth-prime         # check passed (label `verified`); fidelity: not reviewed
vl show nth-prime --json > review-context.json  # retain this before reading the evaluator at trust.commit
meaning_digest=$(python -c 'import json; print(json.load(open("review-context.json"))["items"][0]["meaning_digest"])')
vl review nth-prime --kind fidelity --expected-meaning-digest "$meaning_digest" --verdict faithful --author agent:referee --text "CASES and judge test the n-th prime"
git add research && git commit -qm "review the evaluator"
cp -r ../verifylab/fixtures/python-planted/defects/wrong/experiments .   # a wrong candidate
vl check nth-prime        # fail, exit 1, with the cases that did not pass
vl show nth-prime         # check passed, stale (label `verified-stale`): the admitted pass no longer matches
vl validate               # says which input changed
```

### First protected Lean check

After configuring the [Lean tools](#set-up-a-machine), open the included Lean fixture in a scratch checkout:

```sh
cp -R fixtures/lean-planted ../lean-demo
cd ../lean-demo
git init -q -b main
git config vl.trustedRef main
printf '.vl-cache/\n.lake/\n' > .gitignore
git add -A && git commit -qm "freeze the Lean questions and clean candidate"
vl show double-even --snapshot
vl check double-even
vl admit
```

The snapshot identifies the target and imports being checked. To create a fresh VerifyLab project instead,
run `vl init` in a Git checkout with its Lake files, then use `vl new ITEM` to create a Lean question template.
Review its target and candidate separately and commit the fixed question on the trusted branch before checking.

## Many items without a person in the loop

For hundreds of items the integrator runs three commands on the trusted checkout. `vl check --all` (every item a
protected check can ask: a checker applies and the item is on the trusted ref), `--stale` (only those a check could
improve: unverified, `verified-stale`, explored, inconclusive, or failed on an answer that has since changed) or
`--ids FILE` (one id per line, `#` comments) starts one protected `vl check` per item, `-j N` at a time (default: the
queue's slots), each waiting in the machine's job queue as a single check does, so the queue, not `-j`, decides how
many run at once. It prints one table (item, verdict, seconds, seconds waited in the queue) and exits 0 when every
check passed, else 1 if any failed, else 3; `--json` gives the same. Then `vl admit` commits the new receipts and
reviews: it refuses unless `vl validate` passes and the only changes in the worktree are new files under
`research/evidence/<id>/` and `research/reviews/<id>/` (an existing record changed or deleted, a new item, any other
file: refused), and it commits exactly those with a title that names the counts (`Admit 120 receipts and 3 reviews`).
It never pushes, and it runs only on the trusted branch: committing a record there is the admission, so `vl admit`
is the integrator's step, never the candidate's.

### Several machines

There is no daemon and no network code: the distribution is ssh and `vl check --ids`. On the integrator, at the
trusted commit:

```
vl jobs export jobs.json --stale
vl jobs shard jobs.json --weights worker-a=3,worker-b=2,worker-c=4 --by reservation --out shards
```

`export` writes the job list (item, trusted commit, question digest and the expected reservation and seconds taken
from earlier admitted receipts); `shard` writes `shards/worker-a.txt`, `worker-b.txt` and `worker-c.txt`, balanced by
expected cost in proportion to the weights. Each machine needs the repository at the trusted commit the list names,
its pinned tools and its own `machine.toml` (its queue's slots, threads and memory floor bound the load), and runs
`vl check --ids shards/NAME.txt -j N`; the receipts it writes are plain files under `research/evidence/`, which
return to the integrator by rsync or a git bundle. There `vl validate` and `vl admit` take them as any others, and
`vl jobs missing jobs.json` prints the ids no admitted receipt answers yet (`--out left.json` keeps them as a list to
shard again for a retry). A receipt counts for a job only if it was checked at the list's commit for the list's
question.

## Replaying a receipt from a clean clone

`vl replay ID` (ADR 0009) runs the protected check of the receipt that decides the item's status, the one `vl show`
names, again (`--receipt RID` names another admitted receipt) in a fresh clone of the repository, made by git's own transport so
that nothing of this worktree comes along, and compares the new receipt with the admitted one: verdict, question
digest, every input digest, toolchain, tool hashes, Lake package revisions and traces, kernels, and any statement-probe
hit (`vacuous_by`, `trivial_by`) that the replay finds and the receipt lacks. It prints `MATCH`, or
`MISMATCH` with every difference, and keeps a record under `.vl-cache/replay/`; exit 0, 1, or 3 when it could not
compare. The answer is checked out at the receipt's `candidate_head` (`inputs.candidate_head`), else at the trusted
commit that admits it, wherever the clone holds every checked file with its recorded digest and the item's answer
binding (`question_digest`); the question is read from the receipt's `inputs.trusted_commit`. A receipt of an edit
nobody committed (`candidate_uncommitted`) does not replay: git does not hold what was checked, so commit the answer
before checking.

The replay uses this machine's pinned tools and build cache, and builds the trusted side anew. On the integrator's
own machine it shares that machine's tools, caches and account; a replay on another machine is what closes the
same-user hole. [REPLAY.md](REPLAY.md) is the runbook for a stranger, with what a match proves and what it
does not. A match does not stale or verify anything: status is unchanged, and the replay is of the receipt, against
the commits it names.

## How agents use it

Install one entry skill from the same VerifyLab package as the CLI. Use `vl init --skills codex`,
`vl init --skills claude`, or `vl init --skills PATH` with Pi's skill directory. Open a new session and point
the project instructions at the `verifylab` skill. It directs the agent to understand, explain, improve, create,
check, or choose what to study first using source or compiled dependency views. Every route names a first command and a source-only fallback.

External agents and their harness choose tasks and context. `vl` supplies records, search, compact cards and
isolated workspaces. A **lane** is a separate Git worktree. Each agent edits its own copy, and the integrator
reviews and merges useful changes into the trusted branch.

A person can use the skill's `understand.md` reference for scientific dialogue: clarify a goal, ask which results matter for it,
contribute an intuition, or discuss and refine an experiment against recorded attempts. Explanations adapt
to the person; evidence and attributed human/agent appraisals remain distinct, and a human appraisal is optional input, not a guarantee. Useful contributions stay
in the project's existing records or notebook for later readers. The interaction happens through the
external agent; `vl` provides the local evidence and records.
The cost of reproducing a whole result does not settle the value of a bounded contribution. Assess reusable
lemmas, simpler certificates or application bridges against the goal, with reasons for pursuing or stopping.

For a hard problem, the coordinator maintains a living Markdown plan in the project's chosen location: goals,
hypotheses, alternative roads, why each intermediate result matters, abandoned roads and the next discriminating
check. Link records and read their current status with `vl show`; the plan is reasoning, not a second status store.
The plan explains which work unlocks the goal; agents can change routes with a reason when new ideas or evidence help. Targets are
frozen after a prior-art search, by the most capable agent available (a person may also do it), with a novelty label in the
item body (`known: <name, where>` or `new: prior art searched in <where>`); reports count new results and known
ports apart, and each record names the downstream result or decision that uses it.
Review the meaning of important intermediate targets as well as the final result. A faithful review is an
attributed judgement, and a verified lemma is not by itself evidence that the research strategy will succeed.
Read `vl show ID --json` before a fidelity review and retain its `meaning_digest` and `trust.commit`.
Read the target and definitions at that commit, then write the review with
`vl review ID --kind fidelity --expected-meaning-digest DIGEST --verdict faithful --author agent:referee --text "REASON"`.
A changed meaning rejects the write; reread it before judging. The digest binds the context, not understanding.

Choose context for each task, including a deliberately small or independent start. Useful context includes
lemmas with assumptions and limits, examples, unsuccessful attempts and source excerpts. Different agents can
receive rich or sparse context, varied objectives/prompts or an invitation to find a new strategy; no amount of
strategic reading or choice of model is prescribed. `vl show --brief` packs only the requested records; it does
not retrieve linked sources. Compact cards include reviews, relations and appraisals within their budget;
`--no-appraisals` omits appraisals with an explicit count. Add the relevant plan and dependency links yourself, and open full cards
when needed. The default character budget is adjustable, not a limit on the agent's context window.

After meaningful work, keep a dense note in the item or existing research plan: method or connection, conditions,
result, remaining gap and useful next uses, with detail reachable by links. A short run retrospective can record
which ideas helped and where attempts failed. These notes support later agents and the same agent after context
compaction. Their interpretation and selection remain with the researcher.

Purpose and attempts work in any Git repository. The protected check route uses a VerifyLab project, and the native route uses the project's own fixed target and Comparator. The reference `improve.md`
improves research results; toolkit/instruction fixes follow the contribution workflow. Optional appraisals preserve
configured ratings and their actual input basis; no numerical rating decides proof validity or scientific truth.

## Context and appraisals

On a new research repository, `vl init --context --write-agents` offers 3 Markdown homes: `research/problem.md`
for the question/objective, `research/map.md` for results, routes, limits and useful failures, and
`research/ideas.md` for attributed speculation. Existing files are preserved. The AGENTS section points to these
homes. `[context] problem`, `map` and `ideas` can select existing `.md` paths; optional `notes = ["research", "notes"]`
registers Markdown search homes. The default notes scope is `research_dir` plus the 3 role files. Paths are
relative to the repository; role paths must be distinct and unknown `[context]` keys are errors. Strategic reading remains a choice.

```sh
vl find "failed route" --scope notes
vl find "novelty" --scope reviews
vl find "objection|missing bridge" --scope notes --regex
```

Search reads notes and validated reviews live, with reopenable `path:line` locators, scope counts and warnings.
Default search includes all groups; `--kind` retains the item/Lean filter. Dependency libraries and unregistered
notes are outside the scope. A miss establishes no wider absence. Useful negative evidence and uncertain routes
remain available even when an appraisal is low or another route is selected.

For an existing item, save the actual evaluator contribution as `assessment.json`, for example:

```json
{
  "schema": "vl.appraisal.v1",
  "subject": "connection",
  "ratings": {"novelty": {"value": "medium"}, "usefulness": {"value": "high"}},
  "goal": "Find a reusable route to the current problem",
  "input_context": "Item statement, assumptions and limits; selected source passages and research map.",
  "context_files": ["research/problem.md", "research/map.md"],
  "provenance": {"model": "actual-model-id", "role": "evaluator", "run": "actual-run-id"},
  "sources": [{"locator": "source item or paper section actually read", "access": "full-text-read"}],
  "raw_output": {"novelty": "medium", "usefulness": "high"}
}
```

This is a format example, not an observed model result. Use only fields the contributor actually supplied;
`input_context` must describe or locate its actual input. Optional `context_files` bind local file contents.
The contribution can omit prose; a later explanatory review has its own author and can cite the earlier full
review ID in `review_references`. Keep useful objections when writing a concise synthesis.

```sh
vl review ID --kind appraisal --assessment assessment.json --author agent:evaluator
vl review ID --kind appraisal --assessment synthesis.json --author agent:researcher --text "Reasoned synthesis"
vl show ID --brief
```

Each contribution stays its own record, including disagreements. There is no latest-wins rule, average or
consensus score. Stale, unknown and abstaining work stays reachable through search and full cards. A current
local basis cannot detect later literature. Changed item revisions, rubric or recorded local context can make
an appraisal stale without changing a proof receipt.

The [appraisal schema](SPEC.md#contextual-appraisal-format) defines custom rubrics, abstentions, numeric
ratings and distributions. Full views and exports can include the actual source context and raw output.
Choose what to record and publish under your project's rules.

Existing projects can print the current shared agent instructions with `vl init --print-agents`.

## Read a Lean checkout

In an existing Lake project, use its own build and check workflow. `lake build TARGET` checks compilation,
and Lean's `#print axioms Qualified.name` reports the axioms used. Use the project's Comparator when the task
is to check a candidate against a frozen statement and an allowed set of axioms. A project that already uses
VerifyLab records can use `vl check ID` for that workflow.

Use `vl pin PROJECT` to pin local Git inputs and `vl inspect LOCK NAME` to read a source signature and related source indicators. The [Lean checkout guide](FOREIGN.md) describes lockfiles, inputs and coverage.

`vl graph LOCK` shows dependencies. `vl value LOCK --root NAME` exposes deterministic columns. `vl order LOCK --root NAME` emits a bounded
packet of source signatures and locations. Open the committed source at those locations to read proof bodies,
section variables and instance assumptions. Use Lean's goal view or `#check @Qualified.name` for elaborated context.

For a compiled study view of a static TOML library, build the pinned Lake targets on the authorized worker
and use `vl graph LOCK --dump inputs --lean LEAN`. Obtain the matching executable path from `elan which lean`
in the checkout. Pass `--graph inputs/graph-inputs.json --study` to `vl value LOCK --root NAME`, where `NAME` is a
qualified Lean constant. The [Lean checkout guide](FOREIGN.md#graph-value-and-reading-packets) defines the
columns, target coverage and optional block labels.

`vl trial LOCK` runs a patch trial through a chosen backend and keeps its cost and evidence. `vl lane integrate NAME --no-merge` previews a lane integration.

## Purpose and attempts

`vl attempts check DIR` validates the diary in any repository (default folder `attempts/`). Pass a single
Markdown file to audit it alone. In Git, the command checks forecasts against the last committed version
of that file before its first recorded outcome. A source edit or run may have happened earlier.

Use `vl attempts table DIR` to compare forecasts with recorded outcomes. A kept edit awaiting a proof check
is `unverified` and resolves measured costs. An author reporting `accepted` supplies statement references
and a list of axioms used, which can be empty. Refer to [the convention](PURPOSE-AND-ATTEMPTS.md) for statuses,
forecast resolution and score rules.

| Record | Written by | Read with |
|---|---|---|
| `attempts/*.md` | A person or agent. It holds plans, forecasts, reported outcomes and scores. | `vl attempts check` and `vl attempts table` |
| `*.trial.json` | `vl trial`. It records a build comparison at a Git pin and can link to a Markdown attempt. | The JSON record itself |
| `research/items/*.md` and `research/evidence/` | Project authors and `vl check`. They record items and sealed check evidence. | `vl show ID` |

For a new attempt run
`vl attempts new attempts/edit.md --purpose improve --claim "Remove a redundant step" --by agent:reader`.
Complete the forecast and commit it before work. Pin the attempt's recorded `base` even if the forecast moved HEAD. The command
`vl trial vl.lock --attempt attempts/edit.md --patch edit.diff --targets Tiny --runner RUNNER`
writes its associated `edit.trial.json`. Add the observed outcome to the Markdown after measuring the run and
checking the contract. The runner receives `DIR TARGET...` and returns the build exit status. Use
[the runner examples](FOREIGN.md#try-a-simplification) from the matching source checkout. A passing build can
include a changed statement. Compare the old and new contract separately.

## Commands

`vl --help` groups the agent commands by purpose. For a basic file search use `rg`, for diffs use Git,
and use the project's runner or scheduler for build jobs. `vl queue` can provide local admission for a build
when the project uses VerifyLab machine scheduling. `vl import lake PROJECT` (including the shorthand `vl import PROJECT`) and `vl simplify LOCK` still
run with a deprecation message. Use `vl pin` and `vl trial`.

In a VerifyLab project use `vl show` for item cards, `vl find` for project records and `vl replay` for receipts.
The compatibility selectors `vl inspect --item`, `vl inspect --search` and `vl check --replay` accept the same
arguments as the respective canonical commands.

`--help` lists each command's flags. Most `--json` commands use `vl.output/1`. Diary checks and tables and
graph dumps carry their own schemas. See [JSON schemas](SPEC.md#versions). Lane execution passes the
command's output and exit code through and refuses `--json`.
Exit 2 is always a usage or setup problem, or a wait in the job queue past `--max-wait`; never a verdict.

| Command | Does |
|---|---|
| `vl pin PROJECT --at REV` | write a local Lake lock without running Lean or evaluating Lake configuration |
| `vl inspect LOCK NAME` | show the pinned source signature and source indicators, or locate a name with `--find` |
| `vl graph LOCK --dump DIR --lean LEAN` | extract kernel dependencies and ranges from compiled inputs and write a manifest |
| `vl value LOCK --root NAME --graph MANIFEST --study` | report study and simplification rankings for the named roots, with the numbers behind each row; `--blocks FILE` supplies block labels |
| `vl order --from PROJECT --at REV --root NAME` | make a budgeted reading packet from a Git pin in one command; also accepts a lockfile instead of `--from` and `--at` |
| `vl trial LOCK --attempt FILE` | compare pinned and patched builds with a named runner; link the measurements to a Markdown attempt, or use `--out` for standalone JSON |
| `vl attempts new FILE` | start a forecast at a fixed Git base; pass `-` for stdout |
| `vl init` | create `research/` and `research/vl.toml`, write `git config vl.trustedRef`; `--context` adds optional problem/map/ideas homes without replacing files, `--write-agents` adds pointers; `--tools` sets up verifier binaries; `--skills [TARGET]` symlinks (or `--copy`) the `verifylab` agent skill idempotently |
| `vl status` | repository-wide triage: counts by derived status and kind, open lanes vs `[lanes] max_parallel`, commits ahead of trusted ref/upstream, worktree edits vs the trusted commit, and items needing admission, check, or fidelity review |
| `vl new ID` | scaffold `research/items/ID.md` (with `[provenance]` and `[origin]`) and its Lean target under `research/targets/` (`--no-lean` for non-formal items) so the result passes `vl validate` |
| `vl show ID[@rev]` | result card with status, verdict ladder, reviews and relations. `--brief` gives a bounded handoff. `--snapshot` provides the trusted target and meaning digest. |
| `vl find TEXT` | search items, project Lean declarations, registered Markdown notes and validated reviews; `--scope all\|items\|lean\|notes\|reviews`, `--regex` for explicit notes/reviews; source hits show reference/access, notes/reviews have locators; local only, exit 1 when nothing matches |
| `vl check ID` | check against the trusted ref and write a receipt. `--explore` uses exploratory evidence. Use `--help` for batches, check outcomes, queue limits and known-answer runs. |
| `vl replay ID` | run an admitted receipt's protected check again in a fresh clone and compare (ADR 0009): exit 0 match, 1 mismatch with the differences, 3 could not compare; `--receipt RID`; a foreign receipt that matches is replaced by the replay's own receipt in `research/evidence/`, for `vl admit` (ADR 0015, proposed) |
| `vl receipts export OUT.tar[.gz]` / `import FILE --from NAME --trust own\|foreign` | move protected receipts between your machines (ADR 0015, proposed): `export` bundles pending receipts from this machine and lists other origins so you can forward their original bundles; experimental `import --trust own` writes receipts to `research/evidence/` (they count once `vl admit` commits them; every tool sha256 must be pinned on this machine), `--trust foreign` quarantines them, shown on the card as "foreign, not counted until vl replay"; set `[machine] name` in machine.toml first |
| `vl review ID[@rev] --kind K` | record an attributed review. Fidelity and correspondence reviews bind the meaning digest. See [review formats](SPEC.md#reviews). |
| `vl validate` | check records, references, receipts, reviews and targets, and warn on process state written into a record's prose, on target hypotheses its `assumptions` leave out, on modules `lake build` of their library leaves out, and on new items that declare no `[origin]` (or, with a target, no `[provenance]`); exit 1 on errors; `--incoming BRANCH` before a merge |
| `vl admit` | the integrator's step on the trusted branch: refuses unless `vl validate` passes and only new receipts and reviews differ from the trusted commit; commits exactly those, titled with their counts; never pushes |
| `vl lane new\|list\|exec\|done\|integrate\|close` | isolate parallel work in Git worktrees. Run lane commands in the jail with a read-only shared cache. Use `vl lane integrate NAME --no-merge` to preview integration. |
| `vl export catalog OUT` | write `OUT/catalog.json` and one self-contained `OUT/index.html` (readable on a phone, light or dark, filters by status, kind, area and text) listing every item of the trusted ref with its derived status, kernels, probes, fidelity, provenance and origin (declared by the author, ADR 0014), relations and the `vl replay` command for its receipt; read from the trusted commit alone and byte-identical for the same records (ADR 0011); the schema is in docs/SPEC.md |
| `vl jobs export\|missing\|shard` | the protected checks to run on several machines, as plain files: `export OUT.json (--all\|--stale\|--ids FILE)` lists them (item, trusted commit, question digest, expected reservation and seconds), `missing JOBS.json` prints those no admitted receipt answers yet (`--out FILE` keeps them as a list), `shard JOBS.json --weights thinkpad=3,hp=2 [--by reservation\|seconds]` writes one ids file per machine for `vl check --ids`, balanced by expected cost; read from the trusted commit alone; the format is in docs/SPEC.md |
| `vl queue run\|status` | the machine's job queue: `run --mem SIZE -- CMD` runs any heavy job (an experiment, a training) once admitted, with `SIZE` reserved for admission. A usable systemd user manager also applies the hard cap in `vl.slice`. The command keeps its output and exit code; `status` shows running jobs, reservations, MemAvailable, the floor and how many wait, and with `--explain` each limit and whether a flag, `machine.toml` or the default set it |
| `vl attempts check\|table [DIR]` | the attempt files of a mathematics repository (`schema: vl.attempt/2`, [the convention](PURPOSE-AND-ATTEMPTS.md)), without a VerifyLab project: `check` validates each file and the order of forecast and outcome in Git (exit 0 clean, 1 with one line per problem); `table` is forecast against outcome per forecaster and dimension, with `p_accepted` also by band of the forecast and by purpose (`--lanes`: also per lane of a forecaster; `--measured`: every measured change under its base); `--no-git` skips the order check; `--json` prints `vl.attempts.check/1` or `vl.attempts.table/1` |
| `vl docs [NAME]` | print a document of VerifyLab's `docs/` folder (`vl docs CHEATS`, `vl docs docs/CHEATS.md`), or list them: the installed `verifylab` skill is a copy of its own folder and names these documents as `docs/NAME.md`; they ship with the wheel |
| `vl import comparator DIR` | preview or import challenges into research items. See [audit a Comparator corpus](#audit-a-comparator-corpus). |

## Status labels

Derived on every read, from receipts and reviews committed on the trusted ref; never stored.

| Status | Meaning |
|---|---|
| `verified` | an admitted protected pass whose inputs, question and answer included, are unchanged in the worktree and on the trusted ref; no newer admitted fail |
| `vacuous` | such a pass, but the statement probes of an admitted pass of the same inputs derive `False` from a target theorem's hypotheses |
| `verified-stale` | admitted protected passes exist, and an input of each has changed since |
| `pending-admission` | a protected pass whose receipt is not committed on the trusted ref yet |
| `explored` | exploratory passes only; they never verify |
| `failed` | no pass and the newest check failed, or an admitted fail of the same inputs, newer or of the same instant, overrides a pass |
| `check-error` | no pass and the newest check was inconclusive: a tool, a timeout, a malformed item |
| `check-unsupported` | no pass and the newest check could not run: a missing tool (including nanoda on new protected Lean checks), toolchain or jail, or an item missing from the trusted ref. A current protected single-kernel pass also counts as this |
| `unverified` | a `formal` or `computation` result with no valid receipt |
| `recorded-numeric`, `recorded-empirical`, `recorded-literature`, `recorded-prose` | a result of a claim kind `vl` does not check |
| `open` | a conjecture, or a question with no verified answer |
| `answered` | a question that a verified item `answers`; for a question with a Lean target, with the same target and theorems, and with a Python evaluator, the same evaluator and entry |
| `refuted` | a result or conjecture that a verified item `refutes` |
| `retracted` | an admitted retraction review |
| `source`, `intuition`, `explanation` | items without a truth status: the label is their kind |
| `undetermined` | `refutes` relations form a cycle |

The card leads with the verdict ladder (ADR 0013): five separate rows, Lean kernel, second kernel, axioms and trusted
base, statement fidelity and paper correspondence, each `not assessed` until a record assessed it. Machine labels stay
stable in `--json` (`status`, `status.label`, `counts`), filters, and the catalog's data; human text (`vl show`, `vl find`,
`vl status`, catalog HTML, shared `AGENTS.md`, and skills) maps `verified` to `check passed` and `verified-stale` to
`check passed, stale` (never showing `verified` as the verdict), while all other status labels keep the same name in
both JSON and text.

Next to the status, the card shows the meaning of the target as a separate fact: the fidelity review (docs/SPEC.md).
`answers`, `refutes` and `uses` are admitted assertions about relationships, not proofs of implication or negation.
For a checker-bound question, `answered` also requires the same target/theorems or evaluator/entry; a prose-only
question has no such mechanical contract. A `refuted` label does not itself certify a proof of the negation.
A fidelity review binds what it read: the target and the definitions it imports, the theorems, the stable Lean
semantic environment, and the item's
statement, limits and assumptions; editing any of them makes it stale. A Lean review recorded before the environment
was bound takes the environment of the commit that added it (docs/adr/0004-older-review-environment.md). The title
and body explain and bind nothing.
Cards, briefs and `--json` show the statement, limits and assumptions as committed on the trusted ref, the text the
status and fidelity hold for; a worktree that says otherwise is shown apart, labelled "uncommitted edit, not
reviewed", and `vl validate` warns when the item is verified.

## Roadmap

**The deep check is planned** (there is no `--deep` flag): a clean rebuild without shared caches,
mutation of the conclusion, and a toolchain and kernel version floor. Replaying one receipt from a clean clone is
`vl replay` (above).

Further coordination commands, shared build-cache improvements and imports from external research platforms
remain future work. The current CLI is the command table above. Agents can already keep reduction and obligation
records, maintain their research plan and review intermediate targets with the existing skills and commands.

## Research philosophy

Research must remain useful across people, agents and lost context. Keep the problem separate from the proposed
solution, and preserve a small readable account of each useful result: what it says, its assumptions, sources,
limits and why it may help. Link the full proof, experiment or failed attempt so the next researcher can examine it.
An optional `vl init --context --write-agents` scaffold separates the problem, research map and speculative ideas;
existing project homes can be named in `[context]` of `research/vl.toml`.

Agents choose their own context and method. They can study known work, prove or refute a claim, split a goal,
run an experiment, or explore a connection to another science. Rich context, a narrow question and a fresh start
serve different purposes. The skills encourage serious alternatives and attempts to disprove an idea, while leaving
how and when to do that to the researcher. A contrary hypothesis or an unexpected experiment can itself open the
route to a solution. An early intuition need not already have a test.

Checks, meaning reviews and scientific judgments answer different questions. A valid known result can unlock the
current problem; a new result can have little use. Novelty may belong to a connection between familiar ideas.
Record what appears new, compared with which sources, and what remains uncertain. A lack of search hits establishes
no global novelty claim. Corrections and useful failures belong beside successes.

Use human attention and agent time, tokens and money where they can most help the research objective. Human
attention is especially scarce; agent work can scale. Compact context and inexpensive judgments should help both
choose where to investigate, compare, check or explain next, while preserving autonomous exploration. Consider
the cost of preparing context and following a misleading priority as well as the cost of scoring. Skills support
a critical scientific dialogue with people; human review of every result is not required. No model, context recipe
or research sequence is built into the toolkit.

### Contextual judgments

An optional `appraisal` review records a few project-defined ratings, a concise attributed judgment when supplied,
and the basis the contributor actually used. The defaults are novelty and usefulness for a named goal. Cards,
compact context and exports keep contributions and unresolved disagreement reachable; no rating changes a proof
status, fidelity verdict or admission.

An agent can ask a clear question using a lemma, selected related results and a compact research map, then obtain
a few ratings from an authorized external decision model. The agent chooses the context, criteria and whether this
is useful. A model may contribute only a score or category; agents can investigate sources and explain the result.
Preserve what each contributor actually produced and the context it used. A classifier's confidence is not proof
of novelty, and a later explanation belongs to its own author. VerifyLab does not call or train models.
See the [context and appraisal examples](#context-and-appraisals) for the local file/CLI workflow.

## A result card

An abridged `vl show` card (lines cut, long ones wrapped) for a Comparator challenge imported from a public corpus:

```text
triangle-removal@0468cacf4107 — TriangleRemoval (Comparator challenge) [result/formal; status: check passed; ...]
LIMITS
  - The target is the source repository's Comparator challenge, unchanged; the check proves the theorem
    against the challenge's own definitions, and whether they express the source paper's statement is not
    established by it.
VERDICT LADDER (five separate facts; none implies another)
  Lean kernel: passed [22e6795b290cad60.json]
  second kernel: passed [22e6795b290cad60.json]
  axioms and trusted base: passed (axioms within propext, Quot.sound, Classical.choice) [22e6795b290cad60.json]
  statement fidelity: faithful (by agent:haiku-5.5) [a4531a661fa7fc4f.json]
  paper correspondence: not assessed
PROOF (kernels, axioms, statement probes)
  protected pass (22e6795b290cad60.json, lean-comparator) · kernels lean, nanoda · axioms within propext,
    Quot.sound, Classical.choice
  - OAI.SharpTerminalLeave.sharp_terminal_leave: not closed by the battery; premise-vacuity skipped: no Prop
    hypothesis; 0 Prop hypotheses
    totalized: `√2` (√x = 0 for x < 0) in definition OAI.SharpTerminalLeave.sharpConstant
```

The first three rows come from the receipt of a protected check. Statement fidelity comes from a review, here
written by an AI model; paper correspondence was never assessed, and the card says so. The statement probes list
what a reviewer must weigh: whether automation alone closes the statement or refutes its hypotheses, and where Lean's
total conventions, such as `√x = 0` for negative `x` or `x / 0 = 0`, enter it.

## Audit a Comparator corpus

In a git clone of a corpus of Comparator challenges (`NAME.json` and `NAME.lean`, as in OpenAI's omath), with its
Lake project built and the verifier tools installed:

```sh
vl init                                    # creates research/; the current branch becomes the trusted branch
git add -A && git commit -qm "Set up vl"
vl import comparator ComparatorChallenges  # a dry run: what would be imported, and what cannot be
vl import comparator ComparatorChallenges --write --source-url URL --source-commit COMMIT
git add -A && git commit -qm "Import the challenges"
vl check --all -j 2                        # one protected check per item, through the machine's job queue
vl admit                                   # commits exactly the new receipts and reviews
vl export catalog site                     # site/index.html and site/catalog.json, one row per item
```

Statement fidelity stays `not assessed` until a reviewer records it with `vl review ID --kind fidelity`; the
traps part of the `verifylab` skill runs such a review with a clean context. See [several machines](#several-machines) and [replaying a receipt](#replaying-a-receipt-from-a-clean-clone) for those workflows.

## License

VerifyLab is licensed under [Apache-2.0](../LICENSE). Comparator's known-answer projects under
`fixtures/comparator-known/` retain their upstream [LICENSE](../fixtures/comparator-known/LICENSE) and
[NOTICE](../fixtures/comparator-known/NOTICE). The Castor name, marks and signature artwork have separate
terms in [NOTICE](../NOTICE).
