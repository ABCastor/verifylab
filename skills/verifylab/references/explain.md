# Explain a proof or change

Start with `vl pin . --at HEAD --lock explain.lock` and `vl inspect explain.lock Tiny.conclusion`. For a change,
make one pin per revision and inspect the same declaration on both. Use the packet from
`vl order explain.lock --root Tiny.conclusion --budget 8 --json` to find dependencies worth reading.

Name the reader and what they should be able to reconstruct. Open the bodies at the cited revision with
`git show REV:PATH`. Use `git diff OLD NEW -- PATH` for a change. Write the claim and its hypotheses, the
decisive inference, the lemma that licenses it and the caller that needs it, with source locations.
If a reader exercise matters, give a related task without the derivation and preserve the raw answer.
See [understand](understand.md) for reading assumptions and [study](study.md) for choosing an explanation target.

Without Lean or a worker, cite pinned source and identify any implicit argument or tactic result that you could
only infer from it. With Lean, use `#check @Tiny.conclusion` and inspect the relevant local goal before generalizing.

Success: the explanation shows why the existing parameter `certificate : p` can replace the call to `step`
and cites the unchanged theorem signature. Failure: the diff shows a shorter body but a stronger hypothesis.
Identify the lost case and revise the explanation of the change.
