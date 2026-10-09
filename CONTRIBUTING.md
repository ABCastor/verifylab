# Contributing to VerifyLab

Start with [README.md](README.md), [ARCHITECTURE.md](docs/ARCHITECTURE.md) and [SPEC.md](docs/SPEC.md).
[AGENTS.md](AGENTS.md) contains the contribution rules for agents; accepted decisions are in
[docs/adr/](docs/adr/), and known attacks and uncovered cases are in [CHEATS.md](docs/CHEATS.md).

## Development setup

The runtime uses Python 3.11 or later and the standard library. Tests use pytest; uv manages the development
environment. The fast suite also needs the system bubblewrap executable to inspect launcher selection,
but does not run jailed checks. From a checkout:

```sh
uv sync --locked
uv run vl --help
scripts/fast
```

The fast suite excludes Lean and jail tests. Follow [INSTALL.md](docs/INSTALL.md) to prepare Linux isolation and
the pinned verifier tools before running the full suite:

```sh
scripts/gate
```

Run one Lean suite at a time. The test process receives a systemd memory cap when a working user manager
is available; a headless runner uses the documented uncapped fallback. The gate refuses failures, missing skip-count evidence, deselected tests and skipped
Lean or jail tests. `VL_GATE_ALLOW_SKIP=1` explicitly accepts skipped Lean/jail tests with a warning; such a run
does not establish that those checks passed. Keep the command's exit code: do not pipe the gate through another
command. Run the fast suite before a commit and the full gate before closing a batch or proposing publication.

## What a change must preserve

- Status is derived from admitted records, never assigned by a caller. Protected checks read the question from
  the trusted ref and the answer from the worktree; exploratory checks never verify a result.
- Receipts are written by `vl check`. Receipts and reviews are immutable: corrections use new records, rather
  than edits to saved evidence.
- Formal validity, statement fidelity, understanding and strategic usefulness are separate facts. Reviews are
  attributed judgements; an author string or approval flag does not authenticate identity.
- Reuse Git, Lake and the verification tools for the jobs they already perform. Add a command for demonstrated
  mechanical repetition; keep research judgement in the skills and with the researcher.
- External agents and their harness select context; the harness launches agents. The toolkit imposes no
  model or harness choice.

A behavior change ships with focused tests and updated documentation. For each guard, plant a defect beside a
clean control and demonstrate that disabling the guard lets the defect through. Check verdicts and exit codes,
including errors and unsupported environments. A second model agreeing with a claim is not independent proof.

Update the affected format, commands, templates and skills in the same change. Record history in
[CHANGELOG.md](CHANGELOG.md); keep current reference documents about current behavior. The documentation drift
suite checks named commands, flags, statuses and configuration keys, but does not prove every prose guarantee.

## Preparing a contribution

Keep changes focused, with a title-only commit message. Explain the concrete problem, resulting behavior and
verification performed, including any skipped checks. Changes to trust boundaries or format contracts need
an explicit account of compatibility and the planted cases that exercise them.

Open a pull request with the change and its verification evidence. VerifyLab is licensed under
[Apache-2.0](LICENSE); preserve the upstream licence and notice for the bundled Comparator fixtures.

For a suspected security defect, follow [SECURITY.md](SECURITY.md) and prepare a minimal reproduction without
private research data.
