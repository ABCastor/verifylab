# ADR 0007 — Comparator challenges are imported as items, never over an admitted target

Status: accepted, 2026-10-11 (owner). Extends ADR 0001 and ADR 0002.

Context: OpenAI's omath repository ships 405 Comparator challenges (a `NAME.json` config and a `NAME.lean` challenge
with `sorry`), the contract `vl` itself checks. Turning each into a result item by hand (target file, theorem list,
solution module, provenance, links to its paper) is mechanical, repeats for every corpus, and was the slowest part of
earlier imports. Two things must not become mechanical: trust in the source's definitions, and the freedom to replace
an admitted target.

Decision: `vl import comparator DIR` writes, per challenge, `research/targets/<id>.lean` (the challenge, byte for
byte) and a result item whose `[lean]` binding is the config's theorem names and solution module. It adds no
front-matter key and no record kind: what marks an item as an import is a set of tags (docs/SPEC.md, "Imported
items"), and provenance is the item's `ref` (`URL@COMMIT:PATH`, which the catalog reads) and, with the sha256 of both files,
the body; the area of the solution module's path is the tag `area:NAME`.

1. Dry run by default; `--write` needs `--source-url` and `--source-commit`. Files are created exclusively and
   all or none (each is written whole and linked into place; a failure removes what the run linked): the importer
   never replaces, edits or deletes a file that was there before it.
2. Idempotence is by content: the tag `import-digest` holds a digest of the challenge file and of the config fields
   that matter. An item whose latest revision has the digest is left alone, whatever commit or date is now given.
3. A changed challenge or config is a new item `<id>-rN` with `supersedes` the previous one. The old item, its target
   and the receipts and reviews bound to them stay as they were; there is no in-place update, so nothing admitted can
   be rewritten by an import.
4. The config's permitted axioms and second kernel are project-wide in `vl` (`research/vl.toml`), not per item. Where
   the project allows more than the config, or does not require nanoda where the config enables it, the item's `limits`
   say so; where the project is stricter, the run warns. The config's `definition_names` are not carried over: they are
   definition holes, whose bodies Comparator does not compare (only their types), while `vl` compares the definitions
   the theorem statements depend on, bodies included, so its check is stricter. A limit says so and the run names the
   holes in a warning.
5. The source's definitions are the target and nothing reviews them: the first limit of every item says the check
   proves the theorem against the challenge's own definitions and does not establish that they express the paper. A
   challenge `vl` cannot represent (no `theorem_names`, a theorem whose body is not `sorry`, a target the strict
   parser cannot read) is skipped with its reason, not adapted.
6. Optional sources: `--notes` reads the source's family scope notes, which link each paper and the challenges that
   formalize it, and writes one `source` item per paper (access `citation-only`: the import reads no paper) that the
   results `cite`. `--catalogue` reads `formalization.yaml` (schema v0.4, or its JSON conversion; any other version is
   refused) with a YAML subset parser that refuses what it does not know; the catalogue marks main results and gives
   titles but, in v0.4, does not link challenges to papers.

Consequences: a corpus of hundreds of challenges is one command and one review of its output; the integrator still
commits the targets, runs `vl check` and writes reviews. The `created` date of a new item is the day of the import.
Catalogue and notes are read at the first import of a challenge: a later run with `--notes` does not add `cites` to an
item that is already there. A challenge that returns to an older version is a new revision, not a reuse of the old
item.
