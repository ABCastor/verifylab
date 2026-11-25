"""Views over the same pinned declaration graph."""
import argparse
import shlex
import sys
from collections import Counter
from pathlib import Path

from ..foreign import Checkout, Reading, build_estimate
from ..output import emit
from ..records import RecordError
from ..reading_order import graph, kernel_graph, kernel_origin, kernel_values, model_columns, packet, source_edges, values


def positive(text):
    value = int(text)
    if value <= 0:
        raise argparse.ArgumentTypeError("must be positive")
    return value


def register(sub):
    for name, help_text in (("graph", "show module imports or declaration dependencies"),
                            ("value", "measure dependencies and rank study or simplification candidates"),
                            ("order", "assemble a checked, budgeted statement-only reading packet")):
        parser = sub.add_parser(name, help=help_text)
        if name == "order":
            origin = parser.add_mutually_exclusive_group(required=True)
            origin.add_argument("lock", type=Path, nargs="?", help="lockfile written by vl pin")
            origin.add_argument("--from", dest="checkout", type=Path, metavar="PROJECT",
                                help="pin this local Lake checkout for this read, without writing a lockfile")
            parser.add_argument("--at", metavar="REV", help="with --from: Git revision to read (default HEAD)")
        else:
            parser.add_argument("lock", type=Path, help="lockfile written by vl pin")
        deps_help = "local dumpx TSV or TSV.gz of direct olean dependencies"
        if name == "graph":
            deps_help += " (declaration graph only)"
        parser.add_argument("--deps", type=Path, help=deps_help)
        if name == "graph":
            group = parser.add_mutually_exclusive_group()
            group.add_argument("--build", metavar="TARGET", help="estimate target closure and missing local oleans")
            group.add_argument("--modules", action="store_true", help="show source-file import headers")
            parser.add_argument("--dump", type=Path, metavar="DIR",
                                help="write kernel.tsv.gz and ranges.gz, the inputs of --deps and --removal, from the "
                                     "built checkout, with their digests; all default targets, or the --build one")
            parser.add_argument("--lean", type=Path, metavar="LEAN",
                                help="with --dump: path to the Lean executable of the checkout's toolchain; it runs "
                                     "the two shipped extractors on compiled files, outside the checkout")
            parser.add_argument("--built-tree", type=Path, metavar="DIR",
                                help="with --dump: read pinned source bytes and compiled objects from a prepared tree")
        else:
            parser.add_argument("--graph", type=Path, metavar="MANIFEST",
                                help="read the verified kernel and range files from a vl graph dump manifest")
            parser.add_argument("--root", required=True, action="append",
                                help="fully qualified declaration name; repeat it for the removal view")
            parser.add_argument("--removal", type=Path, metavar="RANGES",
                                help="Ranges.lean output (TSV or TSV.gz) for the multi-root removal view; needs --deps")
        if name == "value":
            parser.add_argument("--study", action="store_true",
                                help="rank study and simplification candidates for these roots; needs compiled ranges")
            parser.add_argument("--select", action="append", default=[], metavar="NAME",
                                help="report a reached declaration with per-root distances; repeat for more names")
            parser.add_argument("--limit", type=positive, default=12, help="with --study: rows per ranking")
            parser.add_argument("--judgments", type=Path, help="optional local model judgments with input hashes and cost")
            parser.add_argument("--blocks", type=Path, metavar="FILE",
                                help="with --removal: sum the located reached lines per block; FILE has lines of a "
                                     "block name, a tab and a regular expression on the module name, first match wins")
            parser.add_argument("--against", type=Path, metavar="PRIOR",
                                help="with --removal: an earlier --json output of this view for the same roots; print "
                                     "lines before, after and the difference per block, or per module prefix")
        if name == "order":
            parser.add_argument("--aim", choices=("understand", "simplify"), default="understand",
                                help="order by root-conditioned demand (understand, default) or source length (simplify)")
            parser.add_argument("--rank-by", choices=("length", "removal"), default="length",
                                help="with --aim simplify: source length (default) or exclusive removal lines")
            parser.add_argument("--budget", type=positive, default=12, help="maximum number of declarations")
            parser.add_argument("--bytes", type=positive, default=8192, help="maximum UTF-8 bytes of model input")
            parser.add_argument("--file", metavar="PATH", help="read only this pinned Lean file, without compiled inputs")
            parser.add_argument("--full-omissions", action="store_true", help="with --json: include all omission details")
        json_help = "print versioned JSON instead of text"
        if name == "order":
            json_help += ", with source locations and ranking details"
        parser.add_argument("--json", action="store_true", help=json_help)
        parser.set_defaults(handler=run)


