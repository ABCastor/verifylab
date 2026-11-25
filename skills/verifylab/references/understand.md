# Understand an argument

Reference for the `verifylab` skill ([SKILL.md](../SKILL.md)). Explain a pinned theorem, its assumptions and the short route a reader should follow.

Start with `vl order --from . --at HEAD --root Tiny.conclusion --budget 8 --json`. For a foreign checkout, start with `vl pin PROJECT --at REV`, then `vl inspect vl.lock NAME`. If NAME is unknown, use `vl inspect vl.lock --find TERM`. Copy the signature and locate the binder or implication that supplies the hardest assumption.

For an existing pin run `vl order vl.lock --root NAME --budget 8 --json`. Read several source roots one at a time.
If a name is ambiguous, choose the intended pinned path from the diagnostic and add `--file PATH`. Use the source
locations to follow the first reduction. If the packet skips the root's statement, read its `root_source` in Git.
A source-name edge is approximate. Read the cited definitions before
translating symbols into prose. On an admitted item use `vl show ITEM --brief` to recover corrections,
outstanding fidelity judgments and limits. Source indicators and the five rows of an admitted result card
answer different questions.

Preserve an attributed human **direction** as an `intuition` with `author = "human:<name>"`, an explanation of the priority in the body. Link the affected item with `uses = ["<id>"]` or the topic with `area:NAME`. Record a person's own words with their approval. Directions on the trusted ref can guide subsequent work. See docs/GUIDE.md and docs/FOREIGN.md (`vl docs GUIDE`, `vl docs FOREIGN`).

For a timed reader task, save the exact source pin, prompt and allowed materials before handing them to a
fresh reader. Keep the reader's raw answer, start and end times from the session, and the attribution together.
Label one observed session a timed walkthrough. Label a masked between-edition test a comprehension comparison.
In that comparison keep the task and scoring fixed, with readers' raw answers and generality in separate
fields. Report when both editions yield correct answers and the task cannot distinguish them.

Finish with the statement in the reader's terms, the source location supporting the explanation and any premise whose meaning still needs a specialist.

Without Lean or a worker, follow the packet into `git show REV:PATH` and use `rg -n 'NAME' PATH` for callers.
Label section variables and typeclass assumptions you have only read from source.

In the Tiny fixture, ask the reader to point to the assumption that proves `p` in `Tiny.conclusion` and
trace the call to `Tiny.step`. The answer should cite the `certificate : p` binder and the application
`step p certificate`. A compiler graph for this fixture counts one located line in `Tiny.step` and three
reached lines under `Tiny.conclusion`. Inspect the named caller before deciding whether changing the step
would help the reader. If the source index omits a declaration using unsupported syntax,
cite the committed file and line and state which elaborated binders still need Lean.
