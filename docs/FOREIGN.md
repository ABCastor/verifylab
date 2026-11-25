# Reading a Lean checkout

## Try the Tiny project

From the VerifyLab checkout, copy the fixture into a new directory and create the pinned checkout used in the README:

```sh
cp -R fixtures/foreign-lake Tiny
git -C Tiny init -q
git -C Tiny add .
git -C Tiny -c user.name=Fixture -c user.email=fixture@example.org commit -qm 'Pin Tiny'
vl pin Tiny --at HEAD
vl inspect vl.lock Tiny.conclusion --file Tiny.lean
vl order vl.lock --root Tiny.conclusion --budget 2
```

## Pin and inspect

Run `vl pin PROJECT --at REV --lock vl.lock` from the analysis directory. The checkout must already hold the revision and have the Lake project at its Git root. For a nested project,
use the native Lake commands from that project directory. The default revision is HEAD. The lock identifies the local checkout relative to itself, the full commit, toolchain, manifest digest, dependency coordinates, default TOML targets and source count. Repeat the command at the same pin or choose another lock for a different revision. A path dependency records its location. Dependencies outside the pinned tree require separate provenance. A declared default target identifies a possible rebuild. Cache availability and rebuild time need a measured build.

When relocating a lock, preserve its relative path to the checkout or repin at the original commit.
If the checkout has gone missing, run the printed recovery command from the intended Git checkout root.
It writes a new lock beside the moved one.

`vl inspect vl.lock NAME` reads the theorem, lemma, definition, abbreviation or named instance as written at the commit and lists its explicit binders. Exact names and their source indicators use the pinned Lake library source directories, roots and globs on static TOML layouts. The output counts Lean files outside that scope. If there is no supported match or ambiguity in that layout, the lookup tries all pinned Lean files. When the declaration name is unknown, use `vl inspect LOCK --find TEXT` to search across the pin. Supply `--file PATH` to restrict source reading to one pinned file, including an archived copy. Use `--json` for channel details and omissions. The JSON includes source indicators for axiom declarations, build code and dependency coordinates, alongside the signature and declared toolchain. Source scanning does not elaborate types, resolve macros or establish which axioms the proof used. Some indicators come from files outside the theorem closure.

`vl inspect vl.lock --find conclusion --json` returns ordinary matches and an `ambiguous` array with
qualified names and candidate files. Choose the intended file and qualified name before running
`vl order` again. Capture the command's own status before reading its JSON. For example:

```sh
if vl order vl.lock --root Tiny.conclusion --file Tiny.lean --json > order.json; then
    python -m json.tool order.json
else
    exit "$?"
fi
```

## Graph, value and reading packets

Use `vl graph LOCK --build TARGET` before budgeting a rebuild. For TOML library targets it counts the in-project source closure and names missing files in the conventional local Lake output folder. A present `.olean` file (Lean’s compiled module) still needs provenance and freshness checks. This estimate requires a static TOML library or module target. For a dynamic lakefile or an executable target,
build it with the project's usual Lake command.

`vl graph LOCK --modules` shows literal import headers by file. The default declaration graph resolves tokens to indexed names or unique short names. Name collisions, notation, compiler-generated declarations, omitted definitions and indirect use limit coverage. `--deps FILE` accepts plain or gzip TSV from the extractor `dumpx.lean`, which ships in the package and which `vl graph LOCK --dump` runs. `vl graph` reports direct edges between indexed declarations. For `vl value` and `vl order`, traversal can also pass through generated constants whose rows identify local pinned module paths. The output still shows indexed source declarations, with depth and demand computed on the traversed rows. The graph metadata counts reached rows, indexed declarations and unresolved frontier references. When the compiled module uniquely selects one supported declaration among duplicate names, use it as a location hint. A missing row has unknown outgoing edges. A graph in which a theorem or lemma of the pinned source is not a theorem row is refused by `vl graph`, `vl value` and `vl order`: a compiled public interface can contain a theorem’s type without its proof term, producing a graph that stops at the statement. The CLI records the supplied file digest; establish the source/artifact relationship separately.

