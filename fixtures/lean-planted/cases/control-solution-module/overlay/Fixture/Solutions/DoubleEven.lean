import Fixture.Proofs

namespace VL.DoubleEven

theorem main (n : Nat) : Fixture.double n % 2 = 0 := Fixture.double_mod_two n

end VL.DoubleEven
