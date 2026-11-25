import Lean
/-!
Ranges.lean — compiler declaration ranges and type hashes of every constant of a module, read from the module's
compiled data (the complete split module, or a single legacy file; no project import or elaboration). Run it on the
same compiled checkout as dumpx.lean (`vl graph --dump` runs both); `vl value --removal` reads the R rows and the
retained roots' T rows. It reads
split module data (`readModuleDataParts`), so it needs a Lean release that has it.

Usage: lean --run Ranges.lean LISTFILE      LISTFILE: one line per module, `OLEAN_PATH<TAB>MODULE_NAME`
Output:
  R<TAB>module<TAB>constant<TAB>line<TAB>column<TAB>endLine<TAB>endColumn<TAB>selectionLine
  T<TAB>module<TAB>constant<TAB>type hash<TAB>constants in the type (space separated)
Lines are 1-based and the end position is exclusive. A space inside a name is written `␣`.
-/
open Lean

def clean (n : Name) : String :=
  let s := toString n
  if s.any (fun c => c == ' ' || c == '\t' || c == '\n') then
    s.map fun c => if c == ' ' || c == '\t' || c == '\n' then '␣' else c
  else s

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
  let out ← IO.getStdout
  for l in (← IO.FS.readFile args[0]!).splitOn "\n" do
    let [path, modName] := l.splitOn "\t" | continue
    let data ← readProofData path
    for (ext, entries) in data.entries do
      if (toString ext).endsWith "declRangeExt" then
        for e in entries do
          let (n, r) : Name × DeclarationRanges := unsafeCast e
          out.putStrLn s!"R\t{modName}\t{clean n}\t{r.range.pos.line}\t{r.range.pos.column}\t{r.range.endPos.line}\t{r.range.endPos.column}\t{r.selectionRange.pos.line}"
    for ci in data.constants do
      out.putStrLn s!"T\t{modName}\t{clean ci.name}\t{hash ci.type}\t{" ".intercalate (ci.type.getUsedConstants.toList.map clean)}"
  return 0