`vl graph LOCK --dump DIR --lean LEAN` writes the two inputs of the removal view from a built checkout: `DIR/kernel.tsv.gz` for `--deps`, `DIR/ranges.gz` for `--removal`, and `DIR/graph-inputs.json` with the sha256 of both, the Lean version and commit that read them, the targets and the module count. It follows the local pinned import closure of every default target of the lock, or of the one named with `--build TARGET`. Lake libraries select their starting modules by glob within their source directories. Local imports include modules covered by a root or a glob, and a covered import without pinned source stops extraction. `LEAN` is the path of the Lean executable of the checkout's toolchain, for example what `elan which lean` prints inside the checkout. VerifyLab looks none up and starts none on its own. That executable runs the two extractors from a temporary directory, without `LEAN_PATH`: they read compiled module files and import nothing from the checkout. Reading a compiled file made by someone else is still not a sandbox, so run the dump where you would run that build.

The dump requires every module in the closure to have a readable artifact written by `LEAN` in `.lake/build/lib/lean`. Sources and Lake configuration must match the pin. It checks dependency and range row syntax, each module's closing row and declared dependency count, and matching sets of constants across the dependency and type rows. It rejects proofless output, including axiom rows with no source axiom. Each indexed declaration must have a compiled constant and a range that names it and covers its source line. Indexed theorems must have theorem rows. Both extractors read the proof data for module-system files and the single compiled artifact for legacy modules. Automatic dump needs a static TOML library layout with string globs. With dynamic layouts or executable
targets, use a source-only packet or supply separately extracted compiler TSVs with `--deps` and `--removal`.
Protected VerifyLab checks use root-relative module paths under `[lean] project`; a library-specific
`srcDir` is supported by the foreign source reader and is outside the protected adapter's layout contract.

The dump reports structural checks. Run `lake build --no-build TARGET` under your build runner to verify artifact freshness at the pin. Same-line proof edits can pass the dump's consistency checks with stale artifacts. Keep the artifacts fixed from the freshness check through both extraction reads. Use a new or empty `DIR`. The three files appear together after validation and writing succeed. A failed write leaves the destination ready for a retry.

`vl value LOCK --root NAME` reports shortest distance, reached incoming users, source lines, statement characters and a demand score. Each of 60 iterations adds 0.5 at the root and distributes half of each previous score across its direct dependencies. A declaration with no outgoing edge passes along none of its score. Optional `--judgments FILE` accepts local JSON with schema `vl.judgments/1`, the full `commit`, `model`, `cost_usd` and `rows` keyed by declaration. Each entry carries the exact `statement_sha256` and a `relevance` from 0 to 1. The agent supplies the judgments and their cost. No provider call runs in VerifyLab.

`vl value LOCK --root A --root B --deps kernel.tsv.gz --removal ranges.gz` is the opt-in removal view. Every requested root must have a unique supported source declaration, a local row in the supplied graph and no conflicting reached row; otherwise the command fails. The ranges file is the `R` rows of a compiler declaration-range dump (`Ranges.lean`, written by the same `--dump`) from the same compiled checkout. Ranges that fall outside the pinned file, or that miss the start line of the indexed declaration they name, fail as stale. Overlapping ranges form one editing unit and generated names inherit their parent's command. `exclusive_source_lines` counts the located command lines whose every route from the retained roots passes through the row; shared lines are charged once and nested rows are not additive. A name with no range counts as a name with zero lines. Without `--removal` the line columns are null. The JSON carries schema `vl.removal/1` metadata (root names and statement identities, per-root sizes, union and coverage counts, range digest, boundary) beside the rows; plain text prints the same rows as a flat TSV. The same JSON lists `elaboration_only` nominations before any pruning: simp-tagged and instance declarations outside the kernel cone, every notation, syntax, parser, option and linter registration, and source names in reached proofs with no kernel edge. The kernel stores elaborated terms, so a bare `simp` lemma, an instance found by search or a notation can be missing from every proof term and still be required to rebuild. Keep them or test their removal with a cold build. A single root without `--removal` keeps the output described above.

