"""`vl import comparator DIR`: turn the Comparator challenges of a directory (`NAME.json` config + `NAME.lean`
challenge with `sorry`, as in OpenAI's omath/lean/ComparatorChallenges/) into items of this project.

One result item per challenge: the challenge, byte for byte, as the trusted target (`research/targets/<id>.lean`),
the config's theorem names and solution module as the `[lean]` binding, claim `formal`. The config's permitted axioms
and second kernel are project-wide in vl (`research/vl.toml`), so where the project allows more than the config, the
item's `limits` say so. Provenance is the item's `ref` (`URL@COMMIT:PATH`, where `vl export catalog` reads it) and, with
the sha256 of both files, the item's body; the area of the solution module's path is the tag `area:NAME`. Optionally
(`--catalogue`, `--notes`) the main results the catalogue marks and one source item per paper the family scope notes
cite, which the results `cite`.

Dry run by default (ADR 0007). Nothing existing is ever overwritten: an item is recognised by its tags, its id
(`--id-prefix`) and its source URL, and one whose challenge
and config digest is unchanged is left alone; a changed challenge or config becomes a new item `<id>-r2`, `-r3`, ...
that `supersedes` the previous one, so a target the integrator admitted keeps its receipts and reviews.
"""

from __future__ import annotations

import hashlib
import json
import os
import posixpath
import re
import secrets
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from .. import leanmod, packages
from ..adapters.lean_comparator import parse_spec
from ..formalization_yaml import Catalogue, CatalogueError, Notes, read_catalogue, read_notes
from ..output import emit, fail, listed
from ..records import (AUTHOR_RE, ID_RE, PROVENANCE_KINDS, RecordError, parse_item, sha256_hex, toml_list,
                       toml_str)
from ..repo import ITEMS, TARGETS, Repo
from . import action_flags, open_repo

MAX_BYTES = 8 * 1024 * 1024
KNOWN_KEYS = {"challenge_module", "solution_module", "theorem_names", "definition_names", "permitted_axioms",
              "enable_nanoda"}
CHALLENGE_TAG, SOURCE_TAG = "comparator-challenge", "comparator-source"
DEFAULT_AUTHOR = "agent:vl-import-comparator"
PROBLEM_STATUSES = ("invalid", "conflict")
ORDER = ("new", "revision", "unchanged", "skipped", "invalid", "conflict")


def today() -> str:
    return date.today().isoformat()


def pin_arguments(parser):
    parser.add_argument("project", type=Path)
    parser.add_argument("--at", default="HEAD", help="local Git revision to read")
    parser.add_argument("--lock", type=Path, default=Path("vl.lock"), help="write this local lockfile")


def register_pin(sub):
    from ..foreign import import_lake
    parser = sub.add_parser("pin", help="pin a local Lake checkout at a Git revision")
    pin_arguments(parser)
    action_flags(parser)
    parser.set_defaults(handler=import_lake)


def register(sub):
    p = sub.add_parser("import", help="pin a Lake checkout or import Comparator challenges as items")
    p.epilog = ("Use 'vl import lake --help' for Lake options. "
                "'vl import PROJECT --at REV' is shorthand for 'vl import lake PROJECT --at REV'.")
    actions = p.add_subparsers(dest="action", required=True)
    lake = actions.add_parser("lake", help="pin a foreign Lake checkout without executing its configuration")
    pin_arguments(lake)
    c = actions.add_parser("comparator", help="Comparator challenges (NAME.json + NAME.lean) into result items; "
                                              "a dry run unless --write")
    c.add_argument("dir", help="the folder holding NAME.json and NAME.lean")
    c.add_argument("--write", action="store_true", help="create the files (default: print what would be created)")
    c.add_argument("--source-url", help="URL of the source repository (provenance; required with --write)")
    c.add_argument("--source-commit", help="the commit of the source repository DIR was taken from (required with "
                                           "--write)")
    c.add_argument("--source-path", metavar="PATH", help="where DIR is in the source repository (default: its name)")
    c.add_argument("--catalogue", metavar="FILE", help="formalization.yaml (schema v0.4) or its JSON conversion: marks "
                                                       "the main results and gives papers their titles")
    c.add_argument("--notes", metavar="DIR", help="the source's family scope notes (docs/NNN.md): one source item "
                                                  "per paper they link next to a challenge, cited by its result")
    c.add_argument("--provenance", choices=PROVENANCE_KINDS, default="lab-authored",
                   help="who wrote the challenges' statements (default lab-authored: the lab that released them); "
                        "recorded in each item's [provenance] with the source URL, commit and file")
    c.add_argument("--id-prefix", default="", help="put this prefix before every id made from a challenge name")
    c.add_argument("--author", default=DEFAULT_AUTHOR, help=f"author of the items (default {DEFAULT_AUTHOR})")
    c.add_argument("--only", nargs="+", metavar="NAME", help="import only these challenges")
    for action in actions.choices.values():     # accepted after the action too, as for `vl lane`
        action_flags(action)
    return p


