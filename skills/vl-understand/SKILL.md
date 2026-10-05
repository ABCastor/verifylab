---
name: vl-understand
description: Help a human understand, study and contribute to the results of a VerifyLab project — explanations grounded in the proved lemmas, recording the human's intuitions, and Socratic study questions. Use when the person asks to understand, study or explain a result, review a result for meaning, or shares an intuition or idea.
---

# vl-understand — the human side of the research

This skill helps a person digest a checked result: what it says, why it matters and how to use it.
The human's attention is the scarcest resource: one question at a time, short first layer.

## 1. Explanations grounded in the lemmas
- Ground claims about project results in the actual supports: each mathematical step cites `[[id@rev]]` or a Lean
  declaration, and says whether that support is verified, explored or only a conjecture
  (`vl show --brief`). Never present an unverified item as proved. List the supports in the explanation's
  `cites`: `vl validate` then reports an error when one is refuted or retracted and a warning when a result,
  conjecture or intuition is not verified; `[[...]]` links in the body are only checked to exist.
- Adapt the account to what the person needs. Useful parts include: what is established (status words); why it matters, in
  three sentences without notation; the hard step (where the real idea is: do not sand it off, do not
  dwell on trivia); what it does NOT give (the limits, the regime where it fails, the question it does
  not answer); what surprised us; at most one question. One idea per sentence; name things by role.
  Each formula: its question, every symbol named nearby, a reading in words, why it follows.
- Place it: what it extends, what it contradicts, why it is worth (or not worth) the reader's time.
- If you reconstruct how the idea could have been found, label it as a reconstruction.
- Save reusable accounts as `explanation` items (`cites = [...]`) within the project; each conversation need
  not become a record. Label new derivations or illustrative analogies according to their actual support.
  Render PDF or HTML only when asked.
- A failed informal explanation does not invalidate a closed Lean proof, and a closed proof does
  not make an explanation correct. They are checked separately.

## 2. The human's intuitions
- When the person wants to contribute or study, invite their own guess before showing candidates; a direct
  explanation does not require that step.
- Record an idea, hunch or picture as an `intuition` item (`author = "human:<name>"`,
  `recorded_by = "agent:<you>"`): their words verbatim, then a restatement they confirm. In the body,
  in their words: where it comes from, whether they have met many cases like it with quick feedback,
  and what would make them drop it. An optional appraisal is contextual opinion, not a verified probability.
- An intuition is an attributed lead, never evidence, including the owner's. Develop useful consequences,
  a bridge, definitions or microgoals when warranted: `conjecture` or `question` items with
  `cites = ["<intuition-id>"]`, their gaps and possible next investigations or refuting evidence when known, or a road
  (`vl-explore`). Hand them on; when much rides on it, one lane tries to refute it.
- Report back one line per consequence; `vl show <intuition-id>` lists what cites it, with statuses.
  A refuted hunch stays recorded.
- Credit: what builds on the idea cites it; an explanation links it in the body (`[[<intuition-id>]]`)
  and names whose idea it was.
- Sign `human:<name>` only on text that person wrote or approved: an intuition in their words, a review
  they dictated or confirmed (`vl review --author human:<name> --human-approved`). Never write that flag
  for text they have not seen; a review you wrote is `agent:<you>`.

## 3. Study mode (Socratic)
- Use this mode when the person wants to study or practice; a request for a direct explanation does not require
  a prerequisite quiz or a study sequence.
- Ask open questions, never multiple choice. One area at a time.
- Start from at most three prerequisites, each with a one-line micro-check.
- Ask for a prediction or a confidence level before revealing an answer or a number.
- Before a check question, fix its answer from a record or a script that ran. A question nobody can
  answer yet is research: say so. Understanding shows as the person's own reformulation, a correct
  prediction or a transfer, never as "yes" or "clear"; quote their words in the `understanding` review.
- Use a hint ladder: re-anchor → narrow the question → bridging analogy → the smallest missing fact.
- Close with four questions: a calculation, a comprehension question, "say it in your own words",
  and a transfer to a new case. Keep the answers separate from the questions.
- Every number you show comes from a script that was actually run or a receipt that exists.
- Record what was understood or misunderstood as an `understanding` review on the result, so the
  next explanation starts from there. Understanding is never a gate for keeping a result.
