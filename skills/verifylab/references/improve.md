# Improve a theorem

Reference for the `verifylab` skill ([SKILL.md](../SKILL.md)). Improve a theorem, proof or reusable interface in a pinned development and check the specific gain.

Start with `vl attempts new attempts/simplify.md --purpose improve --claim "Remove a redundant step" --by agent:reader`. Complete the forecast and commit it before editing. For a foreign Lake checkout, read the attempt's `base` from the scaffold and run `vl pin PROJECT --at BASE --lock base.lock` and `vl inspect base.lock NAME`. Use this lock for the original revision even after the forecast commit moves HEAD. Read the proof at its source location, including definitions, section variables and instances. Inspect a public statement against its cited source if its intended meaning is in doubt. Keep the original revision for comparison.

If the researcher named an aim, follow it. If asked simply to improve the theorem, find one opportunity in the argument itself. Where is the strongest hypothesis used? When can the decisive inequality be an equality? What obligation recurs in every case? What fact does the next theorem actually consume? Pick a question worth answering and state what observation would change your mind. For a speculative strengthening, say what the extra case reveals about the argument. It can be worthwhile before it has an external caller.

## Choose a move and test it on a small case

| Move | What to try | What would establish the gain |
|---|---|---|
| Simplify | Expose the invariant, replace a repeated calculation by a family argument, reconstruct a digested argument, or shorten a local proof. | Fix the intended claim. Show which step or prerequisite disappeared. For cost claims include extracted helpers, generated certificates, imports and the relevant build conditions. |
| Generalise or strengthen | Try a boundary example, remove a premise, sharpen a bound or characterize equality. Attribute a supplied intuition. | Prove how the new result recovers the old one, then exhibit the extra case or stronger conclusion. Keep incomparable versions with their trade-offs. |
| De-import | Try the smallest client that should work with a lighter dependency. | Check the old dependency is absent transitively and rebuild the client. Adopting a maintained library result can instead justify a larger import. |
| Split a long proof | Extract named intermediate lemmas with focused conclusions. Keep them private when their statements depend on private imports. | Name the claims to preserve. Compare existing signatures and compiled types. Record reached declaration spans and rebuild affected importers. An incremental build may skip importers after a private or docstring-only change. |
| Merge proofs | Extract the particular step both proofs use. Try the less obvious caller first. | Both clients build through the shared lemma. Preserve independent routes when their assumptions, explanations or uses differ. |
| Compare blocks | Pin each project separately. Compare definitions, hypotheses and the output clauses a caller consumes. | Prove transport in the direction the use needs. Claim equivalence only with both directions. Report a missing clause, counterexample or toolchain obstacle distinctly. |
| Replace a block | Write a thin adapter in a common integration environment. | Rebuild the actual caller with the old provider and stale build artifacts excluded. Record provider pins, target, assumptions and lost clauses. A standalone adapter is partial evidence. |
| Explain | Identify the decisive inference, how to reconstruct it and where it fails. Link each step to the proof. | Give a reader an account they can use. When transfer matters, let a fresh reader try a related example. If nobody tried it, report comprehension as unmeasured. |

## Use the tools that answer the question

If inspection misses a known declaration, even with the file specified, open the committed source with `git show REV:PATH`. Check its qualified name, namespace and implicit section assumptions in the matching Lean environment with `#check @Qualified.name`. Record the missed declaration as a limit of the source index. A missing search hit alone leaves existence and reachability unknown.

Read a short proof directly. For a large proof-body cleanup use `vl order base.lock --root NAME --aim simplify --budget 8 --json`: this orders by source length. When different aims return the same long lemmas, follow the clause required by each caller and inspect its proof route before choosing an edit. The packet contains signatures and locations. Read the bodies there. Use `vl graph base.lock` to nominate possible callers; check source, including tactic and instance use. To cut a block that several retained theorems share, use `vl value base.lock --root A --root B --deps kernel.tsv.gz --removal ranges.gz --json`: exclusive lines say what loses its last route from every root. Its `elaboration_only` rows (simp lemmas, instances, notations, parsers, linter options, names used only in tactic source) are what the kernel graph cannot see; keep them or remove them only with a cold build. A longer proof with clearer sublemmas can serve the reader better.

Before pricing removal of a shared block, identify each remaining use and which use keeps it reachable.
Scan reached declarations for repeated text that suggests a merge. Rebuild the downstream closure before using
a graph exported after the edit.

A small proof split can start from Git and the pinned source. Lean can run on the agreed build host. Record
the tested source and targets. Attach the build and applicable Comparator evidence for the integrated revision.
If the edit stays while its contract still needs checking, record `unverified`. If a new step runs out of
heartbeats under a fixed ceiling, isolate the subclaim or record the obstruction. Check the extracted signature
for unused variables and instances before paying for a build.

When the projects use different toolchains, inspect each at its native pin first. Try the smallest shared interface on one compatible pin. If it needs a wider migration, record that prerequisite and its scope before spending the migration cost.

For an edit to existing modules in a static Lake project, preview the coverage with
`vl trial base.lock --patch change.diff --downstream --targets MODULE --plan`. Name standalone clients in
`--targets` too. The plan follows pinned source imports and includes affected modules missing from the compiled
cache. Review the whole list, then replace `--plan` with `--runner RUNNER --attempt attempts/simplify.md`.
Use a trusted runner that builds exact modules. For other layouts, supply exact targets and verify the
source import closure with the project's tools.

For standalone JSON use `--out TRIAL.json` with `--by` and `--model`. Inspect both baseline and patched outcomes. A failing baseline leaves the comparison open. Compare exact statements and permitted assumptions separately. For a source-fidelity correction, retain the intended source passage and identify the changed clause. In a VerifyLab project preserve the old item and use a successor for a changed question. Changing a bound question after receipts exist **makes them stale**.

Before queuing the trial, check the designated worker's SSH settings, shared build lock, toolchain and prepared
inputs. See the prebuilt-worker example in `vl docs FOREIGN` for reusing unchanged artifacts and keeping both
build records. Commit the forecast before the edit, then keep the small sidecar with the attempt and check its
chronology as described in [record attempts](attempts.md).

Deliver the improvement for its named aim, the evidence, and the remaining cost or limitation. A useful outcome can be a proved theorem, an explanation, a counterexample, or the precise obligation blocking reuse. Write a failed route as an attempt file when it would save a later researcher work. Use the project's protected checks and meaning review for formal admission. See docs/FOREIGN.md and docs/SPEC.md (`vl docs FOREIGN`, `vl docs SPEC`).

Success: the original contract holds and both builds pass for every module in the reviewed plan, including all
named standalone clients. Record missing coverage explicitly. If the patch breaks a module after the base passes,
keep the trial evidence and name the dependency the patch must preserve.

See [record attempts](attempts.md) for forecast fields, costs and outcomes.
