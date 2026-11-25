"""Root-conditioned reading costs and demand. The field calculation keeps route and effort as separate columns."""
from __future__ import annotations

import gzip
import hashlib
import io
import math
import unicodedata
from collections import defaultdict, deque
from contextlib import contextmanager
from pathlib import Path

from .foreign import Reading, read_json
from .records import RecordError, sha256_hex


def source_edges(index):
    short = defaultdict(list)
    for name in index.decls:
        short[name.rsplit(".", 1)[-1]].append(name)
    rows = {}
    for name, decl in index.decls.items():
        targets = set()
        for token in decl.tokens:
            if token in index.decls:
                targets.add(token)
            elif len(short.get(token, ())) == 1:
                targets.add(short[token][0])
        rows[name] = sorted(targets - {name})
    return rows


def check_dep_row(fields, location):
    if len(fields) != 7 or fields[4] not in ("S", "-"):
        raise RecordError(f"{location}: expected a seven-column dumpx DEP row")


@contextmanager
def artifact_text(path):
    with path.open("rb") as binary:
        if path.suffix == ".gz":
            with gzip.open(binary, "rt", encoding="utf-8") as text:
                yield text
        else:
            with io.TextIOWrapper(binary, encoding="utf-8") as text:
                yield text


def artifact_digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def dump_rows(path):
    # Axiom receipts are part of duplicate-row identity, never dependency edges.
    with artifact_text(path) as stream:
        for n, line in enumerate(stream, 1):
            fields = line.rstrip("\n").split("\t")
            if fields[0] != "DEP":
                continue
            check_dep_row(fields, f"{path}:{n}")
            yield n, fields


def proved(index, path, kinds):
    """A theorem of the pinned source must be a theorem row. The public part of a module-system file holds it as
    an axiom, without its proof: a graph made of such rows stops at the statements and still looks like an answer."""
    bare = sorted(name for name, kind in kinds.items() if kind != "thm" and name in index.decls
                  and index.decls[name].kind in ("theorem", "lemma"))
    if bare:
        raise RecordError(f"{path}: {len(bare)} theorems of the pinned source are not theorem rows, first {bare[0]} "
                          f"({kinds[bare[0]]}): these rows hold no proof. Write the graph with vl graph --dump")


def dump_edges(path, index):
    rows = {}
    hints = {}
    kinds = {}
    for n, fields in dump_rows(path):
        name = fields[3]
        if index.module_paths is not None:
            source = index.module_paths.get(fields[2])
            decl = index.decls.get(name)
            if source is None or (decl is not None and decl.path != source):
                continue
        if name in index.decls or name in index.ambiguous:
            if name in rows:
                raise RecordError(f"{path}:{n}: duplicate constant {name}")
            rows[name] = fields[5].split()
            kinds[name] = fields[1]
            if name in index.ambiguous:
                hints[name] = fields[2]
    if hints:
        index.resolve_dump_modules(hints)
    proved(index, path, kinds)
    names = set(index.decls)
    edges = {name: sorted(names.intersection(targets) - {name})
             for name, targets in rows.items() if name in names}
    if not edges:
        raise RecordError("dump has no matching source declaration")
    return edges


def graph(index, deps):
    if deps is None:
        return source_edges(index), {"kind": "approximate source name references", "scope": "indexed declarations"}
    edges = dump_edges(deps, index)
    return edges, {**dump_origin(deps), "scope": "indexed endpoints only",
                   "covered": len(edges), "indexed": len(index.decls)}


def dump_origin(deps):
    return {"kind": "supplied olean direct references", "artifact_sha256": artifact_digest(deps),
            "artifact_origin": "caller supplied; source correspondence unchecked"}


