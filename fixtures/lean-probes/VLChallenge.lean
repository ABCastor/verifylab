import Probed.Defs

/-!
Planted cases and clean controls for the statement probes that list what a referee must weigh
(tests/test_lean_probes.py): totalized operations, in-project definitions, boundary values, set-theoretic foundations. Core Lean only; the
theorems are never proved here, the probes read their statements.
-/

namespace T

-- Totalized operations: planted, then the clean control next to it.
theorem natSub (n : Nat) (h : 0 < n) : n - 1 + 1 = n := sorry
theorem intSub (n : Int) : n - 1 + 1 = n := sorry
theorem natDiv (n : Nat) : n / 2 ≤ n := sorry
theorem natMod (a b : Nat) : a % b ≤ a := sorry
theorem natModLit (a : Nat) : a % 2 < 2 := sorry
theorem intDiv (a b : Int) : a / b * b + a % b = a := sorry
theorem floatDiv (x y : Float) : x / y = x / y := sorry
theorem floatHalf (x : Float) : x / 2 = x / 2 := sorry
theorem cast (a b : Nat) : ((a - b : Nat) : Int) ≤ a := sorry
theorem castClean (a b : Nat) : (a : Int) - b ≤ a := sorry
theorem toNat (z : Int) : z ≤ z.toNat := sorry

-- In-project definitions, and a totalized operation inside one of them.
theorem viaDef (a b : Nat) : Probed.gap a b ≤ a := sorry
theorem viaClean (a b : Nat) : a ≤ Probed.total a b := sorry
theorem struct (b : Probed.Box) : Probed.area b = b.w * b.h := sorry
theorem viaField (p : Probed.Pos) : 0 ≤ p.n := sorry
theorem viaShared : Probed.shared = Probed.shared + 0 := sorry

-- Boundary values: degenerate at 0, hypotheses refuted at 0 and 1; a statement true at every value is clean.
theorem bndTriv (n m : Nat) : m * n ≤ m ^ (n + 1) ∨ m = 0 := sorry
theorem bndVac (n : Nat) (h : 2 ≤ n) (m : Nat) : m ≤ m * n := sorry
theorem bndClean (n : Nat) : n ≤ n * n := sorry

-- Set-theoretic foundations: one planted theorem per kind, then the controls (names that only contain the words).
theorem fndZFSet (s : ZFSet) : s = s := sorry
theorem fndPSet (p : PSet) : p = p := sorry
theorem fndCardinal (c : Cardinal) : c = c := sorry
theorem fndOrdinal (o : Ordinal) : o = o := sorry
theorem fndChoice (h : Probed.AC) : True := sorry
theorem fndTheory (h : Probed.ZFC) : True := sorry
theorem fndConsistent : Probed.Consistent 0 := sorry
theorem fndViaDef (h : Probed.needsChoice) : True := sorry
theorem fndViaField (c : Probed.Counted) : c = c := sorry
theorem fndPick : Probed.pick = Probed.pick := sorry
theorem fndLookalike (h : Probed.Choiceless) (n : Probed.Cardinality) : True := sorry

end T
