# Connect fields

Reference for the `verifylab` skill ([SKILL.md](../SKILL.md)). Connect developments or fields and test whether a proposed bridge transfers a result.

Start with `vl pin FIRST --at REV_A --lock first.lock`, then `vl pin SECOND --at REV_B --lock second.lock`. For each pin use `vl inspect LOCK --find TERM` to find the two interfaces and `vl inspect LOCK NAME` to read their signatures.

Write the proposed map of objects, hypotheses and conclusions. Identify the assumption available on one side that has no counterpart on the other. Shared names, similar prose and high model scores are search leads. Check paths and citations before claiming novelty. In a VerifyLab project put an open bridge in a conjecture or intuition, attribute its `[origin]`, and preserve the source access actually obtained.

Finish with a checked transfer in the stated direction, a counterexample to it, or the precise missing mapping with both source locations. See docs/FOREIGN.md and docs/GUIDE.md (`vl docs FOREIGN`, `vl docs GUIDE`).
