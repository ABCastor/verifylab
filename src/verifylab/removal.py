"""Multi-root removal view over a supplied kernel graph and, optionally, compiler declaration ranges.

Each distinct source command is a terminal vertex reached by all its declarations. A declaration's exclusive lines
are the located command lines that lose their last route from every retained root if that declaration's former
dependencies disappear: the lines dominated by it in the graph rooted at a synthetic origin. Commands that elaborate
several constants are charged once. Unknown locations stay counted as names with zero lines.
"""
from __future__ import annotations

import json
import re
from collections import defaultdict, deque

from .reading_order import artifact_digest, artifact_text
from .records import RecordError, sha256_hex

SCHEMA = "vl.removal/1"
SEMANTICS = ("exclusive_source_lines: located physical command lines whose every route from the retained roots "
             "passes through the row (dominator subtree over the supplied kernel rows); overlapping declaration "
             "ranges form one unit; unknown ranges count as names with 0 lines; nested rows are not additive")


def dominators(graph, roots):
    """Immediate dominators from a synthetic origin (None) over every vertex reachable from `roots`."""
    origin = None
    if not roots or any(r not in graph for r in roots):
        raise RecordError("all roots must resolve in the graph")
    successors = dict(graph)
    successors[origin] = sorted(set(roots))
    visited = {origin}
    stack = [(origin, iter(successors[origin]))]
    post = []
    while stack:
        node, children = stack[-1]
        child = next(children, StopIteration)
        if child is StopIteration:
            post.append(node)
            stack.pop()
            continue
        if child not in visited:
            visited.add(child)
            stack.append((child, iter(sorted(successors[child], key=repr))))
    order = post[::-1]
    pos = {n: i for i, n in enumerate(order)}
    preds = defaultdict(list)
    for n in order:
        for c in successors[n]:
            preds[c].append(n)
    parent = {origin: origin}
    changed = True
    while changed:
        changed = False
        for n in order[1:]:
            known = [p for p in preds[n] if p in parent]
            p = known[0]
            for other in known[1:]:
                while other != p:
                    while pos[other] > pos[p]:
                        other = parent[other]
                    while pos[p] > pos[other]:
                        p = parent[p]
            if parent.get(n, origin) != p or n not in parent:
                parent[n] = p
                changed = True
    distance = {origin: -1}
    todo = deque([origin])
    while todo:
        n = todo.popleft()
        for c in sorted(successors[n], key=repr):
            if c not in distance:
                distance[c] = distance[n] + 1
                todo.append(c)
    return parent, order, distance, preds


def profile(deps, roots, ownership, weight):
    """{name: (exclusive_lines, exclusive_names, distance, users, idom)}, union lines and reached spans."""
    graph = {n: set(ch) for n, ch in deps.items()}
    for n, span in ownership.items():
        if n in graph:
            graph[n].add(span)
    for span in weight:
        graph[span] = set()
    parent, order, distance, preds = dominators(graph, roots)
    totals = {n: weight.get(n, 0) for n in order}
    counts = {n: int(n in deps) for n in order}
    for n in order[:0:-1]:
        totals[parent[n]] += totals[n]
        counts[parent[n]] += counts[n]
    result = {n: (totals[n], counts[n], distance[n], len([v for v in preds[n] if v is not None]), parent[n])
              for n in order if n in deps}
    return result, totals[None], set(order) & set(weight)


def parse_range_row(fields, location):
    if len(fields) != 8:
        raise RecordError(f"{location}: expected an eight-column R row")
    try:
        first, _col, last, end_col, selection = map(int, fields[3:])
    except ValueError as exc:
        raise RecordError(f"{location}: expected an eight-column R row") from exc
    if not all(field.isascii() and field.isdigit() for field in fields[3:]):
        raise RecordError(f"{location}: expected an eight-column R row")
    _, module, name, *_ = fields
    # Lean records 1-based lines and an exclusive end position.
    return module, name, first, last - (end_col == 0), selection


def parse_type_row(fields, location):
    if len(fields) != 5 or re.fullmatch(r"[0-9]{1,20}", fields[3]) is None:
        raise RecordError(f"{location}: expected a five-column T row with a type hash")
    _, module, name, type_hash, _ = fields
    return module, name, type_hash


