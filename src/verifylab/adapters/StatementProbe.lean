/-
Statement probes of `vl check` (VerifyLab). This is not a module of any project: `vl` writes a probe file
made of the challenge import, `import Lean`, optional tactic imports, the options below, this text, one
`run_elab VLProbe.probe …` command per target theorem, then one `run_elab VLProbe.boundary …` per target theorem,
and runs it with `lake env lean` in the jail.

For a target theorem `T` it never parses statement text: it reads `T`'s type from the environment.
* Triviality: does a fixed battery of tactics close `T`'s type on its own?
* Vacuity: open `T`'s binders (`forallTelescope`), keep its hypotheses, replace the conclusion by `False`;
  does the battery prove that? Then no instance satisfies the hypotheses. Whether this is the intended
  impossibility claim needs review. This does not inspect solution classes denied inside a conclusion. Skipped when
  `T` has no Prop hypothesis, or when its conclusion already is `False` (the contradiction is the target itself).
* Definitions: the in-project declarations `T`'s type reaches, through the types and values of in-project
  definitions (a declaration is in-project when its module is under one of the given module prefixes). Comparator
  checks that they are identical to the trusted ones, not that they mean what the question asks: a referee must.
* Totalized operations: subterms of `T`'s type, and of the values of those definitions and the constructors of those
  inductive types, whose value is a
  convention outside the intended domain (`a - b` on ℕ, `x / 0`, `Real.sqrt` of a negative, `tsum` of a
  non-summable family, …): the kinds below (`structural?`) and the constants named by `vl`. Reported, never judged.
* Foundations: constants, in the same places, whose name says the statement is about set-theoretic foundations
  (`foundation?`): `ZFSet`, `PSet`, `Cardinal`, `Ordinal`, choice principles, ZF, ZFC, consistency and
  independence. There the list of axioms says nothing (Lean always admits `Classical.choice`). Reported, never judged.
* Boundary values (`boundary`): each of the first natural-number binders of `T` set to small values in turn; are
  the hypotheses refuted there, or is the instance closed by the battery there and not at another of the values?
A closing attempt counts only if no goal is left, the term has no `sorry` and no metavariable, the kernel
accepts it, and it uses only the permitted axioms. Each attempt runs under its own heartbeat limit, which
is deterministic; an attempt that hits it (or the recursion limit) counts as not closed and is listed in
`limit_reached`; so does the scan for totalized operations. Results are printed as one JSON line per theorem and
command, tagged with a nonce.
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

/-- Print one line `VLPROBE-<nonce> <json>` about theorem `thm`. -/
def emit (nonce thm : String) (fields : List (String × Json)) : TermElabM Unit :=
  IO.println s!"VLPROBE-{nonce} {(Json.mkObj (("theorem", toJson thm) :: fields)).compress}"

