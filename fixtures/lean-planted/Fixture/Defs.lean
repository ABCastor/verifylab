/-
Definitions the planted targets talk about. A candidate that changes `double`
changes the meaning of every target that mentions it.
-/

namespace Fixture

/-- Doubling. -/
def double (n : Nat) : Nat := n + n

end Fixture
