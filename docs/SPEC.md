# The `research/` format

Format of the records `vl` reads and writes: receipt schema `vl.receipt/1`, review schema `vl.review/1`. Everything
is a plain file in Git and every status is a function of those files, so a project that follows this document is
usable, and checkable by hand, without `vl`.

## Layout and admission

```
research/vl.toml                          project configuration (templates/vl.toml lists every key)
research/items/<id>.md                    items
research/targets/*.lean                   trusted Lean targets
research/evaluators/*.py                  trusted Python evaluators
research/evidence/<id>/<16 hex>.json      receipts of protected checks
research/reviews/<id>/<16 hex>.json       reviews
.vl-cache/explore/<id>/<16 hex>.json      receipts of exploratory checks (gitignored, never admitted)
.vl-cache/index.sqlite                    search index of `vl find` (a cache, safe to delete)
```

`[project] research_dir` renames `research/` for everything except `research/vl.toml`. The **trusted ref** is the
branch named by the repository-local `git config vl.trustedRef` (`main` when unset), resolved to one commit per
command. A receipt or review is **admitted** when it is committed there: admitted records are listed from that
commit's tree and read from it, so a worktree that lacks, renames or alters one cannot hide it. A record only the
worktree holds is a proposal (shown, never counted); a worktree copy whose bytes differ from the admitted record at
the same path is rejected. Nothing under `.vl-cache/` is ever admitted. A read of the trusted commit that fails is
an error (exit 2), never an absent file. Protected checks read `research/vl.toml`, targets, their in-project
definitions, evaluators and the trusted question fields of items (below) from the trusted ref.

## Items

`research/items/<id>.md`, UTF-8: a `+++` line, TOML front matter, a `+++` line, then a free Markdown body. An
unknown front-matter key is an error. The **revision** of an item is the git blob sha of its file (`git hash-object`);
`id@rev` names a revision by a prefix of 4 to 40 hex characters.

| Key | Type | Meaning |
|---|---|---|
| `id` | string matching `^[a-z0-9][a-z0-9-]{1,63}$` | required; equals the file name without `.md` |
| `kind` | `question`, `result`, `conjecture`, `source`, `intuition`, `explanation` | required |
| `title` | non-empty string | required |
| `author` | `human:NAME` or `agent:NAME`, NAME of 1 to 64 of `A-Za-z0-9._@+-` | required |
| `created` | string or TOML date | required |
| `recorded_by` | as `author` | who wrote the item for its author |
| `statement` | string | required for `question`, `result`, `conjecture`, `intuition` |
| `claim` | `formal`, `computation`, `numeric`, `empirical`, `literature`, `prose` | required for `result`; only `formal` and `computation` are checked |
| `ref` | string | required for `source`: DOI, arXiv id, URL, path, declaration |
| `access` | `full-text-read`, `abstract-only`, `citation-only`, `secondary` | required for `source` |
| `assumptions`, `limits`, `tags` | lists of non-empty strings | limits and assumptions come first on the card |
| `uses`, `cites`, `answers`, `refutes`, `supersedes` | lists of `id` or `id@rev` | relations, below |
| `[lean]` or `[python]` | table | the checker binding; `vl check` needs exactly one |

An `explanation` needs a non-empty body. The body may link items as `[[id]]` or `[[id@rev]]`.
Relations: `refutes` makes a result or conjecture `refuted` once the refuting item is `verified`; `answers` makes a
question `answered` once the answering item is `verified` and, when the question has a `[lean]` target, states the
same `target` and the same `theorems` (in any order), and when it has a `[python]` evaluator, the same `evaluator`
and the same `entry` (`vl validate` reports an `answers` that does not as an error); `uses` is a dependency (`vl show --impact` follows it back to the items that use one); `cites` and `uses`
of an explanation must not point to a refuted or retracted item; `supersedes` is written on the successor and names
the earlier item it replaces; it never changes a
status. Only relations stated by items as committed on the trusted ref count; one only the
worktree states is a proposal, shown on the card. A relation to `id@rev` applies only while the trusted item `id`
is at that revision.

