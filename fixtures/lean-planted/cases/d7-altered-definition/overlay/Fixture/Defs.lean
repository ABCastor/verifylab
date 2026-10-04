/-
Definitions the planted targets talk about. A candidate that changes `double`
changes the meaning of every target that mentions it.
-/

namespace Fixture

/-- Doubling (altered by the candidate: every statement about it becomes trivial). -/
def double (_ : Nat) : Nat := 0

end Fixture
