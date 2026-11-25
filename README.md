# VerifyLab

Mathematics is done in community. VerifyLab helps people and agents understand, improve and create formal
mathematics for a stated purpose. It leaves the work open so others can check it and continue, with a plain-text
record of who did it, why, what they predicted, what it cost and what failed.

It is for one person working with agents, a research group, a company, or an agent coordinating thousands of
others.

VerifyLab provides a command-line tool (`vl`) and a plain-text convention in Git. It runs beside Lean, Lake
(Lean's build and package manager) and Comparator (the Lean FRO's checker that compares a candidate proof against
fixed statements and allowed axioms). Lean checks proofs. Comparator checks that the candidate proves the exact
target statement with only permitted axioms. Nobody can make a result true by recording or approving it in
VerifyLab.

## Five-minute start

Start in a VerifyLab checkout on Linux with Git, Python 3.11 or later and `uv`.

```sh
uv tool install .
export PATH="$(uv tool dir --bin):$PATH"
vl --help
vl attempts check examples/toy-attempts/attempts
vl attempts table examples/toy-attempts/attempts
```

The toy example reports 2 open candidates and 0 problems. One candidate forecasts saving 2 to 4 source lines.
You can read and check attempt files without installing Lean. For protected checks and a checkout-only invocation,
see [Installation](docs/INSTALL.md).

In your mathematics repository, write `PURPOSE.md`, commit a forecast for each candidate under `attempts/`, and
commit the measured outcome after the work. Run `vl attempts check` and `vl attempts table` from that repository.
See [Purpose and attempts](docs/PURPOSE-AND-ATTEMPTS.md).

## Limits

VerifyLab does not prove theorems for you, it does not certify (Comparator does), and it gives no objective
ranking: what is better depends on the purpose, and the purpose belongs to whoever does the work.

## Workflows

In an existing Lake project, start with its own Lake build and native Comparator for a frozen challenge.
Add attempt files when you want to compare planned work with outcomes. Adopt `research/vl.toml` when the
project wants VerifyLab's sealed receipts, review and admission.

VerifyLab adds three workflows that can be used together or on their own:

- **Plan and measure changes in any Git repository.** `PURPOSE.md` states what the work is for and what contract
  must stay true. Record an attributed forecast before starting and add the measured outcome after the work.
  `vl attempts check` validates the file and compares forecasts with the version committed before the first
  recorded outcome in that file's Git history. `vl attempts table` compares forecasts against outcomes
  for each forecaster. [The convention](docs/PURPOSE-AND-ATTEMPTS.md)
- **Read a Lean checkout.** Pin a Lake checkout at a Git commit, read
  the exact source signature of a theorem, get a budgeted reading order of declarations, and measure how many located source lines lose their last dependency path from the main theorems when a block
  is removed from the supplied graph. [Lean checkout workflow](docs/FOREIGN.md)