## `[lean]`

| Key | Type | Meaning |
|---|---|---|
| `target` | string | normalized path of a `.lean` file under `research/targets/` |
| `theorems` | non-empty list of distinct dotted names | the target theorems the answer must prove |
| `witnesses` | list of names, each also in `theorems` | target theorems that instantiate the hypotheses of the others |
| `proofs` | table: theorem name to a non-empty Lean term | one term per theorem, in scope of the theorem's binders |
| `imports` | list of module names, only with `proofs` | modules the generated solution imports |
| `solution` | module name under `[lean] roots` | instead of `proofs` and `imports`: a module declaring the target theorems |

Exactly one of `proofs` and `solution`. Module names under the reserved prefixes `VLTrusted`, `VLChallenge` and
`VLSolution` are refused. Target file: a Lean 4 header (`module`, `prelude`, then `import` lines, with `public`,
`meta`, `all`; no `import` after it), commands in column 0 with indented continuation lines, `namespace X` closed
by `end X`, and each listed theorem declared exactly once by `theorem` or `lemma`, not `private`, with body exactly
`sorry` or `by sorry`. Escaped names (`«…»`) are not supported. Imports under `[lean] roots` are in-project: a
protected check reads them, transitively, from the trusted ref; other imports (Init, Std, Mathlib) are used as
built. The generated solution module is the target with each `sorry` replaced by `(term)`, plus `imports`.

## `[python]`

| Key | Type | Meaning |
|---|---|---|
| `evaluator` | path of a `.py` file under `research/evaluators/` | trusted: read from the trusted ref by a protected check |
| `candidate` | path of a `.py` file outside `research/evaluators/` | the module under test |
| `entry` | Python identifier | the callable in `candidate` |
| `files` | list of paths | further candidate files, copied next to it at the same relative paths |

Paths are plain and repository-relative (no `..`, no absolute path, no symbolic link on the way); a candidate
file is at most 8 MiB. The **evaluator** defines `CASES`, a non-empty list of JSON values (no NaN or infinity), and
`judge(case, output)`, returning a bool or `(bool, message)`; optionally `TIMEOUT_S`, the seconds per case
(default 10). It must be deterministic: `CASES` is read twice and must not change. The **candidate** is imported
once, in its own jail, and `entry(case)` is called once per case; its return value must be strict JSON. The judge
runs in a second jail and never sees candidate code. Verdict: `pass` when every case passes; `error` when the judge
raised on any case, or when the candidate process was ended by a signal (the memory or task cap, the OOM killer);
otherwise `fail` when a case returned a wrong value, raised or timed out, or the candidate's process crashed. A
result counts only from a process that exited 0. The whole candidate
run is bounded by `len(CASES) × TIMEOUT_S + 5` seconds.

## The question of an item

The **question fields** are `[lean]` `target`, `theorems`, `witnesses`, `proofs`, `imports`, `solution` and
`[python]` `evaluator`, `candidate`, `entry`, `files`. A protected check takes `target`, `theorems`, `witnesses`
and `evaluator` from the item on the trusted ref, the rest from the worktree.
`question_digest` = `"sha256:"` + hex sha256 of the UTF-8 of `json.dumps({"lean": {...}, "python": {...}},
sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)`, each table holding only the question
fields present.

## Receipts

A JSON object written once, by `vl check`, as `<receipt_id[:16]>.json` (indented, sorted keys).

