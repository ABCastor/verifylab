"""`vl find TEXT`: indexed items/Lean names plus live registered notes and validated reviews.

The index only says where to look. Titles and derived status of hits are read from the records.
No network, and no library outside the project: dependencies such as Mathlib are not indexed.
"""

from __future__ import annotations

import re
import sqlite3

from .. import context
from ..output import emit, fail
from ..ladder import human_label
from ..records import ITEM_KINDS
from ..render import Context, trust_data, trust_text
from . import open_repo
from ..index import INDEX_DISCLAIMER, open_index

DECL_KIND = "lean"


def register(sub):
    p = sub.add_parser("find", help="search items, Lean declarations, registered notes and reviews (local only)")
    p.add_argument("text", help="words to find; every word must match")
    p.add_argument("--kind", choices=(*ITEM_KINDS, DECL_KIND),
                   help=f"only items of this kind, or '{DECL_KIND}' for Lean declarations only")
    p.add_argument("--limit", type=int, default=10, help="results shown per group (default 10)")
    p.add_argument("--scope", choices=("all", "items", "lean", "notes", "reviews"), default="all",
                   help="scope to search (default all); --kind narrows to items or Lean declarations")
    p.add_argument("--regex", action="store_true",
                   help="case-insensitive regular expression; requires --scope notes or reviews")
    return p


def _search(index, args):
    item_ids, decls, decl_total = [], [], 0
    imp_items, imp_decls, imp_decl_total = [], [], 0
    if args.scope in ("all", "items") and args.kind != DECL_KIND:
        item_ids = index.search_items(args.text, args.kind)
        imp_items = index.search_imported_items(args.text, args.kind)
    if args.scope in ("all", "lean") and args.kind in (None, DECL_KIND):
        decls, decl_total = index.search_decls(args.text, args.limit)
        imp_decls, imp_decl_total = index.search_imported_decls(args.text, args.limit)
    return item_ids, decls, decl_total, imp_items, imp_decls, imp_decl_total


