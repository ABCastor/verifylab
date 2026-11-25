# Choose what a human should study or improve first

Pin the named revision with `vl pin . --at REV --lock study.lock` and look in the project's reading guide or evidence index for existing graph inputs.
Check the source manifest, toolchain, target coverage and artifact digests before scheduling any extraction.
With a matching manifest run:

```sh
vl value study.lock --root NAME --graph MANIFEST --study
```

Substitute a qualified Lean constant for `NAME` and the graph manifest path for `MANIFEST`. Add one `--root` per conclusion.
Without a dump, look for a prepared build on the approved worker. Pin the same Git revision there from a
writable directory. Set `LEAN` to the prepared build's Lean executable. Under the worker's build lock and
resource scope, run `vl graph study.lock --dump inputs --lean "$LEAN" --built-tree "$PREPARED"`.
`PREPARED` can be a read-only tree without Git. Omit `--built-tree` when the pinned checkout holds the build.
Keep `inputs/graph-inputs.json` with both dumps and reuse them at the same local Git pin. If a moved lock
can no longer find its checkout, use the printed repin command from the intended checkout root.
The manifest bundles the equivalent of the raw `--deps` and `--removal` inputs. See `vl docs FOREIGN` for
artifact freshness and Lake layout limits. Read their statements first. In **Study first**, use the number of roots reached
and direct callers to find the shared steps. Trace a path from each root and connect the common steps in the order
their mathematical prerequisites introduce them. For a chosen shortlist, rerun the same multi-root value view
with one `--select NAME` per declaration in place of `--study`. This gives each root's distance, joint caller
and exclusive counts, and source anchors.
In **Simplify first**, use exclusive reached lines, command length and repeated lines to find candidates.
Open the proof bodies with Git and find their callers with `rg`. Compare the obligations and assumptions and
name the clients that must still work. Both rankings exclude the root statements.

Use `vl value study.lock --root NAME --graph inputs/graph-inputs.json --blocks blocks.tsv` for block totals.
A row of the TSV is a block name, a tab and a regex on the module. With `--study` the block is a label on each row.
Read the signatures with `vl order study.lock --root NAME --graph inputs/graph-inputs.json --budget 8`.
For the removal order use
`vl order study.lock --root NAME --graph inputs/graph-inputs.json --aim simplify --rank-by removal`.
Reached lines and exclusive lines price different changes. Imports, tactics, instances and notation can still
be needed at elaboration time. For existing raw dumps, or a reading copy with reviewed header differences,
follow the reuse procedure in `vl docs FOREIGN`.

Without compiled inputs or a worker use `vl order --from . --at HEAD --root NAME --budget 8 --json`
and `vl value study.lock --root NAME` for approximate source references and length. Read each root
separately. If a name is ambiguous, select the intended pinned path using `--file`. Leave exclusive costs unknown.
Cite the dependency chain you actually read.

Success: a shared short bridge ranks high for study, while a longer private branch with more exclusive lines ranks
first for simplification. If the manifest belongs to another commit, establish correspondence using the archive
or use approximate source ordering and label the missing compiler evidence.
