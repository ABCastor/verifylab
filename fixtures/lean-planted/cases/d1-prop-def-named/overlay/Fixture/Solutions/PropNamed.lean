import Fixture.Defs

namespace VL.DoubleEven

def main : Prop := ∀ n : Nat, Fixture.double n % 2 = 0

end VL.DoubleEven
