# Purpose and attempts — a text convention for shared formal mathematics (draft v0)

Draft v0: the format may still change. An example is in `examples/toy-attempts/`.

Start a forecast with
`vl attempts new attempts/example.md --purpose improve --claim "Keep the theorem with a shorter proof" --by agent:reader`.
The scaffold resolves its base to a Git commit. Fill in the forecast and commit it before doing the work.
`vl trial LOCK --attempt FILE` can later write a sidecar with build measurements to cite under `outcome.evidence`.

## What it is for
Whoever works on a formal development — a person with agents, a group, a company, an agent that runs many
others — has purposes, values and resources of their own. This convention makes three things readable by the
next person or agent, in the repository itself:
1. what the work is for (the purpose, in the owner's words);
2. what was predicted before each attempt, on every dimension that matters, and what happened;
3. who gave each number or judgment, so that a later and costlier judgment (a build, a literature search, a
   mathematician) can be put next to an earlier and cheaper one.

The point of 2 and 3 is calibration: how good are we, and each model, at predicting before the expensive check.
A wrong forecast is a result worth sharing.

No command is needed to write these files: they are plain text in Git, in the repository of the mathematics, and
nothing has to pass through VerifyLab. Commit the forecast before starting work and commit the outcome later.
The diary can check the order of these records in Git, and a forecast is never edited afterwards (a correction is a new entry).

## 1. `PURPOSE.md` — one per mathematics repository, at its root
Short, in this order:
- **Who.** The entity doing the work and who decides its purposes. When that person has delegated the choices
  (which candidates, in which order, when to stop), record the source and proceed. A one-turn brief can
  supply these choices. Read back the purpose or show candidates when the person's agreement is still needed.
- **Purposes**, in priority order. For each: a name (`understand`, `shorten`, `reuse`, `new-route`, or the
  owner's own), one paragraph in the owner's words, and what "better" means under it.
- **Sources.** Papers, books, other formalisations, with licences where code may be reused.
- **What must stay true** (the contract, the part a machine checks): the statements, the allowed axioms, the
  toolchain, downstream projects that must keep building.
- **Values and costs that are tracked** (section 3), and for each who can give it.
- **Resources.** Machines (cores, memory, disk), model subscriptions and credits, human hours, money ceiling.
- **Who judges** what a machine cannot (for `understand`: a mathematician of that field; until one has looked,
  say so).

## 2. `attempts/<yyyy-mm-dd>-<slug>.md` — one per attempt, successful or not
Front matter (YAML), then prose.

```yaml
---
schema: vl.attempt/2
id: 2026-11-22-direct-implication
purpose: understand                 # a name from PURPOSE.md
base: <commit, or sha256 of the source archive>
claim: one sentence, what is replaced by what and why it should be better under the purpose
forecasts:                          # one entry per forecaster, written BEFORE the work
  - by: proof-reader/high         # model/effort, or person:<name>: the same text in every file
    lane: direct-proof           # optional: the line of work or the session of that forecaster
    at: 2026-11-22T22:10+01:00
    p_accepted: 0.35                # the contract holds and the change is kept
    value:                          # change against base; 80% intervals
      reached_lines: [-800, -200]
    cost:
      agent_hours: [2, 8]
      probe_minutes: [1, 4]         # one focused invocation: one file elaborated
      build_minutes: [13, 16]       # the full build: a cold build of the roots
      peak_ram_gb: [8, 14]          # of the full build
      usd: [0, 0]                   # subscriptions count as 0; name them in `basis`
    basis: why these numbers
outcome:                            # added AFTER the work, in a later commit; an open attempt has no `outcome`
  status: accepted                  # accepted | rejected | abandoned | unverified | not-run
  by: proof-reader/high           # optional: who measured
  at: 2026-11-22T23:59+01:00        # optional
  commit: <commit or archive hash>
  value: {reached_lines: -252, physical_lines: -239}      # measured by the run, as changes against `base`
  cost: {agent_hours: 3.1, probe_minutes: 2.2, build_minutes: 13.9, peak_ram_gb: 11.2, usd: 0}
  contract: {statements: same, axioms: [propext, Classical.choice, Quot.sound], comparator: accepted}
  evidence: path to the build and Comparator logs
scores:                             # optional; judgments; each has an author; a later one never replaces an earlier one
  - what: readability
    value: 5
    reader: SP
    task: map each lemma to the paper
    by: proof-reader/high
    at: 2026-11-22T23:59+01:00
    basis: which fact holds, and which fact made level 7 fail
  - what: novelty
    value: 7
    forecast: true                  # an agent's 7 or 9 predicts what a person of the field would say
    reader: SP
    task: say whether the argument is in the literature
    by: proof-reader/high
    at: 2026-11-22T23:59+01:00
    basis: the search done and the three nearest published results
  - what: novelty
    value: 3
    reader: SP
    task: say whether the argument is in the literature
    by: person:<name>
    at: 2026-11-25T10:00+01:00
    basis: "it is Lemma 3.2 of <citation>"
  - what: explanatory
    text: "explains why X: because Y"
    by: person:<name>
    at: 2026-11-25T10:00+01:00
---
```

Prose, three short parts, each pointing to the exact place in the source (file and declaration). A note without
that pointer has sent an agent after an old diagnosis to the wrong lemma.
- **What was tried.**
- **Decisive evidence.** The build error, the counterexample, the measurement.
- **What would justify trying again.**

Rules:
- Required: `schema`, `id`, `purpose`, `base`, `claim` and `forecasts`. An attempt without `outcome` is open.
  No other top-level key exists, and a forecast, an outcome or a score has only the keys shown above (a forecast
  may add `unconditional`, a score `lane` and an outcome `kept`, below). The names of the dimensions under
  `value` and `cost` are open: add your own.
- An open attempt has no `outcome` key at all: its front matter ends after the forecasts, as in the two files of
  `examples/toy-attempts/`. Nothing holds the place of an outcome. `outcome: {status: not-run}` on a
  candidate that is still live counts it as closed, and `0 open` then hides it; `status: unverified` committed
  with the forecast is an outcome, and the forecast is reported as late. `not-run` is for a candidate that was
  priced and is deliberately not tried.
- A forecast has `by`, `at` and a non-empty `basis`; `p_accepted` is between 0 and 1; each dimension under
  `value` and `cost` is an interval `[low, high]`.
- `by` is the forecaster: the model and its effort, or `person:<name>`, written the same way in every file. The
  table has one row per `by`, so a model that signs with the name of each session or line of work is split into
  as many forecasters. The line of work goes in `lane`, optional on a forecast
  and on a score: `lane: going-up`. A file that is already committed keeps the `by` it has: a forecast is never
  edited.
- A past attempt with no forecast written at the time gets `forecasts: []`. Forecasts are never reconstructed.
- `outcome.status` says how the attempt ended:
  - `accepted`: the author records that the checked contract holds and the change is kept. Give a non-empty
    `contract.statements` reference or list of references and an explicit list of axiom names under `contract.axioms`.
    Use `[]` for a result that uses no axioms, and record permitted axioms separately in the check evidence.
    Missing or malformed fields are a problem when a forecast would resolve and a note with `forecasts: []`.
    The tool validates the fields. A Lean or Comparator run supplies the proof evidence.
  - `rejected`: the check was run and the change fails it, or it is not better under the purpose.
  - `abandoned`: stopped at the ceiling or given up; the change is not kept (a part that stays all the same
    goes under `kept`, below).
  - `unverified`: the change is kept and the contract still needs checking. For a source edit kept without
    Lean, write `outcome: {status: unverified, value: {edited_file_lines: -4}, cost: {build_minutes: null}}`
    if Git shows that an edited file shrank by 4 lines and you measured only that file. Reserve null for unmeasured fields.
    A null outcome or status is a validation error.
  - `not-run`: priced and not tried.
- `not-run` candidates keep their forecast: an option that was priced and not chosen is data too. Its `not-run`
  may be written together with the forecast, in one commit: it resolves nothing, and the order check does not
  read it as an outcome.
- `outcome.kept` is for an attempt that ended `abandoned` or `rejected` and still left something in the
  repository: a lemma that compiles and has a user, a part of the cut. It holds the measured change of what
  stays, with the dimensions of `value`: `kept: {reached_lines: -381, reached_declarations: -37}`. `status` is
  about the claim that was forecast, and the forecasts resolve on it: `kept` resolves nothing, and the table
  reports it apart. On any other status `kept` is refused: what an accepted or an unverified attempt keeps is
  its `value`.
- Every number under the `value` of a forecast, under `outcome.value` and under `outcome.kept` is a change
  against the `base` of its file. Values with different bases are not additive: two patches saved 1,180 lines
  against their parent and 1,796 together against the original base. Only a chain adds up, where the `base` of
  one attempt is the `commit` of the one before, and its sum is the change against the first base. Two changes
  against the same base add up only when they share nothing of what they remove.
- `outcome.commit` names the source revision measured, such as a patch commit made before adding the outcome
  entry, or the SHA-256 of the exact tested source archive. Leave it null when the tested source has neither
  identifier. The diary commit records the observation separately.
- `outcome` may name who measured in `by`. Each `at` is the time the forecast or judgment was made or the
  outcome measured, roughly if needed. An outcome's `at` is optional. Git establishes the order.
- Value intervals are conditional on acceptance (what changes if the change is kept); `p_accepted` carries the
  risk. Cost intervals are unconditional. Two forecasters who price the same candidates, one conditional on
  acceptance and one "including zero if rejected", cannot be compared directly: a forecaster who priced value
  including zero if rejected writes `unconditional: true` in the entry, and a rejected or abandoned attempt then
  resolves its value intervals with 0.
- A number that was measured says so by living under `outcome`; an estimate lives under `forecasts`; a judgment
  lives under `scores`. Nothing else carries numbers. A dimension the run did not measure is `null`.
- Scores are optional: write none rather than invent one. A level whose sentence cannot be tested here (no
  paper, no reader to ask) is not a score; say in the prose that none was given.
- A score has `what`, `by` and `at`. A person signs as `person:<name>`; anything else is read as an agent. The
  rules for its value, `reader`, `task`, `forecast` and `text` are in section 3.

The YAML that is read is what people and the usual dumpers write, and anything else is refused with its line.
Read: mappings and lists by indentation (a list may sit at the indentation of its key, and lists may nest), lists
and mappings on one line (`[a, b]`, `{k: v}`, nested), numbers, `null`, `#` comments, and text in every common
form: plain, 'single-quoted' and "double-quoted" (with YAML's escapes), each of which may run over several lines
indented under its key, and the block forms `>`, `>-`, `|`, `|-`. Refused: anchors and aliases, tags, a second
document, a list or mapping in brackets that does not close on its line, tabs in indentation, and keys that are
not plain names. Put in quotes a text that contains `: ` or ` #`, and a commit made of digits only. Write
`forecasts` as a block list, one `- by:` item per forecaster: the order check compares each item as written.

## 3. Dimensions
Costs: `agent_hours`, `human_hours`, `disk_gb`, `usd`, and two pairs with different denominators, never written
one for the other:
- `build_minutes` and `peak_ram_gb`: the full build, that is a cold build of the roots of the contract;
- `probe_minutes` and `probe_peak_ram_gb`: one focused invocation, such as the elaboration of one file or one
  test; when several were run, the largest of each.

Use the build host's elapsed time and peak resident memory, for example from GNU `time -v` if installed.
A dedicated build cgroup's `memory.peak` can measure peak memory for the whole run. Name the source
in `evidence` and leave the value null if it is unavailable.

For a shared build, keep one log with the source revision, participating attempts, named targets and total
measured time and peak memory. Each attempt can cite that log in `outcome.evidence`. For example, if two
proof splits share a 12-minute build, record 12 minutes in the shared log and leave each attempt's
`build_minutes` null when its share was not measured. Use the same log to link the shared check and cite
any independent evidence for the individual claims. Use `vl attempts table --measured` to see recorded
partial work under its base. If an integrator later checks the kept change, record that actual check with
the outcome and identify which claims it covers.

An attempt that ran only probes leaves `build_minutes` and `peak_ram_gb` `null`. The table has one row per
dimension name and pools none: a probe written as `build_minutes` would be read against the forecast of a build.

Values that are measured (a machine resolves them): `reached_lines`, `physical_lines`, `reached_declarations`,
`named_steps` (the change in the count of declarations on the main path), `build_minutes` of the result, and
`known_share` (reached lines in blocks with an exactly cited published counterpart, divided by all reached lines; a citation copied from the
library without opening the source counts apart, as `known_share_cited`). Report `reached_lines` together with
`named_steps`: fewer lines with fewer named steps is compression, fewer lines with the same or more named steps
is structural. Under the purpose `understand` only the structural kind is an improvement. Report `reached_lines`
(the lines of what the statements reach) and `physical_lines` (the lines of the files they import, reached or
not) apart. A split can save 35 reached lines and add 91 physical lines. If counting only newly extracted
steps, use a separate dimension such as `new_named_steps` consistently in forecast and outcome.

Values that are judged (`scores`). Rules for every score:
1. **Five anchored levels: 1, 3, 5, 7, 9.** Each is a sentence a second judge can check. The judge takes the
   highest level whose sentence is fully true and writes in `basis` which fact made the next one fail. The
   `value` is a whole number from 1 to 10; an even value means "between two anchors", and it is accepted and
   reported as a note. For readability, a reader who can locate the named steps and follow the main path
   through previously unbroken tactic blocks may score 4 when the cited map required for 5 is incomplete.
   Name the steps the reader followed and the missing citations in `basis`.
2. **A score names its `reader` and its `task`**, and has a non-empty `basis`. Reader: a generalist in the field, a specialist in the topic, a Lean user
   outside the field, or a named person. Task: what the reader is trying to do (check a step, rebuild the proof
   from a summary, say where each hypothesis is used, adapt it to a variant, teach it).
3. **A level 7 or more given by an agent is a forecast** of what a person of the field would say: the entry
   carries `forecast: true`, and without it the entry is refused. An agent may mark a lower level as a forecast
   too. A person's entry (`by: person:<name>`) on the same `what`, reader and task, written further down the
   list, resolves it. The earlier entry is never replaced: the pair is the calibration datum.
4. **`elegance` and `explanatory` get no number.** They are a sentence (`text`) signed by a person; for
   `explanatory` in the form "explains why X: because Y". An agent may propose one with `forecast: true`. A
   `value` on them is refused.

Anchors.
- `novelty` (of the statement and of the argument, scored apart when they differ): **1** a citation is given for
  the same statement and argument, in a textbook or in Mathlib · **3** a citation for the same argument, which the
  Lean follows step by step · **5** a citation for the idea; the formal argument differs in a nameable step (a
  different auxiliary object, order of induction, route through the library) · **7** no counterpart found; the
  search is recorded (queries, sources, date, the three nearest published results and how each differs) · **9** a
  person of the field writes that it is new and names what it is new against.
- `readability` (local: can the reader follow each step?): **1** to know what a step does the reader must run
  Lean and look at goals · **3** the reader can locate the main steps by name and tell what each establishes ·
  **5** also: with the paper open, the reader can reconstruct the main argument. Docstrings cite the corresponding
  passages and explain any additional formalization steps and why the proof needs them · **7** also: the reader
  can write a correct proof sketch from statements and docstrings alone (hide the paper, ask for the sketch,
  compare) · **9** also: a blueprint or equivalent graph links the main steps and their dependencies to their declarations.
- `clarity` (global: what is assumed, what is proved, how the parts depend on each other): **1** the main statement cannot be read without opening proof files · **3** a statement file exists
  but imports proof machinery or holds more than the statement needs · **5** a statement file with definitions and
  the main statement only, checked by Comparator or equivalent, no unused hypotheses · **7** also: the import
  graph is layered by mathematical object and every module docstring lists its main results · **9** also: for each
  hypothesis of the main theorem a reader or agent finds, in under five minutes, the lemma where it is first used
  (tested, not assumed).
- `generality`: **1** specialised to the instance this proof needs · **3** parameters are abstracted but the
  statement is used only inside this proof · **5** used at least twice in the development, in different contexts
  (the machine counts uses) · **7** hypotheses are minimal (each was dropped in turn, the build broke, the attempt
  is recorded) and the statement is in the general library's vocabulary · **9** it contains a published theorem as
  an instance under weaker hypotheses, or a reviewer of the general library accepts it as is (a person, or a
  merged pull request).

## 4. `KNOWN-MAP.md` — optional, for the purpose `understand`
A table of the proof by blocks: what each block proves, its size, and three separate judgments, each with its
author: whether it is general-purpose mathematics that belongs in a library; whether it has a published
counterpart (exact citation, or "not found" with the search); to whom it is familiar (a generalist in the relevant field,
specialists of the topic, nobody because it is a device of the formalisation). These are three properties, not
one: a block can be library-like and unpublished, or published and unfamiliar.

## 5. What the tool does, and what it does not
`vl attempts` does what should not rest on an agent's care. It reads a folder of attempt files in any
repository; it needs no `vl.toml`, no trusted ref and no `vl init`. Every `*.md` directly in the folder that
starts with a `---` line is an attempt file, except `README.md`. A Markdown file that does not start with one (a
`BASELINE.md` kept next to the attempts) is not an attempt file: `check` and `table` skip it, print one
`file: note: ...` line that names it, and count it nowhere. A file that starts with `---` and is malformed is a
problem.

- `vl attempts check [DIR]` validates each file against the rules of sections 2 and 3 and prints one line per
  problem (`file: message`); it exits 0 when clean and 1 otherwise. A score between two anchors, and an
  `accepted` without a checked contract on an attempt with `forecasts: []`, are printed as `file: note: ...` and
  change nothing else. Inside a Git repository it compares the current forecast entries with the last committed
  version of the file before the first outcome, matching each occurrence at most once. Extra copies count as
  late. Altered or removed entries are edits. Identical forecasts in the same attempt resolve once, even
  when Git chronology is disabled.
  This witnesses order relative to the recorded outcome. A source edit can predate the forecast commit. Use
  source history or a separate timestamp when you need evidence that the forecast preceded the work.
  `not-run` is not an outcome here: a file that ends as
  `not-run` is not checked, and a version that says `not-run` counts as one without an outcome. The working copy
  counts as the newest version, so the check can run before the outcome is committed. Outside Git, or with
  `--no-git`, it says that the order was not checked.
- `vl attempts check FILE` runs the same checks on one Markdown attempt. The summary names the selected file,
  and JSON includes its path in `scope.file`. Use the directory form for the complete audit.
- `vl attempts table [DIR]` groups resolved forecasts by forecaster and dimension. Intervals report the count
  resolved, the count inside the interval and the median error against its midpoint. Acceptance predictions
  report resolved count, mean prediction, accepted share and Brier score, also by band of the forecast
  (five bands of 0.2, including each lower edge and the upper edge of the last band) and by purpose.
- `p_accepted` resolves on `accepted`, `rejected` and `abandoned`. Value intervals resolve on `accepted` or,
  with `unconditional: true`, as 0 on `rejected` and `abandoned`. Costs resolve whenever measured on a run.
  An `unverified` attempt resolves only measured costs. `not-run` resolves nothing. See the outcome rules above.
- The table lists excluded forecasts and skipped files. `--lanes` expands the rows per forecaster and lane.
  `--measured` shows each measured change with its base, including retained parts under `outcome.kept`.
  Values with different bases are not additive. Agent scores are paired with later human scores on the same
  measure, reader and task.
- JSON `vl.attempts.table/1` always includes lane rows (`intervals_by_lane`, `p_accepted_by_lane`), acceptance
  groups (`p_accepted_by_band`, `p_accepted_by_purpose`) and `measured` entries. Each measured entry names the
  `file`, `status`, `base`, `kind` (`value` or `kept`) and `dimension`.

What it does not do. It does not measure the time or the memory of a run: the numbers under `outcome` are
written by whoever ran it. It does not bind an outcome to a revision: `base` and `commit` are text it does not
resolve. It does not judge a score, its basis or whether its anchor sentence is true. The convention works
without the tool.

`vl trial` writes a different record, JSON with `schema: vl.attempt/1`, at a pinned commit: one patch trial
run by the tool. A linked trial requires the complete open attempt at HEAD, including a real forecast basis.
It records that commit and a SHA-256 of the UTF-8 forecast entries after right-trimming each item and joining
with line feeds. The diary commands compare that fingerprint with the sibling trial record. Keep the sidecar
with the diary entry. It is local run evidence, with the same trust as the archive holding it.
If shallow Git history hides evidence of an earlier forecast, the diary reports partial chronology and leaves
the uncertain entries out of calibration. Deepen the clone to assess them.
An intentional squash can hide that ordering even in a full clone. For such archives, use
`vl attempts check attempts --no-git --json` to validate the records with `git_order` set to `not-checked`.
To establish chronology, match the archived attempt files, including outcomes, with the exact files in the
original checkout. Run the audit there and record its revision and diagnostics.

## Using it

From the VerifyLab checkout, [install `vl` on PATH](INSTALL.md#install-the-command) or run
`uv run vl attempts check examples/toy-attempts/attempts` on its bundled example.
From your mathematics repository, run:

```sh
vl attempts check
vl attempts table
```

With a checkout and no tool install, run `uv run --project /absolute/path/to/verifylab vl attempts check`
from the mathematics repository. The first `uv run` may need the network for build dependencies.
