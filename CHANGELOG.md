# Changelog

## 0.1.0 — 2026-10-04

First public release, licensed under Apache-2.0. Bundled Comparator fixtures retain their upstream licence
and notice.

### Included

- Castor signature footer with light/dark artwork and separate terms in NOTICE; agent contribution
  instructions use AGENTS.md only.

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
