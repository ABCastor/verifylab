import Fixture.Defs

/-!
Trusted target of item `double-even`, stated with a constantly-true definition from the trusted closure.
-/

namespace VL.DoubleEven

theorem main (n : Nat) : Fixture.IsGood (Fixture.double n) := sorry

end VL.DoubleEven