class Invalid(ValueError):
    """A challenge or config that is malformed (as opposed to one vl cannot represent)."""


@dataclass
class Row:
    name: str                                 # a challenge name, or a preprint folder for a source
    id: str = ""
    status: str = ""
    reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    files: dict[str, bytes] = field(default_factory=dict)
    cites: list[str] = field(default_factory=list)
    supersedes: str | None = None
    parts: tuple | None = None                # what a new item is made of, until its sources are known


@dataclass(frozen=True)
class Contract:
    solution: str
    theorems: tuple[str, ...]
    axioms: tuple[str, ...]
    nanoda: bool
    definitions: tuple[str, ...]
    unknown: tuple[str, ...]


@dataclass
class Context:
    repo: Repo
    args: Any
    items: dict[str, Any]                     # id -> Item, the trusted ref's and the worktree's
    taken: set[str]                           # ids with an item file, parsed or not
    trusted_files: set[str]                   # the files under the items and targets folders of the trusted ref
    catalogue: Catalogue | None
    notes: Notes | None
    created: str


# Reading a challenge ------------------------------------------------------------------------------------------

def _read(path: Path) -> bytes:
    if not path.is_file() or path.is_symlink():
        raise Invalid(f"{path.name}: not a regular file")
    if path.stat().st_size > MAX_BYTES:
        raise Invalid(f"{path.name}: larger than {MAX_BYTES} bytes")
    return path.read_bytes()


def _plain(name: str) -> bool:
    return bool(name) and name.isprintable() and not any(c.isspace() for c in name)


def _strings(config: dict[str, Any], key: str, required: bool) -> tuple[str, ...]:
    value = config.get(key)
    if value is None and not required:
        return ()
    if not isinstance(value, list) or not all(isinstance(v, str) and _plain(v) for v in value):
        raise Invalid(f"config: '{key}' must be a list of names (printable text without spaces)")
    return tuple(value)


def read_contract(data: bytes, name: str) -> Contract:
    try:
        config = json.loads(data)
    except (ValueError, RecursionError) as exc:
        raise Invalid(f"config: not valid JSON ({exc})") from None
    if not isinstance(config, dict):
        raise Invalid("config: not a JSON object")
    module = config.get("challenge_module")
    if not isinstance(module, str) or module.rsplit(".", 1)[-1] != name:
        raise Invalid(f"config: challenge_module {module!r} does not name the challenge {name}")
    solution = config.get("solution_module")
    if not isinstance(solution, str) or not leanmod.is_valid_module_name(solution):
        raise Invalid(f"config: solution_module {solution!r} is missing or not a plain module name")
    if leanmod.is_reserved(solution):
        raise Invalid(f"config: solution_module {solution} is under a prefix reserved by vl")
    nanoda = config.get("enable_nanoda", False)
    if not isinstance(nanoda, bool):
        raise Invalid("config: enable_nanoda must be true or false")
    return Contract(solution, _strings(config, "theorem_names", False),     # none: skipped, as an empty list
                    tuple(sorted(set(_strings(config, "permitted_axioms", True)))), nanoda,
                    _strings(config, "definition_names", False), tuple(sorted(set(config) - KNOWN_KEYS)))


# Ids ----------------------------------------------------------------------------------------------------------

def kebab(name: str) -> str:
    """`AbhyankarSathaye` -> `abhyankar-sathaye`, `WLIdentification` -> `wl-identification`."""
    spaced = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1-\2", re.sub(r"([a-z0-9])([A-Z])", r"\1-\2", name))
    return re.sub(r"[^a-z0-9]+", "-", spaced.lower()).strip("-")


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.encode("ascii", "ignore").decode().lower()).strip("-")