def run(args) -> int:
    if not args.text.strip():
        return fail("find needs some text")
    if args.limit < 1:
        return fail("--limit must be at least 1")
    if args.kind:
        expected = "lean" if args.kind == DECL_KIND else "items"
        if args.scope not in ("all", expected):
            return fail(f"--kind {args.kind} requires --scope {expected} or all")
        args.scope = expected
    if args.regex and args.scope not in ("notes", "reviews"):
        return fail("--regex requires --scope notes or reviews")
    try:
        matches = context.matcher(args.text, args.regex)
    except re.error as exc:
        return fail(f"invalid regular expression: {exc}")
    repo = open_repo(args)
    info = None
    item_ids, decls, decl_total = [], [], 0
    imp_items, imp_decls, imp_decl_total = [], [], 0
    if args.scope in ("all", "items", "lean"):
        index = open_index(repo)
        try:
            try:
                item_ids, decls, decl_total, imp_items, imp_decls, imp_decl_total = _search(index, args)
            except sqlite3.DatabaseError as exc:  # corruption found only while querying
                index.close()
                index = open_index(repo, force=f"corrupt: {exc}")
                item_ids, decls, decl_total, imp_items, imp_decls, imp_decl_total = _search(index, args)
            info = index.info
        finally:
            index.close()

    ctx = Context.load(repo) if item_ids else None
    items = []
    for item_id in item_ids:
        item = ctx.items.get(item_id)
        if item is None:  # the index is a cache; a record that no longer parses is not shown
            continue
        items.append({"id": item.id, "kind": item.kind, "claim": item.claim, "title": item.title,
                      "status": ctx.label(item.id), "revision": item.revision,
                      "ref": item.ref, "access": item.access})
    shown = items[:args.limit]
    decl_rows = [{"name": n, "kind": k, "path": p, "line": ln} for n, k, p, ln in decls]

    lines = []
    if args.scope in ("all", "items"):
        lines.append(f"items ({len(shown)} of {len(items)}):")
        for i in shown:
            kind = i["kind"] + (f"/{i['claim']}" if i["claim"] else "")
            source = f"  [ref: {i['ref']}; access: {i['access']}]" if i["kind"] == "source" else ""
            status = human_label(i["status"])
            lines.append(f"  {i['id']}@{i['revision'][:12]}  [{kind}; status: {status}]  {i['title']}{source}")
        if len(items) > len(shown):
            lines.append(f"  [MORE: {len(items) - len(shown)} more items match; raise --limit to see them]")
    if args.scope in ("all", "lean"):
        lines.append(f"lean declarations ({len(decl_rows)} of {decl_total}):")
        for d in decl_rows:
            lines.append(f"  {d['name']}  {d['kind']}  {d['path']}:{d['line']}")
        if decl_total > len(decl_rows):
            lines.append(f"  [MORE: {decl_total - len(decl_rows)} more declarations match; raise --limit to see them]")
    shown_imp_items = imp_items[:args.limit]
    if shown_imp_items or imp_decls:
        lines.append(f"imported package locators ({INDEX_DISCLAIMER}):")
        for i in shown_imp_items:
            lim = f"  limits: {'; '.join(i['limits'])}" if i["limits"] else ""
            loc = f"  receipt={i['evidence_locator'] or 'none'} review={i['review_locator'] or 'none'}"
            lines.append(f"  [locator only] {i['package']}@{i['rev'][:12]}::{i['id']}@{i['revision'][:12]}  {i['title']}{lim}{loc}")
        for d in imp_decls:
            lines.append(f"  [locator only] {d['name']}  {d['kind']}  {d['path']}:{d['line']}")
    roots = ", ".join(repo.config.lean.roots) or "none configured"
    if info:
        lines.append(f"searched {info.items} items and {info.decls} declarations in {info.lean_files} Lean files "
                     f"under [lean] roots ({roots}); {'FTS5' if info.fts else 'LIKE'} search")
    if info and info.rebuilt:
        lines.append(f"index rebuilt ({info.reason}): {info.path}")
    if info and info.unparsed_items:
        lines.append(f"WARNING: {info.unparsed_items} item files do not parse and were not searched; run vl validate")
    notes, reviews, warnings = [], [], []
    note_files, review_records = 0, 0
    if args.scope in ("all", "notes"):
        notes, problems, note_files = context.search_notes(repo, matches)
        warnings.extend(problems)
        lines.append(f"notes ({min(len(notes), args.limit)} of {len(notes)}; {note_files} files searched live):")
        lines.extend(f"  {row['locator']}  {row['text']}" for row in notes[:args.limit])
    if args.scope in ("all", "reviews"):
        reviews, problems, review_records = context.search_reviews(repo, matches)
        warnings.extend(problems)
        lines.append(f"reviews ({min(len(reviews), args.limit)} of {len(reviews)}; {review_records} records searched live):")
        for row in reviews[:args.limit]:
            state = "admitted" if row["admitted"] else "proposal, not admitted"
            snippet = " ".join(row["text"].split())[:500]
            if "assessment" in row:
                ratings = ", ".join(f"{name}: {rating.get('value', rating.get('score', rating.get('abstention')))}"
                                    for name, rating in row["assessment"]["ratings"].items())
                snippet = f"{ratings}; basis {row['applicability']['state']}" + (f"; {snippet}" if snippet else "")
            lines.append(f"  {row['locator']}  [{row['kind']}; {row['author']}; {state}]  {snippet}")
    for group, rows in (("notes", notes), ("reviews", reviews)):
        if len(rows) > args.limit:
            lines.append(f"  [MORE: {len(rows) - args.limit} more {group} match; raise --limit to see them]")
    lines.extend(f"WARNING: {warning}" for warning in warnings)
    trust = trust_data(repo)
    if items and trust_text(trust):
        lines.insert(0, f"NOTE: {trust_text(trust)}")
    found = (
        bool(items) or bool(decl_rows) or bool(notes) or bool(reviews)
        or bool(shown_imp_items) or bool(imp_decls)
    )
    if not found:
        lines.insert(0, f"no match for {args.text!r}")
    emit(args, "\n".join(lines), {
        "query": args.text, "kind": args.kind, "scope": args.scope, "regex": args.regex,
        "items": shown, "items_total": len(items),
        "declarations": decl_rows, "declarations_total": decl_total,
        "imported_items": shown_imp_items, "imported_items_total": len(imp_items),
        "imported_declarations": imp_decls, "imported_declarations_total": imp_decl_total,
        "index_disclaimer": INDEX_DISCLAIMER,
        "notes": notes[:args.limit], "notes_total": len(notes), "note_files": note_files,
        "reviews": reviews[:args.limit], "reviews_total": len(reviews), "review_records": review_records,
        "warnings": warnings,
        "index": {"path": info.path, "rebuilt": info.rebuilt, "reason": info.reason, "fts": info.fts,
                  "items": info.items, "unparsed_items": info.unparsed_items,
                  "lean_files": info.lean_files, "declarations": info.decls} if info else None,
        "roots": list(repo.config.lean.roots), "trust": trust,
    })
    return 0 if found else 1
