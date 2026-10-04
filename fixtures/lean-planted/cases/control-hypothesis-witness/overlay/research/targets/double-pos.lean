import Fixture.Defs

/-!
Trusted target of a genuine result with a hypothesis, and its non-vacuity witness.
-/

namespace VL.DoublePos

/-- Doubling a positive number gives a positive number. -/
theorem main (n : Nat) (h : 0 < n) : 0 < Fixture.double n := sorry

/-- The hypothesis of `main` can hold: it holds at `n = 1`. -/
theorem witness : ∃ n : Nat, 0 < n := sorry

end VL.DoublePos