def revision_number(item) -> int:
    """The `import-revision:N` tag: 1 for the first import of a challenge, then one more per changed version."""
    return max((int(t.split(":", 1)[1]) for t in item.tags if re.fullmatch(r"import-revision:\d+", t)), default=1)


def area(solution: str) -> str | None:
    """The area of a solution module `Root.Area.Name...` (`OAI.Analysis.Foo` is `analysis`, `OAI.NumberTheory.Foo` is
    `number-theory`): its second component, when the module is nested under one; else None."""
    parts = solution.split(".")
    return (kebab(parts[1]) or None) if len(parts) > 2 else None


def source_base(a, name: str) -> str:
    """The path of the challenge `name` in the source repository, without its extension."""
    return posixpath.join(a.source_path or Path(a.dir).name, name)


def source_ref(a, name: str) -> str | None:
    """`URL@COMMIT:PATH` of the challenge file in the source repository, when the URL and the commit are given."""
    return _source_at(a, f"{source_base(a, name)}.lean") if a.source_url and a.source_commit else None


# One challenge ------------------------------------------------------------------------------------------------

def _digest(lean: bytes, c: Contract) -> str:
    basis = {"lean": sha256_hex(lean), "solution": c.solution, "theorems": list(c.theorems), "axioms": list(c.axioms),
             "nanoda": c.nanoda, "definitions": sorted(c.definitions)}
    return sha256_hex(json.dumps(basis, sort_keys=True, separators=(",", ":")).encode())


def _limits(ctx: Context, c: Contract) -> list[str]:
    lean = ctx.repo.config.lean
    limits = ["The target is the source repository's Comparator challenge, unchanged; the check proves the theorem "
              "against the challenge's own definitions, and whether they express the source paper's statement is "
              "not established by it."]
    more = sorted(set(lean.permitted_axioms) - set(c.axioms))
    if more:
        limits.append(f"This project's permitted axioms also allow {', '.join(more)}, which the source config does not.")
    if c.nanoda and not lean.external_kernels:
        limits.append("The source config enables the second kernel (nanoda); this project does not require it.")
    if c.definitions:
        limits.append(f"The source config declares {len(c.definitions)} definition holes ({', '.join(c.definitions)}), "
                      "whose bodies its Comparator run does not compare (only their types); this check does not pass "
                      "them through and requires the challenge's definitions, bodies included, so it is stricter.")
    return limits


def _warnings(ctx: Context, c: Contract) -> list[str]:
    lean = ctx.repo.config.lean
    warnings = []
    if not leanmod.in_roots(c.solution, lean.roots):
        warnings.append(f"solution module {c.solution} is not under [lean] roots {list(lean.roots)}: a check stops")
    fewer = sorted(set(c.axioms) - set(lean.permitted_axioms))
    if fewer:
        warnings.append(f"the config permits {', '.join(fewer)}, which this project's permitted_axioms does not: a "
                        "proof using it fails here")
    if c.unknown:
        warnings.append(f"config keys vl does not read: {', '.join(c.unknown)}")
    if c.definitions:
        warnings.append(f"definition holes not passed through: {', '.join(c.definitions)} (upstream Comparator checks "
                        "only their types; vl compares their bodies too, so a solution upstream accepts can fail here)")
    return warnings


def _source_at(a, path: str) -> str:
    """`URL@COMMIT:PATH`: a place in the source repository, as the importer was told it."""
    return f"{a.source_url or '(source URL not given)'}@{a.source_commit or '(commit not given)'}:{path}"


def _origin_lines(ctx: Context, context: str) -> list[str]:
    """The `[origin]` of an imported item: the importer made it, from the source files named by `context`."""
    return ["[origin]", f"agent = {toml_str(ctx.args.author)}", f"context = {toml_str(context)}"]