- **Check and coordinate results in a VerifyLab project.** Give each parallel agent an
  isolated Git worktree with `vl lane new` and run their commands in the sandbox with `vl lane exec`. Compare
  candidate proofs with targets read from a trusted Git ref, probe the target for statement traps, and keep a
  sealed receipt. Review statement meaning independently. [More below](#the-verifier-around-leans-comparator)

| Record | Use it for |
|---|---|
| `attempts/*.md` | Forecasts, reported outcomes and attributed scores in any Git repository. |
| `*.trial.json` | Timings and build outcomes for a pinned patch trial run by `vl trial`. |
| `research/items/*.md` and `research/evidence/` | Questions and results with protected receipts. Use `vl show ID` to read a result card. |

To install the agent entry skill, run `vl init --skills codex` or `vl init --skills claude`. For Pi, pass its
skill directory to `vl init --skills PATH`. Open a new agent session after installing and add this line to the
project's `AGENTS.md`:

```text
For formal mathematics in this repository, load the `verifylab` skill and follow its route for your purpose.
```

## Two prompts

This example uses the bundled `Tiny` development as a local Git checkout. [Prepare the fixture](docs/FOREIGN.md#try-the-tiny-project)

**“What does Tiny.conclusion prove? Which assumption does the work?”**

The agent runs `vl pin Tiny --at HEAD`, then `vl inspect vl.lock Tiny.conclusion`. Its source read starts with:

```text
Tiny.lean:5
theorem conclusion (p : Prop) (certificate : p) : p
Explicit binders: (p : Prop), (certificate : p)
```

The caller already supplies a proof of `p` as the explicit argument `certificate`. The theorem returns a proof of
that same proposition.

**“Show me the route I would need to read before simplifying it.”**

The agent runs `vl order --from Tiny --at HEAD --root Tiny.conclusion --budget 2`. Its entire reading packet is:

```text
Tiny.conclusion
theorem conclusion (p : Prop) (certificate : p) : p
Tiny.step
theorem step (p : Prop) (h : p) : p
```

Both signatures state the same obligation. Returning `certificate` directly is a candidate patch.

## Commands

| Command | What the agent gets |
|---|---|
| `vl pin` | A pinned checkout, toolchain and Lake dependency inventory (`vl.lock`). |
| `vl inspect` | A pinned theorem or lemma source signature, its explicit binders (`(x : A)`) and source indicators, or a search for a declaration (`--find`), an item card (`--item`) or project records (`--search`). |
| `vl graph` | Module imports, approximate source dependencies, dependency edges from supplied compiled Lean modules (`.olean` files), a graph dump (`--dump`), or a target rebuild estimate. |
| `vl value` | Root-conditioned use and size, line-removal costs, block totals and study or simplification rankings (`--study`). |
| `vl order` | A bounded reading packet of declaration names and signatures. Pass `--json` for source locations and ranking details. |
| `vl trial` | Baseline and patched build outcomes for named targets using a chosen runner, optionally linked to an attempt file (`--attempt`). |
| `vl attempts` | Scaffold a forecast (`new`), check its rules and Git commit order (`check`), or tabulate forecasts against outcomes (`table`). |
| `vl check` | A sandboxed check of a bound item (or a clean-clone reproduction with `--replay`), recording the verdict, input digests, probes and tools used. |
| `vl review` | An attributed judgment bound to an item revision and, for statement fidelity and paper correspondence, to the exact trusted meaning digest. |
| `vl lane` | Isolated Git worktrees and read-only build-cache overlays (`new`, `list`, `exec`, `done`, `integrate`, `close`) for parallel agents. |
| `vl init` / `new` / `status` / `show` / `find` / `validate` / `admit` / `replay` | Project setup, item scaffolding, repository triage, result cards, search, record validation, integrator admission and clean-clone receipt replay. |

Run `vl --help` for commands grouped by purpose and `vl docs GUIDE` for project records and administration.
Use `vl export catalog OUT` to share a static HTML and JSON catalog of the results on a trusted ref.
[Lean checkout workflow](docs/FOREIGN.md) · [Full command guide](docs/GUIDE.md)

## The verifier around Lean's Comparator

The verifier calls Comparator—the Lean FRO's checker of a candidate against fixed statements and permitted
axioms—as an external program. Lean v4.35.0 and later also ship `lake comparator`.

VerifyLab reads the target statements and their in-project definitions from a trusted Git ref, runs Comparator in a
no-network sandbox, and keeps a sealed receipt bound to a revision and the input hashes. Alongside Comparator,
it runs statement probes for targets closed by basic automation, contradictory Prop hypotheses and small
`Nat` boundary values. It also flags set-theoretic foundation constants and totalized operations such as division
by zero, which Lean defines with a fallback value. In a separate fidelity review, a reader reviews what the
statement means.

### What a protected pass means

For a newly issued protected Lean pass, Comparator checks the candidate against a target fixed on the trusted Git
ref. The receipt records agreement on the covered declarations and definitions, permitted axioms, and acceptance by
both Lean's kernel and `nanoda` (an independent kernel for Lean). Once the integrator commits the receipt
on the trusted ref, the admitted current receipt counts toward the item's derived status (`check passed` in human
views, `verified` in JSON). For Python items, a pass means the trusted evaluator accepted the candidate in a
separate sandbox on every recorded test case. Statement fidelity and correspondence with a paper are separate rows
on the verdict ladder and require their own reviews. [Evidence contract](docs/SPEC.md)
