# VerifyLab

`vl` keeps the records of formal and computational research in plain files in Git: questions and results,
trusted Lean targets and Python evaluators, receipts of checks, and reviews. It is built for LLM agents working in
parallel, and for the people who integrate their work. No server, no UI, no database of record: one command line,
seven agent skills, and a [record format](docs/SPEC.md) that is usable without `vl`.

**The promise: protected checks compare an answer with the committed question, and status distinguishes
admitted evidence from exploratory or stale results.** What a result
must prove is committed on a trusted ref; a check reads the question from there and only the answer from the
worktree, runs in a jail, and writes a receipt bound to the digests of everything it read. Status is derived from
receipts and reviews, never written. A receipt counts only once the integrator commits it on the trusted ref, and
goes stale when any input changes.

**The limit: a process of the same user can write any file `vl` reads**, the repository and its records included.
The integrator must run `vl validate --incoming BRANCH` and generate protected receipts with `vl check`:
incoming validation rejects candidate-written receipts and changes or deletions of existing evidence and reviews.
The receipt's self-hash checks content integrity; it does not authenticate who ran a checker or prove execution.
`vl` does not make cheating impossible; it provides evidence for detecting it: a receipt can be re-run from a clean
clone when the checked inputs have been preserved in Git and the recorded tool environment can be reconstructed
(by hand today, below; automatic deep replay is planned), the
trusted ref can be a protected branch on the forge that agents cannot push to, and the meaning of a target is
judged by a reviewer started with a clean context. What `vl` itself never does is derive `verified` from what a
worktree edit, an environment variable or a failed read can change; an alternate trust anchor (`--trusted-ref`, a
machine policy moved by `XDG_*`) is named on the status it produces.

## A weakened statement is not a solution

Suppose the trusted target asks for a proof of `P`. A candidate instead proves `P ∨ True`, which is true
regardless of `P`: the protected Lean check rejects the changed statement. If `P ∨ True` was already the
trusted target, a proof can pass; statement probes warn about triviality, and a fidelity review must judge
whether the target actually represents the intended problem.

## Requirements

- Linux, Python ≥ 3.11 (`vl` uses the standard library only), git.
- bubblewrap (`bwrap`): every check, protected or exploratory, and every `vl lane exec` runs in its jail.
- A working systemd user manager is required for protected Lean checks: an outer service enforces
  Comparator's `RestrictAddressFamilies=~AF_UNIX`, with actual socket denial tested before each jailed command.
  Without enforcement, the check stops as `unsupported`. For Python, exploratory checks and lane execution,
  systemd supplies optional memory/task caps; without it those runs are uncapped, with a warning recorded.