| Field | Meaning |
|---|---|
| `schema` | `"vl.receipt/1"` |
| `receipt_id` | hex sha256 of the receipt without this field, as canonical JSON (sorted keys, separators `,` `:`, UTF-8) |
| `item`, `item_revision` | the item and the blob sha of its worktree file when checked |
| `question_digest` | digest of the question fields the check answered |
| `adapter` | `lean-comparator` or `python-eval` |
| `assurance` | `protected` or `exploratory` |
| `verdict` | `pass`, `fail`, `error` (inconclusive) or `unsupported` (could not run) |
| `reasons` | list of strings |
| `inputs.files` | path to hex sha256 of every candidate-side input, as read from the worktree; never empty |
| `inputs.trusted_files` | path to digest of every trusted input, as read from `inputs.trusted_commit` |
| `inputs.digest` | `"sha256:"` + hex sha256 of canonical JSON of `{files, trusted_files, target}` |
| `inputs.trusted_ref`, `trusted_ref_source`, `trusted_commit` | the trusted ref, where its name came from, its commit |
| `inputs.candidate_head`, `candidate_uncommitted` | the worktree's HEAD, and the checked files it had not committed |
| `target` | what was checked against: path, sha256, theorems or evaluator, source, commit |
| `environment` | toolchain, Lean version, each tool's path, sha256, source and pin, machine file, isolation, `memory_cap`; `machine_policy`: the machine file and its sha256, the tools store, the launchers, and `overridden_by`, the XDG variables of the call that moved them (a status this receipt verifies names them); for Lean, `packages.identity`: each Lake package's checked-out revision next to the manifest's, and Lake's trace (`depHash`, sha256 of the trace file) of each dependency module the trusted side imports |
| `checked` | what the verdict covers: Lean kernels, permitted axioms, `probes`, `probe_run`, `lints`; Python cases |
| `command`, `log_sha256`, `log_tail` | the command run, the digest of its full log, the last 4000 characters |
| `started_at`, `finished_at`, `tool_version` | ISO 8601 UTC times, to the microsecond; `vl` version |
| `extra` | optional: phase timings, slowest modules, Lake jobs, peak memory, notes |

An input is a file path whose digest is the hex sha256 of its content, or a configuration input:
`research/vl.toml#verdict-rules` is the hex sha256 of `json.dumps({"lean": {"permitted_axioms": sorted unique,
"external_kernels": bool, "project": normalized path, "roots": sorted unique}}, sort_keys=True, separators=(",",
":"))` over `research/vl.toml`. Receipts of earlier versions may record `#verdict` (the same plus the `packages` and
`cache` paths) or `#check` (that, without `roots`); they keep their basis, and `#check` also requires the roots of
`inputs.trusted_commit` to equal those of the trusted ref.

A receipt is **stale** when any of these differs now: a `files` digest from the worktree file (and, for an admitted
receipt, from the file on the trusted ref: a gitignored or uncommitted file never verifies); a `trusted_files`
digest from the file on the trusted ref; `question_digest` from the item's current question fields, and, for an
admitted receipt, from those of the item as committed on the trusted ref (an item not there, or not parsing there,
makes it stale: a proof term only the worktree has never verifies); a receipt without `question_digest` is
compared through the item blob `item_revision` names, and is stale when git lacks that blob.
A receipt missing a required field, of the wrong shape, or whose `receipt_id` does not match is rejected.

Tool identities and dependency traces are recorded for replay, not compared with the current machine when deriving
staleness. A changed verifier binary or build cache therefore does not invalidate an old receipt automatically.
`candidate_head` is the HEAD at check time, not a snapshot of dirty candidate files; a later commit may preserve
the checked bytes. Replaying such a receipt requires that commit's candidate files and answer binding, checked
against the recorded digests, together with the original `trusted_commit`.

Statement probes (`checked.probes`, Lean, after a pass): per target theorem `trivial_by` (the battery tactic that
closed the theorem alone, or null), `vacuous_by` (the tactic that derived `False` from its hypotheses, or null),
`prop_hypotheses` (count; null when the probe gave no result), and optionally `limit_reached`, `vacuity_skipped`,
`error`. `checked.probe_run` holds the battery, heartbeats, timeout and `problems`, or `skipped`.

## Reviews

A JSON object written once, by `vl review`, as `research/reviews/<item>/<review_id[:16]>.json`.

