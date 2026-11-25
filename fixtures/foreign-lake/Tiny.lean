import Tiny.Step

namespace Tiny

theorem conclusion (p : Prop) (certificate : p) : p := by
  exact step p certificate

end Tiny
