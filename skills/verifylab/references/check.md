# Check a fixed result

For an existing item start with `vl show ITEM --snapshot`. Read the fixed target, witnesses, imports
and allowed axioms. Compare the intended claim with the target using [referee](referee.md). Use `vl new ITEM`
to scaffold a missing question and have the integrator fix its statements on the trusted ref before the proof.
A changed question gets a successor item.

Run `vl check ITEM` for protected evidence. `vl check ITEM --explore` is a worktree check. If working in a VerifyLab lane, use `vl lane exec NAME -- lake build TARGET` for local build
feedback. `vl check --selftest` checks the project's own checker configuration with planted and clean cases.
Exit 0 is pass, 1 is fail, 2 is setup or input failure and 3 is inconclusive. Record the receipt and revision.
Use `vl replay ITEM --receipt RECEIPT` to reproduce an admitted receipt. Keep receipts as written.
The integrator runs `vl validate` and admits results with `vl admit` on the trusted branch.
A separate reader can write the meaning review:

```sh
vl review ITEM --kind fidelity --verdict faithful --expected-meaning-digest DIGEST --text TEXT --author agent:reader
```

For a native project, identify the exact checked Git revision and read the frozen question there.
Look beside the contracts and in the project's evidence index for a completed check. Identify the checked source
snapshot, build status, `#print axioms` output, frozen target and solution digests, tool and configuration identity,
and Comparator log and status. Verify the recorded source and configuration against that revision using the archive's
manifest. Document any differences and which checked inputs they affect. Findings tied to an older snapshot
remain evidence about that snapshot unless correspondence can be established. Cite each matching archived result
and say when it ran.

For a native handoff, record the input-manifest comparison and the receipt's actual target coverage with
the native verdict. Validate exported attempt files with `vl attempts check --no-git`. When original history
is available, run `vl attempts check` against that history to check chronology. Otherwise record chronology
as unavailable and attribute any historical chronology result to its original log.

If relevant evidence is missing or the task asks for a fresh replay, run the project's native Comparator against
the frozen target. With Lean 4.35 or later the project may use `lake comparator`. Record the toolchain, revisions,
targets, permitted and reported axioms, exit code and log. Use `lake build TARGET` and `#print axioms Qualified.name`
for build and axiom evidence. For a particular statement trap, look up that topic in `vl docs CHEATS`.
If the worker is unavailable, retain the pinned statement and fidelity observations, and mark only the missing
checks pending. Record a kept unchecked change as `unverified`.

Success: Comparator accepts the candidate against the frozen target and the reviewer cites the matching assumptions.
Failure: the build passes but Comparator rejects the changed statement. Preserve the rejection and identify the changed clause.