- For Lean: a toolchain installed with elan, and [Comparator](https://github.com/leanprover/comparator),
  lean4export and landrun (Landlock, Linux ≥ 5.13), and nanoda, the second kernel: protected checks need it
  while `[lean] external_kernels` is on (the default).
- For Python checks: `/usr/bin/python3` (or another interpreter under a system folder).

`vl` never downloads a toolchain, Lake package or verifier tool. A missing required tool or toolchain makes a
check `unsupported`; an incomplete dependency build can instead produce `error`. Prepare the dependencies before
checking.

## Install

See [INSTALL.md](docs/INSTALL.md) for the CLI installation, verifier build recipe and machine setup. The CLI and
skills can be installed from a local checkout:

```sh
uv tool install .                       # puts `vl` on PATH; templates and skills ship in the package
ln -s "$PWD"/skills/vl-* ~/.claude/skills/   # the skills, for example for Claude Code
```

## Set up a machine

Protected checks take their tools from this machine only, never from the caller's `PATH` or `COMPARATOR_*`;
the system programs `vl` starts (bubblewrap, systemd-run, env, git, ...) come from system folders by absolute path,
or from `machine.toml` `[launchers]`:

```sh
vl init --tools --comparator PATH --lean4export PATH --landrun PATH [--nanoda PATH] [--toolchain TOOLCHAIN]
```

copies the binaries to `~/.local/share/verifylab/tools/<toolchain>/` and pins their sha256 in a `REVISIONS` file
there (the toolchain defaults to the current repository's `lean-toolchain`). A protected check refuses any tool
that is not pinned, or that changed since. Everything else machine-local goes in `~/.config/verifylab/machine.toml`
(`templates/machine.toml` is a commented example): tool paths, `elan_home`, the built Lake directory of each
project (`[caches]`), and `[check] memory_total`, the cap of all `vl` runs together (default 70% of RAM). The
`XDG_CONFIG_HOME` and `XDG_DATA_HOME` of a call can point it at another machine file and tools store: a receipt
records that, and a status it verifies says so.

## Set up a project

In a git repository, on the integrator's branch: `vl init` creates `research/` (items, targets, evidence,
reviews, evaluators) and `research/vl.toml`, records the branch as the trusted ref in the repository's own
`git config vl.trustedRef`, adds `.vl-cache/` to `.gitignore`, and prints a section for `AGENTS.md`
(`--write-agents` appends it). Commit `research/`: `vl` reads `research/vl.toml` as committed on the trusted ref.

## How agents use it

The seven skills guide coordination, exploration, proofs, experiments, improvement, human understanding and
fidelity review. The external harness launches agents and chooses their context; `vl` supplies records, search,
compact cards and isolated workspaces. A **lane** is a separate Git worktree: each agent edits its own copy,
and the integrator reviews and merges useful changes into the trusted branch.

For a hard problem, the coordinator maintains a living Markdown plan in the project's chosen location: goals,
hypotheses, alternative roads, why each intermediate result matters, abandoned roads and the next discriminating
check. Link records and read their current status with `vl show`; the plan is reasoning, not a second status store.
Review the meaning of important intermediate targets as well as the final result. A faithful review is an
attributed judgement, and a verified lemma is not by itself evidence that the research strategy will succeed.
Read `vl show ID --json` before a fidelity review and retain its `meaning_digest` and `trust.commit`.
Read the target and definitions at that commit, then write the review with
`vl review ID --kind fidelity --expected-meaning-digest DIGEST --verdict faithful --author agent:referee --text "REASON"`.
A changed meaning rejects the write; reread it before judging. The digest binds the context, not understanding.

Select context for each task: relevant lemmas with their assumptions and limits, examples, unsuccessful attempts
and source excerpts when useful. Different agents can receive different strategies or an invitation to find a
new one. `vl show --brief` packs only the requested records; it does not retrieve their linked sources or include
the full relations and reviews sections. Add the relevant plan and dependency links yourself, and open full cards
when needed. The default character budget is adjustable, not a limit on the agent's context window.

## First run (five minutes, no Lean needed)

On the Python fixture of this repository: an evaluator with the n-th prime as the question, and a candidate.

```sh
cd .. && cp -r verifylab/fixtures/python-planted/repo demo && cd demo   # next to the clone, not inside it
rm research/vl.toml && git init -q -b main && vl init
git add -A && git commit -qm "question, evaluator, candidate"
vl check nth-prime        # protected pass, exit 0; writes research/evidence/nth-prime/<receipt>.json
vl show nth-prime         # status pending-admission: the receipt is not on the trusted ref yet
git add research && git commit -qm "admit the receipt"
vl show nth-prime         # verified; fidelity: not reviewed
vl show nth-prime --json > review-context.json  # retain this before reading the evaluator at trust.commit
meaning_digest=$(python -c 'import json; print(json.load(open("review-context.json"))["items"][0]["meaning_digest"])')
vl review nth-prime --kind fidelity --expected-meaning-digest "$meaning_digest" --verdict faithful --author human:you --text "CASES and judge test the n-th prime"
git add research && git commit -qm "review the evaluator"
cp -r ../verifylab/fixtures/python-planted/defects/wrong/experiments .   # a wrong candidate
vl check nth-prime        # fail, exit 1, with the cases that did not pass
vl show nth-prime         # verified-stale: the admitted pass no longer matches the candidate
vl validate               # says which input changed
```

`fixtures/lean-planted` is the same exercise in Lean (it needs the Lean tools above).

## Re-running a receipt by hand

A receipt records the trusted commit it read the question from (`inputs.trusted_commit`) and the worktree's HEAD
(`inputs.candidate_head`, with `candidate_uncommitted` listing checked files that were not committed). On a machine
set up as above, **when the checked candidate files and item's answer binding were committed at that HEAD**:

```sh
git clone <repository-url> replay && cd replay && git checkout <candidate_head>
vl check ID --trusted-ref <trusted_commit> --json   # then compare verdict and inputs.digest with the receipt
```

If the candidate was edited before the check and committed afterward, the recorded HEAD does not contain the
checked bytes. Use a later commit that preserves them (often the commit admitting the receipt), verify every
candidate input against `inputs.files` and the item's binding against `question_digest`, and run against the
original `trusted_commit`. Committing the answer before checking gives the simpler replay recipe above.

Limits: replay uses this machine's tools and build caches. Compare the recorded tool identities and dependency
traces; changing tools or caches does not automatically stale an existing receipt. Lean verifier binaries are
recorded by sha256; Python records its interpreter path and version. Files never preserved in Git cannot be
reconstructed from a digest. Manual replay is not an automatic reproduction guarantee.

## Commands

`--help` on any command lists its flags; `--json` prints versioned JSON (`vl.output/1`), before or after a lane
action alike; `vl lane exec` passes its command's output and exit code through unchanged and refuses `--json`.
Exit 2 is always a usage or setup problem, never a verdict.

| Command | Does |
|---|---|
| `vl init` | create `research/` and `research/vl.toml`, write `git config vl.trustedRef`; `--tools` sets up the machine |
| `vl show ID[@rev]` | the result card: corrections, limits, statement, status, proof, meaning, evidence, reviews, relations; `--impact`; `--brief` targets `--budget N` characters (default 8,000), naming a source's file, never inlining it; mandatory headers can exceed the budget, with a warning |
| `vl find TEXT` | search items and the project's Lean declarations, local only; exit 1 when nothing matches |
| `vl check ID` | run the item's checker and write a receipt; exit 0 pass, 1 fail, 3 error or unsupported; `--explore` never counts |
| `vl review ID[@rev] --kind K` | record an immutable review: fidelity, compare, correction, retraction, understanding; new fidelity writes require `--expected-meaning-digest`; `--author human:NAME` only with `--human-approved` or a confirmation at the terminal |
| `vl validate` | check records, references, receipts, reviews and targets, and warn on process state written into a record's prose and on target hypotheses its `assumptions` leave out; exit 1 on errors; `--incoming BRANCH` before a merge |
| `vl lane new\|list\|exec\|close` | one git worktree per subagent, bound to its repository (sibling repositories never see each other's lanes); `exec` runs a command in the jail with the shared build cache read-only |

## Status labels

Derived on every read, from receipts and reviews committed on the trusted ref; never stored.

| Status | Meaning |
|---|---|
| `verified` | an admitted protected pass whose inputs, question and answer included, are unchanged in the worktree and on the trusted ref; no newer admitted fail |
| `vacuous` | such a pass, but the statement probes of an admitted pass of the same inputs derive `False` from a target theorem's hypotheses |
| `verified-stale` | admitted protected passes exist, and an input of each has changed since |
| `pending-admission` | a protected pass whose receipt is not committed on the trusted ref yet |
| `explored` | exploratory passes only; they never verify |
| `failed` | no pass and the newest check failed, or an admitted fail of the same inputs, newer or of the same instant, overrides a pass |
| `check-error` | no pass and the newest check was inconclusive: a tool, a timeout, a malformed item |
| `check-unsupported` | no pass and the newest check could not run: a missing tool (nanoda while `external_kernels` is on), toolchain or jail, or an item not on the trusted ref; a pass only the Lean kernel replayed counts as this |
| `unverified` | a `formal` or `computation` result with no valid receipt |
| `recorded-numeric`, `recorded-empirical`, `recorded-literature`, `recorded-prose` | a result of a claim kind `vl` does not check |
| `open` | a conjecture, or a question with no verified answer |
| `answered` | a question that a verified item `answers`; for a question with a Lean target, with the same target and theorems, and with a Python evaluator, the same evaluator and entry |
| `refuted` | a result or conjecture that a verified item `refutes` |
| `retracted` | an admitted retraction review |
| `source`, `intuition`, `explanation` | items without a truth status: the label is their kind |
| `undetermined` | `refutes` or `answers` relations form a cycle |

Next to the status, the card shows the meaning of the target as a separate fact: the fidelity review (docs/SPEC.md).
`answers`, `refutes` and `uses` are admitted assertions about relationships, not proofs of implication or negation.
For a checker-bound question, `answered` also requires the same target/theorems or evaluator/entry; a prose-only
question has no such mechanical contract. A `refuted` label does not itself certify a proof of the negation.
A fidelity review binds what it read: the target and the definitions it imports, the theorems, the stable Lean
semantic environment, and the item's
statement, limits and assumptions; editing any of them makes it stale. The title and body explain and bind nothing.
Cards, briefs and `--json` show the statement, limits and assumptions as committed on the trusted ref, the text the
status and fidelity hold for; a worktree that says otherwise is shown apart, labelled "uncommitted edit, not
reviewed", and `vl validate` warns when the item is verified.

## What a check proves

**Now, the fast check** (`vl check ID`, protected): for Lean, Comparator, in the jail, on a fresh project whose
target and in-project definitions come from the trusted ref: same statement, identical definitions, permitted
axioms only, replay by the Lean kernel and, while `[lean] external_kernels` is on (the default), by nanoda: a
machine without it gets `unsupported`, and a pass that only Lean's kernel replayed does not count. After a pass,
statement probes
look for targets that automation alone closes or whose hypotheses it refutes, and command lints flag what can
change meaning in the files the candidate changed. For Python, the candidate and the trusted evaluator's judge run
in two separate jails; every case must pass.

## Roadmap

**The deep check is planned** (there is no `--deep` flag): a clean rebuild without shared caches,
mutation of the conclusion, reproduction of every committed receipt from a clean clone at the trusted commit, and
a toolchain and kernel version floor.

Further coordination commands, shared build-cache improvements and imports from external research platforms
remain future work. The current CLI is the command table above. Agents can already keep reduction and obligation
records, maintain their research plan and review intermediate targets with the existing skills and commands.

## Documentation

- [INSTALL.md](docs/INSTALL.md): installation, verifier tools and machine setup.
- [CONTRIBUTING.md](CONTRIBUTING.md): development loop and evidence required for changes.
- [SECURITY.md](SECURITY.md): threat model, trust boundaries, reporting and replay limits.
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md): contract, trust model, the protected check step by step, modules.
- [docs/SPEC.md](docs/SPEC.md): the `research/` format: items, targets, receipts, reviews, derived status.
- [docs/CHEATS.md](docs/CHEATS.md): every known way a result can look verified without being so, and what catches it.
- [docs/adr/](docs/adr/): decisions. [CHANGELOG.md](CHANGELOG.md). [AGENTS.md](AGENTS.md): agent contribution rules.
- `skills/`: `vl-orient` (next move, fan-out, integration), `vl-explore`, `vl-prove`, `vl-experiment`,
  `vl-improve`, `vl-understand`, `vl-referee` (a clean-context reviewer of a target's meaning).

## License

VerifyLab is licensed under [Apache-2.0](LICENSE). Comparator's known-answer projects under
`fixtures/comparator-known/` retain their upstream [LICENSE](fixtures/comparator-known/LICENSE) and
[NOTICE](fixtures/comparator-known/NOTICE). The Castor name, marks and signature artwork have separate
terms in [NOTICE](NOTICE).

<p>
  <a href="https://abcastor.com">
    <picture>
      <source media="(prefers-color-scheme: dark)" srcset="docs/castor-footer-dark.svg">
      <source media="(prefers-color-scheme: light)" srcset="docs/castor-footer-light.svg">
      <img src="docs/castor-footer-light.png" width="800" alt="Chip, the Castor beaver, by Castor, we give a dam">
    </picture>
  </a>
</p>
