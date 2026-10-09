import Fixture.Defs

namespace Fixture.SemanticLimits

/-- A toy solution grows by one and starts at the requested datum. -/
def solution (datum : Nat) (u : Nat → Nat) : Prop :=
  u 0 = datum ∧ ∀ n, u (n + 1) = u n + 1

/-- The added flatness condition makes every instance impossible. -/
def impossibleSolution (datum : Nat) (u : Nat → Nat) : Prop :=
  solution datum u ∧ ∀ n, u (n + 1) = u n

/-- This class admits zero datum but silently excludes every other datum. -/
def pinnedSolution (datum : Nat) (u : Nat → Nat) : Prop :=
  solution datum u ∧ datum = 0

theorem solution_exists (datum : Nat) : ∃ u, solution datum u := by
  refine ⟨fun n => datum + n, ?_⟩
  constructor
  · simp
  · intro n
    change datum + (n + 1) = datum + n + 1
    omega

theorem impossible_empty (datum : Nat) : ¬ ∃ u, impossibleSolution datum u := by
  rintro ⟨u, h⟩
  have growth := h.1.2 0
  have flat := h.2 0
  omega

theorem pinned_zero : ∃ datum u, pinnedSolution datum u := by
  obtain ⟨u, h⟩ := solution_exists 0
  exact ⟨0, u, h, rfl⟩

theorem pinned_one_empty : ¬ ∃ u, pinnedSolution 1 u := by
  rintro ⟨u, h⟩
  have impossible : (1 : Nat) = 0 := h.2
  cases impossible

end Fixture.SemanticLimits
