# Create a formal result

Start with `vl attempts new attempts/transport.md --purpose create --claim "Transport the certificate" --by agent:reader`.
Name the actual new mathematical claim, the intended consumer, its assumptions and the nearest existing
result. Search with `rg` and `vl inspect vl.lock --find TERM` after `vl pin . --at HEAD`. Record what
a counterexample would look like. Finish the forecast with its basis and commit it before starting the proof.

Break the claim into obligations with [prove](prove.md) and try the smallest discriminating case first.
In a VerifyLab project, use `vl find TERM` to search its registered records. Prepare a frozen item with `vl new ITEM` and follow [check](check.md). On a native
project run its `lake build TARGET` and check the resulting statement and `#print axioms Qualified.name`.
Record the old result, how it follows or differs, any new assumptions and what downstream check ran.

Without Lean or a worker, keep the candidate statement, obligations and evidence of the closest prior result.
A kept draft goes under `unverified`. Close a route you actually tried with its obstruction and how to reproduce it.
Run `vl attempts check` and `vl attempts table` after recording the outcome.

Success: a new transport lemma builds and the named client derives the old conclusion through it. Failure:
the smallest nonempty example needs a missing compatibility hypothesis. Record that example and the exact obligation.

See [record attempts](attempts.md) for forecast fields, costs and outcomes.
