import Lean
/-!
dumpx.lean — kernel-exact dependencies of every constant of a module, read from the module's compiled data
(the complete split module, or a single legacy file; no project import or elaboration).

Usage: lean --run dumpx.lean LISTFILE      LISTFILE: one line per module, `OLEAN_PATH<TAB>MODULE_NAME`
Output, per module:
  MOD<TAB>module<TAB>n_constants<TAB>direct imports (space separated)
  DEP<TAB>kind<TAB>module<TAB>constant<TAB>flag<TAB>used constants (space separated)<TAB>recorded axioms
  MODEND<TAB>module
`used constants` is `ConstantInfo.getUsedConstantsAsSet` (type and value), the same function the K3 dump used, but
ALL of them: which are inside the archive is decided offline. `flag` is `S` when `sorryAx` is among them, else `-`.
kind `axiom` marks axioms. A space inside a name is written `␣`.
A seventh column gives the axioms that the olean itself RECORDS for the constant (Lean 4.33.1 stores, when a module
is compiled, the axiom list of each of its declarations in the extension `exportedAxiomsExt`; `#print axioms`
reads these lists for imported declarations). It is what the builder's Lean wrote, not a computation made here:
`?` when the olean has no entry for the constant.
-/
open Lean

def clean (n : Name) : String :=
  let s := toString n
  if s.any (fun c => c == ' ' || c == '\t' || c == '\n') then
    s.map fun c => if c == ' ' || c == '\t' || c == '\n' then '␣' else c
  else s

/-- The axiom lists recorded in the olean (extension entries of `exportedAxiomsExt`). -/
unsafe def recordedAxioms (data : ModuleData) : Std.HashMap Name (Array Name) := Id.run do
  let mut m : Std.HashMap Name (Array Name) := {}
  for (ext, entries) in data.entries do
    if (toString ext).endsWith "exportedAxiomsExt" then
      for e in entries do
        let (n, axs) : Name × Array Name := unsafeCast e
        m := m.insert n axs
  return m

/-- Use the full proof view for split Lean modules. The public view may expose a theorem
as an axiom and retain only the constants in its type. A legacy module has one file. -/
def readProofData (path : System.FilePath) : IO ModuleData := do
  unless path.extension == some "olean" do
    throw <| IO.userError "expected the main .olean path"
  let (base, _) ← readModuleData path
  if !base.isModule then
    return base
  let parts ← readModuleDataParts #[path, OLeanLevel.server.adjustFileName path,
    OLeanLevel.private.adjustFileName path]
  let some (full, _) := parts[2]? | throw <| IO.userError "missing proof data"
  return full

unsafe def main (args : List String) : IO UInt32 := do
  let lines := ((← IO.FS.readFile args[0]!).splitOn "\n").filter (· ≠ "")
  let out ← IO.getStdout
  let mut failed := false
  for l in lines do
    match l.splitOn "\t" with
    | [path, modStr] =>
      try
        let data ← readProofData path
        out.putStrLn s!"MOD\t{modStr}\t{data.constants.size}\t{" ".intercalate (data.imports.toList.map (clean ·.module))}"
        let rec_ := recordedAxioms data
        for ci in data.constants do
          let used := ci.getUsedConstantsAsSet
          let kind := match ci with
            | .thmInfo _ => "thm" | .defnInfo _ => "def" | .axiomInfo _ => "axiom" | .opaqueInfo _ => "opaque"
            | .inductInfo _ => "ind" | .ctorInfo _ => "ctor" | .recInfo _ => "rec" | .quotInfo _ => "quot"
          let flag := if used.contains ``sorryAx then "S" else "-"
          let axs := match rec_[ci.name]? with
            | some a => " ".intercalate (a.toList.map clean)
            | none => "?"
          out.putStrLn s!"DEP\t{kind}\t{modStr}\t{clean ci.name}\t{flag}\t{" ".intercalate (used.toList.map clean)}\t{axs}"
        out.putStrLn s!"MODEND\t{modStr}"
        out.flush
      catch e =>
        failed := true
        out.putStrLn s!"FAIL\t{modStr}\tread: {e}"
    | _ =>
      failed := true
      out.putStrLn s!"BADLINE\t{l}"
  return (if failed then 1 else 0)
