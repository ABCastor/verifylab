# ADR 0011 — The catalog is a view of the trusted commit

Status: accepted, 2026-10-11 (owner). Extends ADR 0001 and ADR 0002.

Context: a corpus of hundreds of items (the audit of OpenAI's math release has 416 Comparator challenges) is
read by people who will not run `vl show` item by item, and by tools that want data. A catalog that lists statements
and statuses is also the one place where `vl`'s claims leave the repository: if it can say more than the trusted ref
says, or carry active content from a record, it becomes a way to present a proposal as a result.

Decision: `vl export catalog OUT` writes two files, `catalog.json` (schema `vl.catalog/1`, docs/SPEC.md) and one
static `index.html`.

1. A view, not a record. Nothing it shows is stored anywhere else and nothing reads it back: status stays derived. It
   is built from the cards of `vl show`, so the two cannot disagree.
2. Trusted commit only. It reads through `TrustedRepo`: only admitted receipts and reviews count, a candidate input is
   compared with the trusted ref and never with the worktree, and the exploratory store is not read. A dirty
   worktree, a proposal or a deleted file changes nothing. `--trusted-ref` is named in the file, as everywhere.
3. Deterministic. No time, host or machine path; items by id, keys sorted. The same records give the same bytes, so a
   catalog can be rebuilt by anyone from a clone and compared.
4. The page is inert. One file, no external resource: the style and the filter script are inline under a
   Content-Security-Policy that allows only their sha256; record text is escaped; a `ref` is text, never a link.
   Every item is in the HTML, so the page reads without the script, which only hides what a filter excludes.
5. It repeats and never decides. The probe state of an item is a reading of the receipt's probes (`vacuous`,
   `trivial`, `incomplete`, `skipped`, `clean`, `none`); fidelity is the card's derived label with the reviews that
   decided it. A clean probe says the battery found nothing, not that the statement is adequate.
6. The replay command is `vl replay ID --receipt RID` (ADR 0009) for the item's own protected receipt, the one its
   proof shows; it is not a promise that the tool environment can be rebuilt (docs/REPLAY.md).

Consequences: provenance beyond the item's file and first commit is what the item says: its `ref` (an import's is
`URL@COMMIT:PATH`) and the source items it cites, and the first paragraph of the body, free text. Areas exist only
where items carry `area:NAME` tags (an import tags the area of the solution module's path). The catalog grows with the corpus (about 7 KB of JSON and 7 KB of HTML per item in one measured corpus); a corpus
that needs a smaller page needs pagination, which is a new decision. Changing the keys of `vl.catalog/1` needs a new
version.