def read_ranges(path, root_modules):
    """Compiler ranges and any type hashes of the retained roots. Old range exports may have only R rows."""
    ranges, types = defaultdict(list), {}
    with artifact_text(path) as stream:
        for n, line in enumerate(stream, 1):
            fields = line.rstrip("\r\n").split("\t")
            if fields[0] == "R":
                module, name, first, last, selection = parse_range_row(fields, f"{path}:{n}")
                ranges[module].append((first, last, name, selection))
            elif fields[0] == "T" and len(fields) >= 3 and root_modules.get(fields[2]) == fields[1]:
                _, name, type_hash = parse_type_row(fields, f"{path}:{n}")
                if types.setdefault(name, type_hash) != type_hash:
                    raise RecordError(f"{path}:{n}: conflicting type hashes for {name}")
    return ranges, types


def command_units(ranges):
    """Overlapping ranges of one module form one indivisible editing unit."""
    units, direct = {}, {}
    for module, rows in ranges.items():
        current, end = [], 0
        for row in sorted(rows):
            if current and row[0] > end:
                units[module, current[0][0], end] = current
                current = []
            current.append(row)
            end = max(end, row[1])
        if current:
            units[module, current[0][0], end] = current
    for key, rows in units.items():
        for _, _, name, _ in rows:
            direct[name] = key
    return units, direct


def owners(names, module_of, direct):
    """Generated proof, equation and recursor names share their parent's source command."""
    owner = {n: direct[n] for n in names if n in direct}
    for n in names:
        if n in owner:
            continue
        module = module_of[n]
        short = re.sub(r"^_private\." + re.escape(module) + r"\.\d+\.", "", n)
        for spell in (n, short):
            while "." in spell:
                spell = spell.rsplit(".", 1)[0]
                if spell in direct and direct[spell][0] == module:
                    owner[n] = direct[spell]
                    break
            if n in owner:
                break
    return owner


def reach(edges, root):
    seen, stack = set(), [root]
    while stack:
        n = stack.pop()
        if n not in seen:
            seen.add(n)
            stack.extend(edges.get(n, ()))
    return seen


def removal_view(index, kernel, roots, ranges_path=None, selected=()):
    """`kernel` is the loaded supplied graph (reading_order.kernel_graph). Missing or ambiguous roots fail closed."""
    rows, edges, conflicts, named, module_of, path_of = (kernel[k] for k in
        ("rows", "edges", "conflicts", "named", "module_of", "path_of"))
    roots = list(dict.fromkeys(roots))
    for root in roots:
        index.declaration(root)
        if root not in rows:
            raise RecordError(f"{root} has no local row in the supplied graph")
        if root not in named:
            raise RecordError(f"{root}: source path does not match its supplied module")
    cone = set().union(*(reach(edges, r) for r in roots))
    selected = set(selected)
    if missing := selected - cone:
        raise RecordError("selected declarations are outside the reached local graph: " + ", ".join(sorted(missing)) +
                          ". Check the qualified names and retained roots")
    ambiguous = sorted(conflicts & cone)
    if ambiguous:
        raise RecordError(f"conflicting rows for reached constant {ambiguous[0]}")
    weight, owner, unit_count, types = {}, {}, None, {}
    if ranges_path is not None:
        ranges, types = read_ranges(ranges_path, {root: module_of[root] for root in roots})
        units, direct = command_units(stale_checked(index, ranges, kernel))
        weight = {k: k[2] - k[1] + 1 for k in units}
        owner = owners([n for n in rows if n in cone or n in direct], module_of, direct)
        unit_count = len(units)
    costs, union_lines, spans = profile({n: set(edges[n]) for n in cone}, roots, owner, weight)
    members = {r: reach(edges, r) for r in roots}
    outside = defaultdict(int)
    for n, targets in edges.items():
        if n not in cone:
            for t in targets:
                if t in cone:
                    outside[t] += 1
    users = defaultdict(list)
    for n in cone:
        for t in edges[n]:
            users[t].append(n)
    located = ranges_path is not None
    out = []
    for n, (lines, names, distance, callers, idom) in costs.items():
        unit = owner.get(n)
        out.append({"name": n, "kind": rows[n][0], "module": module_of[n], "path": path_of.get(n),
                    "indexed": n in named, "roots": [r for r in roots if n in members[r]],
                    "root_distance": distance, "reachable_users": callers, "unreached_users": outside[n],
                    "sole_user": users[n][0] if len(users[n]) == 1 else None,
                    "immediate_dominator": idom if isinstance(idom, str) else None,
                    "exclusive_kernel_names": names,
                    "exclusive_source_lines": lines if located else None,
                    "first_line": unit[1] if unit else None, "last_line": unit[2] if unit else None})
    out.sort(key=lambda r: (-(r["exclusive_source_lines"] or 0), -r["exclusive_kernel_names"], r["name"]))
    per_root = {}
    selected_rows = {row["name"]: row for row in out if row["name"] in selected}
    for row in selected_rows.values():
        row["root_distances"] = {}
    for r in roots:
        single, lines, _ = profile({n: set(edges[n]) for n in members[r]}, [r], owner, weight)
        per_root[r] = {"names": len(single), "command_lines": lines if located else None}
        for name, row in selected_rows.items():
            row["root_distances"][r] = single[name][2] if name in single else None
    meta = {"schema": SCHEMA, "semantics": SEMANTICS, "roots": roots, "per_root": per_root,
            "root_statements": {root: {"statement_sha256": sha256_hex(index.decls[root].statement.encode("utf-8")),
                                       "type_hash": types.get(root)} for root in roots},
            "reachable_kernel_names": len(cone), "reachable_indexed": len(cone & named),
            "union_located_lines": union_lines if located else None,
            "located_names_in_cone": len(cone & set(owner)) if located else None,
            "unlocated_names_in_cone": len(cone - set(owner)) if located else None,
            "reached_command_units": len(spans) if located else None, "local_command_units": unit_count,
            "all_located_command_lines": sum(weight.values()) if located else None,
            "ranges_sha256": artifact_digest(ranges_path) if located else None,
            "boundary": "local pinned module rows only; external constants and Mathlib are outside"}
    return meta, out


