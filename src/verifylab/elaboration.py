"""Pre-prune report: source commands that elaboration needs although a kernel dependency graph can miss them.

The kernel stores elaborated terms. A simp lemma used by a bare `simp`, an instance found by search, a notation, a
parser or syntax registration, a linter option or a lemma named only inside tactic source can be absent from every
proof term and still be needed to rebuild the source. Rows here are lexical nominations for keeping or testing,
never a verdict: only a cold build after deletion decides.
"""
from __future__ import annotations

import re
from collections import Counter

from . import leanmod

SEMANTICS = ("lexical scan of the pinned source: named rows outside the kernel cone of the retained roots, every "
             "syntax, parser, option and linter registration, and source names whose kernel edge is missing; a "
             "cold build after deletion is the only check")

ELAB_ATTRS = ("simp", "norm_cast", "push_cast", "ext", "simps", "aesop", "gcongr", "positivity", "fun_prop",
              "measurability", "continuity", "mono", "bound", "grind", "field_simps", "nontriviality", "instance",
              "default_instance", "coe", "elab_as_elim", "induction_eliminator", "cases_eliminator", "csimp",
              "macro", "term_elab", "command_elab", "tactic", "builtin_term_parser", "command_parser")
_ATTR_WORD = re.compile(r"(?<![\w.])(" + "|".join(ELAB_ATTRS) + r")(?![\w])")
_PREFIX = r"^(?P<attrs>(?:@\[[^\]]*\]\s*)*)(?P<mods>(?:(?:private|protected|public|noncomputable|unsafe|partial|nonrec|scoped|local)\s+)*)"
_DECL = re.compile(_PREFIX + r"(?P<kw>theorem|lemma|def|abbrev|instance|structure|class|inductive|opaque|axiom)\b"
                   r"(?:\s+(?P<name>[^\s:(\[{]+))?")
_ATTRIBUTE = re.compile(r"^attribute\s*\[(?P<attrs>[^\]]*)\]\s+(?P<names>[^\n:]+)")
_SYNTAX = re.compile(_PREFIX + r"(?P<kw>notation3?|infixl?|infixr|prefix|postfix|macro_rules|macro|syntax|"
                     r"elab_rules|elab|declare_syntax_cat|binder_predicate|unif_hint|simproc|dsimproc|"
                     r"register_simp_attr)\b")
_REGISTER = re.compile(r"^(?:(?:private|public)\s+)?(?P<kw>initialize|builtin_initialize|register_[a-z_]+)\b")
_ONLY_ATTRS = re.compile(r"^(?:@\[[^\]]*\]\s*)+$")
_LINTER = re.compile(r"^set_option\s+(?P<name>linter\.[\w.]+)\s+\S+\s*$")


def _qualified(local, scopes):
    if local.startswith("_root_."):
        return local[len("_root_."):]
    return ".".join([*(p for kind, parts in scopes if kind == "namespace" for p in parts), local])


def scan(path, source):
    """(kind, name or None, line, detail) for every elaboration-relevant command of one file."""
    masked = leanmod.mask_comments_and_strings(source)
    header = leanmod.parse_header(source, check_body=False)
    scopes, found, previous, line, pending = [], [], 0, 1, ""
    for s, e in leanmod._chunks(masked, header.end):
        line += masked.count("\n", previous, s)
        previous = s
        chunk = pending + masked[s:e]
        if _ONLY_ATTRS.match(chunk):
            # An attribute line in column 0 belongs to the next command.
            pending = chunk.rstrip() + " "
            continue
        pending = ""
        words = chunk.split()
        head = words[0] if words else ""
        if head == "namespace" and len(words) > 1:
            scopes.append(("namespace", words[1].split(".")))
            continue
        if head in ("section", "mutual") or (head in ("noncomputable", "public") and words[1:2] == ["section"]):
            scopes.append(("section", []))
            continue
        if head == "end":
            if scopes:
                scopes.pop()
            continue
        if m := _SYNTAX.match(chunk):
            found.append(("syntax", None, line, m.group("kw")))
        elif m := _REGISTER.match(chunk):
            found.append(("registration", None, line, m.group("kw")))
        elif m := _LINTER.match(chunk.strip()):
            found.append(("linter option", m.group("name"), line, "set_option"))
        elif m := _ATTRIBUTE.match(chunk):
            attrs = sorted(set(_ATTR_WORD.findall(m.group("attrs"))))
            if attrs:
                for name in m.group("names").split():
                    if leanmod.is_valid_decl_name(name):
                        found.append(("attribute", _qualified(name, scopes), line, " ".join(attrs)))
        elif m := _DECL.match(chunk):
            name = m.group("name")
            if name is not None and not leanmod.is_valid_decl_name(name):
                name = None
            name = _qualified(name, scopes) if name else None
            attrs = sorted(set(_ATTR_WORD.findall(m.group("attrs"))))
            if m.group("kw") == "instance":
                found.append(("instance", name, line, " ".join(attrs) or "instance"))
            elif attrs:
                found.append(("attribute", name, line, " ".join(attrs)))
    return found


def _private_stripped(names):
    out = set()
    for n in names:
        m = re.match(r"^_private\..+?\.\d+\.(.+)$", n)
        out.add(m.group(1) if m else n)
    return out


def report(index, kernel_names, cone, source_edges):
    """Hazards for pruning by the kernel cone `cone` (names), given every local kernel row name."""
    known, inside = _private_stripped(kernel_names), _private_stripped(cone)
    rows, omissions = [], []
    for path in index.lean_paths():
        try:
            found = scan(path, index.source_text(path))
        except (leanmod.LeanModError, UnicodeError) as exc:
            omissions.append(f"{path}: {exc}")
            continue
        for kind, name, line, detail in found:
            if kind in ("syntax", "registration", "linter option"):
                rows.append({"kind": kind, "name": name, "path": path, "line": line, "detail": detail,
                             "in_cone": None, "reason": "elaboration infrastructure; no kernel edge records its use"})
                continue
            state = None if name is None or name not in known else name in inside
            if state is True:
                continue
            reason = ("outside the kernel cone; search or tactics may still use it" if state is False
                      else "no kernel row under this name")
            rows.append({"kind": kind, "name": name, "path": path, "line": line, "detail": detail,
                         "in_cone": state, "reason": reason})
    for user in sorted(cone):
        if user not in index.decls:
            continue
        for target in source_edges.get(user, ()):
            if target not in inside and target in index.decls:
                decl = index.decls[target]
                rows.append({"kind": "source reference", "name": target, "path": decl.path, "line": decl.line,
                             "detail": f"named in {user}", "in_cone": False,
                             "reason": "named in a reached proof's source, with no kernel edge from the cone"})
    rows.sort(key=lambda r: (r["path"], r["line"], r["kind"], r["name"] or ""))
    return {"semantics": SEMANTICS, "counts": dict(sorted(Counter(r["kind"] for r in rows).items())),
            "rows": rows, "omissions": omissions}
