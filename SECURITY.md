# Security and trust boundaries

VerifyLab checks candidate answers against trusted questions and records the evidence. A `verified` status means
a current admitted protected check passed under the recorded policy. It does not certify that the question is
faithful, novel, useful or understood. See [SPEC.md](docs/SPEC.md) for exact status and review rules and
[CHEATS.md](docs/CHEATS.md) for known attacks, implemented detectors and uncovered cases.

## Threat model

Candidate Python and Lean code may be hostile. A caller may try to weaken the target, change the evaluator,
substitute tools, forge evidence or hide an adverse admitted result. The integrator controls the trusted ref,
and the machine owner controls the verifier installation and policy. Configure these boundaries using
[INSTALL.md](docs/INSTALL.md).

Protected checks read targets, in-project definitions, evaluators and verdict-relevant configuration from the
trusted commit. Evidence counts only when its exact bytes are admitted there. A seal detects edits; it is not
a signature, and anybody can compute a new seal. Repository admission is the boundary against self-sealed
counterfeits. Alternate trust anchors and caller-selected machine policy locations are reported explicitly.

Checks run in bubblewrap without network and with a cleared environment. Python candidate and judge execute in
separate jails. Lean uses Comparator, pinned verifier binaries and a tested nested Landlock sandbox; nanoda is
required for protected checks while external kernels are enabled. A systemd user manager supplies memory and
task caps; without it, execution is uncapped, warns and records that limit.

## Accepted limits

- A process running as the same user can edit the repository, its Git configuration, machine policy, tools and
  caches. This is not a security boundary between mutually hostile users of one account. Use controlled admission,
  independent replay and review to inspect the evidence; local branch names alone do not prevent tampering.
- Verifier bugs, compromised dependencies, poisoned build artifacts and host isolation failures remain relevant.
  Pinned hashes identify Lean verifier binaries; they do not establish that those binaries are sound. Python records
  interpreter path and version, not an interpreter binary hash. System launchers are selected by absolute path.
- Tool identities and dependency traces are recorded for replay. Changing today's tools or build caches does not
  automatically stale an old receipt. Automatic deep replay, clean cache-free rebuilding and version floors are
  roadmap work, not implemented checks.
- Dirty candidate files are not preserved by the recorded HEAD. Replay requires a commit preserving the checked
  bytes and answer binding, verified against the recorded digests, and the original trusted commit. Digests cannot
  recover lost files. Follow the [manual replay procedure](README.md#re-running-a-receipt-by-hand).
- Statement probes detect some trivial or vacuous targets; a probe miss is not evidence of fidelity. A clean-context
  reviewer judges meaning, including important intermediate targets. Reviews can be wrong; author names and
  `--human-approved` are declarations, not identity authentication.
- Receipts include local paths and environment metadata. Inspect records and Git history before sharing a repository;
  isolation during execution does not make those artifacts safe to publish.

## Reporting a suspected vulnerability

Use this repository's Security tab and its "Report a vulnerability" action to contact the maintainer privately.
Do not put private research,
credentials or an actionable exploit into a public issue. No response-time commitment or support policy for
older releases has been established.

Prepare a minimal local reproduction with the VerifyLab version or commit, operating system, relevant toolchain
and tool identities, command and exit code, expected versus observed behavior, and sanitized records or logs.
Distinguish a wrong candidate verdict from a checker error, stale evidence and a meaning disagreement. Preserve
the original evidence and show how the finding reproduces on a fresh checkout with the stated tools.

Do not run an exploit against another person's repository or machine. For ordinary defects and contribution
checks, see [CONTRIBUTING.md](CONTRIBUTING.md).
