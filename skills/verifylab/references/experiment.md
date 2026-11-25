# Measure a research action

Reference for the `verifylab` skill ([SKILL.md](../SKILL.md)). Compare the cost and yield of reading policies or empirical checks and retain failed attempts.

For a reading experiment start with `vl pin PROJECT --at REV`, then `vl value vl.lock --root NAME`. Choose an independent outcome and a reading budget. Use `vl order vl.lock --root NAME --budget N` to make the deterministic packet. Freeze the candidates and packet before obtaining judgments. Compare deterministic order against the proposed model at the same budget. Record the actual model, input, cost, failures and missing answers.

For an empirical item start with `vl show ITEM --snapshot`, then `vl check ITEM --explore`. A trusted evaluator and a candidate answer have different owners. Inside a lane every build and run of candidate code goes through `vl lane exec LANE -- COMMAND`. If the project uses VerifyLab scheduling, run heavy local jobs outside a lane with `vl queue run --mem SIZE -- COMMAND`. The size is a reservation. With a usable systemd user manager, MemoryMax also caps the process and a process above it can be killed. Otherwise the runner warns and starts uncapped. Run the protected checker directly when the evaluator is ready.

Finish with the measured outcome and cost on both arms, or the missing observation that prevents comparison. For work on a pinned Lake checkout, write the attempt file with the measured outcome. See docs/FOREIGN.md and docs/GUIDE.md (`vl docs FOREIGN`, `vl docs GUIDE`).
