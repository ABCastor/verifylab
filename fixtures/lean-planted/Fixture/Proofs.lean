import Fixture.Defs

namespace Fixture

theorem double_eq_two_mul (n : Nat) : double n = 2 * n := by
  unfold double
  omega

/-- The genuine proof of the `double-even` target. -/
theorem double_mod_two (n : Nat) : double n % 2 = 0 := by
  rw [double_eq_two_mul]
  exact Nat.mul_mod_right 2 n

/-- A more general lemma: `double n` is divisible by every divisor of 2. -/
theorem double_mod_of_dvd_two (k n : Nat) (h : k ∣ 2) : double n % k = 0 := by
  rw [double_eq_two_mul]
  exact Nat.mod_eq_zero_of_dvd (Nat.dvd_trans h (Nat.dvd_mul_right 2 n))

/-- A helper about `double` that is NOT the target statement. -/
theorem double_pos (n : Nat) (h : 0 < n) : 0 < double n := by
  unfold double
  omega

end Fixture
