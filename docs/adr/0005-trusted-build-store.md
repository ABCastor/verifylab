# ADR 0005 — A store of trusted build products, and steps that run beside each other

Status: accepted, 2026-10-11 (owner). Extends ADR 0002 §3 (what belongs to the machine) and §6 (statement probes).

Context: in one measured corpus, a protected Lean check took 92 s at the median. Every check compiles, in a fresh project, the
in-project modules the target imports and the target itself (`prebuild_challenge`, 28% of the time), although they
are the same trusted bytes from one check to the next; the dependency preflight and the statement probes waited for
steps they do not depend on. A verdict must not change, and no candidate may gain a way to influence what the
trusted side of a check is built from.

## 1. What is stored
After `lake build VLChallenge` succeeds in `prebuild_challenge`, and before Comparator runs, `vl` copies Lake's
products of each module that build compiled (the target as `VLChallenge`, and its in-project closure: shared under
their own names, or renamed under `VLTrusted.*`) from the check project into
`$XDG_DATA_HOME/verifylab/lean-builds/<key>/`; the next check copies them back into its fresh project before its own
challenge build, which Lake then replays instead of compiling. Only protected checks with every guard on use the
store; `machine.toml` `[check] build_cache = false` turns it off. Candidate-side products are never stored.

## 2. What a key binds
The key of a module is the sha256 of: the environment (store format, `lean-toolchain`, the `lean --version` line, the
generated lakefile and manifest, each Lake package's checked-out revision and the trace of each dependency module the
trusted side imports) and the module's name, the sha256 of its source and the keys of the in-project modules it
imports. Any change of a definition therefore misses the store for that module and for every module above it,
target included. Lake checks a restored module's trace against its inputs once more and rebuilds what does not
match; the key does not rely on that.

## 3. The trust argument
What is stored was compiled from trusted inputs only: the challenge build runs before any candidate code is
compiled, and in shared mode the modules it compiles are byte-identical to the trusted ref's (checked before).
Only `vl` writes the store, from its own process, outside every jail; no jail mounts it (checks bind their scratch
folder under `$XDG_CACHE_HOME`, lanes their worktree), and candidate code inside a check is also confined by
landrun to the project's `.lake`. Entries appear whole (written aside, then renamed), and are copied, never
linked, into a check project, so a write into that project never reaches them. Each entry lists the sha256 of its
files (`SHA256SUMS`); an entry that no longer matches it (a crash before its data reached the disk, a damaged disk)
is not used, and a note names it: Lake replays a restored module from its trace without reading its olean, so a
damaged one would otherwise turn every later check of that key into an error. Each receipt records the store,
the modules restored with their keys and the modules stored (`environment.build_cache`), so a replay without the
store can tell which artifacts came from it.

Residual, as for the Lake packages a check mounts and the tools store: a process of the same user outside the jail
can write the store, and a poisoned entry would be read as trusted (it can rewrite `SHA256SUMS` too: the sums
catch damage, not an attacker). Build-time code of a trusted module (a `run_cmd`) runs in the challenge build with
the project's `.lake` writable, as without the store; what it writes there for another module of the same build is
stored under that module's key, so it reaches later checks whose target does not import the module that wrote it.
That code is the integrator's, reviewed on the trusted ref like every definition. Like `XDG_DATA_HOME` for the tools store, a
caller who moves it is named in every receipt (`machine_policy.overridden_by`). A replay on a clean machine, or
`build_cache = false`, rebuilds everything.

## 4. Steps that run beside each other
The dependency preflight runs beside the isolation probe and the challenge build; a stale shared cache still stops
the check with the preflight's reason (it ends the challenge build early, and its verdict is raised first). The
statement probes run beside Comparator, on the copy of the challenge build taken before any candidate code ran
(§6 of ADR 0002 is unchanged: that copy is all they read); their result is recorded only after a pass, and a check
that does not pass stops them. The receipt's `extra.phases` then overlap: `preflight` and `probes` are each step's
own duration.

## Rejected
- Lake's own artifact cache (`LAKE_CACHE_DIR`, `LAKE_ARTIFACT_CACHE`): restored artifacts stay in the cache folder,
  so every later step (Comparator's build of the candidate, lean4export, the probes) would have to mount it, and
  the candidate's build would read through it; the boundary would be Lake's cache policy instead of a mount.
- Storing modules the candidate's build compiled, even when byte-identical to the trusted ref: they are compiled
  after candidate code ran in the same project.
