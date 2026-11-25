# Changelog

## 0.2.0 — 2026-11-25

### Release highlights

- Trials can preview and rebuild downstream modules from pinned source imports, including optional libraries
  and selected clients. The SSH runner accepts an explicit SSH configuration file.

- Foreign graph manifests bind the source paths and input digests to the pinned target closure and list
  the compiled artifact digests. Graph extraction can read an independent prepared build. The value view
  can report a selected shortlist with per-root distances and pinned anchors. A moved lock reports the
  resolved checkout and prints a repin command.

- A batch keeps one trusted commit. Linked trials bind the committed forecast, distinguish interruptions
  from build failures and refuse Lake arguments the overlay cannot reproduce. Shallow history leaves
  uncertain forecasts unassessed.

- Read pinned Lean source signatures. Build a budgeted packet from approximate source paths or compiled
  dependency graphs. Use the removal view to estimate which located lines lose their last dependency path.
- Record a purpose in Git, attribute forecasts and compare them with reported outcomes. Run a pinned
  patch-and-build trial separately or link its measurements to a Markdown attempt.
- Protect Lean results with Comparator, Lean's kernel and nanoda. Review statement fidelity separately.
  Replay preserved answers, and move sealed receipts between checkers and the integrator.
- Install the single `verifylab` entry skill. It routes agents to task-specific references.
- The shipped replacements are `vl pin` for checkout locking, `vl trial` for patch trials and
  `vl export catalog` for publishing a static view. Read [the guide](docs/GUIDE.md#commands) for the current
  commands.

<details>
<summary>Development history and detailed fixes</summary>

The sections below preserve interim changes, including commands and skill layouts replaced before release.
Use the release highlights and guide for the shipped interface.

### Reading and value

- Source-only reading uses pinned Lake library roots to avoid historical copies. `vl order` explains how to read
  several roots separately or select an ambiguous declaration by file. Packet JSON gives omission counts and
  samples, with complete diagnostics available through `--full-omissions`. A single-root packet identifies the
  root's source location even when the statement is omitted.

- Group the agent commands in `vl --help` and route the version-matched entry skill by purpose. Add `vl pin`, a
  one-command pin and packet with `vl order --from`, and verified graph manifests as inputs to `vl value` and
  `vl order`. `vl value --study` ranks study and simplification candidates with the underlying numbers.
  The old Lake import and simplification spellings still run with a deprecation message.

- Add an opt-in multi-root removal view to `vl value` and `vl order --rank-by removal`, with fail-closed roots and ranges and a pre-prune report of elaboration-only dependencies. Reading packets normalize CRLF signatures and keep the original hash.

- Add offline import and inspection of ordinary pinned Lake checkouts, rooted dependency and value tables, budgeted signature packets, and attributed patch trials and dead roads. The CLI and agent skills now start from the mathematical aim.

- Vocabulary & skill deduplication (`E2`): unified status vocabulary across cards (`render.py`), `vl find`, `vl status`,
  and the seven skills while keeping `--json` field names and status labels stable with the mapping documented in
  `docs/GUIDE.md` and `templates/AGENTS-snippet.md`; moved repeated context, negative-evidence, and appraisal rules
  from `skills/vl-*` into the guaranteed-to-load `templates/AGENTS-snippet.md`.

- Cross-repo Lean theorem imports (`[[imports]]`): items can pin a source `package`, `item`, full `item_revision`,
  `module`, selected `theorems`, full `receipt` and `fidelity_review`, with `url`, `subDir` and `rev` read solely
  from `lake-manifest.json` and resolved offline over the whole admitted source snapshot at `rev` without touching
  old non-import records. Ambiguous providers, cherry-picked receipts, un-checked declarations, moved/unreviewed source
  items, unused imports and unlinked custom-package dependencies are rejected; local `.vl-cache/index.sqlite` stores
  imported declarations, limits and evidence locators explicitly marked as a locator cache only, never evidence.

- `vl show` (text, `--brief`, `--json`) and `vl export report` section 2 now report trusted-reading size
  (`files`, `lines`, `bytes`, `complete`, `omissions`: external imports or unreadable closure parts) without
  ever affecting status or verdicts (T8).

- Note search reports unreadable subdirectories instead of silently omitting them, and counts only files
  actually read and searched. The optional appraisal rubric is discoverable in the configuration template;
  drift checks cover its separate parser. Skills distinguish appraisal freshness, fixed-target review timing,
  contextual ratings and a useful account of the human's actual decision.

- Audit reports explain that coverage rows include source records; their unassessed entries are not a
  backlog of unproved results. Coverage data and denominators are unchanged.

- Contextual criterion views derive proof status only for visible rows, after filtering and ordering.
  Ratings, ordering, counts and the status of each visible result are unchanged.

- Optional contextual appraisals reuse immutable reviews: configurable criteria, native scores/categories,
  abstention, supplied context, local basis hashes and declared provenance. Full/brief cards, scoped search,
  per-criterion ordering and catalog/report exports retain opposing contributions. No rating alters proof
  status; no model is called. Unknown, unassessed and changed-basis judgments remain inspectable.

- `vl init --context` optionally scaffolds problem/map/ideas; `vl find --scope` searches registered notes and
  validated reviews, with explicit regex support. Seven skills and the README explain self-selected context,
  scientific-process review, novelty of connections and efficient use of human and agent attention.

- Skills: `vl-understand` says how to record a person's direction for `vl rank` (an intuition in their words, signed
  `human:NAME`, tags `direction` and `interest:...`, naming an item or an area, counted once committed);
  `vl-orient` points to docs/SPEC.md for the list of statuses and says `vl rank` orders attention without rating
  truth; `vl-improve` no longer says every edit keeps an item's receipts valid (an edit of a question field makes
  them stale, and of the statement, limits or assumptions the fidelity review).

- `vl import comparator --notes` reads a paper link whose folder holds parentheses (`...-CAT(0)-spaces-...`), which
  it used to cut at the first `)`, leaving two omath challenges with no paper; the run also names the challenges whose
  notes link no paper.

- `vl import comparator` describes a config's `definition_names` as what they are, definition holes whose bodies
  upstream's Comparator does not compare (only their types), in the item's limit and in a warning of the run: vl does
  not pass them through, so its check is stricter (a solution upstream accepts can fail, as SpinAngle does). The
  checking behaviour is unchanged, and so is the import digest, so admitted imports are not re-imported.

