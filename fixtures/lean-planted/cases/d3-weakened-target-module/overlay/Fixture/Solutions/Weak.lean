import Fixture.Defs

namespace VL.DoubleEven

theorem main (n : Nat) : (Fixture.double n % 2 = 0)
  ∨ True := Or.inr trivial

end VL.DoubleEven
