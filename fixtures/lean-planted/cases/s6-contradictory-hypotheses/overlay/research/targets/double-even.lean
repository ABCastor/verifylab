import Fixture.Defs

/-!
Trusted target of item `double-even`, vacuous on the trusted side: no `n` satisfies both hypotheses, so the
(false) conclusion is proved for nothing.
-/

namespace VL.DoubleEven

theorem main (n : Nat) (h₁ : n < 2) (h₂ : 3 < n) : Fixture.double n % 2 = 1 := sorry

end VL.DoubleEven