def run(args):
    source = getattr(args, "checkout", None)
    at = getattr(args, "at", None)
    if at is not None and source is None:
        raise RecordError("--at needs --from PROJECT")
    if source is not None:
        project = Checkout.pinned(source, at if at is not None else "HEAD")
        project.inventory()
    else:
        project = Checkout.locked(args.lock)
    if args.command == "graph" and (args.dump is not None or args.lean is not None or args.built_tree is not None):
        return run_dump(args, project)
    manifest = getattr(args, "graph", None)
    module_paths = None
    if manifest is not None:
        from ..graph_inputs import read_manifest
        if args.deps is not None or args.removal is not None:
            raise RecordError("--graph supplies both inputs; omit --deps and --removal")
        artifacts, module_paths = read_manifest(manifest, project)
        args.deps = artifacts["kernel"]
        if args.command == "value" or args.rank_by == "removal":
            args.removal = artifacts["ranges"]
    if args.command == "graph" and args.build:
        if args.deps:
            raise RecordError("--build uses pinned source headers")
        estimate = build_estimate(project, args.build)
        emit(args, f"{args.build}: {estimate['source_modules']} source modules, "
             f"{len(estimate['missing_oleans'])} local oleans missing. Cache validity and build time unmeasured.",
             {"commit": project.commit, **estimate})
        return 0
    removal = args.command != "graph" and (args.removal is not None or len(args.root) > 1
                                           or getattr(args, "rank_by", "length") == "removal"
                                           or getattr(args, "blocks", None) or getattr(args, "against", None)
                                           or getattr(args, "study", False) or getattr(args, "select", None))
    source_order = args.command == "order" and args.deps is None and args.removal is None and args.rank_by == "length"
    source_file = getattr(args, "file", None)
    if source_file is not None and not source_order:
        raise RecordError("--file is for source-only order; omit compiled inputs and --rank-by removal")
    if source_order and len(args.root) > 1:
        commands = [order_command(args, root, source_file) for root in args.root]
        raise RecordError("source-only order reads one root per call. Run each separately:\n" + "\n".join(commands), has_recovery=True)
    reading = Reading(project, source_file=source_file, module_paths=module_paths,
                      library_scope=args.command != "graph" and not removal and args.deps is None)
    data = {"commit": project.commit, "source_omissions": reading.omissions, "source_scope": reading.scope}
    if manifest is not None:
        data["graph_manifest"] = str(manifest)
    if args.command == "graph" and args.modules:
        if args.deps:
            raise RecordError("--modules reads source headers; --deps selects a declaration graph")
        data.update(kind="source module imports", edges=reading.imports)
        text = [f"{path} -> {', '.join(deps)}" for path, deps in reading.imports.items()]
    elif removal:
        return run_removal(args, reading, data)
    else:
        root = args.root[0] if args.command != "graph" else None
        if source_order and root not in reading.decls:
            try:
                reading.declaration(root)
            except RecordError as exc:
                paths = sorted({path for path, decl in reading.ambiguous.get(root, ()) if decl is not None})
                if paths:
                    commands = "\n".join(order_command(args, root, path) for path in paths)
                    raise RecordError(f"{exc}. If one file is the intended source, run:\n{commands}", has_recovery=True) from exc
                if "." in root and not reading.excluded_by_library_scope:
                    raise
                lock = args.lock or "read.lock"
                find = ["vl", "inspect", str(lock), "--find", root]
                if source_file is not None:
                    find += ["--file", source_file]
                commands = [shlex.join(find)]
                if args.lock is None:
                    commands.insert(0, shlex.join(["vl", "pin", str(args.checkout), "--at", args.at or "HEAD", "--lock", lock]))
                scope = " Source-only order searched pinned Lake libraries." if reading.excluded_by_library_scope else ""
                raise RecordError(f"{exc}.{scope} Find the qualified name and path:\n" + "\n".join(commands) +
                                  "\nRun order with that qualified name and --file with the intended path.",
                                  has_recovery=True) from exc
        if args.command != "graph" and args.deps is not None:
            rows, origin = kernel_values(reading, args.deps, root)
        else:
            edges, origin = graph(reading, args.deps)
            if args.command != "graph":
                rows = values(reading, edges, root)
        data.update(graph=origin)
        if args.command == "graph":
            data["edges"] = edges
            text = [origin["kind"], *(f"{name} -> {', '.join(deps)}" for name, deps in sorted(edges.items()))]
        else:
            data["root"] = root
            if args.command == "value":
                data["judgments"] = model_columns(args.judgments, project.commit, rows, reading) if args.judgments else None
                data["rows"] = rows
                columns = ["name", "level", "users", "demand50", "lines", "signature_chars"]
                text = [origin["kind"]]
                if data["judgments"]:
                    columns.append("model_relevance")
                    text.append(f"model {data['judgments']['model']}, cost USD {data['judgments']['cost_usd']}")
                text.append("\t".join(columns))
                text.extend("\t".join(str(row.get(key, "")) for key in columns) for row in rows)
            else:
                key = "demand50" if args.aim == "understand" else "lines"
                ordered = sorted(rows, key=lambda row: (-row[key], row["name"]))
                declaration = reading.decls[root]
                data.update(aim=args.aim, ranked_by=key,
                            root_source={"path": declaration.path, "line": declaration.line})
                return emit_order(args, reading, ordered, data)
    emit(args, "\n".join(text), data)


