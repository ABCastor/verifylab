# Choose the next action

Reference for the `verifylab` skill ([SKILL.md](../SKILL.md)). Choose the next reading, improvement or verification action for a mathematical aim and budget.

For a local Lake checkout, start with `vl pin PROJECT --at REV`, then `vl inspect vl.lock --find TERM`. Pick TERM from the mathematical question. If the theorem is already named, use `vl inspect vl.lock NAME` instead. Read the signature and ask which premise a downstream user must supply.

For an existing VerifyLab project, start with `vl status`, then `vl show ITEM --brief`. Keep judgment, cost and formal evidence separate. Record the actual agent, model and context in the item's `[origin]`.

Read [understand.md](understand.md) for a reading route, [improve.md](improve.md) for a shorter or more general argument, [explore.md](explore.md) for connections, [prove.md](prove.md) to break a claim into obligations, [referee.md](referee.md) to find traps, and [experiment.md](experiment.md) to measure the cost and yield of a research action. A decision model needs bounded source input, a question whose answer changes an action, and a budget. Carry the input hash, model and actual cost. Send a human the smallest question that still needs their judgment.

For local lane work, every build and run of candidate code goes through `vl lane exec LANE -- COMMAND`. If the project uses VerifyLab scheduling, schedule heavy local runs outside a lane with `vl queue run --mem SIZE -- COMMAND`. Invoke the protected checker directly so it owns its isolation and queue admission. Foreign builds need a disposable runner. See docs/FOREIGN.md and docs/SPEC.md (`vl docs FOREIGN`, `vl docs SPEC`).

Finish with the pinned claim, the next action it justifies, and the evidence or unresolved premise that could change that action.
