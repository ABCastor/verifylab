# ADR 0012 — Third-party Lake packages run only in a disposable sandbox

Status: proposed, 2026-10-11 (awaiting the owner's acceptance). Extends ADR 0001 and ADR 0005.

Context: a Lake package's code runs when its cache is built (a lakefile is a program, as are build steps), and again
whenever a module that imports it is elaborated (macros, `initialize`), also inside a check. `vl` jails the check,
but never builds the package cache: its owner does, with Lake, outside the jail, with network and the home folder and
keys reachable. Mathlib and the packages it inherits come from two organisations the owner already trusts. A corpus
such as OpenAI's math release needs 29 packages besides Mathlib, 27 of them from 17 owners outside the Lean
organisations (read 11 October 2026, the execution-at-scale design); building them on the integrator's workstation
hands 17 unknown authors that workstation.

Decision:

1. **What is trusted.** `[lean] trusted_packages` in `research/vl.toml` on the trusted ref lists package names.
   Without it, the default is Mathlib and its official dependencies (`mathlib`, `batteries`, `aesop`, `Qq`,
   `plausible`, `proofwidgets`, `importGraph`, `LeanSearchClient`, `Cli`), each only as a git package whose URL is
   exactly `github.com/OWNER/REPO` with the owner `leanprover` or `leanprover-community` (no further path, `..` or
   query), so a fork that calls itself `Cli` is third-party. A list the
   integrator writes replaces the default and trusts by name: it is the integrator's own word. Every other package
   in the project's `lake-manifest.json` is third-party.
2. **What the machine is.** `[sandbox] class` in `machine.toml` is `workstation` (the default) or `disposable`. It
   is the owner's declaration and `vl` cannot verify it: `disposable` belongs only in a purpose-built VM image,
   fresh for each batch, with no credentials but a write-only results channel, destroyed afterwards.
3. **The gate.** On a workstation, a protected or exploratory `vl check` (the manifest of the trusted ref and, for
   an exploratory one, the worktree's), `vl lane new` and `vl import` of a project whose manifest on the trusted ref
   lists a third-party package stop with `unsupported` before anything is read, built or written, naming the
   packages and saying they must be built in a disposable sandbox. A manifest `vl` cannot read stops them too. On a
   disposable machine they go on. A check stopped by the gate has read no candidate file, so a protected one leaves no receipt.
4. **What a receipt says.** Every receipt records `environment.host`: sandbox class, kernel release, architecture,
   CPU model, logical cores and total RAM, so that receipts of different machines are comparable (no verdict
   depends on it). A check that a disposable machine let through records `environment.third_party_packages`.
   Lean receipts also record `extra.cpu_seconds`, the CPU time of all the check's runs added up (overlapping or
   not), so that cost is comparable across machines.

Not decided here: building the package cache in a sandbox, the promotion of receipts made on
another machine (they are ordinary receipts of that machine, and admitting one stays the integrator's act), and the
disposable image itself.

Consequences: a Mathlib-only project is unchanged and no receipt goes stale: the list is not a verdict input and is
not in the configuration digests. The gate decides where code may run, not that it is safe: trust is by name and
owner, not by revision, and `vl` does not look inside the cache the owner built (a package present there that the
manifest does not list is not seen). Projects that depend on a package of their own must list it, or run on a
disposable machine. Changing the default list is a change of this decision.
