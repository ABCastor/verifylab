import Fixture.Defs

namespace VL.DoubleEven

/-- The claim, compiled as a proposition. Nothing proves it. -/
def target_claim : Prop := ∀ n : Nat, Fixture.double n % 2 = 0

end VL.DoubleEven
