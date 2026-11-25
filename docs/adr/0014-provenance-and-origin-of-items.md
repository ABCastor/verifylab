# ADR 0014 — Items declare where their statement and their making come from

Status: accepted, 2026-10-11 (owner, decision 2 of the evening). Extends ADR 0007 and ADR 0011.

Context: the audit of a corpus a lab released, and the exploration of variants around its results, mix statements
written by the lab, by a model, by an upstream reviewer and by the project. A reader who orders results by importance,
or wants to follow an idea back to what inspired it, needs to know which is which and which agent, with which model
and context, made each item. The tokens and the model are also the cost of a result.

Decision: two optional tables in the item's front matter, shown by the card and the catalog and read by nothing else.

1. `[provenance]` answers where the statement comes from: `kind` is `upstream-reviewed`, `lab-authored`,
   `model-authored` or `own`; `source` (URL, commit, path) is required for the first two. It is the statement's, not
   the proof's: a proof is what the receipts say.
2. `[origin]` answers how the item came to be: `agent` (required), `model`, `context` (a pointer to the brief, plan or
   record the agent was given, never its text), `tokens` when the harness reports them, `from` (items it was made
   from: an idea, a result, a failure) and `inspired_by` (records, sources, people, URLs). Ideas are items (`intuition`,
   `conjecture`, `question`), so the chain from an idea to the theorems made from it is a chain of `from`.
3. Declared, not checked. A model-authored item can say `own`; `upstream-reviewed` is a claim about someone else's
   review that `vl` cannot see. Neither table enters a status, a question digest or the binding of a fidelity review;
   an `upstream-reviewed` item without receipts is `unverified` like any other. The card heads the section "declared by
   the author, not checked". This is the same footing as `author` and `recorded_by`.
4. Only the shape and `origin.from` are checked. Words are single lines of printable text, so none can pass for
   another line of the card; `origin.from` is a relation, so `vl validate` reports an id that names no item.
5. Asked, not required. `vl validate` warns when an item created after 2026-10-11 has no `[origin]`, or has a
   `[lean]` or `[python]` binding and no `[provenance]`. Older items are never asked: their record stays valid and quiet.
6. The importer fills them. `vl import comparator` writes `[provenance]` on each result (`--provenance`, default
   `lab-authored`, with `SOURCE-URL@COMMIT:PATH.lean`) and `[origin]` on every item it makes (the importer's author, the
   source files as the context). Items imported before keep what they have.
7. Skills tell agents to fill `[origin]` on every item they create, with the model and tokens when they know them,
   and the card, not the agent, is where a reader sees it.

Consequences: the catalog gains two keys per item, `statement_provenance` and `origin`; `vl.catalog/1` stays, since
SPEC allows adding a key beside the others. The front-matter key set grows by two. The tables can be wrong or missing;
a reader who orders results by provenance orders by what authors said, and the card says so. A later value function
(ordering of exploration and human attention) may read them, and may not turn them into a status.
