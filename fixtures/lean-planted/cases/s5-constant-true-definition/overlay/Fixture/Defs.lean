/-
Definitions the planted targets talk about. A candidate that changes `double`
changes the meaning of every target that mentions it.
-/

namespace Fixture

/-- Doubling. -/
def double (n : Nat) : Nat := n + n

/-- "n is good": a predicate that names a property and holds of everything. -/
def IsGood (_ : Nat) : Prop := True

end Fixture
