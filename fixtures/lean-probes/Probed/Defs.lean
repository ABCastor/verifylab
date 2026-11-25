import Lean

/-!
In-project definitions of the statement-probe fixture: the probes list those a statement depends on, and look for
totalized operations in their values and in the fields of their structures (`gap` subtracts in ℕ, and so does a
field of `Pos`; `total`, `area` and `Box` do not). `shared` is a value whose subterms are shared 2^60 times over, like
the proof terms tactics leave in a definition: walked as a tree it never ends, so the scan must stop at its
heartbeat limit.

The set-theoretic foundations the probes name are planted by name, as Mathlib has them at the root (`ZFSet`, `PSet`,
`Cardinal`, `Ordinal`) and inside the project (`AC`, `ZFC`, `Consistent`, a definition that uses `AC`, a structure
with a `Cardinal` field); `Choiceless` and `Cardinality` only contain such words, and `pick` uses `Classical.choice`
as a tool (a totalized constant, not foundations): the controls.
-/

structure ZFSet where
  rank : Nat

structure PSet where
  rank : Nat

def Cardinal : Type := Nat

def Ordinal : Type := Nat

namespace Probed

def AC : Prop := ∀ n : Nat, n = n

def ZFC : Prop := ∀ n : Nat, n = n

def Consistent (t : Nat) : Prop := t = t

def needsChoice : Prop := AC ∧ True

noncomputable def pick : Nat := Classical.choice ⟨0⟩

structure Counted where
  size : Cardinal

def Choiceless : Prop := ∀ n : Nat, n = n

def Cardinality : Type := Nat

def gap (a b : Nat) : Nat := a - b

def total (a b : Nat) : Nat := a + b

structure Box where
  w : Nat
  h : Nat

def area (b : Box) : Nat := b.w * b.h

structure Pos where
  n : Nat
  h : n - 1 < n

open Lean Elab Command in
elab "def_shared " n:ident depth:num : command => do
  let mut value : Expr := mkNatLit 1
  for _ in [0:depth.getNat] do value := mkApp2 (mkConst ``Nat.add) value value
  liftCoreM <| addDecl (.defnDecl { name := `Probed ++ n.getId, levelParams := [], type := mkConst ``Nat, value,
                                    hints := .abbrev, safety := .safe })

def_shared shared 60

end Probed