def _body(ctx: Context, name: str, c: Contract, lean: bytes, config: bytes, supersedes: str | None,
          sources: list[str]) -> str:
    a = ctx.args
    base = source_base(a, name)
    lines = [f"Imported from the Comparator challenge `{name}` of {a.source_url or '(source URL not given)'}, "
             f"commit {a.source_commit or '(commit not given)'}.", "",
             f"- Challenge `{base}.lean` (sha256 `{sha256_hex(lean)}`) and config `{base}.json` "
             f"(sha256 `{sha256_hex(config)}`).",
             f"- Config: solution module `{c.solution}`; permitted axioms {', '.join(c.axioms) or 'none'}; "
             f"second kernel (nanoda) {'on' if c.nanoda else 'off'}."]
    if ctx.catalogue:
        for declaration, file in ctx.catalogue.results.get(name, ()):
            lines.append(f"- Main result of the catalogue: `{declaration}`" + (f" in `{file}`" if file else "") + ".")
        if name in ctx.catalogue.results and ctx.catalogue.review:
            lines.append(f"- Review status in the catalogue: {ctx.catalogue.review}.")
    if ctx.notes and name in ctx.notes.families:
        lines.append(f"- Family scope notes: {', '.join(f'{n}.md' for n in ctx.notes.families[name])}.")
    if sources:
        lines.append(f"- Papers: {', '.join(f'[[{s}]]' for s in sources)}.")
    if supersedes:
        lines.append(f"- The challenge or its config changed since [[{supersedes}]], which stays as it was.")
    return "\n".join(lines) + "\n"


def _render_result(ctx: Context, row: Row, name: str, c: Contract, lean: bytes, config: bytes, digest: str,
                   number: int) -> str:
    target = ctx.repo.rel(TARGETS, f"{row.id}.lean")
    tags = [CHALLENGE_TAG, f"comparator:{name}", f"import-digest:{digest}", f"import-revision:{number}"]
    if name_area := area(c.solution):
        tags.append(f"area:{name_area}")
    if ctx.catalogue and name in ctx.catalogue.results:
        tags.append("main-result")
        if ctx.catalogue.review:
            tags.append(f"catalogue-review:{ctx.catalogue.review}")
    names = ", ".join(f"`{t}`" for t in c.theorems)
    statement = f"The Lean theorem{'s' if len(c.theorems) > 1 else ''} {names} of the Comparator challenge {name}."
    front = [f'id = {toml_str(row.id)}', 'kind = "result"', f'title = {toml_str(f"{name} (Comparator challenge)")}',
             f'author = {toml_str(ctx.args.author)}', f'created = {toml_str(ctx.created)}',
             f"statement = {toml_str(statement)}",
             'claim = "formal"', f"limits = {toml_list(_limits(ctx, c))}", f"tags = {toml_list(tags)}"]
    if ref := source_ref(ctx.args, name):
        front.append(f"ref = {toml_str(ref)}")
    if row.cites:
        front.append(f"cites = {toml_list(row.cites)}")
    if row.supersedes:
        front.append(f"supersedes = {toml_list([row.supersedes])}")
    base = source_base(ctx.args, name)
    front += ["", "[provenance]", f"kind = {toml_str(ctx.args.provenance)}",
              f"source = {toml_str(_source_at(ctx.args, base + '.lean'))}", "",
              *_origin_lines(ctx, _source_at(ctx.args, base + ".json") + " and the challenge file beside it")]
    front += ["", "[lean]", f"target = {toml_str(target)}", f"theorems = {toml_list(c.theorems)}",
              f"solution = {toml_str(c.solution)}"]
    return "+++\n" + "\n".join(front) + "\n+++\n" + _body(ctx, name, c, lean, config, row.supersedes, row.cites)


_BODY_SOURCE = re.compile(r"(?:Imported from the Comparator challenge `[^`\n]*`|A paper cited by the family scope "
                          r"notes) of (\S+), commit ")


def source_url(item) -> str | None:
    """The source repository an imported item names: the URL of its `[provenance] source`, `ref` or `[origin]
    context` (`URL@COMMIT:PATH`), else of its body's first line (items imported before they recorded these)."""
    for text in (item.provenance.get("source"), item.ref, item.origin.get("context")):
        m = re.fullmatch(r"(.+)@[0-9a-f]{7,64}:.*", text or "", re.S)
        if m:
            return m.group(1)
    m = _BODY_SOURCE.match(item.body)
    return m.group(1) if m else None


def _same_source(ctx: Context, item) -> bool:
    """Whether `item` names the source URL of this run, or one of the two is not known."""
    url = source_url(item)
    return not (ctx.args.source_url and url) or url.rstrip("/") == ctx.args.source_url.rstrip("/")


