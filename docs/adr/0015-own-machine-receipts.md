# ADR 0015 — Receipts from the owner's own machines count; others wait for a replay

Status: proposed, 2026-10-11. Extends ADR 0002
(trust from the repository and the machine) and ADR 0009 (clean-clone replay).

Context: a corpus audit can use several machines with the same pinned tools. The integrator needs to move
protected receipts from those machines and quarantine receipts from unknown or rented machines until replay.
The admission rule still ties status to the trusted ref (ADR 0009).

## Decision
1. **Two trust classes**, chosen by the integrator at import, never by the receipt. `own`: a machine the owner
   controls, running `vl` with pinned tools. `foreign`: any other. `machine.toml` has `[machine] name` (recorded in
   every receipt the machine writes, `environment.machine_policy.machine`) and `trust = "own" | "foreign"` (default
   `own`), which says how that machine classes its own receipts: an `own` import of a receipt from a machine that
   declares itself `foreign` is refused.
2. **`vl receipts export OUT.tar [--ids FILE]`** writes the protected receipts the trusted ref does not hold (the
   new files of `research/evidence/`) with a manifest (`vl.receipts/1`: the machine's name and the sha256 of each file).
   The machine needs a name. **`vl receipts import FILE --from NAME --trust own|foreign`** reads a bundle and
   checks each receipt as `vl validate` does (sealed, well-formed, protected, its item on the trusted ref, the machine
   it declares is NAME), without extracting the archive: only the manifest and members named
   `evidence/<item>/<16 hex>.json` are read, as bytes.
3. **`own` receipts are ordinary receipt files.** They are written to `research/evidence/` and count once the
   integrator admits them (`vl admit`), like a local one; nothing else about status changes. Two checks beyond the
   common ones, because a receipt from elsewhere can fail to hold here: every verifier tool it names must have the
   sha256 this machine pins for it (the `REVISIONS` file of its tools store or `[tools]` path), and the commit it
   asked its question of must be in this repository. Without the first the receipt would not be the "pinned tools"
   the class promises; without the second it would be stale (a receipt on the `#check` basis binds the `[lean] roots` of the
   commit it was asked of, and a commit that is not here has none). A receipt that fails either is refused, and may
   be imported as `foreign`.
4. **`foreign` receipts are quarantined.** They go to `.vl-cache/foreign/<NAME>/<item>/` (never versioned), are
   shown on the card as "foreign, not counted until vl replay" and are read by no status. `vl replay ID` takes them
   when the item has no admitted receipt, or by `--receipt RID`; on a match the receipt of the replay's own check
   goes to `research/evidence/`, where the integrator admits it, and the foreign one leaves quarantine; on a
   mismatch or an error it stays. Not the foreign receipt itself: a replay compares only `replay.COMPARED`, so its
   other fields (the statement probes that find a vacuous theorem, above all) were never reproduced.
5. **A receipt holds nothing of the machine that makes it stale or invalid elsewhere.** Status and validation read a receipt's inputs, question, verdict, kernels and seal; the machine policy
   (machine file path and sha256, tools store, launchers, XDG variables, tool paths, `toolchain_dir`, `memory_cap`,
   queue wait) is recorded, and the only reader is the status note that names `overridden_by`. Replay does not compare
   it either (ADR 0009). The two hazards that remain are those of point 3.

## Consequences
Receipts of own machines add throughput without a second integrator: admission is still one commit by the owner.
Nothing authenticates a machine: `own` is the owner's word, as admission is, and a same-user process on a machine can
write any receipt it likes (docs/CHEATS.md R33). A bundle that was altered in transit is caught by its manifest;
one forged on the machine and resealed is caught only by `vl replay`, which the owner can run on any of them, and
which is the only way a receipt from a machine he does not control ever counts. A receipt's tool sha256 is
compared with this machine's pin, not rechecked against a binary here; a check on this machine does that.
Python receipts record no tool pins; for them `own` means the owner's word and the interpreter version a replay
compares. Not decided here: signatures, a shared receipt store, imports across several repositories.
