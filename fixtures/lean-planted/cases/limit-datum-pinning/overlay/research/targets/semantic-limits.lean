import Fixture.SemanticLimits

namespace VL.PinnedClass

theorem main : ∃ datum : Nat, ¬ ∃ u : Nat → Nat, Fixture.SemanticLimits.pinnedSolution datum u := sorry

theorem witness : ∃ datum : Nat, ∃ u : Nat → Nat, Fixture.SemanticLimits.pinnedSolution datum u := sorry

theorem excludedDatum : ¬ ∃ u : Nat → Nat, Fixture.SemanticLimits.pinnedSolution 1 u := sorry

end VL.PinnedClass
