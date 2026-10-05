import Fixture.SemanticLimits

namespace VL.ImpossibleClass

theorem main : ∃ datum : Nat, ¬ ∃ u : Nat → Nat, Fixture.SemanticLimits.impossibleSolution datum u := sorry

end VL.ImpossibleClass
