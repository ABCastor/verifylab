"""`vl find TEXT`: lexical search over items and Lean declaration names under `[lean] roots`.

The index only says where to look. Titles and derived status of hits are read from the records.
No network, and no library outside the project: dependencies such as Mathlib are not indexed.
"""

from __future__ import annotations

import sqlite3

from ..output import emit, fail
from ..records import ITEM_KINDS
from ..render import Context, trust_data, trust_text
from . import open_repo
from ..repo import Repo
from ..index import open_index

DECL_KIND = "lean"


def register(sub):
    p = sub.add_parser("find", help="search items and Lean declaration names (local only)")
    p.add_argument("text", help="words to find; every word must match")
    p.add_argument("--kind", choices=(*ITEM_KINDS, DECL_KIND),
                   help=f"only items of this kind, or '{DECL_KIND}' for Lean declarations only")
    p.add_argument("--limit", type=int, default=10, help="results shown per group (default 10)")
    return p


def _search(index, args):
    item_ids, decls, decl_total = [], [], 0
    if args.kind != DECL_KIND:
        item_ids = index.search_items(args.text, args.kind)
    if args.kind in (None, DECL_KIND):
        decls, decl_total = index.search_decls(args.text, args.limit)
    return item_ids, decls, decl_total


def run(args) -> int:
    if not args.text.strip():
        return fail("find needs some text")
    if args.limit < 1:
        return fail("--limit must be at least 1")
    repo = open_repo(args)
    index = open_index(repo)
    try:
        try:
            item_ids, decls, decl_total = _search(index, args)
        except sqlite3.DatabaseError as exc:  # corruption found only while querying
            index.close()
            index = open_index(repo, force=f"corrupt: {exc}")
            item_ids, decls, decl_total = _search(index, args)
        info = index.info
    finally:
        index.close()

    ctx = Context.load(repo)
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
    if args.kind != DECL_KIND:
        lines.append(f"items ({len(shown)} of {len(items)}):")
        for i in shown:
            kind = i["kind"] + (f"/{i['claim']}" if i["claim"] else "")
            source = f"  [ref: {i['ref']}; access: {i['access']}]" if i["kind"] == "source" else ""
            lines.append(f"  {i['id']}@{i['revision'][:12]}  [{kind}; status: {i['status']}]  {i['title']}{source}")
        if len(items) > len(shown):
            lines.append(f"  [MORE: {len(items) - len(shown)} more items match; raise --limit to see them]")
    if args.kind in (None, DECL_KIND):
        lines.append(f"lean declarations ({len(decl_rows)} of {decl_total}):")
        for d in decl_rows:
            lines.append(f"  {d['name']}  {d['kind']}  {d['path']}:{d['line']}")
        if decl_total > len(decl_rows):
            lines.append(f"  [MORE: {decl_total - len(decl_rows)} more declarations match; raise --limit to see them]")
    roots = ", ".join(repo.config.lean.roots) or "none configured"
    scope = (f"searched {info.items} items and {info.decls} declarations in {info.lean_files} Lean files "
             f"under [lean] roots ({roots}); {'FTS5' if info.fts else 'LIKE'} search")
    lines.append(scope)
    if info.rebuilt:
        lines.append(f"index rebuilt ({info.reason}): {info.path}")
    if info.unparsed_items:
        lines.append(f"WARNING: {info.unparsed_items} item files do not parse and were not searched; run vl validate")
    trust = trust_data(repo)
    if items and trust_text(trust):
        lines.insert(0, f"NOTE: {trust_text(trust)}")
    found = bool(items) or bool(decl_rows)
    if not found:
        lines.insert(0, f"no match for {args.text!r}")
    emit(args, "\n".join(lines), {
        "query": args.text, "kind": args.kind,
        "items": shown, "items_total": len(items),
        "declarations": decl_rows, "declarations_total": decl_total,
        "index": {"path": info.path, "rebuilt": info.rebuilt, "reason": info.reason, "fts": info.fts,
                  "items": info.items, "unparsed_items": info.unparsed_items,
                  "lean_files": info.lean_files, "declarations": info.decls},
        "roots": list(repo.config.lean.roots), "trust": trust,
    })
    return 0 if found else 1
