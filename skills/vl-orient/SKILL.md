---
name: vl-orient
description: Decide the next research move in a VerifyLab project and run it, alone or with parallel subagents. Use at the start of a session, after a stall, when new results or corrections arrive, or when asked to "work on", "push", "attack" or "continue" a research question in a repo that has research/vl.toml.
---

# vl-orient — pick the next move, fan out, integrate

You coordinate research in the current VerifyLab project. Choose work that serves its goal; preserve checked
results, informative failures and explicitly provisional leads. The agent or harness chooses strategy and
context. Cheap discriminating work is useful when it answers the question, not a required research sequence.

## 0. Orient or deliberately explore afresh
- Seed what is known as records: sources with an honest `access`, known partial results (`claim = "literature"`,
  or `formal` when a library proves them), prior attempts, and walls with their scope. Check the literature
  before claiming a problem is open or a contribution is new; an initial independent idea can precede that search.
- Name known obstructions when relevant. A tentative road can leave its escape unresolved; name that gap
  before treating it as a solution strategy.
- Keep a living Markdown plan in the project's chosen home: goals, intermediate objectives, hypotheses,
  alternative roads, why each step helps, the next discriminating check, and closed roads with the reason each
  closed. Link supporting records by id; statuses come from `vl show`, never copied as enduring facts. The plan
  gives direction and assumptions for a reader to examine; it does not replace understanding the problem.
  Prefer results that stand alone if the main attempt fails. A reduction to an open problem does not solve the
  target; it can still establish an equivalence, a barrier or a useful reusable implication. State which.
  Keep condensed notes scoped to their observations, with conditions, unresolved bridges, dated or pinned
  provenance and evidence a reader can reopen; a body link alone does not validate its contents.

## 1. Read the state (never from memory)
- Before relying on saved supports or integrating changes, run `vl validate` and resolve or report relevant
  errors. Independent exploration can begin without reading the existing strategy.
- `vl find "<topic>"` and `vl show <id>` for the question and every result you intend to use.
  Read corrections and limits before the statement. Note exact revisions (`id@rev`).
- Only `verified` means a protected pass of the current inputs is admitted. If a result you need is anything
  else (`verified-stale`, `pending-admission`, `vacuous`, `failed`, `retracted`, `refuted`, ...; the README of
  VerifyLab lists every status), that is the news: deal with it before building on it.
- `vl <command> --help` lists every flag; this skill says when and why to use them.

## 2. Choose the next actions within the plan
When useful, compare actions by the uncertainty they reduce, possible discriminating work, cost and reasons
to stop or reconsider. Study, new definitions, speculative connections and formulation of microgoals may come
before a check is known. Pick the next move and update the plan when evidence changes a hypothesis or road.
When the whole result is beyond the available resources, assess bounded contributions against the goal:
reusable lemmas, simpler certificates, weaker assumptions or application bridges. Cost of the whole effort
alone does not close these roads; prioritize promising ones and give contribution-specific reasons for stopping.
Typical moves and the skill that carries them:

| Obstacle | Skill |
|---|---|
| No idea how to attack; need literature, analogies, new hypotheses or roads | `vl-explore` |
| A precise statement exists and needs a proof or a counterexample | `vl-prove` |
| The claim is computational, numerical or empirical | `vl-experiment` |
| Results exist but are long, narrow, duplicated or hard to reuse | `vl-improve` |
| A human wants to discuss goals, results, promising routes, experiments, intuitions or study | `vl-understand` |
| A target's meaning needs an independent reader (a new target, before a merge request) | `vl-referee`, in a fresh agent |

For a hard target, consider alternative roads when useful: forward from verified results, backward by
reductions (`vl-prove` records them as obligations), the negation as a sibling. A cheap challenge can expose
a mistaken reduction when applicable. Reconsider a road when its stop rule fires, a failure repeats, or it needs what a
recorded wall excludes. Before closing a road, record what it proved and where it broke: that boundary is the
next obligation. Reframe first if the formal statement no longer captures the question; correct first if a
support you rely on is compromised. This is reasoning, not a state machine: a ten-line computation may come
before an expensive experiment, a counterexample may send you back to the literature.