def order_command(args, root, source_file):
    origin = [str(args.lock)] if args.checkout is None else ["--from", str(args.checkout)]
    if args.at is not None:
        origin += ["--at", args.at]
    words = ["vl", "order", *origin, "--root", root]
    if source_file is not None:
        words += ["--file", source_file]
    if args.aim != "understand":
        words += ["--aim", args.aim]
    words += ["--budget", str(args.budget), "--bytes", str(args.bytes)]
    if args.json:
        words.append("--json")
    if args.full_omissions:
        words.append("--full-omissions")
    return shlex.join(words)


def emit_order(args, reading, ordered, data):
    data.update(packet(reading, ordered, args.budget, args.bytes))
    if not args.json:
        sys.stdout.write(data["input"])
        return 0
    data["source_omission_count"] = len(reading.omissions)
    data["omitted_counts"] = dict(Counter(row["reason"] for row in data["omitted"]))
    if not args.full_omissions:
        data["source_omissions"] = [line[:240] for line in reading.omissions[:10]]
        data["omitted"] = data["omitted"][:10]
    emit(args, "", data)
    return 0


def run_dump(args, project):
    """The graph inputs of a built checkout. Lean is the executable the caller names: none is looked up."""
    from ..graph_inputs import RECORD, dump
    if args.dump is None or args.lean is None:
        raise RecordError("--dump DIR needs --lean LEAN, the Lean executable of the checkout's toolchain: "
                          "VerifyLab starts none on its own")
    if args.modules or args.deps:
        raise RecordError("--dump reads compiled files; --modules and --deps select another view")
    lock = project.inventory()
    record = dump(project, lock["toolchain"], [args.build] if args.build else lock["default_targets"],
                  args.lean, args.dump, built_tree=args.built_tree)
    text = [f"{args.dump / entry['path']}  sha256 {entry['sha256']}  {entry['rows']} {entry['row_kind']} rows"
            for entry in record["files"].values()]
    text.append(f"{record['modules']} modules of {', '.join(record['targets'])} at {project.commit[:12]}, read by "
                f"Lean {record['lean']['version']} ({record['lean']['commit'][:12]}). Record: {args.dump / RECORD}")
    text.append("Artifact checks are structural. Check build freshness at the pin with Lake before extraction.")
    text.append(shlex.join(["vl", "value", str(args.lock), "--root", "NAME", "--graph", str(args.dump / RECORD)]))
    emit(args, "\n".join(text), {"record": str(args.dump / RECORD), **record})
    return 0


REMOVAL_COLUMNS = ["name", "kind", "exclusive_source_lines", "exclusive_kernel_names", "root_distance",
                   "reachable_users", "unreached_users", "sole_user", "immediate_dominator", "roots", "module",
                   "path", "first_line", "last_line", "indexed"]