def _imports(ctx: Context, item, name: str) -> bool:
    """Whether `item` is an import of the challenge `name` from this source: its tags name the challenge, its id is
    the one this run would make (`--id-prefix`, `-rN`), and it names the same source URL when both are known. A
    challenge of the same name from another corpus or prefix is never taken for an earlier version."""
    return (CHALLENGE_TAG in item.tags and f"comparator:{name}" in item.tags and _same_source(ctx, item)
            and re.fullmatch(re.escape(ctx.args.id_prefix + kebab(name)) + r"(-r\d+)?", item.id) is not None)


def _plan_challenge(ctx: Context, folder: Path, name: str) -> Row:
    row = Row(name)
    try:
        lean_bytes, config_bytes = _read(folder / f"{name}.lean"), _read(folder / f"{name}.json")
        contract = read_contract(config_bytes, name)
        text = leanmod.decode(lean_bytes, f"{name}.lean")
    except (Invalid, leanmod.LeanModError) as exc:
        row.status, row.reasons = "invalid", [str(exc)]
        return row
    chain = sorted((i for i in ctx.items.values() if _imports(ctx, i, name)), key=revision_number)
    digest = _digest(lean_bytes, contract)
    row.warnings = _warnings(ctx, contract)
    if chain and f"import-digest:{digest}" in chain[-1].tags:       # imported before, and nothing changed
        row.id, row.status = chain[-1].id, "unchanged"
        return row
    number = revision_number(chain[-1]) + 1 if chain else 1
    row.id = ctx.args.id_prefix + kebab(name) + (f"-r{number}" if number > 1 else "")
    problems = []
    if not contract.theorems:
        problems.append("no theorem_names (a challenge that leaves definitions to fill): vl items need a theorem")
    else:
        problems += leanmod.target_problems(text, contract.theorems)
    if not ID_RE.match(row.id):
        problems.append(f"the id '{row.id}' is not a valid item id")
    if problems:
        row.status, row.reasons = "skipped", problems
        return row
    repo, target, item_path = ctx.repo, ctx.repo.rel(TARGETS, f"{row.id}.lean"), ctx.repo.rel(ITEMS, f"{row.id}.md")
    if row.id in ctx.taken or any((repo.root / p).exists() or p in ctx.trusted_files for p in (target, item_path)):
        row.status = "conflict"
        row.reasons = [f"'{row.id}' or {target} already exists and is not an import of {name}; "
                       "use --id-prefix"]
        return row
    row.supersedes = chain[-1].id if chain else None
    row.status = "revision" if chain else "new"
    row.parts = (name, contract, lean_bytes, config_bytes, digest, number)
    return row


def _finish(ctx: Context, row: Row, broken: set[str]) -> None:
    """Render a new item once the ids of the papers it can cite are known."""
    name, contract, lean_bytes, config_bytes, digest, number = row.parts
    repo = ctx.repo
    papers = ctx.notes.cited_by.get(name, ()) if ctx.notes else ()
    row.cites = sorted(c for c in (f"{ctx.args.id_prefix}{_paper_id(p)}" for p in papers) if c not in broken)
    target, item_path = repo.rel(TARGETS, f"{row.id}.lean"), repo.rel(ITEMS, f"{row.id}.md")
    data = _render_result(ctx, row, name, contract, lean_bytes, config_bytes, digest, number).encode("utf-8")
    try:                                          # never write an item vl itself cannot read
        item = parse_item(data, item_path)
        spec, problems = parse_spec(item.lean, repo.rel(TARGETS))
        if spec is None:
            raise RecordError("; ".join(problems))
    except RecordError as exc:
        row.status, row.reasons = "skipped", [f"the item would not be valid: {exc}"]
        return
    row.files = {target: lean_bytes, item_path: data}


# Sources ------------------------------------------------------------------------------------------------------

def _paper_id(folder: str) -> str:
    return f"paper-{slug(folder)[:40].strip('-')}-{hashlib.sha256(folder.encode()).hexdigest()[:6]}"