`vl value LOCK --root A --root B --deps kernel.tsv.gz --removal ranges.gz --blocks blocks.tsv` sums the located reached lines per block. Each line of the file is a block name, a tab and a regular expression searched in the module name, to be anchored with `^` and `$` where needed. A module belongs to the first line that matches, several lines may carry the same name, and blank lines and lines that start with `#` are skipped. A command is counted once, so the blocks and the modules that match no block, which are listed, add up to the union of located lines. `--against PRIOR.json` takes an earlier `--json` output of this view for matching root fingerprints and prints, per block, the lines before, the lines after, their difference, and how many reached names came and went. Without `--blocks` the groups use up to three components of the parent module prefix. For example, `A.B.C` is grouped under `A.B`; a one-component module keeps its own name. Each root must have the same pinned source signature hash and, when the range files supply it, the same compiler type hash. When both range exports predate type rows, the comparison checks only the literal source signatures. Both hashes check syntax: a definition used in the type or its dependency environment can change without changing either fingerprint. For a proof-preservation claim, check the new proof with Comparator against a frozen target and its definitions. Regenerate prior JSON without root statement identities at its original pin. The comparison requires an explicit source span or two null bounds on each row. A compiler span with an exclusive end at its start can cover zero lines. Invalid bounds and rows that do not add up to the prior's totals are refused. With either flag plain text prints the table in place of the rows; `--json` keeps the rows and adds `blocks` and `against`.

`vl order LOCK --root NAME --budget 12 --bytes 8192` orders reachable declarations by demand. With `--aim simplify` it orders by source length. Ties use qualified names. `vl order LOCK --root A --root B --deps kernel.tsv.gz --removal ranges.gz --aim simplify --rank-by removal` opts into the removal order: descending exclusive lines, then fewer reachable users, then name; without ranges it uses exclusive kernel names and says so. It is a hypothesis generator; source length stays the default. Newlines in a signature are normalized to LF in the packet; `statement_sha256` remains the hash of the original source text and `emitted_sha256` hashes the emitted bytes. The plain-text packet contains only names and complete supported signatures. `--json` separates that same model input from source locations, byte count, hashes, ranking rationale and omissions. Empty input means the budget or contract admitted no rows. A score can change what gets read. Only the evidence machinery derives verification status.

A dump's `graph-inputs.json` can replace both artifact paths with `--graph MANIFEST`. The reader checks the pin,
toolchain and both artifact sizes and digests. It parses the same bytes it checked. It validates
`module_paths` against the declared targets' pinned import closure and `input_sha256` against Git. Those paths
scope the source index, including range and signature lookup. Older manifests without either field use the
full pinned source index. Compare the declared targets with the coverage required for your question. A fresh
manifest also lists the observed compiled files and their `olean_sha256` digests, including present split parts.
These digests identify the files seen by the extractor's preflight. With `vl value LOCK --root NAME --graph MANIFEST` this
selects the removal view. With `vl order LOCK --root NAME --graph MANIFEST` it supplies kernel dependencies;
`--aim simplify --rank-by removal` also uses the ranges. For a one-time read use
`vl order --from PROJECT --at REV --root NAME`. It pins the same committed input in memory. Source-only
`vl order` and single-root `vl value` read the pinned TOML libraries' source directories, module roots and globs
when that layout can be read statically. Otherwise they scan the pinned Lean files. This keeps copies outside
the library layout from shadowing the active declarations. Read one source root per call. If candidates within
scope share a name, choose the intended path from the diagnostic and run
`vl order --from PROJECT --at REV --root NAME --file PATH`. This limits both the signature search and the
approximate dependencies to that file. `vl inspect LOCK --find TEXT` can locate declarations outside the library
layout. In order JSON, `source_omission_count` counts source index omissions and `omitted_counts` counts skipped
packet candidates by reason. Each diagnostic list defaults to a sample of up to 10 entries. Add
`--full-omissions` to recover both complete lists. Single-root packets give `root_source` even if the
statement contains comments or falls outside the packet budget. Use that locator to read the root in Git.

### Reuse an existing graph