def run_removal(args, reading, data):
    """Opt-in multi-root removal view. The single-root value and order outputs are unchanged without it."""
    from ..elaboration import report
    from ..removal import UNMATCHED, block_view, difference, read_blocks, read_prior, removal_view
    if getattr(args, "study", False) and (args.removal is None or args.against is not None):
        raise RecordError("--study needs --graph or both --deps and --removal, and cannot use --against")
    if args.deps is None:
        raise RecordError("the removal view needs --deps or --graph with the supplied kernel graph")
    grouped = args.command == "value" and (args.blocks is not None or args.against is not None)
    selected = getattr(args, "select", [])
    if selected and (grouped or args.study or args.judgments):
        raise RecordError("--select is a focused removal report; omit --blocks, --against, --study and --judgments")
    if grouped and args.removal is None:
        raise RecordError("--blocks and --against sum located lines: they need --removal with the compiler ranges")
    blocks = read_blocks(args.blocks) if grouped and args.blocks is not None else []
    if args.command == "value" and args.judgments:
        raise RecordError("--judgments applies to the single-root value view")
    if args.command == "order" and (args.aim != "simplify" or args.rank_by != "removal"):
        raise RecordError("several roots or --removal in vl order need --aim simplify --rank-by removal")
    kernel = kernel_graph(reading, args.deps)
    meta, rows = removal_view(reading, kernel, args.root, args.removal, selected)
    cone = {row["name"] for row in rows}
    data.update(graph=kernel_origin(reading, args.deps, kernel, cone), removal=meta)
    if selected:
        selected = list(dict.fromkeys(selected))
        by_name = {row["name"]: row for row in rows}
        rows = [by_name[name] for name in selected]
        columns = ["name", "root_distances", "reachable_users", "exclusive_source_lines", "path", "first_line", "last_line"]
        text = ["\t".join(columns)]
        for row in rows:
            distances = ", ".join(f"{root}={distance if distance is not None else 'unreached'}"
                                  for root, distance in row["root_distances"].items())
            values = {**row, "root_distances": distances}
            text.append("\t".join("" if values[key] is None else str(values[key]) for key in columns))
        emit(args, "\n".join(text), {**data, "rows": rows, "selected": selected})
        return 0
    prior = read_prior(args.against, meta) if grouped and args.against is not None else None
    data["elaboration_only"] = report(reading, set(kernel["rows"]), cone, source_edges(reading))
    if args.command == "value":
        data["rows"] = rows
        hazards = data["elaboration_only"]["counts"]
        text = [f"# removal view, roots {', '.join(meta['roots'])}; union located lines "
                f"{meta['union_located_lines']}; elaboration-only nominations {hazards or 'none'}"]
        if blocks:
            data["blocks"] = view = block_view(rows, blocks, args.blocks)
        if args.study:
            from ..reading_order import study_rankings
            data["study"] = study_rankings(reading, rows, meta["roots"], blocks, args.limit)
            for key, title in (("study_first", "Study first"), ("simplify_first", "Simplify first")):
                text.append(f"\n{title}")
                columns = ["name", "block", "roots_reached", "reachable_users", "root_distance",
                           "exclusive_source_lines", "source_lines", "repeated_lines", "reason"]
                text.append("\t".join(columns))
                text.extend("\t".join("" if row[k] is None else str(row[k]) for k in columns)
                            for row in data["study"][key])
        elif prior is not None:
            data["against"] = change = difference(prior, rows, blocks, args.against)
            columns = ["group", "lines_before", "lines_after", "difference", "names_added", "names_removed"]
            text.append(f"# against {args.against}, commit {str(change['prior_commit'])[:12]}, by {change['by']}")
            text.append("\t".join(["block" if blocks else "module_prefix", *columns[1:]]))
            text.extend("\t".join(f"{row[k]:+d}" if k == "difference" else str(row[k]) for k in columns)
                        for row in [*change["rows"], {"group": "TOTAL", **change["total"]}])
        elif blocks:
            text.append("block\tlines\tnames\tmodules")
            text.extend(f"{row['block']}\t{row['lines']}\t{row['names']}\t{row['modules']}" for row in view["rows"])
            if view["unmatched"]:
                text.append(f"{UNMATCHED}\t{sum(m['lines'] for m in view['unmatched'])}\t"
                            f"{sum(m['names'] for m in view['unmatched'])}\t{len(view['unmatched'])}")
            text.append(f"TOTAL\t{view['total_lines']}\t{len(rows)}\t{len({row['module'] for row in rows})}")
        else:
            text.append("\t".join(REMOVAL_COLUMNS))
            text.extend("\t".join("" if row[k] is None else ",".join(row[k]) if isinstance(row[k], list)
                                   else str(row[k]) for k in REMOVAL_COLUMNS) for row in rows)
        if blocks:
            text.extend(f"# no block: {m['module']}\t{m['lines']}" for m in view["unmatched"])
            if prior is not None:
                text.extend(f"# no block, before only: {m}" for m in change["unmatched_before"]
                            if m not in change["unmatched_after"])
        emit(args, "\n".join(text), data)
        return 0
    if meta["union_located_lines"] is not None:
        key, reason = "exclusive_source_lines", "exclusive located lines, then fewer reachable users, then name"
    else:
        key, reason = "exclusive_kernel_names", "no compiler ranges supplied: exclusive kernel names instead of lines"
    ordered = sorted((r for r in rows if r["indexed"]),
                     key=lambda r: (-r[key], r["reachable_users"], r["name"]))
    if len(meta["roots"]) == 1:
        declaration = reading.declaration(meta["roots"][0])
        data["root_source"] = {"path": declaration.path, "line": declaration.line}
    data.update(root=None, roots=meta["roots"], aim=args.aim, ranked_by=key, ranking_reason=reason,
                ranking_status="opt-in hypothesis generator; source length stays the default")
    return emit_order(args, reading, ordered, data)