| Field | Meaning |
|---|---|
| `schema`, `review_id` | `"vl.review/1"`; hex sha256 of the review without `review_id`, as for receipts |
| `item`, `item_revision` | the item and the full blob sha of the revision reviewed |
| `kind` | `fidelity`, `compare`, `correction`, `retraction` or `understanding` |
| `author`, `text`, `created` | `human:NAME` or `agent:NAME` (`human:NAME` says NAME wrote or approved the text: `vl review` writes it only with `--human-approved` or after NAME confirms at the terminal); the judgement with its reason; ISO 8601 UTC, to the microsecond (earlier versions wrote seconds) |
| `verdict` | fidelity: `faithful`, `too-weak`, `vacuous`, `wrong-definition`, `unclear`; other kinds: optional, one line, at most 64 characters |
| `compare_with` | compare only: `id@<full revision>` |
| `acknowledges` | fidelity only: `["trivial"]`, the target is meant to be closed by automation alone |
| `target_path`, `target_sha256` | fidelity only: the `[lean] target` (else the `[python] evaluator`) and the sha256 of its content on the trusted ref |
| `meaning`, `meaning_digest` | fidelity only: what the reviewer read, from the trusted ref: `files` (the target or evaluator and, for Lean, every in-project module of its import closure, path to sha256), `theorems`, `witnesses`, `statement_sha256` (the item's claim text), `limits_sha256` and `assumptions_sha256` (the hex sha256 of the canonical JSON of the item's `limits` and `assumptions` lists), and `closure_error` when the closure could not be read; `meaning_digest` is `"sha256:"` + the hex sha256 of its canonical JSON |

Effects, once admitted: a `retraction` makes the item `retracted`; a `correction` is shown first on every card; a
`fidelity` review decides the item's fidelity; `compare` and `understanding` are shown only.

A new fidelity review names the current trusted item revision, even if the worktree has proposed edits.
An explicit `ID@rev` differing from that trusted revision is refused, including a title/body-only older revision:
an item blob cannot reconstruct the historical target and definitions. Other review kinds can name old revisions.
When a saved fidelity review's item blob is available, its bound statement, limits, assumptions, target and selected
theorems are compared with its recorded meaning; a disagreement rejects the review and `vl validate` reports it.
Immutable records remain on disk: read the admitted meaning and write a new review. Older target-only bindings
retain their existing, labelled compatibility policy. A missing item blob cannot establish this mismatch and
does not by itself reject a full-meaning review.
Self-review is evaluated against the trusted item's author, so an uncommitted author edit cannot hide it.

**Fidelity**, for an item with a target or an evaluator (as committed on the trusted ref): `target not admitted`
when the file is not on the trusted ref; else, among admitted fidelity reviews whose `meaning_digest` is that of
the meaning there now (target, definitions closure, theorems, witnesses, claim text, limits, assumptions), the
newest gives `faithful` or `disputed: <verdict>` (`created` compared as a time at full precision; among reviews of
the same instant, a verdict other than `faithful` is the newer, then the path decides); `review stale: <what> changed since review` (target,
definitions, theorems, claim, limits, assumptions) when only reviews of another meaning exist; `not reviewed` when
none. The `title` and the body are explanatory text: no review binds them. Older reviews bind less, are marked on
the card with what they do not bind, and `vl validate` asks for a new one: a review whose `meaning` lacks
`limits_sha256` and `assumptions_sha256` (written before reviews bound them) counts while the rest of its meaning
is the meaning now and no admitted fidelity review of the item records them; a review without `meaning` (written before reviews recorded one) counts while its
`target_sha256` matches and no admitted fidelity review of the item is newer, whatever made that one stale. A review whose author is the item's author is marked as a self-review.

