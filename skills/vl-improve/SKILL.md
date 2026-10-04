---
name: vl-improve
description: Improve existing results in a VerifyLab project — clean the source, simplify the mathematics, strengthen or generalize a lemma, or make it understandable and reusable — and prove the improvement is real. Use when results exist but are long, narrow, duplicated, burdened with assumptions, or hard for others to use.
---

# vl-improve — four different kinds of better

"Shorter" is not the goal. Pick the mode from the bottleneck, say which one you are doing, and
show what was gained and what was lost.

| Mode | Bottleneck | Real gain | Not a gain |
|---|---|---|---|
| **Clean the source** | maintenance hurts: duplication, fragile proofs | same statements, less real burden (one general proof instead of two copies) | complexity moved into a dependency |
| **Simplify the mathematics** | the argument is hard to follow or to check | fewer or more natural steps, same or weaker premises; compare the full dependency burden | fewer Lean lines with the same idea |
| **Strengthen / generalize** | assumptions block reuse, scope too narrow, bound too weak | fewer premises, wider scope, better bound or a cleaner trade-off; may need MORE code | a new name for the same statement |
| **Explain / reuse** | others cannot apply the result correctly | a fresh reader or agent applies it to a new question and names its limits | a summary that hides the hard step |

## Rules
- A stronger or more general version is a claim: state the exact implication, equivalence or
  parameter-dependent trade-off and prove it. Test each removed premise: is it really unnecessary?
- Keep alternatives that are incomparable (a general lemma and a narrower one with a better bound).
  Link them with `supersedes` only when the new one is better for every use; otherwise record a
  `compare` review that says which is better for what.
- Never overwrite history. A correction is a `correction` review on the old revision plus a new
  revision; `vl show --impact <id>` tells you which uses must be re-checked.
- A longer version can be the better one. Conclude that when it is true.
- Look for the canonical form: the natural generality, the right proof rather than the first one,
  and the links to neighbouring results (Tao's "canonicalization"). Generalize by concentration, not
  dilution; prefer natural generality to finitary simplifications, where blueprint errors cluster.
- After an obligation closes, look back: which hypotheses were used, a second proof, the sibling
  questions the method reaches, where it stops. Write the method in the result's body: trigger (the
  goal's shape), move, example, fails-when.

## Record
New or revised `result` items with targets, a `compare` review against the previous version
(mode, what improved, what got worse, evidence), and the protected checks run by the coordinator.
