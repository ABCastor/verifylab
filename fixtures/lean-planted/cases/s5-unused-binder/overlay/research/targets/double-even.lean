import Fixture.Defs

/-!
Trusted target of item `double-even` with an unused binder: it claims something about `double 2` only.
-/

namespace VL.DoubleEven

theorem main (n : Nat) : Fixture.double 2 % 2 = 0 := sorry

end VL.DoubleEven