def check_range_bounds(path, name, first, last, lines):
    if first < 1 or last < first - 1 or last > lines:
        raise RecordError(f"ranges do not match the pinned source: {name} at {path}:{first}-{last}")


def stale_checked(index, ranges, kernel):
    """Ranges must name local modules and contain the pinned start line of every indexed declaration they cover."""
    lines_of = {}
    kept = {}
    for module, rows in ranges.items():
        paths = index.local_dump_paths(module)
        if len(paths) != 1:
            continue
        path = next(iter(paths))
        if path not in lines_of:
            lines_of[path] = len(index.source_text(path).splitlines())
        for first, last, name, _ in rows:
            check_range_bounds(path, name, first, last, lines_of[path])
            decl = index.decls.get(name)
            if decl is not None and decl.path == path and not first <= decl.line <= max(first, last):
                raise RecordError(f"ranges do not match the pinned source: {name} starts at {path}:{decl.line}")
        kept[module] = rows
    return kept


# Blocks and the difference between two versions ----------------------------------------------------------------------

PREFIX_DEPTH = 3
UNMATCHED = "(no block)"


def read_blocks(path):
    """[(block, compiled expression)] from `name<TAB>regular expression` lines, in file order."""
    blocks = []
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise RecordError(f"{path}: {exc}") from exc
    for n, line in enumerate(text.splitlines(), 1):
        if not line.strip() or line.startswith("#"):
            continue
        fields = line.split("\t")
        if len(fields) != 2 or not fields[0].strip() or not fields[1]:
            raise RecordError(f"{path}:{n}: expected a block name, a tab and a regular expression")
        try:
            blocks.append((fields[0].strip(), re.compile(fields[1])))
        except re.error as exc:
            raise RecordError(f"{path}:{n}: {exc}") from exc
    if not blocks:
        raise RecordError(f"{path}: no block")
    if UNMATCHED in {name for name, _ in blocks}:
        raise RecordError(f"{path}: {UNMATCHED} names the modules that match no block")
    return blocks


def grouper(blocks):
    """module -> its group: the first block whose expression is found in the name, or the module prefix."""
    def by_block(module):
        return next((name for name, pattern in blocks if pattern.search(module)), None)

    def by_prefix(module):
        parts = module.split(".")
        return ".".join(parts[:max(1, min(PREFIX_DEPTH, len(parts) - 1))])
    return by_block if blocks else by_prefix


def located(rows):
    """({module: located reached lines}, {module: reached names}) of removal rows. A command is counted once."""
    units, names = defaultdict(set), defaultdict(set)
    for row in rows:
        names[row["module"]].add(row["name"])
        if row["first_line"] is not None:
            units[row["module"]].add((row["first_line"], row["last_line"]))
    return {module: sum(last - first + 1 for first, last in spans) for module, spans in units.items()}, names


def block_view(rows, blocks, path):
    """Located reached lines and reached names per block, in file order, and the modules that match none."""
    lines, names = located(rows)
    group = grouper(blocks)
    table = {name: {"block": name, "lines": 0, "names": 0, "modules": 0} for name, _ in blocks}
    unmatched = []
    for module in sorted(names):
        entry = table.get(group(module))
        if entry is None:
            unmatched.append({"module": module, "lines": lines.get(module, 0), "names": len(names[module])})
            continue
        entry["lines"] += lines.get(module, 0)
        entry["names"] += len(names[module])
        entry["modules"] += 1
    return {"file_sha256": artifact_digest(path), "rule": "first block whose expression is found in the module name",
            "rows": list(table.values()), "unmatched": unmatched, "total_lines": sum(lines.values())}