- `vl export report` no longer says that no human signed or reviewed anything when items, reviews or corrections are
  signed `human:NAME`: it counts them (`human_signed`) and says so; with none the all-AI positioning is unchanged. The
  changelog reads a SHA-256 source commit, groups a declared provenance without a source commit (a `vl explore`
  variant's `own`) by its kind instead of as `not declared`, and the reading order no longer calls itself a
  placeholder for a value function that `vl rank --attention` now provides. In `report.md` a URL, `www.` name or
  e-mail address from a record is a code span, since GitHub links them even escaped.

- `vl import comparator` recognises an earlier import by its tags, its id (the same `--id-prefix`) and its source URL:
  a challenge of the same name from another corpus or under another prefix is no longer reported `unchanged` under
  the first corpus's id, nor written as a revision that `supersedes` it; a paper's source item is reused only from the
  same source. A config with no `theorem_names` key is `skipped`, as ADR 0007 says (it was `invalid`), and a warning
  prints as `warning NAME: ...` whatever the challenge's status (it read `unchanged NAME: ...`).

- `vl rank` reads a human `direction` item as committed on the trusted ref: an uncommitted edit of its author, tags or
  `uses` (or of an agent's intuition into a direction) no longer moves the order. Strength S goes only to a verified
  refuter of a variant, as SPEC says, not to an unverified one.

- `vl export catalog`, `vl export report`, `vl show` and `vl rank` on a large repository: the folders of the trusted
  tree are found from one sorted index (no scan of the whole tree per record lookup), item revisions named by a full
  sha are read through the one `cat-file --batch` process, and a trusted input's digest and an item's meaning are
  computed once per command. On a 728-item corpus with a 124k-file tree the catalog takes about 5 s instead of 36 s;
  its output is unchanged.

- `vl rank` orders what a human reads next (`--attention`) or what to explore next (`--explore`) by a value derived on
  every read from admitted records, the worktree's proposals and the `[value]` table of the trusted configuration
  (ADR 0016, proposed); `--explain` shows every component. It writes nothing and changes no status. `vl explore ID --ops
  split,drop,weaken,const` writes variants of a result as `conjecture` items with a target each and a ledger, under the
  `[explore]` limits, in the worktree or a lane; the variants are text rewrites, not elaborated, with their direction
  by construction. The non-goal "no automatic scoring policy" becomes "no automatic verification policy". Not built:
  the Lean metaprogram (kernel-checked implication, `ablate`, Mathlib novelty), agent appraisals and agent operators.

- `vl export report OUT` writes `OUT/report.md` and `OUT/report.html`, the audit of the trusted ref as a document: a
  coverage table over all items (Lean artifact, Comparator verdict, nanoda, statement fidelity, paper correspondence,
  each state counted with `not assessed`; withdrawn, retracted and rejected counted apart), the items in reading order
  (a `value` when an entry has one, else derived status, area, solution closure), a changelog pinned to the source
  commits the items declare, the cost the admitted receipts measured (check time and CPU hours by machine class, the
  largest memory peak, declared tokens), the planted suite listed as the positive controls, and a fixed section on what
  the audit cannot show. It states that everything is made by AI and claims no human signature. Same trusted-commit
  and byte-identical rules as the catalog; layout in docs/SPEC.md.

- Provenance and origin of items (ADR 0014). An item may declare `[provenance]` (`kind`: `upstream-reviewed`,
  `lab-authored`, `model-authored` or `own`, with a `source`) and `[origin]` (`agent`, `model`, `context`, `tokens`,
  `from` records, `inspired_by` leads). The card and the catalog (`statement_provenance`, `origin`) show both as
  declared and never as a check; `vl validate` warns on items created after 2026-10-11 that lack them and reports an
  `origin.from` that names no item. `vl import comparator` writes both (`--provenance`, default `lab-authored`, with
  the source URL, commit and file) and the skills tell agents to fill `[origin]`.

- The verdict is a ladder, not one badge (ADR 0013). Cards, briefs, `vl show --json` (`ladder`) and the catalog show
  five separate rows, always in the same order: Lean kernel, second kernel, axioms and trusted base, statement
  fidelity, paper correspondence, each `not assessed` until a receipt or a review assessed it; none implies another.
  A new review kind `vl review ID --kind correspondence --verdict same|stronger|weaker|different [--statement TEXT]`
  records how the formal statement relates to the paper's, per paper statement, optional and never assessed by
  default; it binds the meaning as a fidelity review does (so it goes stale with it), needs
  `--expected-meaning-digest`, and changes no status. Text for people no longer shows `verified` alone: it leads with
  the ladder and shows the derived label as `check passed` (`check passed, stale`); the labels stay in `--json`, the
  catalog's data and the docs. The catalog entries carry `ladder` and `counts.ladder`.

- `vl import comparator DIR` turns a folder of Comparator challenges (`NAME.json` + `NAME.lean`, as in OpenAI's
  omath) into one result item each: the challenge as the trusted target, the config's theorem names and solution
  module as the binding, claim `formal`, provenance (`--source-url`, `--source-commit`, `--source-path`) in the body.
  A dry run unless `--write`; files are created exclusively and all or none, so a re-import of the same version changes nothing and a
  changed challenge or config becomes `<id>-r2` that supersedes the old item, never an overwrite of an admitted target
  (ADR 0007). Axioms, second kernel and definition names of the config that the project does not share are
  said in the item's `limits`; challenges `vl` cannot represent are skipped with the reason. `--catalogue
  formalization.yaml` (schema v0.4, a YAML subset read by the standard library, or its JSON conversion) marks main
  results; `--notes DIR` (the family scope notes, which link papers to challenges) adds one source item per paper,
  cited by its results. `--only`, `--id-prefix` and `--author` narrow and name what is written. Each item is tagged with
  its area, `area:NAME` from the solution module's path (`OAI.Analysis.Foo` is `area:analysis`), which the catalog's area
  filter reads, and its `ref` is `URL@COMMIT:PATH`, which the catalog shows as the item's source; a card or page shows
  `access` only when an item has one.

- `vl export catalog OUT` writes `OUT/catalog.json` (schema `vl.catalog/1`, in docs/SPEC.md) and `OUT/index.html`:
  every item of the trusted ref with its derived status, verdict and receipt ids, kernels and permitted axioms, the
  statement probes (`clean`, `trivial`, `vacuous`, `incomplete`, `skipped`, with notes a probe entry carries beyond
  its known fields), fidelity reviews with authors and dates, provenance (file, first commit, cited sources, the
  body's first paragraph), relations and the command that checks the item again. It reads the trusted commit alone
  (`TrustedRepo`), so a worktree's edits and proposals never reach it, and the same records give the same bytes. The
  page is one file with no external resource (inline style and script under a Content-Security-Policy of their own
  hashes, record text escaped, a `ref` never a link), readable without the script, which only filters
  (ADR 0011). It carries the probe lists of ADR 0010 under the keys the probes write: per theorem `definitions`,
  `totalized` and `boundary` (rows that are not well formed left out), and `probes.boundary_unfinished`; the page lists
  them. `replay` is `command`, `vl replay ID --receipt RID`, and `receipt`; `vl.catalog/1` keeps its name because no
  catalog was published with earlier keys.

- Source `ref` identifiers are searchable and shown with their access level; older derived indexes rebuild.

- Document the integrator trust assumption, asserted relations and finite probe limits. Skills support scoped
  blind read-back, provisional notebooks and appropriate empirical methods without per-edit review ceremony.

- Documentation pass: the module list, SPEC layout and report section, and the AGENTS snippet name `vl rank` and
  `vl explore`; the help of `vl show`, `vl validate` and `vl explore` says what they do now.

- The seven skills, run end to end by a literal reader on a scratch Lean project, now say what they left out: the
  first commit and the trusted build before the first lane, the shape of a frozen item, importing a new proof module
  from a library root, `check passed` as the text of `verified`, `vl rank` and `vl explore`, the ladder and the
  provenance in a fidelity review, `--reserve` of `vl lane exec` being no memory cap, and that any item edit
  re-pins `@rev`. Text only; no format, status or verdict changes.

- The source reader behind `vl inspect`, `vl graph`, `vl value` and `vl order` reads more of ordinary Lean. It indexes `def`, `abbrev` and named `instance` commands beside theorems and lemmas, accepts a body introduced by `where` or by a first equation alternative that opens a line, and treats bracketed text as opaque, so a named argument `(R := R)`, a set builder with a typed binder, a structure instance or a bracketed tactic block no longer makes a signature unsupported. Anonymous constructor brackets are brackets, an escaped name `«…»` is one word, `<|`, `|>`, `<|>`, `||`, `|||` and `::` are operators and a bar that cannot open an alternative is an absolute value; an `=>` after one stays unsupported. A `let`, `have`, `letI` or `haveI` in the stated type keeps its own `:=`: before, the statement was cut at that `:=` and the truncated text entered the packet as a complete signature (104 declarations in one test checkout, 62 of them in its main library). On that checkout the unsupported signatures of theorems and lemmas go from 435 to 4, and 944 definitions, abbreviations and instances are indexed, 23 of them unsupported (21 state no type); a `noncomputable def … where` from the third largest block of the removal view enters the simplify packet. On a Mathlib checkout read as text (8,157 files), the unsupported theorems and lemmas go from 13,575 to 86 of 182,798; 414 statements that were cut at a local definition are now whole or, for 5 whose local definition has a tactic value, unsupported. The reader of trusted targets (`find_theorems`) still sees theorems and lemmas only.

- Add `vl graph LOCK --dump DIR --lean LEAN`: from a built checkout that matches the lock it writes `kernel.tsv.gz` and `ranges.gz`, the inputs of `vl value --deps … --removal …`, and `graph-inputs.json` with their sha256, the Lean version and commit, the targets and the module count. It covers the import closure of the lock's default targets, or of `--build TARGET`. The Lean executable is the one named with `--lean`; it runs the two extractors outside the checkout and without `LEAN_PATH`. The dump writes nothing when a compiled file is missing, was written by another Lean, cannot be read, or does not hold the declarations of the pinned source on their lines, or when the working tree is not the pin. The two extractors move from `scripts/lean/` to `src/verifylab/lean/`, so an installed `vl` has them: the wheel shipped neither. `Ranges.lean` had never run on the toolchain of the gate; the Lean-marked test on the Tiny fixture now runs both.

- `dumpx.lean` reads the proofs of module-system sources. Lean compiles a source that starts with `module` into three files and the public one holds a theorem as an axiom, without its proof; `dumpx.lean` read that one, so on the same test checkout its rows gave `vl value` 83 reached names and 476 lines where the proof cone has 5,307 names and 53,038 lines, with exit code 0. It now reads the part that holds the proofs, as `Ranges.lean` already did, and exits 1 when a file cannot be read. `vl graph --dump` refuses rows that hold no proof: an axiom row for a name the pinned source does not declare as an axiom, a theorem of the source that is another kind of row, or no theorem row at all. `vl graph --deps`, `vl value --deps` and `vl order --deps` refuse a supplied graph in which a theorem or lemma of the pinned source is not a theorem row; before, they answered from it.

- Add `--blocks FILE` and `--against PRIOR.json` to the removal view of `vl value`. `--blocks` sums the located reached lines per block; a line of the file is a block name, a tab and a regular expression on the module name, the first match wins, and the modules that match no block are listed. `--against` reads an earlier `--json` output of the view for the same roots and prints lines before, after and the difference, and the reached names added and removed, per block or, without `--blocks`, per module prefix of three components. In a 25-block validation the block totals match two independently produced tables: 60,666 located lines before, 53,038 after, every block equal.

### Attempts and forecasts

- Scaffold a pre-work record with `vl attempts new`. `vl trial --attempt` links immutable build measurements to
  the Markdown forecast. The build comparison and mathematical outcome remain independently reviewable.


- `vl attempts`: an optional `lane` on a forecast and on a score, for the line of work or the session of one forecaster. `by` stays the model and its effort, or `person:<name>`, and the table keeps one row per `by`: in the field one model signed with nine names and was read as nine forecasters. `vl attempts table --lanes` adds one row per lane under each row of a forecaster, and the JSON always carries them as `intervals_by_lane` and `p_accepted_by_lane`. Files already written are read as before.

- `vl attempts table`: under the `p_accepted` of each forecaster, the resolved forecasts by band of the forecast (five bands of 0.2; a band holds its lower edge, the last one also 1) and by purpose of the attempt, each with the number resolved, the mean forecast, the share accepted and the Brier score; in the JSON, `p_accepted_by_band` and `p_accepted_by_purpose`. In the field one mean, 0.62 forecast against 0.92 accepted over 25 forecasts, mixed a change of route priced at 0.12 with follow-ons priced at 0.7 to 0.9.

- `vl attempts`: `outcome.kept`, for what stays in the repository of an attempt that ended `abandoned` or `rejected`: measured changes with the dimensions of `value`. It resolves no forecast, since `status` is about the claim that was forecast, and `check` refuses it on any other status. In the field an agent wrote such gains as `residual_*` dimensions of its own under `outcome.value`; those files are read as before.

- `vl attempts table`: every measured change is given next to the `base` of its attempt. The text lists what was kept of abandoned and rejected attempts under each base, and with `--measured` the measured changes of every attempt; the JSON has them all as `measured`, one row per number with `file`, `status`, `base`, `kind` (`value` or `kept`) and `dimension`. A line under the tables says that values with different bases are not additive: in the field two patches saved 1,180 lines against their parent and 1,796 together against the original base.

- The convention names two pairs of cost dimensions with different denominators: `build_minutes` and `peak_ram_gb` for the full build (a cold build of the roots), `probe_minutes` and `probe_peak_ram_gb` for one focused invocation (one file elaborated, one test; the largest when several were run). In the field both were written as `build_minutes`. The table has one row per dimension name and pools none.

- The convention and the skill say that an open attempt has no `outcome` key at all: in the field a provisional `not-run` on live candidates made `0 open` hide them, and an `unverified` committed with its forecast was reported as late, as it should. `not-run` stays what it was, for a candidate that was priced and is deliberately not tried.

- Add `vl docs [NAME]`: it prints a document of `docs/` (`vl docs CHEATS`, `vl docs docs/CHEATS.md`, `vl docs adr/0009-clean-clone-replay`) or lists them, and the wheel now carries `docs/`. `vl init --skills DIR --copy` installs the folder of the skill alone, and the skill named `docs/PURPOSE-AND-ATTEMPTS.md`, `docs/CHEATS.md` and three more documents that such a copy does not have. Each text keeps its one home under `docs/`; the skill names the command next to each document, and a test fails when a pointer of the skill leads to nothing from an installed copy.

- Part A of the skill carries, one sentence each inside its steps, what one night of field use taught: report reached lines and the lines of the imported files apart; before pricing a candidate ask which remaining use keeps a shared block alive; a report that types and axioms match is not a Comparator pass; rebuild the downstream closure before taking a value from an exported graph; scan the reached declarations for shared text; when a version keeps fewer statements than the original, compare sizes only for the same statements; in a reading test with masked readers keep the task, the raw answers and the generality apart. The convention says the first of these under its dimensions.

- Show how to use an installed `vl` or invoke it from a VerifyLab checkout while working in a mathematics repository. One-turn briefs can supply the purpose and candidate choice. Reject null outcomes and point a kept unchecked change to `unverified`.

- `vl attempts`: a fifth status, `outcome.status: unverified`, for a change that exists and whose contract was not checked; in `table` it resolves the costs it measured and neither `p_accepted` nor a value. `accepted` now means checked: `outcome.contract` gives `statements` (not null) and a non-empty list of `axioms`; without them `check` reports a problem that names `unverified` when the attempt carries a forecast, and a note when it has `forecasts: []` (3 of the 56 files of a real history, none with a forecast). `not-run` is no longer an outcome for the order read from Git: an option that was priced and not chosen may be recorded together with its forecast. `outcome` accepts an optional `by` (who measured) next to `at`. `table` prints three lines under its tables that say which attempts resolve `p_accepted`, a value and a cost. Two outside agents with only the documents, on a repository where no build was possible, had recorded a kept change as `abandoned` and as `accepted`: anonymized cases are fixtures (`tests/fixtures/attempt-files-usability/`). The convention and part A of the skill now say that without a build a kept change can only end as `unverified`, that scores are optional, that `at` is informative and Git is the witness of order, and what to write in `PURPOSE.md` when the person has delegated the choices and is not available.

- Replace the eight `vl-*` skills by one, `skills/verifylab`, in three parts: A purpose and attempts (the former `vl-method`), B the check of a result of your own (the target from the trusted ref, Comparator, the receipt, the review of meaning), C the traps, with a checklist drawn from docs/CHEATS.md. The text of the other seven skills moves to `skills/verifylab/references/`, one file each. `vl init --skills` installs the one skill; entries named `vl-*` left by an earlier install are not touched, so remove them by hand. The README and `templates/AGENTS-snippet.md` carry the line a group pastes into its own `AGENTS.md`.

- `vl attempts` reads the YAML that people and the usual dumpers write: text over several lines (plain, single- and double-quoted with YAML's escapes, and the block forms `>`, `>-`, `|`, `|-`), lists at the indentation of their key, nested block lists, a value that starts on the line after its key, floats with an exponent. The first files an agent wrote from the convention alone, with a dumper, failed at `unexpected indentation`; nine cases are preserved in synthetic fixtures (`tests/fixtures/attempt-files-real/`). Anchors, tags, a second document and a bracketed list or mapping that does not close on its line are refused with their line. The order check reads a forecast list that is not indented under `forecasts:` and takes the author of an entry from the entry as parsed. The catalogue reader (`vl import comparator`) is unchanged.

- Add the skill `vl-method`: agree a purpose and its resources, forecast every candidate before working, attempt under a ceiling, write the measured outcome and signed scores, and read the result with `vl attempts check|table`. `vl init --skills` installs eight skills.

- Add `vl attempts check|table [DIR]` for the purpose-and-attempts convention (docs/PURPOSE-AND-ATTEMPTS.md): attempt files with `schema: vl.attempt/2` front matter in any repository, without a VerifyLab project. `check` validates each file and reads from Git that a forecast was committed before its outcome and not edited afterwards; `table` puts forecast against outcome per forecaster and dimension, and each agent's scores against a later score by a person (`vl.attempts.table/1`). Scores follow the anchored levels 1, 3, 5, 7, 9 with a reader and a task; an agent's 7 or more carries `forecast: true`; `elegance` and `explanatory` are sentences. The YAML reader gains an opt-in typed mode (numbers, nested flow collections); the catalogue reader is unchanged. `vl simplify` and `vl record` and their `vl.attempt/1` records are unchanged.

- Skills carry the lessons of the first long research campaign: work the plan's load-bearing items first and
  justify an off-plan target against the top open item; search prior art before freezing a target and label
  it `known` or `new` in the item body, so reports count new results and known ports apart; targets are frozen
  by the most capable agent available or the human, cheaper agents prove only frozen targets; witnesses
  exercise the headline claim; referees also check the project's summary row against the card; each record
  names its consumer; pre-registration states sign, thresholds and fixed confounders. Duplicated guidance
  across skills is merged, and the skills note the lane and queue throughput habits (`vl lane done`,
  `vl queue run`). The referee checklist of docs/CHEATS.md carries the same new checks.

- Broaden `vl-understand` to scientific dialogue about goals, relevant results, intuitions and experiments,
  with reciprocal critique, scoped failure evidence and distinct human/agent attribution. Coordinators
  support user-requested dialogue as well as autonomous work.

- Skills support agent-owned rich, sparse or independent context, varied research tasks and dense continuity
  notes. Exploration heuristics remain optional; check/review boundaries retain their contracts. Scope research
  improvement explicitly to the current research project.

### Simplify

- Add a prebuilt-worker trial example that checks toolchain and build inputs, reuses unchanged artifacts
  and recompiles named modules and changed imports in separate overlays.

- `vl simplify` keeps internal symbolic links and leaves unresolved links outside the build context.

- Derive lane preview and integration follow-ups from the merged tree, surface unchecked bound items, and validate generated Lean scaffold names.

- Exploration labels retain their actual endpoint: parent, selected theorem or selected conjunct. Comparison
  rewrites have unknown direction without semantic evidence; a strengthened part is not a strengthened whole.

- `vl explore` counts the waiting variants and writes new ones under one repository lock, so two runs at once cannot
  pass the `[explore]` limits; names the parent's admitted revision, title and areas (not an uncommitted edit);
  builds and parses only the variants within the budget; succeeds when run again with nothing new; uses 10 hex
  characters of the key in a variant's id; and its closing hint no longer sends variants without a proof to
  `vl check`. `vl rank` skips a ledger whose counts are not counts (a crash, or a novelty rate below 0, before).

- `vl explore`: `weaken` and `const` call a variant `stronger` only where the rewritten text is comparisons joined
  by `∧` and `∨`; under a negation, a quantifier or binder notation (`∀ ε > 0`), an implication or a function the
  variant is `than-parent:unknown` (it can be weaker or vacuous) and `vl rank` gives it no strength and the novelty of
  a weaker variant. A statement naming `Other.P` is no longer unfolded with the file's own `P`.

### Removals

- Remove the top-level `vl integrate` and its routes `vl integrate --validate` and `vl integrate --admit`: the usage census of 2026-11-22 found no project that ran them. They were other names for `vl lane integrate`, `vl validate` and `vl admit`, which are unchanged.

- Remove `vl record`: the usage census of 2026-11-22 found no project that ran it, and the attempt files of docs/PURPOSE-AND-ATTEMPTS.md (`vl attempts`) hold what it recorded, a result, a dead road or an open question with its author and cost, together with the forecast. `vl simplify` and its `vl.attempt/1` trial record are unchanged.

- Remove `vl explore` (theorem variants by text operators), `vl rank` with its `vl order --items` route (the heuristic attention and exploration order, and the per-criterion appraisal order `--criterion`), `value.py`, the `[value]` and `[explore]` tables of `vl.toml` and ADR 0016: the usage census of 2026-11-22 found no project that ran them. A `vl.toml` that still has those tables loads, and they are ignored. `vl value` and `vl order` on a pinned Lake checkout are unchanged; appraisals are still recorded, shown and exported.

- Remove `vl cache status|prune` and the store inventory only it read (`lean_build_cache.store_status`): the usage census of 2026-11-22 found no project that ran it, and it deleted nothing. The build stores themselves are unchanged.

- Remove `vl export report` (the audit as `report.md` and `report.html`) with `report.py` and `report_page.py`: the usage census of 2026-11-22 found no project that ran it. `vl export catalog` is unchanged.

### Fixes

- Remove the empty job slices a systemd user manager left behind. On one machine 19,735 of them made each queue
  state read take 2.7 s under the admission lock, so checks started together queued behind each other.

- Count identical attempt forecasts once per outcome and accept explicit empty axiom lists.

- Attribute kernel verdicts to observed kernel outcomes. The catalog reports skipped premise tests and
  states that inhabitation of data binders is unassessed.

- Publish imported receipts whole, verify the content on retry, and make gzip receipt bundles reproducible.

- Include local modules selected outside a Lake library root by its globs. Graph consumers parse the same
  compressed bytes they verified, and every single-root packet keeps the root locator.

- Replay can recover an answer from the original receipt admission as the trusted branch advances.
  Use `--answer-rev` to check another published preservation commit against the receipt.

- Hold the batch concurrency control open until three workers reach the adapter. Use test-sized memory reservations
  in concurrency fixtures.

- Check a single attempt with the same Git chronology rules as a folder check, and report the selected scope.

- Require matching source signatures and available compiler type hashes in value comparisons.

- Check graph row syntax, per-module dependency counts and matching type coverage before publication.

- Publish all three graph dump files atomically so an output error leaves a clean retry.

- Retry lane overlay setup failures briefly while the kernel releases the previous mount.
  Bubblewrap signals setup completion before the command can run, ending retries.

- Publish the installation and first-use docs with a toy proof. Replace private field records with synthetic attempts and replay profiles while keeping the parser and compatibility cases.

- The batch cancellation and queue receipt tests synchronize on the running worker and the waiting ticket, with no wall-clock thresholds for the behavior they check. The service-timeout test uses a finite stop deadline and a child held until cleanup.

- README: the verifier section says that the comparison is Lean FRO's Comparator, called as an external program and, since Lean v4.35.0 (2026-10-12, `lake comparator`), available in the toolchain, and that the verifier adds around it the statements from a trusted Git ref, the statement probes, receipts bound to a revision and the review of meaning.

- Preserve current fidelity dissent and historical replay coverage. Classify kernel panics as tool errors, bind cache keys to the compiler binaries and refuse changed package sources and shared-store deletion.

- Commands (`E1`): added `vl status` (repo-wide actionable triage across pending admissions, stale/failed items,
  missing fidelity, open lanes, commits ahead, and worktree edits), `vl new ID` (item TOML + Lean target scaffold that
  passes `vl validate`), `vl show --snapshot` (blind referee packet from the trusted commit with transitive in-project
  definitions, `meaning_digest`, and `trusted_commit`, stripping prose and earlier reviews), `vl init --skills`
  (idempotent symlink or `--copy` installation of the seven agent skills), and `vl lane integrate NAME` (incoming
  validation, trusted-input refusal unless `--accept-trusted-change`, merge, and check/fidelity triage).

- Replay separates three explicit contracts (`--contract auto|exact|compat|recheck`): `exact` reproduces the full
  recorded basis; `compatibility` replays recognized historical receipt profiles through a justified projection of
  preserved inputs (question from `item_revision`, historical config bindings, legacy `VLTrusted/` paths, explicit
  omission of absent `environment.packages.identity`) without rewriting receipts or inventing coverage; `stronger`
  (`recheck`) records separate stronger-kernel/policy re-check evidence without conflation or auto-promotion.

- Protected Lean checks and newly issued protected receipts now require an external kernel (`nanoda`) regardless of
  `[lean] external_kernels = false`; `rules_digest`, `records`, `status`, `vl receipts import` and `vl replay` enforce
  the minimum uniformly, while historical single-kernel receipts identified strictly by receipt profile (never by date)
  retain their pre-minimum coverage label.

- `fixtures/comparator-known/` adds `opt_param_*`, `auto_param_*` and `out_param_*` mismatch and clean-control
  cases (26 projects total), and `vl check --selftest` runs `sorry`, `extra-axiom`, `weakened-target`,
  `altered-definition` and a clean control against the project's trusted Lean configuration without writing
  receipts (T7).

- `vl cache status` and `vl cache prune` (`--dry-run`, `--older-than DAYS`) inspect and prune the trusted
  build store (`lean-builds/`) and solution build store (`lean-solution-builds/`), keeping every key needed
  to replay any supported admitted receipt on the trusted ref; receipts record `environment.environment_class`
  (digest excluding time, host name, machine name and local paths) and `build_cache.stored_keys` (X2).

- `vl review --emit FILE` and `vl review --import FILE` let a referee without write access to the Git common
  directory seal a review externally and let an integrator import it under the existing `vl-review-write.lock`
  and meaning/human-approval checks (X3).

- Batch checks and `vl check` workers clean up running child processes and only the transient systemd
  services `vl` owns when cancelled or when the batch coordinator exits unexpectedly (X1c).

- Lane Git pointers are protected in the jail and checked against the owning repository before use. Internal
  Git calls disable hooks/fsmonitor. Normal formatted CLI text escapes terminal control bytes; structured JSON
  preserves the original data.

- A set of files written together (`vl export catalog` and `report`, job lists, lane metadata) is put in place under a
  lock on its folder, and a failure while putting it in place puts back the files already replaced: an export no
  longer leaves a new `catalog.json` beside an old `index.html`, nor two exports' files mixed.

- The verdict ladder's axioms row says `uninformative` (`uninformative: true`, in its detail and in the one-line
  header form) when the probes found set-theoretic foundations in the target, as the card's proof section already
  did; its state is unchanged. The catalog page names a related item's status as people read it (`check passed`, not
  `verified`) and its summary line puts the ladder before the status badge. A sealed receipt whose probe fields have another shape (`limit_reached` true, a
  number for a list) no longer makes `vl show` fail.

- Machines without bubblewrap or a systemd user session stop with a message instead of a crash or an unjailed run.
  `vl lane exec` without bubblewrap in a system folder returns `unsupported` and exit 2 (it used to look `bwrap` up in
  the caller's PATH); a protected Lean check on a machine that cannot start its systemd user service returns
  `unsupported` before anything builds. `vl validate --incoming` names an unknown branch, Ctrl-C ends any command with
  `vl: interrupted` (exit 130), and `vl rank --ops` refuses a repeated or not-yet-built operator as `vl explore` does.
  Internally the commands share their atomic writes, JSON text, exit codes, time parsing and `--json` flags; no record,
  receipt, verdict or status changes.

- Receipts from the owner's own machines count (ADR 0015, proposed). machine.toml gets `[machine] name` (recorded in
  every receipt, `environment.machine_policy.machine`) and `trust = "own" | "foreign"` (default own).
  `vl receipts export OUT.tar [--ids FILE]` bundles the protected receipts the trusted ref lacks;
  `vl receipts import FILE --from NAME --trust own|foreign` reads the bundle without extracting it: own receipts
  become ordinary files of `research/evidence/` and count once `vl admit` commits them, provided each verifier tool's
  sha256 is pinned on this machine and the commit asked is here; foreign ones wait in `.vl-cache/foreign/`, shown on
  the card as "foreign, not counted until vl replay", and a matching `vl replay` puts its own receipt in their place in `research/evidence/`.
  A receipt's machine policy decides nothing, so a receipt made on another machine is valid here.

- `vl jobs export OUT.json (--all|--stale|--ids FILE)` writes the protected checks to run as a content-addressed list
  (`vl.jobs/1`): per job the item, the trusted commit, the question digest and the expected reservation and seconds
  from earlier admitted receipts. `vl jobs missing JOBS.json` prints the jobs no admitted receipt answers yet
  (`--out` keeps them as a list) and `vl jobs shard JOBS.json --weights worker-a=3,worker-b=2,worker-c=4 [--by
  reservation|seconds]` writes one ids file per machine, balanced by expected cost, for `vl check --ids`. Plain
  files, no daemon, no network: the distribution is ssh. A list whose text disagrees with its ids or digest is refused.

- Foundations profile. The statement probes also report, per target theorem, the constants of the statement and of
  its in-project definitions whose name says it is about set-theoretic foundations (`ZFSet`, `PSet`, `Cardinal`,
  `Ordinal`, choice principles, ZF, ZFC, consistency and independence words): `foundations` in
  `checked.probes` (an optional key; older receipts have none). Cards say the axiom list is uninformative there
  (Lean always admits `Classical.choice`) and show each constant with why; `vl validate` warns until a fidelity
  review of the current target acknowledges them with `vl review --acknowledge foundations` (new, beside `trivial`),
  whatever its verdict. No status changes. Planted case and control in fixtures/lean-probes; CHEATS S18.

- Third-party Lake packages run only in a disposable sandbox (ADR 0012, proposed). `[lean] trusted_packages` in
  `research/vl.toml` lists the packages whose code may run on a workstation (default: Mathlib and its official
  dependencies, by name and GitHub owner); `machine.toml` gains `[sandbox] class = "workstation" | "disposable"`. On a
  workstation `vl check` (protected or exploratory), `vl lane new` and `vl import` return `unsupported` before
  anything builds when `lake-manifest.json` lists another package; on a disposable machine they go on. Receipts record
  `environment.host` (sandbox class, kernel, architecture, CPU model, cores, RAM) and, when a disposable machine let
  packages in, `environment.third_party_packages`. Lean receipts also record `extra.cpu_seconds`. No verdict, status or digest
  changes.

- `scripts/install-tools.sh --toolchain SPEC` builds and pins the verifier tools for another reviewed Lean toolchain
  (default: the fixture's, `v4.34.0-rc2`; `v4.34.1` added with the same Comparator and lean4export revisions). A
  toolchain without reviewed source pins is refused. `VL_TEST_FIXTURE` runs the Lean tests on a copy of the fixture
  with another `lean-toolchain`; the Mathlib smoke test skips a Mathlib built for a different toolchain.

- Many items without a person in the loop. `vl check --all`, `--stale` or `--ids FILE`, with `-j N`, runs protected
  checks of the selected items (`--all`: a checker applies and the item is on the trusted ref; `--stale`: those whose
  status a check could change; `--ids`: the listed ones), each as a `vl check --json` process that waits in the machine's
  job queue, so the queue bounds what runs at once. It prints one table (item, verdict, seconds, queue wait), also as
  `--json`, and exits 0 when all passed, else 1 if any failed, else 3. `vl check --json` now carries
  `queue_wait_seconds`. `vl admit` is the integrator's step after checks and reviews: on the trusted branch, only
  when `vl validate` passes and the only changes are new files under `research/evidence/` and `research/reviews/`,
  it commits exactly those with a title that names the counts and never pushes. Admission stays the integrator's
  commit on the trusted ref (docs/ARCHITECTURE.md, Trust model; docs/CHEATS.md R28, R29).

- `vl replay ID [--receipt RID]` runs an admitted receipt's protected check again in a fresh clone of the repository
  (git's transport: nothing of the caller's worktree) at the commits the receipt names, with the machine's pinned
  tools and the trusted side built anew, and compares verdict, question digest, every input digest, toolchain, tool
  hashes, Lake package revisions and traces, and kernels: exit 0 on a match, 1 with the differences, 3 when it could
  not compare. A receipt of bytes no commit holds is a mismatch without running. Records go to `.vl-cache/replay/`;
  statuses do not change. Without `--receipt` it replays the receipt that decides the item's status, the one `vl show`
  names (a newer admitted fail of the same inputs, or the vacuous pass, rather than simply the newest pass), and
  compares the statement probes in one direction: a `vacuous_by` or `trivial_by` hit the replay finds and the receipt
  lacks is a difference; a hit only the receipt has is not. D1 is answered as integrator admission plus this replay as the public guarantee
  (ADR 0009); docs/REPLAY.md is the runbook for a stranger.

- The statement probes list what a referee must weigh (ADR 0010), per target theorem in the receipt's
  `checked.probes`: `definitions`, the in-project definitions the statement depends on; `totalized`, its totalized
  operations and those of the definitions (ℕ subtraction, ℕ and ℤ division and modulo, `x / 0` and `0⁻¹`, real and
  integer powers, casts into `Fin`/`ZMod` or of a truncated value, `Real.sqrt`, `Real.log`, `tsum`, integrals,
  `deriv`, `Classical.choose`, `sSup`, `Nat.card`, ...), each with why it may change the meaning; `boundary`, the
  values 0, 1, 2 of its first two ℕ binders where its hypotheses are refuted or it degenerates. Cards show them
  (definitions under MEANING, with those no fidelity review names); `vl validate` warns about each definition no
  admitted fidelity review of the current meaning names, and about the rest until a review finds the target
  faithful. No verdict or status changes; a timeout among the boundary values leaves `probe_run.boundary_unfinished`,
  not "probes incomplete"; the scan stops at one attempt's heartbeats (`limit_reached.totalized`), even on a
  definition whose subterms are shared; it also reads the fields of in-project structures. The `vl-referee` skill adds a source-coverage map and the definition question, by name.

- Reused solution builds, off by default (ADR 0008): with `[check] reuse_solution_builds = true` in
  machine.toml, what Comparator's build of the solution compiled is kept in
  `~/.local/share/verifylab/lean-solution-builds/` under one key that binds every candidate input, the generated
  solution module, the build environment and the tools, and a later protected check of exactly those inputs mounts
  it read-only through an overlay instead of compiling it again. It stays untrusted: the kernels replay it, an entry
  that is not exactly what was stored or that holds a challenge-side module is not mounted, and receipts record
  `environment.solution_build` (key, reused, stored). Verdicts are unchanged; a re-run of the same inputs skips the
  solution build (a candidate chain of 40 modules: a check of 31-44 s took 4 s, its solution
  build 27-37 s took 0.3 s).

- ADRs 0003 to 0011, 0013 and 0014 are accepted (the owner, 10 and 11 October); 0012, 0015 and 0016 are proposed.
  Reused solution builds (ADR 0008) stay off by default; audit projects set `[check] reuse_solution_builds = true` in
  machine.toml. A test keeps the "proposed" marks in the docs in step with the ADRs' status.

- Heavy runs wait in a machine-wide job queue instead of failing (ADR 0003). `vl check` and `vl lane exec` start only
  while a CPU slot is free, the reservations fit `memory_total`, and MemAvailable stays above `[queue] keep_free`
  (default 3G); a check reserves the largest peak of its item's recent receipts, for a first check the largest of its
  checker's in the repository (`--reserve`), and `--max-wait` gives up with exit 2 and no receipt.
  `vl queue run -- CMD` queues any other job in `vl.slice`; `vl queue status` shows the queue. A run's
  `memory_peak_bytes` is the most anonymous memory its unit held, the basis the queue counts usage on; a Lean check's
  adds up the runs that overlap (preflight and challenge build, Comparator and statement probes), also those cut
  off. Each queued job's runs share its own slice in `vl.slice`, and the queue charges every job its own usage.
  Receipts record `extra.queue_wait_seconds`; verdicts and status are unchanged.
  A state folder that cannot be written is a clear error naming it and `XDG_STATE_HOME`, exit 2. A place stays held
  until its job ends, also when `vl queue run` or `vl lane exec` is killed: the job inherits the lock. `vl lane exec`
  takes `--reserve` and `--max-wait` (default: its `--timeout`, or `[queue] max_wait` when shorter), and `lane done`/`close` say "queued or running"
  while it waits. The oldest waiting job's reservation counts against younger jobs, so a big check no longer
  starves behind a stream of small runs. `vl queue status --explain` prints each limit with its source (flag,
  machine.toml, default).

- One rule for heavy runs in the skills and the AGENTS snippet: inside a lane every build and run goes through
  `vl lane exec` (jailed, and it queues itself); outside a lane a heavy run goes through `vl queue run --mem SIZE`,
  where SIZE is both the reservation and the command's hard MemoryMax. The snippet lists `vl queue run|status` and
  `vl check --reserve/--max-wait`.

- `vl validate` warns on a module under `[lean] roots` that `lake build` of its library leaves out: no module the
  library's globs (in the trusted ref's `lakefile.toml`) select imports it. Globs that cover it say nothing.

- A Lean fidelity review recorded before the semantic environment was bound takes the environment of the commit,
  in the trusted commit's history, that added it (and of the commit it says it read): unchanged since, it counts,
  and the card names that commit; changed, it is stale and says so. A shallow clone infers nothing (ADR 0004).

- Faster protected Lean checks, same verdicts. Lake's products of the trusted challenge build are kept in a
  trusted build store (`~/.local/share/verifylab/lean-builds/`, keyed per module by everything that determines
  them, each entry with the sha256 of its files, a damaged one never used) and reused by later checks; only `vl`
  writes it and no jail mounts it (ADR 0005). The dependency preflight
  runs beside the challenge build, and the statement probes beside Comparator (recorded after a pass only; a check that does not pass cancels them).
  Receipts record `environment.build_cache`; `[check] build_cache = false` in machine.toml turns the store off.

- Name systemd's result when it, not the command, set a protected Lean run's exit status: systemd-run exits 1 for
  `oom-kill` or a stop `timeout` whatever the command returned (the "exit 1" of Comparator runs that printed both
  kernels' acceptance, and of `lake build --no-build` printing "All targets up-to-date", in protected receipts made
  before the stop-timeout fix below). Such a Comparator run is `error`, never `pass` or `fail`; an external kernel
  killed by a signal is no longer read as a rejection; a solution build that Lake could not start (ENOMEM) quotes
  Lake's error.

- `vl lane done NAME` marks a finished lane: it stops counting toward `[lanes] max_parallel`, so a new lane can open
  while it waits for integration; its worktree, branch and overlay stay until `vl lane close`, and `vl lane exec`
  refuses it. `--undo` counts it again while a slot is free. `vl lane list` shows open or done (JSON `done`).

- Preserve a guarded command's exit status while reaping surviving children. Services kill children immediately
  and allow one second for reaping, avoiding false stop-timeout failures after successful probe runs.

- Text and brief cards display recorded premise-vacuity skip reasons and the scope of that probe; skipped
  premise tests no longer read as “not refuted”. Status and receipt formats are unchanged. Planted solution-class
  and datum-pinning cases document the remaining semantic limits. Skills assess bounded contributions and
  preserve scoped, source-linked notes before relying on them.

- Enforce the pinned Comparator's outer AF_UNIX restriction for protected Lean through a guarded systemd
  service, preserving resource caps and whole-unit deadlines. Protected Lean now requires a working user manager.

- Protected passing receipts require coherent target provenance, input digests, adapter coverage and environment;
  incomplete historical passes remain on disk but cannot verify a result. Partial execution records stay readable.

- New fidelity writes require `--expected-meaning-digest`; `show --json` exposes the pinned meaning basis and digest.
  Lean reviews bind the stable toolchain, dependency manifest and relevant Lake configuration. Older Lean reviews
  without that environment require renewal; cache placement alone does not invalidate them.

- `vl replay` no longer mounts a stored solution build (`[check] reuse_solution_builds`, ADR 0008): it compiled the
  candidate's sources only when the store lacked their key, so a match could confirm a stored artifact rather than
  the sources git holds. Both sides are now built anew.

- The job queue no longer admits one check per CPU, each building with every CPU (a 16-thread, 12 GB node running 4
  checks of about 16 Lean processes each starved, hit ENOMEM and timed out). `[queue] threads` (default 4, at most
  the CPUs) is what one check may use: Lean and Lake in its jail get LEAN_NUM_THREADS, and its job's slice a CPU
  quota of that many CPUs, which also bounds the builds Comparator sandboxes (Comparator does not pass the variable
  on). The default `[queue] slots`, and so the default `-j` of `vl check --all`, is the CPUs divided by it. The
  receipt's `environment.isolation.threads` records it; `vl queue status --explain` shows it.

- `vl receipts import` reads a bundle compressed with gzip, bzip2 or xz as well as a plain tar (it refused one that
  had been gzipped on the way with "truncated header"), as one stream of at most 512 MiB uncompressed, headers
  included, so a small compressed file can no longer make it hold gigabytes. `vl receipts export OUT.tar.gz` (or
  `.tgz`) writes a gzip bundle instead of a plain tar under a misleading name, a name of another compression is
  refused, and an export that loses the race for its file no longer deletes the file the other process wrote.

- `vl check --all|--stale|--ids` refuses to run under `vl queue run` (exit 2): every check of the batch inherited
  that one admission, so all of them started at once with no slot, reservation or cap of their own.

- A check's runs now share one budget: its job's slice is capped at `memory_max` as well as at its CPUs, so
  Comparator and the statement probes beside it can no longer hold twice `memory_max`. The probes give way there
  (they raise their own oom_score_adj, so the kernel kills them first), and probes that did not finish beside
  Comparator (a timeout before every theorem had a result, a crash, their unit ended by systemd) run again alone
  after a pass; the receipt records that run, with why (`checked.probe_run.rerun_alone`, phase `probes_alone`). A
  vacuous target is thus less often shown `verified` for want of a probe result.

- The job queue refuses at once (exit 2, naming what to lower) a job whose reservation, with `keep_free`, exceeds the
  machine's RAM. It used to wait forever (a 4 GiB machine with the default 2G reservation and 3G floor), and as the
  head of the line hold up every job behind it.

- A run that closes its output and keeps working is now stopped by a cancellation (a failed dependency preflight, the
  end of the check) within a quarter second, not only by its deadline.

- A `vl check` killed while its Comparator service runs (a systemd service, not its child) no longer frees its queue
  slot for another check at once: a job slice that is still populated after its holder died counts as running, with
  what it uses as its reservation, until its runs end.

- `vl check --all|--stale|--ids` shows a check that failed before reading any candidate file (a module of the answer
  is missing; no receipt, exit 3) as `fail`, as `vl check ID` reports it, instead of `error`.

- `vl jobs shard` files name the trusted commit their jobs were cut for, and `vl check --ids` warns when the trusted
  ref is elsewhere (its receipts would then never answer the list's jobs). Jobs that cost nothing (`expected_seconds`
  0) now spread over the machines instead of all landing on the first.

- The build stores (`lean-builds/`, `lean-solution-builds/`) no longer keep the half-written `.tmp-*` folders of a
  save that was killed: the next save removes those older than an hour.

- `vl.slice` gets a low CPU weight (20 against the default 100) along with its memory cap, so the owner's own
  programs stay responsive while checks build.

</details>

## 0.1.0 — 2026-10-04

First public release, licensed under Apache-2.0. Bundled Comparator fixtures retain their upstream licence
and notice.

### Included

- One CLI: `init`, `show`, `find`, `check`, `review`, `validate` and `lane`, with versioned JSON output
  (`vl.output/1`). Questions, results, receipts and reviews are plain records in Git.
- Protected Lean checks against committed targets and definitions using Comparator, pinned verifier tools
  and nanoda; statement probes for triviality and vacuity, command lints and recorded dependency traces.
- Python checks with candidate and trusted evaluator in separate bubblewrap jails.
- Status derived from admitted evidence and current inputs; stale results and meaning reviews are reported
  separately. Fidelity reviews bind the trusted statement, definitions, assumptions and limits.
- Isolated Git worktrees for parallel agents, a rebuildable search index, compact result cards, seven agent
  skills and project/machine templates. The external harness launches agents and selects their context.
- A pinned-source tool installer, installation and contribution guides, security boundaries, planted-defect
  tests, known-answer fixtures and CI configuration.

### Limits

- Reviews are attributed judgements, not formal guarantees of meaning, usefulness or authenticated identity.
- The repository and machine policy remain controlled by their owner; same-user processes are not mutually
  isolated trust principals. Runs without a working systemd user manager are uncapped and record that limit.
- Replay is manual and requires preserved candidate inputs and a reconstructed tool environment. Automatic
  deep replay, cache-free rebuilding and toolchain version floors are planned; `--deep` is not implemented.

See [README.md](README.md), [SPEC.md](docs/SPEC.md) and [SECURITY.md](SECURITY.md) for the current contract.