def _plan_source(ctx: Context, folder: str, citing: list[str]) -> Row:
    paper = ctx.notes.papers[folder]
    row = Row(folder, f"{ctx.args.id_prefix}{_paper_id(folder)}")
    existing = ctx.items.get(row.id)
    if existing is not None and SOURCE_TAG in existing.tags and _same_source(ctx, existing):
        row.status = "unchanged"
        return row
    path = ctx.repo.rel(ITEMS, f"{row.id}.md")
    if row.id in ctx.taken or (ctx.repo.root / path).exists() or path in ctx.trusted_files:
        row.status, row.reasons = "conflict", [f"'{row.id}' already exists and is not an import of this paper from "
                                                 "this source; use --id-prefix"]
        return row
    title = (ctx.catalogue.papers.get(folder) if ctx.catalogue else None) or paper.title
    tags = [SOURCE_TAG]
    if ctx.catalogue and folder in ctx.catalogue.papers and ctx.catalogue.review:
        tags.append(f"catalogue-review:{ctx.catalogue.review}")
    a = ctx.args
    body = (f"A paper cited by the family scope notes of {a.source_url or '(source URL not given)'}, commit "
            f"{a.source_commit or '(commit not given)'}, at `{paper.path}`. Only its title and place were recorded; the "
            f"import did not read it.\n\nChallenges: {', '.join(sorted(citing))}.\n")
    front = ['id = ' + toml_str(row.id), 'kind = "source"', f"title = {toml_str(title)}",
             f"author = {toml_str(a.author)}", f"created = {toml_str(ctx.created)}", f"ref = {toml_str(paper.path)}",
             'access = "citation-only"', f"tags = {toml_list(tags)}"]
    front += ["", *_origin_lines(ctx, _source_at(ctx.args, paper.path) + " (named by the family scope notes)")]
    data = ("+++\n" + "\n".join(front) + "\n+++\n" + body).encode("utf-8")
    try:                                          # never write an item vl itself cannot read
        parse_item(data, path)
    except RecordError as exc:
        row.status, row.reasons = "skipped", [f"the item would not be valid: {exc}"]
        return row
    row.files = {path: data}
    row.status = "new"
    return row


# The command --------------------------------------------------------------------------------------------------

def _context(args) -> Context:
    repo = open_repo(args)
    worktree, _ = repo.load_items()
    trusted, _ = repo.trusted_items()
    folder = repo.rel(ITEMS)
    trusted_files = set(repo.trusted_paths(folder)) | set(repo.trusted_paths(repo.rel(TARGETS)))
    taken = {p.stem for p in repo.item_files()}
    taken |= {posixpath.basename(p)[:-3] for p in trusted_files if posixpath.dirname(p) == folder and p.endswith(".md")}
    catalogue = read_catalogue(Path(args.catalogue)) if args.catalogue else None
    notes = read_notes(Path(args.notes)) if args.notes else None
    return Context(repo, args, {**trusted, **worktree}, taken, trusted_files, catalogue, notes, today())


def _comparator(args) -> int:
    folder = Path(args.dir)
    if not folder.is_dir():
        return fail(f"{args.dir}: not a folder")
    if args.write and not (args.source_url and args.source_commit):
        return fail("--write needs --source-url and --source-commit: the provenance every imported item records")
    if args.source_commit and not re.fullmatch(r"[0-9a-f]{7,64}", args.source_commit):
        return fail("--source-commit must be a hex commit id")
    if not AUTHOR_RE.match(args.author):
        return fail("--author must look like 'human:<name>' or 'agent:<name>'")
    if not re.fullmatch(r"[a-z0-9-]*", args.id_prefix):
        return fail("--id-prefix may hold only a-z, 0-9 and '-'")
    try:
        ctx = _context(args)
    except CatalogueError as exc:
        return fail(str(exc))
    unsupported = packages.project_refusal(ctx.repo)       # before anything is planned or written
    if unsupported:
        return fail(f"unsupported: {unsupported}")
    names = sorted(p.stem for p in folder.glob("*.json"))
    unknown = sorted(set(args.only or ()) - set(names))
    if unknown:
        return fail(f"--only: no config for {', '.join(unknown)}")
    if args.only:
        names = [n for n in names if n in args.only]
    rows, seen = [], {}
    for name in names:
        row = _plan_challenge(ctx, folder, name)
        if row.status in ("new", "revision") and row.id in seen:
            row.status, row.reasons, row.parts = "conflict", [f"id '{row.id}' is also made from {seen[row.id]}"], None
        elif row.status in ("new", "revision"):
            seen[row.id] = name
        rows.append(row)
    sources: list[Row] = []
    if ctx.notes:
        citing: dict[str, list[str]] = {}
        for row in rows:
            for paper in (ctx.notes.cited_by.get(row.name, ()) if row.parts else ()):
                citing.setdefault(paper, []).append(row.name)
        sources = [_plan_source(ctx, f, c) for f, c in sorted(citing.items())]
    broken = {s.id for s in sources if s.status in ("conflict", "skipped")}
    for row in rows:
        if row.parts:
            _finish(ctx, row, broken)
    if args.write:
        try:
            _write(ctx.repo.root, [*sources, *rows])
        except OSError as exc:
            return fail(f"nothing was written: {exc}")
    return _report(ctx, rows, sources)


