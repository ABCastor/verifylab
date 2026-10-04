import Fixture.Defs

/-!
Trusted target of item `double-even`. Checks read this file from the trusted ref only.
-/

namespace VL.DoubleEven

/-- Every doubled natural number is even. -/
theorem main (n : Nat) : Fixture.double n % 2 = 0 := sorry

end VL.DoubleEven
