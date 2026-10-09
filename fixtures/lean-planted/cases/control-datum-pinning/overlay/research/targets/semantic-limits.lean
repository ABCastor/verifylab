import Fixture.SemanticLimits

namespace VL.UnpinnedClass

theorem main : ∀ datum : Nat, ∃ u : Nat → Nat, Fixture.SemanticLimits.solution datum u := sorry

theorem witness : ∃ datum : Nat, ∃ u : Nat → Nat, Fixture.SemanticLimits.solution datum u := sorry

end VL.UnpinnedClass