Look for a documented graph for the required targets before scheduling extraction.
For a matching VerifyLab manifest, use `--graph MANIFEST`. Check its target coverage against the question.
For an archived raw dump, verify both artifact hashes against the archive and identify the Lean version and
commit, extractor, targets, excluded modules and manifest of the compiled sources. Compare the manifest with
the pinned bytes of those sources and check the toolchain, Lake configuration and dependency revisions. With
matching inputs, use `vl value LOCK --root A --root B --deps kernel.tsv.gz --removal ranges.gz --study` and keep
that provenance with the output. The raw-input flags record artifact digests. Source correspondence remains a
caller-supplied check.

If the dumps are missing, look for a prepared build on the approved worker before scheduling a rebuild.
Pin the intended source from a writable directory on that worker. Use the operator's SSH settings to reach it.
A prepared build may be read-only and may have no Git metadata. Its sources and Lake inputs must match
the Git pin byte for byte within the target closure. Point to that tree separately:

```sh
vl pin "$PINNED_CHECKOUT" --at "$REV" --lock study.lock
flock "$LEAN_LOCK" systemd-run --user --scope -p MemoryMax=11G -p MemorySwapMax=0 -- \
  vl graph study.lock --dump inputs --lean "$LEAN" --built-tree "$PREPARED"
```

`PINNED_CHECKOUT` holds the Git revision, `PREPARED` holds the checked sources and the default Lake build
directory, and `LEAN` names the executable for the prepared build. Omit `--built-tree` when the Git checkout
also holds the artifacts. Put the lock, dump output and temporary files outside the prepared build. Keep the
worker lock and resource limits appropriate to the machine. Carry both dumps with the manifest and reuse
them against a local pin for the same commit. Build only when the available artifacts lack coverage, freshness
or provenance for that pin.

`vl value LOCK --root NAME --graph MANIFEST --study` runs two rankings over the reached local declarations.
Pass multiple roots as repeated `--root` flags and set `--limit` for rows per ranking. Requested roots are
the starting statements and are excluded from both rankings. Study order sorts by roots that reach the name,
direct reached users, then nearest-root distance. Simplification order sorts by exclusive located lines, source
length and repeated lines. Repeated lines count occurrences of the same nonblank, whitespace-normalized
source line within the reached command spans, skipping comment markers and punctuation-only lines. Missing
locations leave repetition unknown. Qualified names break ties. These are reading and edit leads. Use
`--blocks FILE` for block totals and to label declarations in the two rankings.

For a chosen shortlist, run `vl value LOCK --root A --root B --graph MANIFEST --select NAME --json`.
Repeat `--select` in reading order. Each row retains the joint graph's caller and exclusive counts,
the pinned path and compiler span, and a distance from each root. An unreached root's distance is null.
Use qualified names from the removal or study output. The report retains the union totals and emits only
the selected rows. Plain text returns a TSV with `name`, `root_distances`, `reachable_users`,
`exclusive_source_lines`, `path`, `first_line` and `last_line`. For scripts read `rows` in the JSON,
where `root_distances` maps each requested root to a distance or null. Compiler spans identify editing
units in the pinned source. To cite a different source edition, keep a checked mapping that names both
revisions and their file/line anchors for each declaration. Use the full view for `--against` comparisons.

## Try a simplification

`vl trial LOCK --patch CHANGE.diff --targets Tiny --runner /path/to/build --by agent:reader --model none --out trial.json` compares the named builds. Give it a unified Git diff and a new output path. It reconstructs two copies of the locked commit from regular Git blobs, applies the patch to one, and tests the base first. A symbolic link that resolves inside the pinned tree is recreated as the same relative link. An escaping, dangling or looping link is left out, and listed under `materialized.omitted_links`, only when it sits outside the build context (Lean sources, Lake configuration and non-root library source directories); inside it the trial refuses. Submodules are refused. If the baseline fails, the comparison remains open. A patch or patched-build failure records a dead road. A successful trial records the patch, target list, runner digest, changed-line counts, wall time and each build's exit code, log digest and final 8 KiB of output.

