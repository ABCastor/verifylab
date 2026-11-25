# ADR 0004 — An older Lean review binds the environment of the commit that added it

Status: accepted, 2026-10-11. Extends ADR 0002 §10.

Context: ADR 0002 §10 binds the stable Lean semantic environment (project and roots, `lean-toolchain`,
`lake-manifest.json` without `packagesDir`, the lakefile's propagated `leanOptions`, `lakefile.lean`) into the
meaning of a fidelity review. Reviews admitted before that binding record the rest of the meaning but not the
environment, so they read `review stale: semantic environment not bound; review again` for ever. The stale warning also appears when all other bindings are unchanged and the historical environment is byte-identical to the current one.

## Decision
For an admitted Lean fidelity review whose `meaning` lacks `semantic_environment`, `vl` asks git for the commits in
the trusted commit's history that added or changed the review file (`git log --diff-filter=AM --no-renames`), and
computes the semantic environment at each of them, with that commit's own `research/vl.toml` project and roots.
When the record names the `trusted_commit` it read, that commit is computed too. When they all agree, the recorded
meaning is completed with that environment and compared with the meaning now like any other: equal, the review
counts, and the card says `semantic environment inferred from commit C, which added it` (`environment_from` in
`--json`); different, it is `review stale: semantic environment changed since review`. When no such commit exists or
can be read, they disagree, or the repository is shallow (its boundary commit would seem to add every file), the
review stays `semantic environment not bound`. No record is rewritten or added, and nothing is stored.

## Why trust is unchanged
- The commit is found by git from the trusted commit's history, never taken from the record. Records are admitted
  by being on the trusted ref, so whoever can put a commit into that history can already admit a fully bound review
  of any meaning: inference grants nothing a well-formed new record could not claim.
- A record can only narrow the inference. Its `trusted_commit`, when present, must exist and agree; it never
  chooses the environment on its own.
- The environment is read as today's is: from git objects through `gitref`, without the caller's `GIT_*`
  variables and with `--no-replace-objects`; a failed read is an error, never an absent file.
- The rest of the meaning is still matched by digest; only the environment is inferred, and only for records that
  predate its binding (`vl review` always writes it now).

## Residual
The commit that added a record stands in for the commit its reviewer read. A record without `trusted_commit`,
written on a branch that changed the environment before the trusted ref had that change, would be credited with an
environment its reviewer did not see; `vl validate --incoming` names such a branch's Lean build-file changes as
trusted inputs. Grafts and a rewritten history are the same-user hole of docs/ARCHITECTURE.md.
