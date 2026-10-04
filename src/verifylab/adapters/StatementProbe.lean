/-
Statement probes of `vl check` (VerifyLab). This is not a module of any project: `vl` writes a probe file
made of the challenge import, `import Lean`, optional tactic imports, the options below, this text, and one
`run_elab VLProbe.probe …` command per target theorem, then runs it with `lake env lean` in the jail.

For a target theorem `T` it never parses statement text: it reads `T`'s type from the environment.
* Triviality: does a fixed battery of tactics close `T`'s type on its own?
* Vacuity: open `T`'s binders (`forallTelescope`), keep its hypotheses, replace the conclusion by `False`;
  does the battery prove that? Then no instance satisfies the hypotheses and `T` says nothing. Skipped when
  `T` has no Prop hypothesis, or when its conclusion already is `False` (a negation, not a vacuous claim).
A closing attempt counts only if no goal is left, the term has no `sorry` and no metavariable, the kernel
accepts it, and it uses only the permitted axioms. Each attempt runs under its own heartbeat limit, which
is deterministic; an attempt that hits it (or the recursion limit) counts as not closed and is listed in
`limit_reached`. Results are printed as one JSON line per theorem, tagged with a nonce.
-/

namespace VLProbe

open Lean Meta Elab Term

/-- Result of one attempt. -/
inductive Attempt where
  | closed
  | notClosed
  | limitReached

/-- Run `tac` on a fresh goal `goal` under `heartbeats` (in thousands, as the `maxHeartbeats` option), then
restore every piece of state, the environment included. -/
def attempt (levelParams : List Name) (goal : Expr) (tac : Syntax) (permitted : Array Name)
    (heartbeats : Nat) : TermElabM Attempt := do
  let saved ← saveState
  try
    tryCatchRuntimeEx
      (withOptions (fun o => maxHeartbeats.set o heartbeats) <|
        withTheReader Core.Context (fun c => { c with maxHeartbeats := heartbeats * 1000 }) <|
          withCurrHeartbeats do
            let mvar ← mkFreshExprMVar goal (kind := .syntheticOpaque)
            let rest ← withoutErrToSorry <| Tactic.run mvar.mvarId! (Tactic.evalTactic tac)
            unless rest.isEmpty do return .notClosed
            synthesizeSyntheticMVarsNoPostponing
            let proof ← instantiateMVars mvar
            if proof.hasMVar || proof.hasSorry then return .notClosed
            let aux := `VLProbe.aux
            addDecl (.thmDecl { name := aux, levelParams, type := goal, value := proof })
            let axioms ← collectAxioms aux
            return if axioms.all permitted.contains then .closed else .notClosed)
      (fun ex => return if ex.isRuntime then .limitReached else .notClosed)
  finally
    saved.restore (restoreInfo := true)

/-- The first battery tactic that closes `goal`, and the tactics that reached the limit before it. -/
def firstClosing (levelParams : List Name) (goal : Expr) (battery : Array (String × Syntax))
    (permitted : Array Name) (heartbeats : Nat) : TermElabM (Option String × Array String) := do
  let mut limited := #[]
  for (text, tac) in battery do
    match ← attempt levelParams goal tac permitted heartbeats with
    | .closed => return (some text, limited)
    | .notClosed => pure ()
    | .limitReached => limited := limited.push text
  return (none, limited)

def optStr : Option String → Json
  | some s => .str s
  | none => .null

/-- Probe target theorem `thm` and print one line `VLPROBE-<nonce> <json>`. -/
def probe (nonce thm : String) (tactics permitted : Array String) (heartbeats : Nat)
    (trivial vacuity : Bool) : TermElabM Unit := do
  let emit (fields : List (String × Json)) : TermElabM Unit :=
    IO.println s!"VLPROBE-{nonce} {(Json.mkObj (("theorem", toJson thm) :: fields)).compress}"
  let env ← getEnv
  let some info := env.find? thm.toName
    | emit [("error", "not found in the challenge environment")]
  let permitted := permitted.map String.toName
  let mut battery := #[]
  let mut unavailable := #[]
  for t in tactics do
    match Parser.runParserCategory env `tactic s!"({t})" with
    | .ok stx => battery := battery.push (t, stx)
    | .error _ => unavailable := unavailable.push t
  let lps := info.levelParams
  let (hypotheses, vacuityGoal?, skipped?) ← forallTelescope info.type fun xs body => do
    let hyps ← xs.filterM fun x => do isProp (← inferType x)
    if hyps.isEmpty then
      return (hyps.size, none, some "no Prop hypothesis")
    if body.isConstOf ``False then
      return (hyps.size, none, some "the conclusion is False")
    return (hyps.size, some (← mkForallFVars xs (mkConst ``False)), none)
  let (trivialBy, limitedT) ←
    if trivial then firstClosing lps info.type battery permitted heartbeats else pure (none, #[])
  let (vacuousBy, limitedV) ← match vacuity, vacuityGoal? with
    | true, some goal => firstClosing lps goal battery permitted heartbeats
    | _, _ => pure (none, #[])
  emit [("trivial_by", optStr trivialBy), ("vacuous_by", optStr vacuousBy),
    ("prop_hypotheses", toJson hypotheses), ("vacuity_skipped", optStr skipped?),
    ("limit_reached", Json.mkObj [("trivial", toJson limitedT), ("vacuous", toJson limitedV)]),
    ("unavailable", toJson unavailable)]

end VLProbe
