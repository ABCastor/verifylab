# The `research/` format

Format of the records `vl` reads and writes: receipt schema `vl.receipt/1`, review schema `vl.review/1`. Everything
is a plain file in Git, readable with a text editor. Deriving status and reproducing a check use the record
bindings and the verifier described below.

## Layout and admission

```
research/vl.toml                          project configuration (templates/vl.toml lists every key)
research/problem.md                       optional question/objective context
research/map.md                           optional research map, useful failures and open routes
research/ideas.md                         optional speculative ideas and connections
research/items/<id>.md                    items
research/targets/*.lean                   trusted Lean targets
research/evaluators/*.py                  trusted Python evaluators
research/evidence/<id>/<16 hex>.json      receipts of protected checks
research/reviews/<id>/<16 hex>.json       reviews
.vl-cache/explore/<id>/<16 hex>.json      receipts of exploratory checks (gitignored, never admitted)
.vl-cache/replay/<id>/<16 hex>-<time>.json  records of `vl replay` (gitignored, never admitted)
.vl-cache/foreign/<machine>/<id>/<16 hex>.json  receipts imported as foreign (gitignored, never admitted, never counted)
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

### Optional context and local retrieval

`vl init --context` creates the 3 optional Markdown homes above without replacing an existing file;
`--write-agents` includes pointers to them. They are ordinary editable context, not records or proof inputs.
`[context]` in the trusted configuration can set `problem`, `map`, `ideas` to repository-relative `.md` paths;
without these keys they follow `research_dir`. Optional `notes` lists Markdown files or folders to search,
defaulting to `research_dir`; role files are also included. The 3 role paths must name distinct normalized files;
unknown `[context]` keys are errors. Existing project homes can be selected directly.
The problem is independent of proposed solutions; the map records current evidence/routes; ideas retain
attributed speculation. No strategic reading, research routine or model invocation is mandatory.

`vl find TEXT --scope all|items|lean|notes|reviews` defaults to all groups. `--kind K` narrows to items or Lean
and preserves the existing kind filter. Items/Lean use `.vl-cache/index.sqlite`; notes/reviews are read live,
with no second cache. Notes return path, line, locator and a snippet of at most 500 characters; every query word
must occur on the matching line, case-insensitively. Reviews match the complete validated JSON record, including
structured criteria, supplied basis and raw evaluator output; hits carry their author, kind and admission state.
An admitted review remains searchable when its worktree copy is absent. `--regex` supports case-insensitive
regular expressions only with explicit `--scope notes` or `--scope reviews`. `--limit` caps each displayed group;
totals and incomplete-scope warnings remain visible. No match is exit 1, invalid arguments exit 2.

Only configured note homes are scanned, excluding hidden/build/vendor folders and item, target, evidence and
review subtrees. Note files over 2 MiB and unreadable/invalid files or folders are reported as outside the
searched scope; the note-file count includes only files successfully read as UTF-8 and searched.
Escaping paths and symbolic links are not followed to read notes outside the repository. Search is a locator,
not an assessment of scientific completeness; a miss establishes no absence outside its reported scope.

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
| `[[imports]]` | list of tables | pinned cross-repo Lean theorem imports, below |
| `[provenance]` | table | where the statement comes from, below |
| `[origin]` | table | how the item came to be, below |

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

## `[provenance]` and `[origin]`

Both are declared by the item's author (ADR 0014). Their words are single lines of printable text, so none can pass
for another line of the card. `vl` shows them on the card and in the catalog and checks their
shape; it never checks that they are true and they never enter a status, a question digest or a review's binding.
An upstream-reviewed statement is not a verified one.

| `[provenance]` key | Type | Meaning |
|---|---|---|
| `kind` | `upstream-reviewed`, `lab-authored`, `model-authored`, `own` | required: the statement was reviewed by its source's authors, written by the lab that released it, written by a model, or is the project's own |
| `source` | non-empty single line | the URL, commit and path, or other reference, of the statement; required for `upstream-reviewed` and `lab-authored` |

| `[origin]` key | Type | Meaning |
|---|---|---|
| `agent` | as `author` | required: who made the item (the agent that ran, not the person who asked) |
| `model` | non-empty single line | the model id, when known |
| `context` | non-empty single line | a pointer to the context the agent was given: a brief, plan or record, a file, a commit |
| `tokens` | integer, 0 or more | tokens the work took, when the harness reports them |
| `from` | list of `id` or `id@rev` | the items (an idea, a result, a failure) it was made from; `vl validate` reports one that does not exist |
| `inspired_by` | list of non-empty single lines | other leads: records, sources, people (`human:NAME`), URLs; free text |

`vl validate` warns when an item created after 2026-10-11 has no `[origin]`, or has a `[lean]` or `[python]` binding and
no `[provenance]`; older items are never warned. `kind = "intuition"`, `conjecture` and `question` items are the ideas:
they carry `[origin]` like any other.

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

## `[[imports]]`

Cross-repo Lean theorem imports pin upstream verified items cleanly across Lake package dependencies (offline snapshot resolution):

| Key | Type | Meaning |
|---|---|---|
| `package` | valid Lake package name | package in consumer `lake-manifest.json` (git entry with valid `url`, optional normalized `subDir`, and full-SHA `rev`; setting `url`, `subDir`, or `rev` in the item itself is refused) |
| `item` | valid item id | source item id in that package |
| `item_revision` | full 40- or 64-hex Git blob sha | exact blob sha of the source item at the pinned commit |
| `module` | non-reserved Lean module name | Lean module providing the declarations; must not collide with consumer `[lean] roots` or another manifest package |
| `theorems` | non-empty list of distinct declaration names | declarations imported; every name must appear in the source item's `[lean] theorems`, its pinned receipt's `checked.theorems`, and the pinned `module` |
| `receipt` | full 64-hex `receipt_id` | admitted protected `pass` receipt in the source snapshot |
| `fidelity_review` | full 64-hex `review_id` | admitted `faithful` fidelity review matching the source meaning at the pinned commit |

Resolution is strictly offline against installed/configured package stores (`machine.cache_for` packages, `[lean] packages`, `[lean] cache`, `.lake/packages`, or configured store paths), never fetches over the network, and never adopts a source repository's `vl.trustedRef` or checked-out HEAD. Source status and fidelity are derived over the whole admitted snapshot at the manifest's pinned `rev` so a cherry-picked pass cannot hide a retraction, correction, newer failure, vacuity, or stale/disputed fidelity review. Upstream branch movement past `rev` does not stale a consumer while its manifest pin (`url`, `subDir`, `rev`) and `[[imports]]` remain unchanged; updating the manifest pin, `[[imports]]`, or mismatched blobs/reviews stales or rejects the consumer. Consumer fidelity and proof status remain distinct from source status and fidelity; `vl validate` rejects unused `[[imports]]` modules and custom-package Lean imports lacking an `[[imports]]` entry, and warns when consumer `limits` or `assumptions` omit constraints stated by imported items. Local index caching (`.vl-cache/index.sqlite`, searched by `vl find`) stores resolved package items, limits, declarations and evidence/review locators explicitly as a locator cache only, never as proof, fidelity, or freshness evidence.

## The question of an item

The **question fields** are `[lean]` `target`, `theorems`, `witnesses`, `proofs`, `imports`, `solution`,
`[python]` `evaluator`, `candidate`, `entry`, `files`, and (when non-empty) `[[imports]]`. A protected check takes `target`, `theorems`, `witnesses`,
`evaluator`, and `[[imports]]` from the item on the trusted ref, the rest from the worktree.
`question_digest` = `"sha256:"` + hex sha256 of the UTF-8 of `json.dumps({"lean": {...}, "python": {...}}` (plus `"imports": [...]` only when `[[imports]]` is non-empty, leaving non-import items unchanged),
`sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)`, each table holding only the question
fields present.

## Imported items

`vl import comparator` (ADR 0007) writes ordinary items; tags say what an item is. Each result has `[provenance]`
(`kind` from `--provenance`, default `lab-authored`; `source` = `SOURCE-URL@COMMIT:PATH.lean`) and each item has
`[origin]` (`agent` the importer's author, `context` the source files).

| Tag | On | Meaning |
|---|---|---|
| `comparator-challenge`, `comparator:NAME` | result | imported from the challenge `NAME.json` + `NAME.lean` |
| `import-digest:HEX` | result | hex sha256 of the canonical JSON of `{lean, solution, theorems, axioms, nanoda, definitions}`: the sha256 of the challenge file, the config's solution module, theorem names in order, sorted permitted axioms, the second-kernel flag and sorted definition names |
| `import-revision:N` | result | 1 for the first import, one more for each changed version |
| `main-result`, `catalogue-review:STATUS` | result, source | the catalogue (`formalization.yaml`, schema v0.4) marks the challenge as a main result; its `review: status` |
| `area:NAME` | result | the area of the solution module's path: its second component in kebab case, when the module has three or more (`OAI.Analysis.Foo` is `area:analysis`, `OAI.NumberTheory.Foo` is `area:number-theory`); none for a shorter module name. The catalog's area filter reads it |
| `comparator-source` | source | a paper cited by the family scope notes of the source repository |

The target file is the challenge, byte for byte; `theorems` and `solution` are the config's `theorem_names` and
`solution_module`; the id is the challenge name in kebab case (`WLIdentification` is `wl-identification`) with
`--id-prefix`, and `-r2`, `-r3`, ... for a changed version, whose `supersedes` is the previous one. An earlier import
is one with the challenge's tags, the id this run would make (same `--id-prefix`) and the same source URL (from its
`[provenance]`, `ref`, `[origin]` or body; an item that names none matches any): a challenge of the same name from
another source or prefix is never `unchanged` under it nor a revision of it, and a paper's source item is reused only
from the same source. The item's `ref` is
`URL@COMMIT:PATH`, the challenge file in the source repository (as `--source-url`, `--source-commit` and `--source-path`
give them; the catalog shows it as the item's source), and the body also records them with the sha256 of both files. Project-wide rules the config also states (permitted
axioms, second kernel) appear in `limits` where the project's differ. The config's `definition_names` are definition
holes, whose bodies Comparator does not compare (only their types); `vl` does not pass them through and compares the
bodies too, so its check is stricter: a limit names them and the run warns about them. A source item has `ref` the
paper's path in the source repository and access `citation-only`.

## Receipts

A JSON object written once, by `vl check` (or by the replay of a foreign receipt, which runs one; `vl receipts import`
copies one whole), as `<receipt_id[:16]>.json` (indented, sorted keys).

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
| `inputs.digest` | `"sha256:"` + hex sha256 of canonical JSON of `{files, trusted_files, target}` (plus `imports` when `inputs.imports` is non-empty) |
| `inputs.imports` | when present: resolved `[[imports]]` bindings (`package`, `item`, `item_revision`, `module`, `theorems`, `receipt`, `fidelity_review`, manifest `{url, subDir, rev}`, `source_meaning_digest`) |
| `inputs.trusted_ref`, `trusted_ref_source`, `trusted_commit` | the trusted ref, where its name came from, its commit |
| `inputs.candidate_head`, `candidate_uncommitted` | the worktree's HEAD, and the checked files it had not committed |
| `target` | what was checked against: path, sha256, theorems or evaluator, source, commit |
| `environment` | toolchain, tool identities, isolation and machine context (see below) |
| `checked` | what the verdict covers: Lean kernels, observed per-kernel `kernel_results` (`passed` or `failed` when a solution check stopped with an attributed kernel outcome), permitted axioms, `probes`, `probe_run`, `lints`; Python cases |
| `command`, `log_sha256`, `log_tail` | the command run, the digest of its full log, the last 4000 characters |
| `started_at`, `finished_at`, `tool_version` | ISO 8601 UTC times, to the microsecond; `vl` version |
| `extra` | optional: phase timings (`preflight` and `probes` run beside other steps, ADR 0005; `probes_alone`, the probes run again alone after a pass), slowest modules, Lake jobs, peak memory (`memory_peak_bytes`: the most anonymous memory, memory.stat `anon` without page cache, that the check's runs held, polled, with the runs that overlap added together, for Lean the dependency preflight and the challenge build, then Comparator and the statement probes, or the probes run again alone; the job queue reserves it for the item's next checks), `cpu_seconds` (the CPU time of the check's units added up, whether or not they overlapped: cpu.stat `usage_usec`, polled, a lower bound), seconds the check waited in the machine's job queue (`queue_wait_seconds`), notes |

The `environment` table records:

- The toolchain and Lean version, each tool's path, SHA-256, source and pin, the machine file, isolation and
  `memory_cap`. `machine_policy` records the machine file and its digest, tool store, launchers and any XDG
  override that moved them. When configured, `machine` records its name and trust class (ADR 0015, proposed).
- Lean `packages.identity` records the Lake checkout revisions alongside the manifest revisions and the
  trusted imports' build trace `depHash` values and trace digests.
- `host` records available sandbox class, kernel, architecture, processor, cores and RAM
  (ADR 0012, proposed). `environment_class` normalizes adapter, assurance, sandbox class, architecture, kernel, memory
  cap and toolchain/tools/packages identity or Python version. It excludes host names, paths and timestamps
  and carries a content `digest`.
- `third_party_packages` lists packages outside `[lean] trusted_packages` accepted on a disposable host
  (ADR 0012, proposed).
- `build_cache` lists challenge modules restored or stored and their cache keys, including `stored_keys`
  for safe pruning (ADR 0005). If solution reuse is on, `solution_build` records its cache key, modules,
  `reused` and `stored` (ADR 0008). The key covers candidate inputs, the generated solution module, its
  build environment and build tools. A reused artifact was loaded under that key.

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
For a protected `pass`, validation also recomputes `inputs.digest` and requires normalized repository-relative
input paths with hex sha256 digests, a full `inputs.trusted_commit`, and a `target` from that same commit whose
path and hash match `inputs.trusted_files`. Lean passes require the target theorem list to match
`checked.theorems`, coverage including the Lean kernel, an axiom list, toolchain/version, verifier tool paths and
hashes, and recorded isolation. A trusted policy requiring nanoda also rejects missing kernel coverage.
Python passes require the candidate in `inputs.files`, an entry identifier, a consistent case digest, positive
case coverage with every case passed and no failure/judge error, interpreter/version and recorded isolation.

Protected Lean execution additionally requires the pinned Comparator's outer AF_UNIX restriction: a systemd
service applies `RestrictAddressFamilies=~AF_UNIX`, and a trusted helper observes socket denial before every
jailed command. Receipts record the executed service command and isolation. Missing enforcement is
an inconclusive `error` or `unsupported`, never `pass`; so is a service that systemd ended with a result other than
its main process's exit status (`oom-kill`, `timeout`), which the reasons name; Python/exploratory resource caps retain their independent optional fallback.
Incomplete historical passes remain files but fail validation and cannot verify a result; error, fail,
unsupported and exploratory receipts can describe partial execution. These consistency checks do not
authenticate execution: a trusted integrator can still admit a well-formed fabricated receipt.

Tool identities and dependency traces are recorded for replay, not compared with the current machine when deriving
staleness. A changed verifier binary or build cache therefore does not invalidate an old receipt automatically;
`vl replay` compares them on demand (below).
`candidate_head` is the HEAD at check time, not a snapshot of dirty candidate files; a later commit may preserve
the checked bytes. Replaying such a receipt requires that commit's candidate files and answer binding, checked
against the recorded digests, together with the original `trusted_commit`.

### Statement probes

`checked.probes` is recorded after a Lean pass. For each target theorem it can record:

- `trivial_by`, the tactic that closed the theorem alone, or null.
- `vacuous_by`, the tactic that derived `False` from the hypotheses, or null.
- `prop_hypotheses`, a count or null when there is no result, with optional `limit_reached`,
  `vacuity_skipped` and `error` fields.
- `definitions`, the in-project declarations reached through types and values, including inductive types
  for their constructors and projections (ADR 0010).
- `totalized`, up to 40 rows of `{op, term, in, why}` for operations in the theorem type, definition values
  or structure fields. `in` locates the statement or definition. `op` identifies the kind or constant.
- `boundary`, rows of `{binder, value, vacuous_by | trivial_by}` for cases flagged by the battery on the
  first two ℕ binders at 0, 1 and 2.
- `foundations`, up to 40 rows of `{kind, const, in, why}` for set-theoretic constants in the same places as
  `totalized`. This includes the `ZFSet`, `PSet`, `Cardinal` and `Ordinal` namespaces and name components
  recognized for choice, theory or independence. `Classical.choice` is a totalized finding.

The scan shares an attempt's heartbeat budget. `limit_reached.totalized` can report a stopped scan with
the partial results retained.

`checked.probe_run`
holds the battery, heartbeats, timeout and `problems`, or `skipped`, and `boundary_unfinished`, the theorems whose
boundary values the run did not reach (boundary values run last; their absence is not a problem); `rerun_alone`, when
the probes that ran beside Comparator did not finish and this record is of their second run, alone, after the pass,
says why.
Vacuity tests inconsistency of top-level Prop hypotheses. Inhabitation of data binders, adequacy of definitions
and solution classes denied inside a conclusion are outside that test. An intentional per-theorem `vacuity_skipped` reason is shown in
full and brief text cards when that proof section is included; it is not a failed/incomplete run and does not
change status. A witness establishes only its own stated instance, not coverage of other data or fidelity.
Cards show each theorem's totalized operations and boundary values under PROOF, and under MEANING the definitions
(`definitions` in `vl show --json`: `names`, and `not_named`, those no admitted fidelity review of the current
meaning names). None of them changes a status: listing a definition does not establish its adequacy, and a listed
operation may be guarded by a hypothesis.

## Replay records

`vl replay ID` (ADR 0009) runs the protected check of one admitted protected receipt (`--receipt RID`; by default the
one that decides the item's status, the one `vl show` names, else the newest pass, else the newest receipt) again in a
fresh clone and writes `.vl-cache/replay/<id>/<receipt_id[:16]>-<started, compact UTC>.json`: `schema` `"vl.replay/1"`, `item`,
`receipt` (its path on the trusted ref) and `receipt_id`, `contract` (`exact`, `compatibility`, or `stronger`), `result` (`match`, `rechecked`, `mismatch` or `error`), `differences`
(for `match`, `rechecked` and `mismatch`) or `reasons` (for `error`), `trusted_commit` (the receipt's, the question's commit),
`answer_commit` and `answer_from` (where the fresh clone held the checked candidate files and their answer
binding, or null before an answer revision is found). Replay tries `candidate_head`, the first admission on the
trusted first-parent history, and the current trusted commit. `--answer-rev REV` selects a published revision
and applies the same checks. `replay` (the receipt the replay's check produced, absent or null when nothing ran), `started_at`,
`finished_at`, `tool_version`. Three contracts (`--contract auto|exact|compat|recheck`, default `auto`) are kept apart:

- `exact`: reconstructs the recorded tool and kernel generation and compares the full recorded basis (`<field>: receipt
  <value>, replay <value>`, `absent` for a missing field) over: `adapter`, `verdict`, `question_digest`,
  `inputs.digest`, `inputs.imports`, each of `inputs.files` and `inputs.trusted_files`, `environment.toolchain`,
  `lean_version`, each tool's `sha256`, each Lake package's revision `head` and each traced module's `depHash`,
  `interpreter`, `python_version`, and `checked.kernels`; and, in one direction, `checked.probes[THEOREM].vacuous_by`
  and `.trivial_by` (`receipt absent, replay TACTIC`: a hit the replay finds and the receipt lacks).
- `compatibility`: selected by `auto` or `--contract compat` only for recognized historical receipt profiles identified
  structurally (never by date; receipts carrying `#verdict-rules` or `environment.packages.identity` are excluded). It
  projects preserved historical inputs (`question_digest` from `item_revision`, historical config binding
  `research/vl.toml`/`#verdict`/`#check`, `VLTrusted/` paths, explicit omission of absent `environment.packages.identity`,
  and recomputed `inputs.digest`) without rewriting the receipt or inventing old package coverage, and records `compatibility`.