def kernel_graph(index, path):
    """Supplied dumpx rows whose modules have pinned local source paths, with conflicts kept for the caller."""
    index.prepare_local_dump()
    modules = {}
    rows = {}
    conflicts = set()
    locations = defaultdict(set)
    for _, (_, kind, module, name, flag, targets, axioms) in dump_rows(path):
        if module not in modules:
            modules[module] = index.local_dump_paths(module)
        if not modules[module]:
            continue
        locations[name].add(module)
        row = (kind, flag, frozenset(targets.split()), frozenset(axioms.split()))
        if rows.setdefault(name, row) != row:
            conflicts.add(name)
    hints = {name: next(iter(locations[name])) for name in index.ambiguous
             if name in locations and len(locations[name]) == 1 and name not in conflicts}
    if hints:
        index.resolve_dump_modules(hints)
    named = {name for name, decl in index.decls.items()
             if any(decl.path in modules[module] for module in locations.get(name, ()))}
    proved(index, path, {name: rows[name][0] for name in named})
    edges = {name: sorted(row[2].intersection(rows) - {name}) for name, row in rows.items()}
    module_of = {name: min(locations[name]) for name in rows}
    path_of = {}
    for name in rows:
        if name in named:
            path_of[name] = index.decls[name].path
        elif len(modules[module_of[name]]) == 1:
            path_of[name] = next(iter(modules[module_of[name]]))
    return {"rows": rows, "edges": edges, "conflicts": conflicts, "named": named, "module_of": module_of,
            "path_of": path_of}


def kernel_origin(index, path, kernel, cone):
    frontier = set().union(*(kernel["rows"][name][2] for name in cone)) - kernel["rows"].keys()
    return {**dump_origin(path), "scope": "supplied rows with pinned local module paths; named source output",
            "covered": len(kernel["named"]), "indexed": len(index.decls), "local_rows": len(kernel["rows"]),
            "reachable_kernel": len(cone), "reachable_indexed": len(kernel["named"].intersection(cone)),
            "frontier_constants": len(frontier)}


def kernel_values(index, path, root):
    kernel = kernel_graph(index, path)
    rows, named, edges = kernel["rows"], kernel["named"], kernel["edges"]
    index.declaration(root)
    if root not in rows:
        raise RecordError(f"{root} has no local row in the supplied graph")
    if root not in named:
        raise RecordError(f"{root}: source path does not match its supplied module")
    level, users = reachable(edges, root)
    ambiguous = sorted(kernel["conflicts"].intersection(level))
    if ambiguous:
        raise RecordError(f"{path}: conflicting rows for reached constant {ambiguous[0]}")
    return rank_rows(index, edges, root, level, users, named), kernel_origin(index, path, kernel, level)


def reachable(edges, root):
    level = {root: 0}
    users = defaultdict(int)
    pending = deque([root])
    while pending:
        node = pending.popleft()
        for target in edges.get(node, ()):
            users[target] += 1
            if target not in level:
                level[target] = level[node] + 1
                pending.append(target)
    return level, users


def values(index, edges, root):
    index.declaration(root)
    if root not in edges:
        raise RecordError(f"{root} has no row in the supplied graph")
    level, users = reachable(edges, root)
    return rank_rows(index, edges, root, level, users, index.decls)


def rank_rows(index, edges, root, level, users, named):
    # Port of exact_values.py: restart at the root, split mass over outgoing dependency edges.
    demand = {root: 1.0}
    for _ in range(60):
        current = defaultdict(float, {root: 0.5})
        for node, mass in demand.items():
            deps = edges.get(node, ())
            for target in deps:
                current[target] += 0.5 * mass / len(deps)
        demand = current
    return [{"name": name, "level": level[name], "users": users[name], "demand50": demand.get(name, 0.0),
             "lines": index.decls[name].lines, "signature_chars": len(index.decls[name].statement),
             "edges_observed": name in edges} for name in sorted(level) if name in named]


def model_columns(path: Path, commit, rows, index):
    data = read_json(path)
    if not isinstance(data, dict) or data.get("schema") != "vl.judgments/1" or data.get("commit") != commit:
        raise RecordError("judgments require vl.judgments/1 and this commit")
    cost = data.get("cost_usd")
    if (not isinstance(data.get("model"), str) or not data["model"].strip()
            or type(cost) not in (float, int) or not math.isfinite(cost) or cost < 0):
        raise RecordError("judgments require a model and finite nonnegative cost_usd")
    scores = data.get("rows")
    if not isinstance(scores, dict) or set(scores) - {row["name"] for row in rows}:
        raise RecordError("judgments contain unknown rows")
    for row in rows:
        score = scores.get(row["name"])
        if score is None and row["name"] not in scores:
            continue
        decl = index.decls[row["name"]]
        if (not isinstance(score, dict) or score.get("statement_sha256") != sha256_hex(decl.statement.encode())
                or type(score.get("relevance")) not in (int, float) or not 0 <= score["relevance"] <= 1):
            raise RecordError(f"{decl.name}: expected this statement digest and relevance in [0,1]")
        row["model_relevance"] = score["relevance"]
    return {"model": data["model"], "cost_usd": cost}


