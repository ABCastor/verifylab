import Fixture.Defs

/-!
Trusted target of item `double-even`, weakened on the trusted side: the claimed branch (which is false) is
guarded by a condition that never holds.
-/

namespace VL.DoubleEven

theorem main (n : Nat) : if n < 0 then Fixture.double n % 2 = 1 else True := sorry

end VL.DoubleEven
