import Fixture.Proofs

namespace VL.DoubleEven

theorem helper (n : Nat) : Fixture.double n = 2 * n := Fixture.double_eq_two_mul n

theorem main (n : Nat) : Fixture.double n % 2 = 0 := sorry

end VL.DoubleEven