- `stronger` (`--contract recheck`): reruns the same preserved question and inputs under the current policy and tools. It
  checks the verdict and one-way probes, enforces the external kernel on historical configs, and records
  `result` `"rechecked"` and `relation` (`added_kernels`, `removed_kernels`, `changed_tools`, `separate_evidence`), and
  never conflates the outcome with an exact match or auto-promotes foreign receipts.

A replay record is never admitted and changes no status. A replay of a foreign receipt (below) that matches under `exact`
or `compatibility` also writes the receipt of its own check (`replay`) to `research/evidence/`, where it is an ordinary
new record, and removes the foreign one from quarantine; the foreign receipt itself is never counted, since the fields
the comparison leaves out (its probes, or omitted historical coverage) were not reproduced.

## Receipt bundles

`vl receipts export OUT.tar` (ADR 0015, proposed) writes a plain tar of a manifest and receipts, and nothing else
(compressed with gzip when OUT ends in `.gz` or `.tgz`; a name ending in another compression's suffix is refused):
`manifest.json` (`schema` `"vl.receipts/1"`; `machine`: `name` and `trust` of `[machine]` in the exporting machine's
machine.toml; `receipts`: member name to hex sha256 of its bytes) and one member `evidence/<item id>/<16 hex>.json`
per protected receipt that the trusted ref does not hold, byte for byte as `vl check` wrote it. Members carry no time,
owner or path. The gzip header also omits the output filename and uses a fixed timestamp. `vl receipts import FILE --from NAME --trust own|foreign`
reads it, plain or compressed with gzip, bzip2 or xz, as one stream without extracting: a bundle larger than 512 MiB
once uncompressed, a member that is not a regular file of that name, of at most 8 MiB, a missing or
different manifest, a manifest digest that differs, or a `NAME` that is not the manifest's machine refuses the whole
bundle (exit 2). Each receipt is then refused (exit 1, listed) unless it is sealed and well-formed, protected, filed
under its own item and name, of an item on the trusted ref, and declares no other machine than `NAME`. For `own` it must
also not come from a machine that declares `trust = "foreign"`, name only verifier tools whose sha256 this machine
pins in the `REVISIONS` of their tools store or `[tools]` folder, and have been asked of a commit that is in this
repository. `own` writes `research/evidence/<item>/<16 hex>.json`; `foreign` writes
`.vl-cache/foreign/<NAME>/<item>/<16 hex>.json`, shown on the card (`foreign_receipts` in `vl show --json`) and read
by no status. Import stages the complete receipt bytes and publishes them at a new filename in one step. A retry
counts an existing receipt as present only if its sealed content matches. An incomplete or different record at
the destination is refused and left in place.

## Reviews

A JSON object written once, by `vl review`, as `research/reviews/<item>/<review_id[:16]>.json`.

| Field | Meaning |
|---|---|
| `schema`, `review_id` | `"vl.review/1"`; hex sha256 of the review without `review_id`, as for receipts |
| `item`, `item_revision` | the item and the full blob sha of the revision reviewed |
| `kind` | `fidelity`, `compare`, `correction`, `retraction`, `understanding`, `correspondence` or `appraisal` |
| `author`, `text`, `created` | `human:NAME` or `agent:NAME` (`human:NAME` says NAME wrote or approved the text: `vl review` writes it only with `--human-approved` or after NAME confirms at the terminal); the judgement with its reason (`text` may be empty only for a structured appraisal); ISO 8601 UTC, to the microsecond (earlier versions wrote seconds) |
| `verdict` | fidelity: `faithful`, `too-weak`, `vacuous`, `wrong-definition`, `unclear`; correspondence: `same`, `stronger`, `weaker`, `different` (the formal statement against the paper's); forbidden for appraisal; other kinds: optional, one line, at most 64 characters |
| `assessment` | appraisal only, required: structured contribution and separate applicability basis (below) |
| `statement` | correspondence only, optional: which statement of the paper the verdict is about, one line of at most 200 characters; a review without it is about the item's statement as a whole |
| `compare_with` | compare only: `id@<full revision>` |
| `acknowledges` | fidelity only: a non-empty list of `trivial` (the target is meant to be closed by automation alone) and `foundations` (the reviewer judged what the statement says about Choice and the set theory it works in) |
| `target_path`, `target_sha256` | fidelity and correspondence: the `[lean] target` (else the `[python] evaluator`) and the sha256 of its content on the trusted ref |
| `trusted_ref`, `trusted_commit` | new fidelity records: the ref and pinned commit read by the command; provenance, not reviewer authentication |
| `meaning`, `meaning_digest` | fidelity and correspondence (required for a correspondence review): what the reviewer read, from the trusted ref: `files` (the target or evaluator and, for Lean, every in-project module of its import closure, path to sha256), `theorems`, `witnesses`, `imports` (when non-empty), `statement_sha256` (the item's claim text), `limits_sha256` and `assumptions_sha256` (the hex sha256 of the canonical JSON of the item's `limits` and `assumptions` lists), and `closure_error` when the closure could not be read; `meaning_digest` is `"sha256:"` + the hex sha256 of its canonical JSON |

Effects, once admitted: a `retraction` makes the item `retracted`; a `correction` is shown first on every card; a
`fidelity` review decides the item's fidelity; a `correspondence` review fills the last row of the verdict ladder
(below) and changes no status; `compare`, `understanding` and `appraisal` are shown only. An appraisal supplies
optional ratings with their own applicability and provenance.

### Contextual appraisal format

`vl review ID[@rev] --kind appraisal --assessment FILE --author agent:NAME [--text T]` reads the contributor's
JSON (`schema = "vl.appraisal.v1"`); it generates neither context nor prose. Only this kind allows an empty
`text`. Duplicate JSON keys, nonfinite numbers, unsupported fields and malformed options are invalid output,
not abstention. The existing review seal, immutable write, human-author approval and admission rules apply.
Identical content by the same author, including the entire assessment, is refused with the existing path; changed
basis, raw output or author is a distinct contribution. No appraisal is authenticated by its declared model.

| Input field | Contract |
|---|---|
| `schema` | `vl.appraisal.v1` |
| `subject` | `statement`, `proof`, `connection`, `assumptions`, `application` or `result` |
| `ratings` | non-empty map of configured criterion IDs to rating objects; a subset of the rubric is allowed |
| `input_context` | required non-empty text describing or locating the actual supplied context |
| `goal` | optional non-empty text; required whenever `usefulness` is rated, including an abstention |
| `context_files` | optional distinct normalized repository-relative paths to existing local files; each binds its sha256; no escape outside the repository |
| `provenance` | optional declared `model`, `provider`, `run`, `role`, `context_summary`, each non-empty text |
| `sources` | optional list of objects with `locator` and optional `access`/`note` text; records what the contributor accessed, not a performed search by `vl` |
| `review_references` | optional full 64-hex IDs of contributions this explanation or challenge refers to |
| `raw_output` | optional original JSON output, retaining native semantics; no automatic explanation is added |

A rating has exactly one `value` (one configured label), `score` (a finite native ordinal number), or
`abstention` (`unknown` or `abstain`). A score is allowed only for ordered criteria, in [0, number of labels − 1];
fractional positions retain their value without inventing a label or prose. Optional
`distribution` maps every configured label to a finite probability in [0, 1] summing to 1 within 0.000001;
optional `confidence` retains a finite native scalar. A score supplied with a distribution must match its expected
label position within 0.000001 absolute/relative tolerance. These do not establish scientific truth, independence,
literature novelty or eventual success. A low rating, unknown, explicit abstention and no rating differ.

`[appraisal] version` is a positive integer (default 1). `[appraisal.criteria.NAME]` tables replace the defaults:
each criterion has a non-empty `question`, distinct non-empty `labels` (excluding reserved `unknown`/`abstain`),
optional boolean `ordered` (default true), and optional non-empty `description`, `version`, `evidence` text.
Criterion IDs are lowercase identifiers. Without a custom table, `novelty` asks novelty relative to supplied
sources and `usefulness` asks usefulness for the named goal; both order `low`, `medium`, `high`.

The persisted assessment replaces `input_context`, `goal` and `context_files` with a `basis` object:
`schema = "vl.appraisal-basis.v1"`, `item`, full `item_revision`, the effective `manifest` (`version`, complete
`criteria`), `goal` (empty when not supplied), `input_context`, and `files` (path to sha256). Its
`basis_digest` is `sha256:` plus the sha256 of canonical JSON of this basis. Subject/revision and manifest are
bound independently of proof/meaning digests; body/connection notes can affect an appraisal without changing
formal evidence. Source packets and objective files can be included explicitly in `context_files`.

Views carry `assessment` and `applicability`: `state` (`current`, `stale`, `unknown`), `reasons` and a scope limit.
The current item revision, rubric and recorded local files are compared; admitted context files are read from
the trusted commit, proposal files from the contained worktree. Invalid/unreadable basis is unknown; changed
revisions, rubrics or changed/missing recorded local files are stale. No local digest detects new literature or unseen external evidence. Historical
rubrics remain readable. None of these states changes a receipt or fidelity review's freshness.

`vl show` full/compact/JSON views retain each contributor; `--no-appraisals` explicitly omits this section and
reports the omission count. Compact output may truncate detail to its character budget with pointers. Notes and
reviews can be retrieved separately with `vl find`. Catalog exports retain admitted appraisals and their
actual supplied context/raw output; no privacy filtering is implicit.

An author may write a later attributed synthesis with references while preserving the earlier contrary output.
Researchers may disregard ratings; no scheduler, automatic model call or committed budget follows.

A new fidelity or correspondence review names the current trusted item revision, even if the worktree has proposed edits.
New fidelity and correspondence writes require `--expected-meaning-digest` equal to the `meaning_digest` obtained from an earlier
`vl show ID --json` card before reading its files at `trust.commit`. A mismatch rejects the write. A fidelity
`--dry-run` can discover the current basis without this flag; a supplied digest is still checked. When a referee cannot
write the repository or its Git common directory, `vl review ... --emit FILE` validates and seals the review to `FILE`
without touching the repository lock, and `vl review --import FILE` verifies the sealed review (including trusted meaning
and `--human-approved` when signed `human:`) and writes it under `vl-review-write.lock`. The guard
establishes context equality, not whether the caller read or understood it. Each command reads one pinned commit.
For Lean, `meaning.semantic_environment` binds the normalized project/roots, toolchain, stable manifest package
identities (excluding the top-level `packagesDir` cache placement), TOML package/root `leanOptions` propagated by
the checker, and arbitrary `lakefile.lean` bytes conservatively. Missing files are recorded as `absent`; invalid
structured files retain their raw hash and `semantic_environment_error`. Machine-local tools and caches are not
part of this meaning basis. A Lean review recorded before this binding is completed (ADR 0004) with the environment of the
commits, in the trusted commit's history, that added or changed its file (`git log --diff-filter=AM`), and of the
`trusted_commit` it names, when they all agree; the card names that commit (`environment_from`). Without such a
commit (a shallow clone, an unreadable configuration, a disagreement) it stays `review stale: semantic environment
not bound; review again` and must be renewed (docs/adr/0004-older-review-environment.md).
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
the meaning there now (target, definitions closure, theorems, witnesses, claim text, limits, assumptions), any current adverse judgment gives `disputed: <verdict>`. The newest adverse judgment supplies the detail. The fidelity row retains every current author and verdict. A later review of the same meaning can replace its author’s named earlier review with `--supersedes-review`. Unanimous favorable reviews give `faithful`. `review stale: <what> changed since review` (target,
definitions, theorems, claim, limits, assumptions) when only reviews of another meaning exist; `not reviewed` when
none. The `title` and the body are explanatory text: no review binds them. Older Python reviews bind less, are marked on
the card with what they do not bind, and `vl validate` asks for a new one: a review whose `meaning` lacks
`limits_sha256` and `assumptions_sha256` (written before reviews bound them) counts while the rest of its meaning
is the meaning now and no admitted fidelity review of the item records them; a review without `meaning` (written before reviews recorded one) counts while its
`target_sha256` matches and no admitted fidelity review of the item is newer, whatever made that one stale. A review whose author is the item's author is marked as a self-review.

A card (and a brief, and `vl show --json`) shows the `statement`, `limits` and `assumptions` of the item as committed
on the trusted ref, the text its status and fidelity hold for (the worktree's while the item is not there). When
the shown revision says otherwise, its differing fields are shown apart (`proposed`), labelled `uncommitted edit, not
reviewed` (or, for an older revision asked with `ID@rev`, `the revision asked for, not reviewed`), and named in the
card's header line.

## The verdict ladder

A card, a brief, `vl show --json` (`ladder`) and the catalog (below) show five separate facts about a result, always
in this order, each `not assessed` until a record assessed it (ADR 0013). No row implies another: a passing kernel
says nothing of fidelity, and nothing is assessed by default.

| Row (`row`) | Assessed by | States |
|---|---|---|
| Lean kernel (`lean_kernel`) | the receipt the card's proof rests on: a Lean protected pass admitted on the trusted ref whose `checked.kernels` has `lean`, or an admitted protected failure with a recorded outcome for `lean` | `passed`, `failed`, `stale` (an input changed since), `not assessed` |
| second kernel (`second_kernel`) | the same receipt, using `nanoda` | as above |
| axioms and trusted base (`axioms`) | the same receipt, `checked.permitted_axioms`: the check allowed only those axioms | as above; when the probes found set-theoretic foundations in a target theorem (witnesses excepted) a `passed` or `stale` row carries `uninformative: true`, its detail starts with `uninformative` and the one-line form reads `axioms passed (uninformative)`: Lean always admits Classical.choice, so the list says little there |
| statement fidelity (`statement_fidelity`) | the deciding `fidelity` review of the current meaning | `faithful`, `disputed` (detail: the verdict), `stale`, `not assessed` |
| paper correspondence (`paper_correspondence`) | the admitted `correspondence` reviews of the current meaning: per paper statement (`statement`, null for the item as a whole) the newest one | `same`, `stronger`, `weaker`, `different`, `mixed` (statements disagree, or one was judged only before the meaning changed and shows as `stale`; `statements` lists each), `stale`, `not assessed` |

A row has `row`, `title`, `state`, `detail` (null or a short reason, the author of a review and whether it is a
self-review) and `record` (the receipt or review behind it, null when none or several; the catalog shows its id), and
the last row also `statements` (`statement`, `outcome`, `review`, `by`, `created`, `self_review`). An exploratory
receipt, a receipt not on the trusted ref, a Python check, an error or an unsupported check assess no row of the first
three. On a protected fail, the kernel rows use only outcomes explicitly recorded in `checked.kernel_results`.
For example, a statement mismatch before kernel replay leaves both rows `not assessed`. A correspondence review binds the meaning as a fidelity review does,
so an edit of the target, its definitions, the theorems or the statement makes it `stale`. The derived status label
below stays in `--json` and in the catalog's data for compatibility; text for people shows `verified` as
`check passed` (and `verified-stale` as `check passed, stale`) beside the ladder, which is the verdict.

## Derived status

Relations are integrator assertions. `refutes` is not checked as a proof of negation, and `uses` does not propagate
fidelity staleness. `answers` requires matching checker bindings when the question has them; a prose-only question
has no machine-checked correspondence. The derived relationship labels below do not strengthen those assertions.

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

   A protected Lean pass that only the Lean kernel replayed (`checked.kernels` without `nanoda`) counts as
   `unsupported`, with the reason, never as a pass when `[lean] external_kernels` is on OR when the receipt has a
   current profile (newly issued protected passes always require an external kernel regardless of `[lean]
   external_kernels`; historical single-kernel profiles identified structurally keep their pre-minimum status and coverage
   label when `[lean] external_kernels = false`).

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

`vl validate` warns about the following conditions. The warnings leave the derived status as it is.

- A protected pass has no faithful review or has incomplete probes. An admitted receipt is stale.
- Automation closed a target theorem, or the probes identified definitions it depends on, without the
  current fidelity review acknowledging the finding. A definition can be named by its full name or last
  component. Declared witnesses are exempt from the triviality warning.
- A target theorem has totalized operations or flagged boundary values before a faithful review, or
  set-theoretic foundations before a current review acknowledges `foundations`. These warnings exempt
  witnesses. For foundations, the permitted axiom list alone says little because Lean admits
  `Classical.choice`.
- Probes found Prop hypotheses and there is no witness or the item leaves `assumptions` empty. The
  assumptions warning uses the newest admitted receipt with a probe result, exempting witnesses.
- The worktree's `statement`, `limits` or `assumptions` differs from the trusted version of a verified item.
- Item prose contains process state, such as check mode, pending admission, fidelity review status,
  supersession or an import note. The diagnostic names the phrase. Fenced code is treated as quoted text.
- An in-project Lean module under `[lean] roots` has no import path from the modules selected by its
  library's globs among tracked worktree files. The check reads `lakefile.toml` from the trusted ref.
  It skips this warning when a trusted `lakefile.lean` takes precedence. Lake defaults library roots to
  the library name and globs to the roots. `A.*` selects `A` and its submodules. `A.+` selects submodules.

A reference to an absent item appears as `missing` on cards.

## The catalog

`vl export catalog OUT` (ADR 0011) writes `OUT/catalog.json`, schema `vl.catalog/1`, and `OUT/index.html`, one
page with the same content. Both come from the trusted commit alone: the items as committed there, its admitted
receipts and reviews, statuses derived as above, with every candidate input compared with the trusted ref and never
with the worktree. Nothing a worktree holds, lacks or edits, and no exploratory receipt, changes them. Neither file
has a time, a host or a path of the machine, so the same records give the same bytes. Items are by id; JSON keys are
sorted. A field with no value is `null` or an empty list, except where noted. The page has no external resource: its
style and script are inline and a Content-Security-Policy allows only their sha256; record text is escaped and a
`ref` is shown as text, never a link. Every item is in the page as HTML, so it reads without the script, which only
hides the items that the filters (status, kind, area, text) exclude.

| Key | Meaning |
|---|---|
| `schema`, `project` | `"vl.catalog/1"`; `[project] name` of the trusted `research/vl.toml` |
| `trust` | `ref`, `source` (where the ref's name came from), `commit` (full), `configured` (false for `--trusted-ref` or the default `main`: statuses hold for that ref only) |
| `policy` | `permitted_axioms` and `external_kernels` of the trusted configuration |
| `counts` | `items`, and the number of items per `status` label, per `kind`, per `probes` state and, under `ladder`, per row and state |
| `problems` | item files of the trusted ref that do not parse; they are not listed |
| `items` | one object per item, below |

An item has `id`, `title`, `kind`, `claim`, `statement`, `limits`, `assumptions` (as committed), `tags` and `areas`
(the values of its `area:NAME` tags), and:

| Key | Meaning |
|---|---|
| `status` | `label` and `notes` as `vl show` derives them, and the record they come from (`receipt`: a refuted item's is the refuting item's receipt, a retracted item's the retraction) |
| `target` | `path` and `sha256` of the Lean target or Python evaluator, `theorems`, `witnesses`, `solution`, `evaluator`, `entry` |
| `proof` | the receipt behind the status (else the newest): `receipt`, `adapter`, `assurance`, `verdict`, `kernels` and `permitted_axioms` as the check recorded them; null without a receipt |
| `receipts` | every admitted receipt, newest first: `id`, `adapter`, `assurance`, `verdict`, `finished_at`, `stale` |
| `probes` | the statement probes of that receipt (Lean only, else null): `state`, `receipt`, `skipped`, `problems`, `data_inhabitation` (always `not assessed`), `boundary_unfinished` (the theorems whose boundary values were not probed because the run ended first), `counts` and `theorems` |
| `ladder` | the five rows of the verdict ladder above, as the card has them (`row`, `title`, `state`, `detail`, `record`, and in the last `statements` with `statement`, `outcome`, `review`, `by`, `created`, `self_review`), a record named by its id |
| `fidelity` | the deciding fidelity state of the card: `label`, `review`, `by`, `created`, `self_review`, `acknowledges`, `legacy`, `unbound`; null without a target or evaluator |
| `reviews` | every admitted review but corrections and retractions, newest first: `id`, `kind`, `author`, `created`, `verdict`, `at_current_revision`; appraisals also retain `text`, `assessment` (including actual input context/raw output) and `applicability` |
| `corrections` | corrections and retractions with their `text` |
| `rejected_records` | admitted records that failed validation and are ignored, with their `problems` |
| `provenance` | `path` and `revision` of the item file, `added_in` (the oldest commit of the trusted history that added it; null in a shallow clone), `author`, `recorded_by`, `created`, `ref` (an import's is `URL@COMMIT:PATH`) and `access` (a source item's), `sources` (the source items it cites or uses, with `ref` and `access`) and `origin`, the first paragraph of the body, free text (up to 400 characters), where an import or a note says what the item came from |
| `statement_provenance`, `origin` | the item's `[provenance]` and `[origin]` tables as declared (keys sorted), null when absent; not checked, not a status |
| `imports` | resolved or unresolved `[[imports]]` entries, keeping source proof/fidelity and source limits separate from consumer state; empty list when none |
| `relations` | `uses`, `cites`, `answers`, `refutes`, `supersedes` and `used_by`, `cited_by`, `answered_by`, `refuted_by`, `superseded_by`: `id`, the derived `status` of that item (`missing` when there is none), `pinned_revision` when the relation is pinned |
| `replay` | for a result with a `[lean]` or `[python]` binding whose `proof` is a protected receipt, else null: `command`, `vl replay ID --receipt RID` (docs/REPLAY.md), and `receipt`, the item's own receipt shown as its `proof` (not the record a refuted or retracted status borrows) that it runs again and compares |

A receipt or review `id` is the 16 hex characters that name its file under `research/evidence/<item>/` or
`research/reviews/<item>/`. The `probes` `state` of a Lean check is `vacuous` or `trivial` when a theorem's battery
found it, else `incomplete` when a theorem has no result or the run recorded problems, else `skipped` when the run
was skipped or a theorem records that its premise test was skipped, else `clean`; `none` when the receipt records no probes. A theorem has the same states and
lists the fields its probe entry set (`trivial_by`, `vacuous_by`, `prop_hypotheses`, `vacuity_skipped`,
`limit_reached`, `error`) and `witness`; the lists of ADR 0010 under the keys the probes write them, as the card
shows them: `definitions` (names), `totalized` (`op`, `term`, `in`, `why`) and `boundary` (`binder`, `value`,
`trivial_by` or `vacuous_by`), a row that is not well formed left out; any other field of a probe entry, such as the
foundations findings, is carried under `extra`. A clean state says the battery found nothing in the probes it ran. The fidelity of an item is its reviews: the catalog repeats the card's derived label and never decides it.

## Job lists

`vl jobs export OUT.json (--all | --stale | --ids FILE)` writes the protected checks to run, schema `vl.jobs/1`, from
the trusted commit alone (the selection is that of `vl check --all|--stale|--ids`, read through the same view as the
catalog: only admitted receipts count). `vl jobs missing JOBS.json` lists the jobs no admitted receipt answers yet;
`vl jobs shard JOBS.json --weights NAME=W,...` writes one ids file per machine. A list is a plain file; `vl` opens no
connection and starts nothing on another machine.

| Key | Meaning |
|---|---|
| `schema`, `project` | `"vl.jobs/1"`; `[project] name` of the trusted `research/vl.toml` |
| `trust` | as in the catalog: `ref`, `source`, `commit`, `configured` |
| `digest` | `sha256:` of the canonical JSON of the jobs' ids in order: a changed, added, dropped or reordered job is another list |
| `jobs` | one object per job, in the order selected (`--ids`: the file's; else by id), below |

A job has `item`, `adapter` (the checker's name), `trusted_commit` (the full commit of the list), `question_digest`
(the item's, as `vl check` records it in a receipt) and `job`, the first 16 hex characters of the sha256 of the
canonical JSON of those three, so the same item, commit and question are the same job on every machine. Two costs are
figures from the admitted receipts, null when there are none: `reservation_bytes`, what `vl check` would reserve (the
largest memory peak among the item's 5 newest receipts that record one, else the largest of the same checker's in the
repository), and `expected_seconds`, the longest wall time among the item's own 5 newest receipts. They are not part of
the job's name. Every reader recomputes the ids and the digest and refuses a list whose text disagrees.

A job is done when an admitted, valid, protected receipt of the item records that `question_digest` and was checked at
that `trusted_commit`; its verdict does not matter, and a receipt that exists only in a worktree, was checked at another
commit or answers another question does not count. `missing --out FILE` writes the jobs left as a list of the same
schema, to shard again.

`shard` takes machines and relative speeds (`worker-a=3,worker-b=2,worker-c=4`; names are letters, digits, `.`, `_`, `-`, and
name the files) and balances by `--by reservation` (default) or `seconds`: jobs in descending cost, each to the machine
that would finish it earliest in proportion to its weight (on a tie, the one with the fewest jobs for its weight). A
job with no figure costs the median of those that have one (1 when none does). The result is deterministic, every job
lands on exactly one machine, and a file `NAME.txt` (`--out DIR`, default beside the list) starts with a `#` line
naming the list and a `# trusted commit SHA` line naming the commit its jobs were cut for, and holds one item id per
line, biggest first: exactly what `vl check --ids` reads. `vl check --ids` warns when the trusted ref is at another
commit than the file names: its receipts would answer that commit's questions, which no job of the list asked.
Every child of a batch uses the coordinator's resolved commit and ref provenance. The summary names that commit.

## Integration follow-up

`vl status` lists bound items that need a check and items whose statement fidelity still needs review. A referee may start once the target exists. `vl lane integrate NAME --no-merge` computes both lists on the prospective merged tree, using the same decisions as a successful integration. A prose-only note requires neither action. A divergent preview requires Git with `merge-tree --write-tree` support. A real integration requires HEAD on
the trusted branch, which can be configured by short or fully qualified name. A failed merge in an ordinary or
linked worktree aborts Git's merge state when present.

`vl new` checks explicit theorem and namespace components before writing a scaffold. Numeric item IDs receive a generated namespace starting with `Item`.

## Versions

Receipts and reviews carry their schema, and `vl` rejects one it does not know. A new digest basis gets a new input
name, as `#verdict-rules` after `#verdict` and `#check`, and receipts that recorded an old one keep its basis. Item
front matter is a closed key set, extended only together with this document. Most commands use `vl.output/1`
for `--json`. These commands use their own top-level schema:

| Command | Schema |
|---|---|
| `vl attempts check DIR --json` | `vl.attempts.check/1` |
| `vl attempts table DIR --json` | `vl.attempts.table/1` |
| `vl graph LOCK --dump DIR --lean LEAN --json` | `vl.graph-inputs/1` |

`vl lane exec` passes through its command's output and refuses `--json`. `vl check --json` also carries `queue_wait_seconds` (null when the check did not queue); `vl check --all|--stale|--ids --json` lists
`checks` (item, verdict, seconds, queue_wait_seconds, receipt, reasons) and `counts`; `vl check --selftest --json`
carries `ok`, `trusted_commit`, `rules_digest`, and `cases` (`case`, `expected`, `verdict`, `ok`, `reasons`) and
writes no receipts to `research/evidence/`; `vl admit --json` lists the
`admitted` receipt and review paths and the `commit`. The
catalog file carries `vl.catalog/1` and the job list `vl.jobs/1`; renaming or removing a key, or changing what one
means, gets a new version, and a key added beside the others does not (readers ignore keys they do not know; ADR 0013).
