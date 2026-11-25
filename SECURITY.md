# Security and trust boundaries

VerifyLab checks candidate answers against trusted questions and records the evidence. A `verified` status (`check passed` in
text for people, ADR 0013) means a current admitted protected check passed under the recorded policy. It does not certify that the question is
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

Lane execution mounts the worktree's `.git` pointer read-only after its writable worktree mount. Lane commands
check that pointer against the owning repository's worktree registration before running Git in the lane; a changed,
missing or symlinked pointer refuses the operation. Git processes started by `vl` disable `core.fsmonitor` and Git
hooks through command-line configuration, including admission commits. These guards prevent a jailed build from
planting a repository callback that later executes outside the jail. They do not apply to an editor or to Git
commands an integrator starts independently, and do not defend against a same-user process outside the jail.

Text emitted by `vl`'s normal output/error formatter escapes terminal C0/C1 controls other than newline and tab;
JSON retains the original record data. This protects formatted record text, not arbitrary subprocess streams
passed through by commands such as lane execution.

## Accepted limits

- A process running as the same user can edit the repository, its Git configuration, machine policy, tools and
  caches. This is not a security boundary between mutually hostile users of one account. Use controlled admission,
  independent replay and review to inspect the evidence; local branch names alone do not prevent tampering.
- Third-party Lake packages run code when their cache is built, outside the jail. `vl` refuses to check, open a lane
  for or import a project whose manifest lists a package outside `[lean] trusted_packages` unless the machine
  declares itself `disposable` (ADR 0012, proposed); that class is the owner's word, `vl` cannot verify it, and the
  gate does not inspect a cache that was already built.
- Verifier bugs, compromised dependencies, poisoned build artifacts and host isolation failures remain relevant.
  Pinned hashes identify Lean verifier binaries; they do not establish that those binaries are sound. Python records
  interpreter path and version, not an interpreter binary hash. System launchers are selected by absolute path.
- Tool identities and dependency traces are recorded for replay. Changing today's tools or build caches does not
  automatically stale an old receipt. `vl replay ID` reruns an admitted receipt's check in a fresh clone and
  compares them ([docs/REPLAY.md](docs/REPLAY.md); ADR 0009); a match shows the receipt reproduces, not
  that the integrator ran it, and on the integrator's own machine it shares that machine's tools and caches. Clean
  cache-free rebuilding of the Lake packages and version floors are roadmap work, not implemented checks.
- Dirty candidate files are not preserved by the recorded HEAD. Replay requires a commit preserving the checked
  bytes and answer binding, verified against the recorded digests, and the original trusted commit; `vl replay`
  reports a receipt whose checked bytes no commit holds as a mismatch. Digests cannot recover lost files.
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
