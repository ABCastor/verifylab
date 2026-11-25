# Record attempts

Use docs/PURPOSE-AND-ATTEMPTS.md (`vl docs PURPOSE-AND-ATTEMPTS`) in any Git project. For a sequence of
attempts, write `PURPOSE.md` with the researcher's purpose, contract, budget, tools and judge. When a brief
already resolves these choices, record them and proceed. For a one-off source read the cited pin is enough.

1. Commit each forecast before the work. Name the model and effort or the person in `by`. Use the same `by`
   across attempts and the work stream in `lane`. Supply a basis, `p_accepted` and 80% value and cost intervals
   when you can estimate them. Git gives ordering. A past attempt with no forecast uses `forecasts: []`.
2. Omit `outcome` on open candidates. A deliberately skipped candidate is `not-run`. Use `accepted` after
   the named contract passes and `unverified` for a kept unchecked change. Record any surviving part of an
   abandoned or rejected claim under `outcome.kept`.
3. Measure against the attempt's own base. Keep reached and physical lines separate. Use the convention's
   time and memory units and use null for unmeasured dimensions. Attribute shared build costs only when you
   measured each share. Cite the named targets, reviewed statements and observed axioms.
4. Keep signed scores separate from measurements. For novelty cite the closest known result and record your
   search. A score of 7 or higher from an agent forecasts a person's judgment. Record a later reader's score
   separately with their task and evidence.
5. Run `vl attempts check FILE` for your attempt and `vl attempts check` for the whole folder. Both check
   forecast order in Git. Run `vl attempts table` to read calibration by band and by purpose. Here
   `unverified` resolves measured costs only. Run `vl attempts table --lanes --measured` to see lanes and
   measurements under their respective bases.

Keep the forecast commit, patch and small trial sidecar in the attempt's checkout. If the Git directory must
be isolated in that workspace, use Git's `--separate-git-dir` so the checkout's `.git` points there. Put the
separate directory outside the staged paths. Run `vl attempts check attempts --json` and look for
`"git_order": "checked"` and an empty problem list after adding the measured outcome. Temporary Git environment
overrides do not establish the checkout's visible chronology.

For a squashed archive, run `vl attempts check attempts --no-git --json` to check the records with chronology
explicitly unassessed. Audit the same files in the original history when it is available. Match the attempt
files, including outcomes, and cite that checkout's exact revision and separate audit.
