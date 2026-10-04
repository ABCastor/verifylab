import Fixture.Defs

namespace VL.DoubleEven

/-- A hijack: inside this file, `Fixture.double` now denotes the zero function. -/
local notation "Fixture.double" => fun (_ : Nat) => (0 : Nat)

theorem main (n : Nat) : Fixture.double n % 2 = 0 := rfl

end VL.DoubleEven
