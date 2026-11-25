"""`vl new`: scaffold a new item record and Lean target that pass `vl validate`."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

from .. import leanmod, records
from ..output import emit, fail
from ..records import (AUTHOR_RE, CLAIM_TYPES, ID_RE, ITEM_KINDS, PROVENANCE_KINDS, RecordError, SOURCE_REQUIRED,
                       parse_item, toml_list, toml_str)
from ..repo import ITEMS, TARGETS
from . import open_repo

LEAN_KEYWORDS = frozenset("theorem lemma def axiom by let in fun forall match with where end namespace section "
                          "import open variable inductive structure instance class if then else do return _".split())
DEFAULT_AUTHOR = "agent:vl"
NEEDS_STATEMENT = ("question", "result", "conjecture", "intuition")


def register(sub):
    p = sub.add_parser("new", help="scaffold an item TOML and Lean target that pass `vl validate`")
    p.add_argument("id", metavar="ID", help="kebab-case item identifier (becomes research/items/ID.md)")
    p.add_argument("--kind", choices=ITEM_KINDS, default="conjecture", help="item kind (default: conjecture)")
    p.add_argument("--title", help="human-readable title (default: derived from ID)")
    p.add_argument("--author", default=DEFAULT_AUTHOR, help=f"author (`human:name` or `agent:name`, default {DEFAULT_AUTHOR})")
    p.add_argument("--claim", choices=CLAIM_TYPES, help="claim type (required for `result`; default formal with Lean target)")
    p.add_argument("--statement", help="plain-language statement of what is claimed or asked")
    p.add_argument("--provenance", choices=PROVENANCE_KINDS, default="own",
                   help="statement provenance kind (default: own)")
    p.add_argument("--source", dest="provenance_source", help="provenance source URL, commit, or path")
    p.add_argument("--model", dest="origin_model", help="model identifier recorded in [origin]")
    p.add_argument("--context", dest="origin_context", default="rich",
                   help="context mode or source recorded in [origin] (default: rich)")
    p.add_argument("--uses", nargs="+", default=[], metavar="ID", help="item ids this item depends on")
    p.add_argument("--cites", nargs="+", default=[], metavar="ID", help="source ids or refs cited")
    p.add_argument("--tags", nargs="+", default=[], metavar="TAG", help="tags such as area:NAME")
    p.add_argument("--theorem", default="main", help="theorem name inside the Lean namespace (default: main)")
    p.add_argument("--namespace", help="Lean namespace (default: VL.<PascalCase(ID)>)")
    p.add_argument("--no-lean", action="store_true", help="skip creating a Lean target file and [lean] table")
    p.add_argument("--ref", help="bibliographic ref (for `source` items)")
    p.add_argument("--access", choices=records.SOURCE_ACCESS, help="access level (for `source` items)")
    p.epilog = ("Refuses when the item id or target path already exists or violates schema rules, validates the "
                "generated front matter before writing, and leaves a Lean target skeleton ready to formalize.")
    return p


def _pascal_id(item_id: str) -> str:
    name = "".join(part.capitalize() for part in item_id.split("-") if part)
    return "Item" + name if name[0].isdigit() else name


def _lean_scaffold(item_id: str, namespace: str, theorem: str) -> str:
    return "\n".join([
        f"-- Target scaffold for item `{item_id}`",
        "",
        f"namespace {namespace}",
        "",
        f"theorem {theorem} : True := by",
        "  sorry",
        "",
        f"end {namespace}",
        "",
    ])


def _validate_args(args) -> str | None:
    if not ID_RE.match(args.id):
        return f"invalid item id '{args.id}': must match {ID_RE.pattern}"
    if not AUTHOR_RE.match(args.author):
        return f"invalid author '{args.author}': must match {AUTHOR_RE.pattern}"
    if args.provenance in SOURCE_REQUIRED and not args.provenance_source:
        return f"provenance kind '{args.provenance}' requires --source"
    if args.kind == "source" and (not args.ref or not args.access):
        return "kind 'source' requires both --ref and --access"
    if not leanmod.is_valid_component(args.theorem) or args.theorem in LEAN_KEYWORDS:
        return f"invalid Lean theorem name '{args.theorem}'"
    if args.namespace is not None and (not leanmod.is_valid_module_name(args.namespace)
                                       or set(args.namespace.split(".")) & LEAN_KEYWORDS):
        return f"invalid Lean namespace '{args.namespace}'"
    return None


def _item_markdown(args, title: str, target_rel: str | None, decl: str | None) -> str:
    today = date.today().isoformat()
    claim = args.claim or ("formal" if args.kind in ("result", "conjecture") and target_rel else None)
    if args.kind == "result" and claim is None:
        claim = "prose"
    statement = args.statement or (f"Formal statement for {title}." if args.kind in NEEDS_STATEMENT else None)
    lines = [
        "+++",
        f"id = {toml_str(args.id)}",
        f"kind = {toml_str(args.kind)}",
        f"title = {toml_str(title)}",
        f"author = {toml_str(args.author)}",
        f"created = {toml_str(today)}",
    ]
    if statement is not None:
        lines.append(f"statement = {toml_str(statement)}")
    if claim is not None:
        lines.append(f"claim = {toml_str(claim)}")
    if args.ref:
        lines.append(f"ref = {toml_str(args.ref)}")
    if args.access:
        lines.append(f"access = {toml_str(args.access)}")
    for field, values in (("uses", args.uses), ("cites", args.cites), ("tags", args.tags)):
        if values:
            lines.append(f"{field} = {toml_list(values)}")
    if target_rel and decl:
        lines += ["", "[lean]", f"target = {toml_str(target_rel)}", f"theorems = {toml_list([decl])}"]
        lines += ["", "[provenance]", f"kind = {toml_str(args.provenance)}"]
        if args.provenance_source:
            lines.append(f"source = {toml_str(args.provenance_source)}")
    elif args.provenance != "own" or args.provenance_source:
        lines += ["", "[provenance]", f"kind = {toml_str(args.provenance)}"]
        if args.provenance_source:
            lines.append(f"source = {toml_str(args.provenance_source)}")
    lines += ["", "[origin]", f"agent = {toml_str(args.author)}"]
    if args.origin_model:
        lines.append(f"model = {toml_str(args.origin_model)}")
    if args.origin_context:
        lines.append(f"context = {toml_str(args.origin_context)}")
    body = f"Scaffold for `{args.id}`.\n" if args.kind == "explanation" else ""
    return "\n".join([*lines, "+++", body])


def run(args) -> int:
    if problem := _validate_args(args):
        return fail(problem)
    try:
        repo = open_repo(args)
    except FileNotFoundError as exc:
        return fail(str(exc))

    item_rel = f"{repo.rel(ITEMS)}/{args.id}.md"
    item_path = repo.root / item_rel
    if item_path.exists() or repo.read_trusted(item_rel) is not None:
        return fail(f"item '{args.id}' already exists ({item_rel})")

    with_lean = (not args.no_lean
                 and args.kind in ("result", "conjecture")
                 and (args.claim in (None, "formal")))
    target_rel = f"{repo.rel(TARGETS)}/{args.id}.lean" if with_lean else None
    target_path = (repo.root / target_rel) if target_rel else None
    if target_path and (target_path.exists() or repo.read_trusted(target_rel) is not None):
        return fail(f"Lean target '{target_rel}' already exists")

    title = args.title or args.id.replace("-", " ").title()
    namespace = args.namespace or f"VL.{_pascal_id(args.id)}"
    decl = f"{namespace}.{args.theorem}" if with_lean else None
    markdown = _item_markdown(args, title, target_rel, decl)
    try:
        parse_item(markdown.encode("utf-8"), Path(item_rel))
    except RecordError as exc:
        return fail(f"generated item invalid: {exc}")

    item_path.parent.mkdir(parents=True, exist_ok=True)
    item_path.write_text(markdown, encoding="utf-8")
    created = [item_rel]
    if target_path and target_rel:
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_text(_lean_scaffold(args.id, namespace, args.theorem), encoding="utf-8")
        created.append(target_rel)

    data: dict[str, Any] = {
        "id": args.id,
        "kind": args.kind,
        "item": item_rel,
        "target": target_rel,
        "theorems": [decl] if decl else [],
        "created_files": created,
    }
    text = f"created {item_rel}" + (f" and {target_rel}" if target_rel else "")
    emit(args, text, data)
    return 0