def read_prior(path, current):
    """The rows of an earlier `vl value … --json` removal output for the same root statements."""
    try:
        data = json.loads(path.read_bytes())
    except (OSError, ValueError) as exc:
        raise RecordError(f"{path}: {exc}") from exc
    meta = data.get("removal") if isinstance(data, dict) else None
    rows = data.get("rows") if isinstance(data, dict) else None
    if not (isinstance(meta, dict) and meta.get("schema") == SCHEMA and isinstance(rows, list)
            and all(isinstance(row, dict) and isinstance(row.get("name"), str) and isinstance(row.get("module"), str)
                    and all(key in row and (row[key] is None or type(row[key]) is int)
                            for key in ("first_line", "last_line"))
                    and (row["first_line"] is None) == (row["last_line"] is None) for row in rows)):
        raise RecordError(f"{path}: expected the --json output of vl value with --removal ({SCHEMA})")
    if meta.get("union_located_lines") is None:
        raise RecordError(f"{path}: that output has no located lines; repeat it with --removal")
    roots = meta.get("roots")
    if not isinstance(roots, list) or not all(isinstance(root, str) for root in roots):
        raise RecordError(f"{path}: expected a list of root names")
    if sorted(roots) != sorted(current["roots"]):
        raise RecordError(f"{path} measures other roots ({', '.join(roots) or 'none'}): "
                          "a difference compares the same statements")
    statements = meta.get("root_statements")
    if not isinstance(statements, dict) or statements.keys() != current["root_statements"].keys():
        raise RecordError(f"{path}: missing root statement identities; regenerate the prior at its pin")
    for root, identity in current["root_statements"].items():
        if statements[root] != identity:
            raise RecordError(f"{path}: statement identity differs for {root} "
                              "(source signature or compiled type hash)")
    # A compiler range ending at its start in column zero covers zero lines.
    if any(row["first_line"] is not None
           and (row["first_line"] < 1 or row["last_line"] < row["first_line"] - 1) for row in rows):
        raise RecordError(f"{path}: invalid source span in the prior removal output")
    if (len({row["name"] for row in rows}) != meta.get("reachable_kernel_names") or len(rows) != len(
            {row["name"] for row in rows}) or sum(located(rows)[0].values()) != meta["union_located_lines"]):
        raise RecordError(f"{path}: its rows do not add up to its {meta.get('reachable_kernel_names')} reached names "
                          f"and {meta['union_located_lines']} located lines")
    return data


def difference(prior, rows, blocks, path):
    """Per block, or per module prefix, the located reached lines before and after and the names that came or went."""
    group = grouper(blocks)
    before_lines, before_names = located(prior["rows"])
    after_lines, after_names = located(rows)
    was = set().union(*before_names.values()) if before_names else set()
    now = set().union(*after_names.values()) if after_names else set()
    table = {name: None for name, _ in blocks}
    for side, lines, names, other, count in (("lines_before", before_lines, before_names, now, "names_removed"),
                                             ("lines_after", after_lines, after_names, was, "names_added")):
        for module in names:
            key = group(module) or UNMATCHED
            entry = table.get(key) or {"group": key, "lines_before": 0, "lines_after": 0, "names_added": 0,
                                       "names_removed": 0}
            table[key] = entry
            entry[side] += lines.get(module, 0)
            entry[count] += len(names[module] - other)
    out = [entry or {"group": key, "lines_before": 0, "lines_after": 0, "names_added": 0, "names_removed": 0}
           for key, entry in table.items()]
    if not blocks:
        out.sort(key=lambda entry: entry["group"])
    for entry in out:
        entry["difference"] = entry["lines_after"] - entry["lines_before"]
    total = {key: sum(entry[key] for entry in out)
             for key in ("lines_before", "lines_after", "difference", "names_added", "names_removed")}
    return {"prior_sha256": artifact_digest(path), "prior_commit": prior.get("commit"), "by": "block" if blocks else
            f"module prefix of {PREFIX_DEPTH} components", "rows": out, "total": total,
            "unmatched_before": sorted(m for m in before_names if blocks and group(m) is None),
            "unmatched_after": sorted(m for m in after_names if blocks and group(m) is None)}
