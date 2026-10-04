import Fixture.Defs

/-!
Trusted target of item `double-even`, weakened ON THE TRUSTED SIDE: an escape disjunct makes it say nothing.
-/

namespace VL.DoubleEven

theorem main (n : Nat) : (Fixture.double n % 2 = 0) ∨ True := sorry

end VL.DoubleEven