To link the run to a forecast use `vl trial LOCK --attempt FILE` in place of `--out`. The Markdown attempt must
be open, complete and committed at HEAD. Its base commit must match the lock. Its sibling `FILE.trial.json`
(replacing the Markdown extension) holds the measurements, attempt ID, forecast commit and forecast fingerprint. An existing
record is never replaced. Use `--by` to name the runner's operator if they differ from the sole forecaster.

For a proof edit in an existing module, add `--downstream` to plan the rebuild from the base and patched
import headers. The scope is every pinned module covered by the static TOML libraries' roots or globs,
including optional libraries and modules with no cached object, plus each module named in `--targets`.
List standalone clients by module name under the package source directory, even when Lake's default targets
omit them. This mode accepts edits to existing sources in that scope. For source moves, build configuration
changes or a dynamic Lake layout, supply the project's own explicit target list and coverage evidence.
The trial adds a `downstream` record with the edited, planned, base-passed, patch-passed and missing module
lists. A phase's successful runner exit confirms its whole target list. A failed phase leaves its entire list
unconfirmed. Completion requires two successful builds. Standalone files outside the library scope and
outside the named clients require a separate check.

Before booking a worker, run
`vl trial LOCK --patch CHANGE.diff --downstream --targets Tiny Checks.Client --plan`.
This applies the patch in scratch space and prints every exact module the trial would pass to the runner.
It starts no runner and writes no attempt. Use `--json` for the pin, patch digest and the requested, edited
and planned module lists. Run the trial with the same lock, patch and targets after reviewing the plan.

The runner examples live in the VerifyLab source checkout that matches the installed CLI. For a
preprovisioned disposable SSH worker, set `VL_BUILD_HOST` to its SSH destination and pass
`--runner "$VERIFYLAB/scripts/runners/ssh-lake-build"` with `VERIFYLAB` set to that checkout. It streams the prepared tree, omits Git metadata, builds the named targets and removes its temporary directory on normal exit. The worker must supply the toolchain and any dependencies or allowed cache access. SSH batch authentication is configured by the operator.
Set `VL_BUILD_SSH_CONFIG` to an SSH configuration file when the worker needs an explicit `ssh -F` path.
The SSH runner receives the same exact module list in `--downstream` mode. Exit 125 signals an infrastructure
problem and keeps the attempt open.

For a trusted TOML project with an existing compatible build, run the whole trial on the authorized worker.
From the caller, use the operator's designated SSH configuration, for example
`ssh -F "$HOME/.ssh/config" -o BatchMode=yes WORKER`. In your isolated workspace on the worker, install VerifyLab
and make its Python available on PATH. Check the worker's shared Lean lock and set `VL_BUILT_TREE` to the prepared
build and `VL_LEAN` to the absolute executable for its pinned toolchain. Set `VERIFYLAB` to the VerifyLab checkout:

```sh
test -f "$LEAN_LOCK"
flock "$LEAN_LOCK" systemd-run --user --scope -p MemoryMax=11G -p MemorySwapMax=0 -- \
  vl trial vl.lock --patch change.diff --downstream --targets Tiny --runner "$VERIFYLAB/scripts/runners/lean-overlay" --attempt attempts/change.md
```

The example runner takes explicitly named modules and runs Lean on their local source closure. It checks the
toolchain and exact Lake input bytes and builds
both named targets in separate overlays. Dependencies with unchanged source and unchanged local dependencies
can reuse prepared artifacts. The runner links these into the overlay and writes rebuilt files there. VerifyLab
records timing without a worker timing executable. The prepared package artifacts and their provenance are
the operator's responsibility. Use the project's own runner for custom Lake build targets, generators or
native build steps. The overlay exits 125 on nonempty Lake `moreLeanArgs` or `weakLeanArgs`; use
the Lake runner when the package or library needs them. This example executes trusted source on the worker in a resource scope.