A card (and a brief, and `vl show --json`) shows the `statement`, `limits` and `assumptions` of the item as committed
on the trusted ref, the text its status and fidelity hold for (the worktree's while the item is not there). When
the shown revision says otherwise, its differing fields are shown apart (`proposed`), labelled `uncommitted edit, not
reviewed` (or, for an older revision asked with `ID@rev`, `the revision asked for, not reviewed`), and named in the
card's header line.

## Derived status

Computed on every read, in this order; only well-formed receipts and reviews count. The kind and claim that choose
the rule, and the relations of rules 2 and 5, are those of the item as committed on the trusted ref (the worktree's
while the item is not there yet).

1. An admitted `retraction` review: `retracted`.
2. A result or conjecture with an item that `refutes` it and is `verified`: `refuted`. When deriving a refuting
   item leads back to the item itself (a cycle), every item on the way: `undetermined`.
3. A `formal` or `computation` result, from its receipts:
   - the newest admitted protected pass that is not stale decides: `failed` when an admitted protected fail of the
     same item, not older and not stale, exists (a fail of the same instant counts as newer); else `vacuous` when
     the probes of any admitted protected pass that is not stale have a `vacuous_by` (a later run whose probes did
     not finish never erases it); else `verified`, noting "probes incomplete" when its probes were skipped, failed
     or lack a result, and when it ran under a machine policy the caller's environment chose;
   - else, when admitted protected passes exist, all stale: `verified-stale`;
   - else a protected pass not admitted: `pending-admission`; else an exploratory pass: `explored`;
   - else the newest receipt: `failed` for `fail`, `check-error` for `error`, `check-unsupported` for
     `unsupported`; no receipt: `unverified`.

   A protected Lean pass that only the Lean kernel replayed (`checked.kernels` without `nanoda`) while
   `[lean] external_kernels` is on counts as `unsupported`, with the reason, never as a pass.

   "Newer" compares `finished_at` as a time at full precision; at the same instant the more adverse verdict (fail,
   then error, then unsupported, then pass) is the newer, and then the path decides.
4. Any other result: `recorded-<claim>`. A conjecture: `open`.
5. A question: `answered` when an item that `answers` it is `verified` (and, for a question with a `[lean]` target,
   states the same target and theorems; with a `[python]` evaluator, the same evaluator and entry), else `open`.
6. A source, an intuition or an explanation: its kind.

| Status | Rule |
|---|---|
| `retracted` | 1 |
| `refuted` | 2 |
| `undetermined` | 2 |
| `verified`, `vacuous`, `verified-stale`, `pending-admission`, `explored` | 3 |
| `failed`, `check-error`, `check-unsupported`, `unverified` | 3 |
| `recorded-numeric`, `recorded-empirical`, `recorded-literature`, `recorded-prose` | 4 |
| `open` | 4, 5 |
| `answered` | 5 |
| `source`, `intuition`, `explanation` | 6 |

`vl validate` adds warnings that do not change a status: a protected pass whose target has no `faithful` review; a
target theorem closed by automation alone (`trivial_by`, witnesses excepted) that no fidelity review of the current
target acknowledges; a protected pass showing Prop hypotheses while `witnesses` is empty; incomplete probes; stale
admitted receipts; target theorems with Prop hypotheses (by the probes of the newest admitted receipt that has
them, witnesses excepted) while the item's `assumptions` is empty; a `verified` item whose worktree `statement`,
`limits` or `assumptions` differ from the item as committed on the trusted ref (an uncommitted edit, not reviewed);
process state written into an item's `statement`, `limits`, `assumptions` or body (exploratory
or explore-mode checks, "no protected receipt", "not verified (yet)", "not yet admitted", "proposal until",
"supersedes (is) not set", fidelity reviews, the coordinator's next step, "notes from the import"), naming the
phrase; text inside fenced code blocks is a quote and is ignored. A reference to an id with no item shows the
status `missing` on cards.

## Versions

Receipts and reviews carry their schema, and `vl` rejects one it does not know. A new digest basis gets a new input
name, as `#verdict-rules` after `#verdict` and `#check`, and receipts that recorded an old one keep its basis. Item
front matter is a closed key set, extended only together with this document. The CLI's `--json` output carries
`"schema": "vl.output/1"`; `vl lane exec`, whose output is its command's, unchanged, refuses `--json`.
