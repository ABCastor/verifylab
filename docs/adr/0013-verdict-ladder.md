# ADR 0013 — The verdict is a ladder of five facts, not one badge

Status: accepted, 2026-10-11. Extends ADR 0001 (trust boundary and lanes) and
ADR 0010 (the probes list what a referee must weigh).

Context: the derived status `verified` is the most visible word of a card, a `vl find` line and the catalog, and it
means one thing: an admitted protected check passed. A reader (a mathematician on a published catalog, an agent
choosing what to build on) takes it for more: that the statement says what the paper says, that a second kernel agrees,
that the axioms are the usual ones. Those are separate facts with separate evidence, and for an audit of an external
corpus (OpenAI's math release) most of them are exactly what is not yet assessed. One word cannot say "the kernel
passed and nobody has compared the statement with the paper". The audit is also run by AI agents with no human
signature to claim, so what each fact rests on has to be on the page.

Decision:

1. **Five rows, always in this order, each `not assessed` until a record assessed it**: Lean kernel, second kernel
   (nanoda), axioms and trusted base, statement fidelity, paper correspondence. The first three are read from the
   receipt the card's proof rests on (a Lean protected pass admitted on the trusted ref: `checked.kernels`,
   `checked.permitted_axioms`; an input that changed since makes them `stale`; an exploratory receipt, a Python check,
   an error or an unsupported check assesses none). Statement fidelity is the deciding fidelity review, as before.
   Paper correspondence is new. No row implies another and none is assessed by default.
2. **A new review kind, `correspondence`** (D6): how the item's formal statement relates to the paper's, as an
   outcome `same`, `stronger`, `weaker` or `different` in `verdict`, per paper statement (the optional `statement`
   field names which; the newest review of each statement counts; statements that disagree give `mixed`, and one judged only before the meaning changed stays as a `stale` statement, so it is never hidden by a fresh review of another). It is
   optional: an item without one shows `not assessed`. It binds the meaning exactly as a fidelity review does
   (`meaning`, `meaning_digest`, and `--expected-meaning-digest` on writing), so an edit of the target, its
   definitions, the theorems or the statement makes it `stale`. It changes no status and no other row.
3. **No single `verified` badge in text for people.** Cards, briefs, impact views, `vl find` and the catalog page lead
   with the ladder (the header line carries a one-line form of it, so it survives truncation) and show the derived
   label under another name: `verified` as `check passed`, `verified-stale` as `check passed, stale`. The derived
   labels themselves are unchanged and stay in `--json`, in the catalog's data, in its filters and in the docs, for
   compatibility; their rules (docs/SPEC.md, "Derived status") do not change.
4. **Derived, never stored.** A row is a function of receipts and reviews already on the trusted ref; nothing is
   written, and `vl export catalog` repeats the card's ladder (`ladder` per item; per row and state in `counts`).

Alternatives rejected: a numeric or colour score (it hides which row is missing and invites ranking by it, which is
the value function's job, not the verdict's); folding fidelity into `verified` (the status would then depend on a
judgement and change when a review goes stale, which today only changes the fidelity line); renaming the labels in
the data (every consumer, test and skill would break for a wording gain that text for people already gets).

Consequences: the catalog has not been released, so its new keys are added to `vl.catalog/1`; once it is, changing its
keys gets a new version (ADR 0011). A passing kernel row with `not assessed` below it is now the normal look of an
imported corpus item, which is the point. `vl validate` still warns about a protected pass with no faithful review;
no warning is added for a missing correspondence review, because it is optional. What `vl` cannot show: whether any
reviewer, human or model, was right.