The explicit trusted runner receives the absolute source directory followed by the target arguments. It must wait for completion and propagate the compiler result. Return 125 for setup failures or interruptions, including signals
reported by a shell as 128 plus the signal number. A remote backend transfers that tree to a disposable worker, provisions the locked toolchain and dependency revisions, runs Lake there, and streams the result back. The field job applied a patch to a pristine archive and built only the named targets. This CLI keeps that base/patch comparison independent of the hosting provider. A checkout from someone else can execute arbitrary elaborators and Lake scripts. Use the runner to isolate those builds. The source checkout includes `scripts/runners/lake-build`. A minimal equivalent for an owned fixture on an isolated worker is:

```sh
#!/bin/sh
set -eu
cd "$1"
shift
exec lake build "$@"
```

For `--downstream`, every target argument names an exact module. The runner receives
`VL_TRIAL_EXACT_MODULES=1`. The bundled Lake runners select Lake's `+MODULE` targets in this mode.
If Lake cannot address a standalone client, use the overlay or a project runner that can check it.

A successful build answers whether the named targets built. Compare the old and new signatures and permitted
axioms before claiming a mathematical simplification. A project-native check can travel with its pinned build
inputs, their hashes, toolchain, exact targets, statement and axiom output, and exit statuses. When reusing it for
a documentation edit, compare its complete build-input set with the edited tree, including auxiliary files used
at build time. Record the check's original scope and compare that scope with the new claim.
Pass a measured total with `--cost-usd` when available. Unknown cost is stored as null. The runner controls
scheduling, cancellation, dependency provisioning and the resource cap.

The trial is an immutable JSON record at the pin, with its outcome (result, dead-road or open), time, attribution and cost, separate from receipts and reviews. A dead road, a useful result or an open question that no trial measured goes into an attempt file, with what was forecast and what was found: [the convention](PURPOSE-AND-ATTEMPTS.md).

## Source reader coverage

The reader indexes named `theorem`, `lemma`, `def`, `abbrev` and `instance` commands that start in column zero and state their type. The statement runs to the first `:=` or `where` outside every bracket after the colon of the stated type, or to an equation alternative `| pattern =>` that opens a line. Bracketed text, the anonymous constructor brackets `⟨ ⟩` included, is kept verbatim, so a named argument such as `f (R := R) x`, a set builder, a structure instance, a default value and a bracketed tactic block are read. A `let`, `have`, `letI` or `haveI` in the stated type keeps its own `:=`. The source-only index has a limited Lean grammar. Escaped declaration names, comment or string delimiters
inside an escaped identifier, compound namespace partial closes, nested attribute arguments and column-zero
equation arms can be omitted. Open the pinned source or ask Lean for the elaborated declaration. `<|`, `|>`, `<|>`, `||`, `|||` and `::` are operators, and a bar inside a line, a bar right after the colon of the stated type and a pair of bars with no `=>` between them are an absolute value. The reader lists as an unsupported signature, and keeps out of every packet, what it cannot decide from the text: `by`, `do`, `match` or `calc` outside brackets in the stated type; an `=>` outside brackets after an absolute value, where the bars could be alternatives, which covers an alternative with two patterns and alternatives written on one line; an alternative after a `fun` or a local definition; a `sorry`, a backquote or an escaped name that does not close on its line; brackets that do not match; a definition with no stated type; and a command with a continuation line in column zero. An anonymous instance, a `structure`, a `class` and an `inductive` are not indexed, and a private declaration is indexed under its source name: a kernel graph names it differently, so `--deps` does not connect it.

The source reader accepts sections that extend through the end of a file and recognizes modifiers such as `@[expose] public section`.

## Mapping archived ranges

If the reading checkout differs only in reviewed license headers or line endings, pin a separate checkout of
the exact archived compiler inputs and run the graph analysis on that pin. Verify every covered file against
the archive before mapping the results to the reading checkout. Normalize only the documented transformations,
then compare the remaining text byte-for-byte. For a replaced leading header, add the difference in header line
counts to each compiler line in the unchanged body. CRLF to LF preserves line numbers. Retain both pins, the
per-file offset and any exceptions. A body edit or missing source leaves the affected mapping unestablished.
Keep the raw ranges and their digests with the compiler snapshot and cite mapped locations in the reading
snapshot. The manifest still requires its exact commit.