## 2b. When to involve the human
A person may choose an ongoing scientific dialogue: use `vl-understand` to discuss goals, interpret results,
develop intuitions and critique or refine experiments together. A stall is not required. During autonomous
work, protect their attention: involve them for a decision they own (what the question means,
the meaning of a target cited outside the project, which goal comes first, a road past its stop rule),
knowledge only they hold (an intuition, the question's origin, what would make a result interesting), a
surprise (a sign flip, a counterexample, an easy success on a hard target), or a stall. Before interrupting
autonomous work for a decision, write
the two likeliest answers and the move each leads to; if it is the same move, state your assumption and go
on. Never ask what records, a computation or `vl check` can settle. One open question per round, in role
words (`vl-understand`); do not show the answer to a question they want to attack themselves. Sign
`--author human:<name>` (with `--human-approved`) only on text that person wrote or approved.

## 3. Fan out (parallel subagents)
Parallelize latency, never authority.
- One lane per subagent: `vl lane new <name>` (starts from the trusted ref). Disjoint write sets;
  each lane writes only its own files and its own `research/` records. Inside a lane every build or
  run goes through `vl lane exec <name> -- <cmd>`: no network, the shared build cache is read-only
  and rebuilt modules land in the lane's own layer. Close finished lanes with `vl lane close`; it
  refuses while the lane holds gitignored files (exploratory receipts, scratch) until you move them
  out or pass `--discard-ignored`.
- Give each subagent its lane path and exact `git rev-parse HEAD`, task, skill and operational boundaries.
  Add relevant records from `vl show <ids> --brief`, plan excerpts and stop rules when useful. The brief targets 8,000
  characters by default; `--budget` adjusts it, and mandatory headers may exceed it with a warning. Full reviews
  and relations are excluded even when the brief is not truncated: open full cards for those you need.
  Give the task, goal and operational boundaries; choose rich, small or no prior strategic context deliberately.
  Agents can construct their own context through item search, links, rg, library tools and permitted literature
  search. `vl find` excludes dependency libraries, arbitrary plan files and review text; a project miss does not
  establish absence elsewhere. Use pointers to optional detail. The external agent/harness owns context selection
  and launching; this character budget does not constrain its context window. Include the operational rules:
  "delegate only within the owner/harness authorization and shared concurrency budget; do not touch other
  lanes; never modify an existing target or evaluator
  (a new target is a proposal I will review); do not write receipts by hand; run `vl` and git directly in
  the lane and every build through `vl lane exec`; report what you checked and how".
- Respect `[lanes] max_parallel` in `research/vl.toml`, counted across all open lanes of this repository
  (`vl lane new` refuses beyond it).
- Give each lane a clear research task: an obligation, alternative route, distant analogy, definition,
  microgoal, premise reduction, generalization, explanation or review. Choose nearby supports and failure
  history when they help; an independent restart may intentionally defer them. Before claiming a conclusion,
  confront relevant counterevidence. Preserve informative attempts with their result and reason; one short note
  may cover several routine trials.
  `vl show <reduction>` lists its obligations with their statuses under "uses"; `vl show --impact <id>` lists
  what uses an item, not open obligations.
- Use assigned dissent: when it matters, send one lane to prove and one to refute, two lanes with different
  lenses, or independent restarts on one obligation (they find different proofs). Agreement between models of
  any family is not proof; review independence comes from a clean context and not having produced the result.
  Vary prompts, objectives and context deliberately when it helps: one lane studies known supports, another
  seeks an independent route, another tests a physics or geometry connection or proposes shared microgoals.
  Different available, authorized models are an option, never a requirement. Give exploration lanes room to
  challenge the strategy; verification and isolation rules still apply. Before a strong novelty claim, seek
  a separate judgment from an agent that did not produce it.
- A subagent's claim is a hypothesis until you re-derive what is load-bearing: ids and quotes
  exist, the receipt is real, the Lean declaration is the one named.
- A human intuition enters a brief with its id, its author and what would refute it, labelled as a
  lead; when much rides on it, brief one lane to refute it.

## 4. Integrate (you work in the main checkout, on the trusted branch)
1. `vl validate --incoming <lane-branch>`: receipts never arrive by merge; records are immutable. Changed
   trusted inputs (`research/vl.toml`, evaluators, Lean build files, an existing item's question fields),
   added reviews, deleted files and receipts the merge would stale are warnings: read each diff, and the code.
2. Merge the lane into the trusted branch. That admits everything the lane committed: items, targets,
   code, and any review it added (a retraction, a correction or a fidelity review then counts); receipts
   written in a lane are refused by `--incoming`, and the integrator's own check writes them.
3. For every new or changed target, read the admitted file and judge its meaning: does it say what
   the question asks, is it non-vacuous, are the definitions right? Prefer a reviewer started with a clean
   context and the `vl-referee` skill (it can run while a slow check runs), or the human; a review by the
   item's own author is marked as not independent.
   Read `vl show <id> --json`, retain `meaning_digest` and `trust.commit`, and read files at that commit. Record
   `vl review <id> --kind fidelity --expected-meaning-digest <digest> --verdict faithful|too-weak|vacuous|wrong-definition|unclear --author agent:<you>`
   with `--text "<why>"`: it binds to the target, the definitions it imports, the theorems and the statement,
   limits and assumptions, and goes stale when any of them changes; commit it on the trusted branch to admit it. If you would not
   defend the target, revert the merge. `vacuous` means automation refutes the hypotheses; inspect whether
   that matches an intentional impossibility claim or defeats the intended application. The current status
   policy still applies; a target theorem closed by automation alone needs `--acknowledge trivial`, or a stronger target.
4. Run every load-bearing check yourself on the trusted checkout: `vl check <id>` (protected).
   Commit the receipts and reviews it produced; committing them on the trusted branch admits them.
   If a protected check fails, the result stays unverified; revert the merge if it misleads.
5. `vl validate` must end with no errors; read every warning.
6. Record what changed the research (a result, a failure that rules something out, a correction), not
   every message or temporary lemma; `supersedes` only for a verified successor better for every use.

## 5. Close the loop
After meaningful work, leave a short run retrospective in the existing plan/notebook: what changed, which
intuition or method helped, where a road failed and what that failure actually excludes, unresolved questions
and promising next uses. Link artifacts, sources and detailed attempts. Qualitative lessons from this run are
not causal evidence that a prompt or model is superior. Record checked results with receipt paths and keep
provisional appraisals/reasons distinct from formal evidence. The current CLI has no dedicated appraisal/rating
kind; keep such comments in the item body or notebook, not as fidelity verdicts or retractions.
If nothing moved, say so. Update the map; consider sibling questions the same object or method may reach.
Then, for the human, in announced parts: what changed for the question; the hard step or the surprise; what happened to each of
their intuitions; at most one question, with the move each likely answer leads to.
