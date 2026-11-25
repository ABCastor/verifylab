"""`vl export catalog OUT`: every item of the trusted ref as OUT/catalog.json and one static page, OUT/index.html.

Reads only the trusted commit (repo.TrustedRepo), so the worktree and its proposals change nothing, and writes only
those two files, each in one step, so a reader never finds half of one; both are staged before either is put in place,
so a failure to write leaves the previous pair. The same records give the same bytes.
"""

from __future__ import annotations

from pathlib import Path

from .. import catalog, catalog_page, fsio
from ..output import emit, fail
from ..records import pretty_json
from ..render import trust_text
from ..repo import TrustedRepo
from . import action_flags, open_repo

FILES = ("catalog.json", "index.html")


def register(sub):
    p = sub.add_parser("export", help="static exports of the trusted ref's records")
    actions = p.add_subparsers(dest="action", required=True)
    cat = actions.add_parser("catalog", help="write OUT/catalog.json and OUT/index.html: every item with its derived "
                                             "status, evidence, probes, fidelity, provenance and relations")
    cat.add_argument("out", metavar="OUT", help="the folder to write into (created when missing)")
    action_flags(cat)
    return p


def run(args) -> int:
    repo = open_repo(args, cls=TrustedRepo)
    if repo.trusted_commit is None:
        return fail(f"the trusted ref '{repo.config.trusted_ref}' does not resolve to a commit: nothing to export")
    out = Path(args.out)
    if out.exists() and not out.is_dir():
        return fail(f"{out}: not a folder")
    if any((out / name).is_dir() for name in FILES):
        return fail(f"{out}: a folder stands where {' or '.join(FILES)} goes")
    data = catalog.build(repo)
    texts = (pretty_json(data), catalog_page.render(data))
    try:
        out.mkdir(parents=True, exist_ok=True)
        sizes = fsio.replace_files(out, dict(zip(FILES, texts)))
    except OSError as exc:
        return fail(f"cannot write the catalog into {out}: {exc}")
    trust = data["trust"]
    lines = [f"wrote {', '.join(f'{out / n} ({sizes[n]} bytes)' for n in FILES)}",
             f"{data['counts']['items']} items from trusted ref {trust['ref']} at {trust['commit'][:12]}; "
             "status: " + (", ".join(f"{n} {s}" for s, n in sorted(data["counts"]["status"].items())) or "none")]
    if data["problems"]:
        lines.append(f"WARNING: {len(data['problems'])} item files do not parse on the trusted ref and are not "
                     "listed; run vl validate")
    if trust_text(trust):
        lines.insert(0, f"NOTE: {trust_text(trust)}")
    emit(args, "\n".join(lines), {"out": str(out), "files": sizes, "counts": data["counts"], "trust": trust,
                                  "problems": data["problems"]})
    return 0
