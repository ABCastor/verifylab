"""Read one declaration and the five source-evidence channels."""
import shlex
from pathlib import Path

from ..foreign import Checkout, Reading
from ..output import emit
from ..records import RecordError


def register(sub):
    p = sub.add_parser("inspect", help="read a pinned declaration signature, binders and five source channels")
    p.add_argument("lock", type=Path, help="lockfile written by vl pin")
    target = p.add_mutually_exclusive_group(required=True)
    target.add_argument("name", nargs="?",
                        help="fully qualified Lean theorem, lemma, definition, abbreviation or named instance")
    target.add_argument("--find", metavar="TEXT", help="list up to 50 matching pinned names or signatures")
    p.add_argument("--file", help="limit reading and source indicators to this pinned file")
    return p


def run(args):
    project = Checkout.locked(args.lock)
    index = Reading(project, source_file=args.file, library_scope=args.name is not None)
    if args.find is not None:
        query = args.find.casefold()
        hits = [{"name": d.name, "file": d.path, "line": d.line} for d in index.decls.values()
                if query in d.name.casefold() or query in d.statement.casefold()]
        ambiguous = [{"name": name, "candidate_files": sorted({path for path, _ in entries})}
                     for name, entries in sorted(index.ambiguous.items()) if query in name.casefold()]
        text = [f"{r['name']}  {r['file']}:{r['line']}" for r in hits[:50]]
        text.extend(f"{r['name']}  (ambiguous) {', '.join(r['candidate_files'])}" for r in ambiguous[:50])
        emit(args, "\n".join(text),
             {"commit": project.commit, "matches": hits[:50], "total": len(hits),
              "ambiguous": ambiguous[:50], "ambiguous_total": len(ambiguous),
              "source_scope": index.scope, "omissions": index.omissions})
        return 0
    if index.excluded_by_library_scope and args.name not in index.decls and args.name not in index.ambiguous:
        index = Reading(project)
    try:
        decl = index.declaration(args.name)
    except RecordError as exc:
        prefix = ["vl", "inspect", str(args.lock)]
        paths = sorted({path for path, _ in index.ambiguous.get(args.name, ())})
        commands = ([shlex.join([*prefix, args.name, "--file", path]) for path in paths]
                    if paths else [shlex.join([*prefix, "--find", args.name])])
        raise RecordError(f"{exc}. Run:\n" + "\n".join(commands), has_recovery=True) from exc
    inventory = project.inventory()
    channels = {
        "statement": {"file": decl.path, "line": decl.line, "imports": index.imports[decl.path],
                      "scope": "source signature; implicit section context and elaborated type unchecked"},
        "axioms": {"source_indicators": index.signals["axioms"], "kernel_axioms": "unchecked"},
        "kernel": {"toolchain": inventory["toolchain"], "replay": "unchecked"},
        "build_code": {"configs": inventory["configs"], "source_indicators": index.signals["build_code"],
                       "execution": "unchecked"},
        "supply_chain": {"dependencies": inventory["dependencies"], "artifacts": "unchecked"},
    }
    text = [f"{decl.path}:{decl.line}", decl.statement, "Explicit binders: " + (", ".join(decl.binders) or "none"),
            f"Source indicators: {len(index.signals['axioms'])} axiom-related, {len(index.signals['build_code'])} build-related.",
            f"Kernel: {inventory['toolchain']}, unchecked. Dependencies: {len(inventory['dependencies'])}, artifacts unchecked."]
    if index.excluded_by_library_scope:
        text.append(f"Source scope: {index.scope}. Lean files outside this scope: {index.excluded_by_library_scope}.")
    emit(args, "\n".join(text), {"commit": project.commit, "name": decl.name, "statement": decl.statement,
         "binders": decl.binders, "channels": channels, "source_scope": index.scope,
         "excluded_by_library_scope": index.excluded_by_library_scope, "omissions": index.omissions})