/-- The battery entries that parse as tactics in this environment, and those that do not. -/
def parseBattery (tactics : Array String) : TermElabM (Array (String × Syntax) × Array String) := do
  let env ← getEnv
  let mut battery := #[]
  let mut unavailable := #[]
  for t in tactics do
    match Parser.runParserCategory env `tactic s!"({t})" with
    | .ok stx => battery := battery.push (t, stx)
    | .error _ => unavailable := unavailable.push t
  return (battery, unavailable)

/-- The goal "the hypotheses of `type` are contradictory", or why there is none to try. -/
def vacuityGoal (type : Expr) : MetaM (Nat × Option Expr × Option String) :=
  forallTelescope type fun xs body => do
    let hyps ← xs.filterM fun x => do isProp (← inferType x)
    if hyps.isEmpty then
      return (hyps.size, none, some "no Prop hypothesis")
    if body.isConstOf ``False then
      return (hyps.size, none, some "the conclusion is False")
    return (hyps.size, some (← mkForallFVars xs (mkConst ``False)), none)

/-! Definitions the statement depends on -/

/-- Whether constant `c` was declared in a module under one of `prefixes`. -/
def inProject (env : Environment) (prefixes : Array Name) (c : Name) : Bool :=
  match env.getModuleIdxFor? c with
  | some idx => prefixes.any (·.isPrefixOf env.header.moduleNames[idx.toNat]!)
  | none => false

/-- The declaration a referee reads for `ci`: an inductive type for its constructors and projections; none for
theorems and for what Lean generates (recursors, matchers, internal names). -/
def shownAs (env : Environment) (ci : ConstantInfo) : Option Name :=
  let c := ci.name
  if c.isInternal || isAuxRecursor env c || isNoConfusion env c || Meta.isMatcherCore env c then none
  else match ci with
    | .thmInfo _ | .recInfo _ => none
    | .ctorInfo i => some i.induct
    | _ => match env.getProjectionFnInfo? c with
      | some p => match env.find? p.ctorName with
        | some (.ctorInfo i) => some i.induct
        | _ => none
      | none => some c

/-- The in-project declarations `type` reaches through the types and values of in-project declarations and the
constructors of in-project inductive types, in the order found. -/
def projectDefinitions (prefixes : Array Name) (type : Expr) : MetaM (Array Name) := do
  let env ← getEnv
  let mut stack := type.getUsedConstants.reverse
  let mut seen : NameSet := {}
  let mut shown := #[]
  while !stack.isEmpty do
    let c := stack.back!
    stack := stack.pop
    if seen.contains c || !inProject env prefixes c then continue
    seen := seen.insert c
    let some ci := env.find? c | continue
    if let some s := shownAs env ci then
      unless shown.contains s do shown := shown.push s
    let mut next := ci.type.getUsedConstants
    match ci with
    | .thmInfo _ => pure ()
    | .inductInfo i => next := next ++ i.ctors.toArray
    | .ctorInfo i => next := next.push i.induct
    | _ => if let some v := ci.value? (allowOpaque := true) then next := next ++ v.getUsedConstants
    stack := stack ++ next.reverse
  return shown

/-! Totalized operations -/

/-- `n` when `e` is the numeral `n` (a raw literal or `OfNat.ofNat _ n _`). -/
def numeral? (e : Expr) : Option Nat :=
  match e.consumeMData with
  | .lit (.natVal n) => some n
  | e' =>
    if e'.isAppOfArity ``OfNat.ofNat 3 then
      match e'.appFn!.appArg!.consumeMData with
      | .lit (.natVal n) => some n
      | _ => none
    else none

def nonzeroNumeral (e : Expr) : Bool := (numeral? e).any (· != 0)

def isConstType (type : Expr) (n : Name) : MetaM Bool := return (← whnfR type).isConstOf n

/-- Whether `0 / 0`-style conventions apply to `α`: true when it is a group with zero, and when that class is not
in the environment (no Mathlib) or the instance search fails. -/
def withZero (α : Expr) : MetaM Bool := do
  unless (← getEnv).contains `GroupWithZero do return true
  try return (← synthInstance? (← mkAppM `GroupWithZero #[α])).isSome
  catch _ => return true

/-- `a - b` or `a / b` computed in ℕ, or `a / b` in ℤ, written syntactically. -/
def truncates (e : Expr) : Bool :=
  (e.isAppOfArity ``HSub.hSub 6 && (e.getArg! 0).isConstOf ``Nat)
  || (e.isAppOfArity ``HDiv.hDiv 6 && ((e.getArg! 0).isConstOf ``Nat || (e.getArg! 0).isConstOf ``Int))
  || [``Nat.sub, ``Nat.div, `Int.ediv, `Int.tdiv, `Int.fdiv].any e.isAppOf

/-- The kind of totalized operation `e` is, among those recognized by its shape; `vl` explains each kind. (No `←`
inside a condition: do-notation would run it before the test that guards it.) -/
def structural? (e : Expr) : MetaM (Option String) := do
  if e.isAppOfArity ``Nat.sub 2 || e.isAppOfArity ``Nat.pred 1 then return some "nat_sub"
  if e.isAppOfArity ``HSub.hSub 6 then
    if ← isConstType (e.getArg! 0) ``Nat then return some "nat_sub"
  if e.isAppOfArity ``HDiv.hDiv 6 then
    let α := e.getArg! 0
    if ← isConstType α ``Nat then return some "nat_div"
    if ← isConstType α ``Int then return some "int_div"
    unless nonzeroNumeral (e.getArg! 5) do
      if ← withZero α then return some "div_zero"
  if e.isAppOfArity ``HMod.hMod 6 then
    unless nonzeroNumeral (e.getArg! 5) do
      if ← isConstType (e.getArg! 0) ``Nat then return some "nat_mod"
      if ← isConstType (e.getArg! 0) ``Int then return some "int_mod"
  if e.isAppOfArity ``Inv.inv 3 then
    unless nonzeroNumeral (e.getArg! 2) do
      if ← withZero (e.getArg! 0) then return some "inv_zero"
  if e.isAppOfArity ``HPow.hPow 6 then
    let β := e.getArg! 1
    if ← isConstType β `Real then return some "rpow"
    if ← isConstType β ``Int then
      unless ← isConstType (e.getArg! 0) ``Int do return some "zpow"
  if e.isAppOfArity ``Nat.cast 3 || e.isAppOfArity ``NatCast.natCast 3 || e.isAppOfArity ``Int.cast 3
      || e.isAppOfArity ``IntCast.intCast 3 then
    let target ← whnfR (e.getArg! 0)
    if target.isAppOf ``Fin || target.isAppOf `ZMod then return some "cast_wrap"
    if ((e.getArg! 2).find? truncates).isSome then return some "cast_truncated"
  return none

/-- Call `f` on every subterm of `e` that is an application or a constant, under the binders of `e` (opened as
local declarations, so that a subterm prints with its binder names). A term can share subterms (the proofs inside
a definition do), so the walk is as long as the term unfolded as a tree: it checks the heartbeats. -/
partial def visit (f : Expr → MetaM Unit) (e : Expr) : MetaM Unit := do
  Core.checkSystem "statement scan"
  match e with
  | .app .. =>
    f e
    let fn := e.getAppFn
    unless fn.isConst do visit f fn
    for a in e.getAppArgs do visit f a
  | .const .. => f e
  | .lam n t b bi | .forallE n t b bi =>
    visit f t
    withLocalDecl n bi t fun x => visit f (b.instantiate1 x)
  | .letE n t v b _ =>
    visit f t
    visit f v
    withLetDecl n t v fun x => visit f (b.instantiate1 x)
  | .mdata _ b | .proj _ _ b => visit f b
  | _ => pure ()

/-! Set-theoretic foundations -/

/-- The kind of set-theoretic foundations constant `c` names, by its name only; `vl` explains each kind. A root
namespace (`ZFSet.mem`), or a component of the name as a whole word in lower case (`SetFormula.choice`,
`PartitionConsistency.Consistent`, `AC`), so a name that merely contains a word (`Choiceless`) does not count.
`Classical.choice` itself does not: a definition picks an element with it all the time, which says nothing of the
foundations (`vl` lists it as a totalized constant). -/
def foundation? (c : Name) : Option String :=
  if c == ``Classical.choice then none else
  match c.getRoot.toString with
  | "ZFSet" | "PSet" => some "zfset"
  | "Cardinal" => some "cardinal"
  | "Ordinal" => some "ordinal"
  | _ =>
    let words := c.components.map (·.toString.toLower)
    if words.any (["choice", "axiomofchoice", "ac", "dc"].contains ·) then some "choice"
    else if words.any (["zf", "zfc"].contains ·) then some "theory"
    else if words.any (["consistent", "isconsistent", "consistency", "independence", "independentof", "undecidable",
        "unprovable"].contains ·) then some "independence"
    else none

/-- Totalized operations and foundations constants in `e`, each as (kind, subterm as printed or constant, `place`),
added to `found` and `foundations` up to `cap` each (a constant once). -/
def totalized (named : Array Name) (cap : Nat) (place : String)
    (found foundations : IO.Ref (Array (String × String × String))) (e : Expr) : MetaM Unit :=
  visit (fun s => do
    if let some c := s.getAppFn.constName? then
      if let some k := foundation? c then
        let seen := (← foundations.get)
        if seen.size < cap && !seen.any (·.2.1 == c.toString) then
          foundations.modify (·.push (k, c.toString, place))
    if (← found.get).size >= cap then return
    let kind ← match ← structural? s with
      | some k => pure (some k)
      | none => pure (match s.getAppFn.constName? with
        | some c => if named.contains c then some c.toString else none
        | none => none)
    if let some k := kind then
      let text := (← withOptions (fun o => pp.deepTerms.set (pp.maxSteps.set o 400) false) (ppExpr s)).pretty 100000
      unless (← found.get).contains (k, text, place) do found.modify (·.push (k, text, place))) e

/-- Probe target theorem `thm` and print one line `VLPROBE-<nonce> <json>`. `named`: constants whose application
counts as a totalized operation; `prefixes`: the modules of the project; `cap`: the most totalized operations
(and, apart, foundations constants) listed. -/
def probe (nonce thm : String) (tactics permitted named prefixes : Array String) (heartbeats cap : Nat)
    (trivial vacuity scan : Bool) : TermElabM Unit := do
  let env ← getEnv
  let some info := env.find? thm.toName
    | emit nonce thm [("error", "not found in the challenge environment")]
  let permitted := permitted.map String.toName
  let (battery, unavailable) ← parseBattery tactics
  let lps := info.levelParams
  let (hypotheses, vacuityGoal?, skipped?) ← vacuityGoal info.type
  let (trivialBy, limitedT) ←
    if trivial then firstClosing lps info.type battery permitted heartbeats else pure (none, #[])
  let (vacuousBy, limitedV) ← match vacuity, vacuityGoal? with
    | true, some goal => firstClosing lps goal battery permitted heartbeats
    | _, _ => pure (none, #[])
  let mut fields := [("trivial_by", optStr trivialBy), ("vacuous_by", optStr vacuousBy),
    ("prop_hypotheses", toJson hypotheses), ("vacuity_skipped", optStr skipped?)]
  let mut limitedS := #[]
  if scan then
    let definitions ← projectDefinitions (prefixes.map String.toName) info.type
    let found ← IO.mkRef #[]
    let foundations ← IO.mkRef #[]
    let named := named.map String.toName
    let scanAll : TermElabM Unit := do
      totalized named cap "statement" found foundations info.type
      for d in definitions do
        match env.find? d with
        | some (.defnInfo v) => totalized named cap d.toString found foundations v.value
        | some (.inductInfo i) =>      -- the fields of a structure and the arguments of a constructor
          for c in i.ctors do
            if let some ctor := env.find? c then totalized named cap d.toString found foundations ctor.type
        | _ => pure ()
    -- Under the heartbeats of one attempt; what was found before the limit (or an error) stays found.
    let stopped ← tryCatchRuntimeEx
      (withOptions (fun o => maxHeartbeats.set o heartbeats) <|
        withTheReader Core.Context (fun c => { c with maxHeartbeats := heartbeats * 1000 }) <|
          withCurrHeartbeats do scanAll; return none)
      (fun ex => return some (if ex.isRuntime then "scan" else "scan failed"))
    if let some why := stopped then limitedS := limitedS.push why
    fields := fields ++ [("definitions", toJson (definitions.map toString)),
      ("totalized", Json.arr ((← found.get).map fun (k, text, place) =>
        Json.mkObj [("op", toJson k), ("term", toJson text), ("in", toJson place)])),
      ("foundations", Json.arr ((← foundations.get).map fun (k, c, place) =>
        Json.mkObj [("kind", toJson k), ("const", toJson c), ("in", toJson place)]))]
  emit nonce thm (fields ++ [
    ("limit_reached", Json.mkObj [("trivial", toJson limitedT), ("vacuous", toJson limitedV),
      ("totalized", toJson limitedS)]),
    ("unavailable", toJson unavailable)])

/-- `type` with its binder number `i` (from 0) replaced by `v`, when that binder is a `∀`. -/
def instantiateBinder (type : Expr) (i : Nat) (v : Expr) : MetaM (Option Expr) :=
  forallBoundedTelescope type (some i) fun xs rest => do
    match rest.consumeMData with
    | .forallE _ _ b _ => return some (← mkForallFVars xs (b.instantiate1 v))
    | _ => return none

/-- Boundary values of target theorem `thm`: each of its first `binders` binders of type ℕ set to each of
`values` in turn (the others stay universally quantified); print one line `VLPROBE-<nonce> <json>` naming the
instances whose hypotheses the battery refutes (`vacuous_by`) and those it closes (`trivial_by`) while it closes
neither at another of the values: there the statement degenerates. (A proved statement is true at every value, and
a decidable instance is closed at every one: that says nothing of its meaning.) -/
def boundary (nonce thm : String) (tactics permitted : Array String) (values : Array Nat) (heartbeats binders : Nat) :
    TermElabM Unit := do
  let some info := (← getEnv).find? thm.toName
    | emit nonce thm [("phase", "boundary"), ("error", "not found in the challenge environment")]
  let permitted := permitted.map String.toName
  let (battery, _) ← parseBattery tactics
  let lps := info.levelParams
  let naturals ← forallTelescope info.type fun xs _ => do
    let mut out := #[]
    for h : i in [0:xs.size] do
      let decl ← xs[i].fvarId!.getDecl
      if (← isConstType decl.type ``Nat) && out.size < binders then out := out.push (i, decl.userName)
    return out
  let mut hits := #[]
  let mut limited := #[]
  for (i, name) in naturals do
    let mut rows := #[]
    for v in values do
      let some goal ← instantiateBinder info.type i (mkNatLit v) | continue
      let (_, vacuity?, _) ← vacuityGoal goal
      let (vacuousBy, limitedV) ← match vacuity? with
        | some g => firstClosing lps g battery permitted heartbeats
        | none => pure (none, #[])
      let (trivialBy, limitedT) ← if vacuousBy.isSome then pure (none, #[])
        else firstClosing lps goal battery permitted heartbeats
      limited := limited ++ (limitedV ++ limitedT).map (s!"{name} = {v}: " ++ ·)
      rows := rows.push (v, vacuousBy, trivialBy)
    let contrast := rows.any fun (_, vac, triv) => vac.isNone && triv.isNone
    for (v, vacuousBy, trivialBy) in rows do
      if vacuousBy.isSome || (contrast && trivialBy.isSome) then
        hits := hits.push (Json.mkObj [("binder", toJson name.toString), ("value", toJson v),
          ("trivial_by", optStr trivialBy), ("vacuous_by", optStr vacuousBy)])
  emit nonce thm [("phase", "boundary"), ("boundary", Json.arr hits), ("limit_reached", toJson limited)]

end VLProbe
