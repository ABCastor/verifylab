---
name: verifylab
description: Use for any formal-mathematics work in a Lean or Lake project, including reading, explaining, studying, changing, creating or checking a theorem, lemma, definition, proof or formalization.
metadata:
  version: "0.2.0"
---

# VerifyLab

Read the linked route for your purpose now. Use `vl` for its first action and follow the route's checks. Read another
route whenever the purpose changes. Keep the brief's stated purpose and confirm it with the researcher only if
the work still depends on a choice they own.

Run `command -v vl` and `vl --version` in the shell that will do the work. This skill uses CLI
0.2.0. If that shell finds an older copy, use the absolute path to the intended installation's `bin/vl` for every
command. From a VerifyLab checkout use `uv run --project /absolute/path/to/verifylab vl --help` and keep that prefix.
For a bundled document `docs/NAME.md`, run `vl docs NAME`. Native Lake projects use their own toolchain and checks.
A VerifyLab project also has `research/vl.toml`.

Substitute the actual Git revision, Lean names and paths in these commands. Every route has a source-only fallback.

| Purpose and route | First action | Artifact |
|---|---|---|
| [Understand](references/understand.md) | `vl order --from . --at HEAD --root NAME --budget 8 --json` | A pinned statement, its assumptions and a reading path. |
| [Explain](references/explain.md) | `vl pin . --at HEAD --lock explain.lock` | A derivation for the named reader with source citations and unresolved steps. |
| [Improve](references/improve.md) | `vl attempts new attempts/simplify.md --purpose improve --claim "Remove a redundant step" --by agent:reader` | A forecast committed before the edit, then the tested gain or obstruction. |
| [Create](references/create.md) | `vl attempts new attempts/transport.md --purpose create --claim "Transport the certificate" --by agent:reader` | A fixed question and a new declaration or a reproducible failed route. |
| [Check](references/check.md) | `vl show ITEM --snapshot` for a VerifyLab item. In native Lake, locate the frozen target and the project's check evidence. | A receipt or native check log and a judgment about the intended statement. |
| [Study first or simplify first](references/study.md) | `vl pin . --at REV --lock study.lock`, then look for an existing compiled graph in the project's evidence index. | Two reasoned rankings at the same pin. |

Use Git to pin and compare revisions and `rg` to find names and repeated source. Use Lean's `#check` and
`#print axioms` for elaborated information, Lake for compilation and native Comparator for an existing challenge.
Record the limits of the evidence. For forecast ordering and outcomes, read [record attempts](references/attempts.md).
If Lean or the worker is unavailable, leave a kept unchecked change `unverified` and list the checks still due.

[Choose a next move](references/orient.md) · [Compare work strategies](references/experiment.md) ·
[Check statement fidelity](references/referee.md)