def packet(index: Reading, ranked, limit, byte_budget):
    """Only verbatim supported signatures enter model input. Locators, scores and omissions stay outside it."""
    chunks, sources, omissions = [], [], []
    size = 0
    for row in ranked:
        decl = index.decls[row["name"]]
        # Transport newlines are normalized explicitly; the source hash stays that of the original statement.
        statement = decl.statement.replace("\r\n", "\n").replace("\r", "\n")
        # Comments can carry proofs, labels or the answer. A literal or attribute needs a richer input contract.
        has_control = any(unicodedata.category(ch) in ("Cc", "Cf") and ch not in "\t\n" for ch in statement)
        if has_control or any(word in statement for word in ("/-", "--", '"', "@[", "«", "`")):
            omissions.append({"name": decl.name, "reason": "outside plain statement contract"})
            continue
        text = decl.name + "\n" + statement + "\n"
        length = len(text.encode())
        if len(chunks) >= limit or size + length > byte_budget:
            omissions.append({"name": decl.name, "reason": "budget"})
            continue
        chunks.append(text)
        size += length
        sources.append({"name": decl.name, "path": decl.path, "line": decl.line,
                        "statement_sha256": sha256_hex(decl.statement.encode()),
                        "emitted_sha256": sha256_hex(text.encode()),
                        "newlines": "normalized" if statement != decl.statement else "unchanged"})
    content = "".join(chunks)
    return {"input_contract": "qualified name and verbatim source signature only, newlines normalized to LF", "input": content,
            "bytes": size, "input_sha256": sha256_hex(content.encode()), "sources": sources,
            "omitted": omissions}


def study_rankings(index, rows, roots, blocks, limit):
    """Rank the same reached declarations twice, using removal costs and pinned source text."""
    from collections import Counter

    from .removal import grouper

    group = grouper(blocks)
    sources = {}
    units = {}
    for row in rows:
        path, first, last = (row[k] for k in ("path", "first_line", "last_line"))
        span = (path, first, last)
        if path is None or first is None or span in units:
            continue
        if path not in sources:
            sources[path] = index.source_text(path).splitlines()
        lines = (" ".join(line.split()) for line in sources[path][first - 1:last])
        units[span] = [line for line in lines if line and not line.startswith(("--", "/-", "-/"))
                       and any(ch.isalnum() for ch in line)]
    counts = Counter(line for lines in units.values() for line in lines)
    candidates = []
    for row in rows:
        name = row["name"]
        if not row["indexed"] or name in roots:
            continue
        span = tuple(row[key] for key in ("path", "first_line", "last_line"))
        candidates.append({
            **row, "block": group(row["module"]), "roots_reached": len(row["roots"]),
            "source_lines": index.decls[name].lines,
            "repeated_lines": sum(counts[line] > 1 for line in units[span]) if span in units else None,
        })

    study = sorted(candidates, key=lambda row: (-row["roots_reached"], -row["reachable_users"],
                                               row["root_distance"], row["name"]))
    simplify = sorted(candidates, key=lambda row: (-row["exclusive_source_lines"], -row["source_lines"],
                                                  -(row["repeated_lines"] or 0), row["name"]))
    return {
        "study_first": [{**r, "reason": f"reached by {r['roots_reached']} roots; "
                                        f"{r['reachable_users']} direct reached users; "
                                        f"{r['root_distance']} edges from the nearest root"}
                        for r in study[:limit]],
        "simplify_first": [{**r, "reason": f"{r['exclusive_source_lines']} exclusive located lines; "
                                           f"{r['source_lines']} source lines; "
                                           f"{r['repeated_lines'] if r['repeated_lines'] is not None else 'unknown'} "
                                           "repeated lines"}
                           for r in simplify[:limit]],
    }
