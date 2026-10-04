# Contributing to VerifyLab

Read docs/ARCHITECTURE.md and docs/SPEC.md first; decisions are in docs/adr/, known attacks in docs/CHEATS.md.

- Python standard library only; tests use pytest. A change ships with its tests.
- Every guard has a planted case, next to a clean control, that slips through when the guard is disabled.
- Status is derived, never stored; receipts are written only by `vl check`; receipts and reviews are immutable.
- Two speeds. `scripts/fast` (`uv run pytest -q -m "not lean and not jail"`, seconds) before every commit; the full
  `scripts/gate` (Lean and jail tests included, minutes; it refuses skipped or deselected ones, and drops an
  inherited `PYTEST_ADDOPTS`) before closing a batch of work or pushing. One Lean suite at a time. Never pipe a
  gate: `scripts/gate && git push`; a pipe hides the exit code.
- Commits have a title only.
- Docs change in the same commit as the behaviour they describe; tests/test_doc_drift.py checks the commands,
  flags and status labels they name. History goes to CHANGELOG.md, never into docs or comments.
- What an integrator does by hand more than twice, and mechanically, becomes a command or a flag.