def _write(root: Path, rows: list[Row]) -> None:
    """Create every file or none. Each is written whole to a temporary file beside its place, then linked there (a
    link never replaces a file); a failure removes what this run linked, so a half import never blocks the next."""
    staged: list[tuple[Path, Path]] = []
    linked: list[Path] = []
    try:
        for row in rows:
            for path, data in row.files.items():
                final = root / path
                final.parent.mkdir(parents=True, exist_ok=True)
                tmp = final.with_name(f".{final.name}.{secrets.token_hex(4)}.vl-import")
                staged.append((tmp, final))
                fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o666)
                with os.fdopen(fd, "wb") as handle:
                    handle.write(data)
                    handle.flush()
                    os.fsync(handle.fileno())
        for tmp, final in staged:
            os.link(tmp, final)
            linked.append(final)
    except BaseException:
        for final in linked:
            final.unlink(missing_ok=True)
        raise
    finally:
        for tmp, _ in staged:
            tmp.unlink(missing_ok=True)


def _report(ctx: Context, rows: list[Row], sources: list[Row]) -> int:
    def count(group: list[Row]) -> dict[str, int]:
        return {status: n for status in ORDER if (n := sum(r.status == status for r in group))}

    counts, source_counts = count(rows), count(sources)
    a = ctx.args
    unlisted = sorted(set(ctx.catalogue.results) - {r.name for r in rows}) if ctx.catalogue and not a.only else []
    no_notes = sorted(r.name for r in rows if ctx.notes and r.name not in ctx.notes.cited_by)
    no_paper = sorted(r.name for r in rows if ctx.notes and ctx.notes.cited_by.get(r.name) == ())
    lines = [f"{'wrote' if a.write else 'dry run, nothing written (--write creates the files)'}: "
             f"{len(rows)} challenges in {a.dir}"]
    lines.append("  " + ", ".join(f"{n} {s}" for s, n in counts.items()) if counts else "  none")
    if ctx.notes:
        lines.append("sources: " + (", ".join(f"{n} {s}" for s, n in source_counts.items()) or "none"))
    if ctx.catalogue:
        lines.append(f"catalogue: review status {ctx.catalogue.review}; {len(ctx.catalogue.results)} configs with "
                     f"main results, {len(unlisted)} of them not in {a.dir}")
    if no_notes:
        lines.append(f"{len(no_notes)} challenges are in no scope note: {listed(no_notes, 8)}")
    if no_paper:
        lines.append(f"{len(no_paper)} challenges are in scope notes that link no paper: {listed(no_paper, 8)}")
    for row in [*rows, *sources]:
        lines += [f"  {row.status} {row.name}: {r}" for r in row.reasons]
        lines += [f"  warning {row.name}: {w}" for w in row.warnings]
    emit(a, "\n".join(lines), {
        "write": a.write, "dir": a.dir, "source_url": a.source_url, "source_commit": a.source_commit,
        "counts": counts, "source_counts": source_counts,
        "catalogue": None if not ctx.catalogue else {"review": ctx.catalogue.review,
                                                     "configs": len(ctx.catalogue.results), "not_in_dir": unlisted},
        "challenges": [_row(r) for r in rows], "sources": [_row(r) for r in sources]})
    return 1 if any(r.status in PROBLEM_STATUSES for r in [*rows, *sources]) else 0


def _row(row: Row) -> dict[str, Any]:
    return {"name": row.name, "id": row.id, "status": row.status, "reasons": row.reasons, "warnings": row.warnings,
            "files": sorted(row.files), "cites": row.cites, "supersedes": row.supersedes}


def run(args) -> int:
    if args.action == "lake":
        from ..foreign import import_lake
        return import_lake(args)
    return _comparator(args)
