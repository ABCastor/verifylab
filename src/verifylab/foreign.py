"""An ordinary Lake checkout as immutable Git input to the reading commands."""
from __future__ import annotations

import json
import os
import re
import shlex
import tomllib
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from . import gitref, leanmod
from .channel_patterns import BUILD, ESCAPES
from .output import emit
from .records import RecordError, sha256_hex, write_new_json


def read_json(path: Path):
    try:
        return json.loads(path.read_bytes())
    except FileNotFoundError:
        raise
    except (OSError, ValueError) as exc:
        raise RecordError(f"{path}: {exc}") from exc


@dataclass(frozen=True)
class Checkout:
    root: Path
    commit: str

    def read(self, path: str) -> bytes:
        data = gitref.show(self.root, self.commit, path)
        if data is None:
            raise RecordError(f"{path} missing at {self.commit[:12]}")
        return data

    def inventory(self):
        paths = set(gitref.ls_files(self.root, self.commit, ""))
        configs = sorted(paths.intersection(("lakefile.toml", "lakefile.lean")))
        if not configs:
            raise RecordError("expected a Lake configuration at the checkout root")
        raw = self.read("lake-manifest.json")
        try:
            manifest = json.loads(raw)
            packages = manifest["packages"]
            if not isinstance(packages, list):
                raise ValueError("packages must be a list")
            dependencies = []
            seen = set()
            for entry in packages:
                if not isinstance(entry, dict) or not isinstance(entry.get("name"), str) or entry["name"] in seen:
                    raise ValueError("invalid or duplicate package name")
                seen.add(entry["name"])
                kind = entry.get("type")
                if kind == "git" and not (isinstance(entry.get("url"), str) and
                                          gitref.is_full_sha(str(entry.get("rev")))):
                    raise ValueError(f"{entry['name']}: expected a URL and a full Git pin")
                if kind == "path" and not isinstance(entry.get("dir"), str):
                    raise ValueError(f"{entry['name']}: missing dependency directory")
                dependencies.append({k: entry[k] for k in ("name", "type", "url", "rev", "dir", "subDir") if k in entry})
            cfg = tomllib.loads(self.read("lakefile.toml").decode()) if "lakefile.toml" in configs else {}
            default_targets = cfg.get("defaultTargets", [])
            if not isinstance(default_targets, list) or not all(isinstance(name, str) for name in default_targets):
                raise ValueError("defaultTargets must be a list of strings")
        except (ValueError, KeyError, TypeError) as exc:
            raise RecordError(f"invalid Lake metadata: {exc}") from exc
        return {"commit": self.commit, "toolchain": self.read("lean-toolchain").decode().strip(),
                "configs": configs, "manifest_sha256": sha256_hex(raw), "dependencies": dependencies,
                "default_targets": default_targets,
                "lean_files": sum(p.endswith(".lean") and not p.startswith(".lake/") for p in paths)}

    @classmethod
    def pinned(cls, path: Path, revision: str) -> Checkout:
        root = path.resolve()
        if gitref.toplevel(root) != root:
            raise RecordError("supply the Git checkout root; reading and trials require the Lake project at that root")
        return cls(root, gitref.rev_parse(root, revision))

    @classmethod
    def locked(cls, path: Path) -> Checkout:
        try:
            data = read_json(path)
        except FileNotFoundError:
            pin = f"vl pin PROJECT --at REV --lock {shlex.quote(str(path))}"
            raise RecordError(f"{path}: no pin file. Run {pin} with the intended checkout and revision",
                              has_recovery=True) from None
        if not (isinstance(data, dict) and data.get("schema") == "vl.lock/1"
                and isinstance(data.get("checkout"), str) and gitref.is_full_sha(str(data.get("commit")))):
            raise RecordError(f"{path}: expected vl.lock/1 with a local checkout and full commit")
        project = cls((path.parent / data["checkout"]).resolve(), data["commit"])
        if not (project.root / ".git").exists():
            recover = shlex.join(["vl", "pin", ".", "--at", project.commit,
                                  "--lock", str(path.absolute().with_name(path.name + ".repin"))])
            raise RecordError(f"{path.absolute()}: no Git checkout at the resolved path {project.root}. "
                              f"From the intended Git checkout root, run:\n{recover}", has_recovery=True)
        if data != {"schema": "vl.lock/1", "checkout": data["checkout"], **project.inventory()}:
            raise RecordError(f"{path}: lock differs from the pinned commit")
        return project


def import_lake(args):
    project = Checkout.pinned(args.project, args.at)
    lock = {"schema": "vl.lock/1", "checkout": os.path.relpath(project.root, args.lock.parent), **project.inventory()}
    try:
        write_new_json(args.lock, lock)
    except FileExistsError:
        if read_json(args.lock) != lock:
            raise RecordError(f"{args.lock} already holds a different pin; choose a new --lock")
    next_build = [f"vl graph {args.lock} --build {target}" for target in lock["default_targets"]]
    text = (f"{args.lock}: {project.commit[:12]} · {lock['toolchain']}\n"
            f"{lock['lean_files']} Lean files, {len(lock['dependencies'])} dependencies. "
            f"Named default targets: {', '.join(lock['default_targets']) or 'unknown'}.")
    emit(args, "\n".join([text, *next_build]), {"lock": str(args.lock), "project": lock,
         "build": "source only; cache availability unmeasured", "estimate_targets": lock["default_targets"]})
    return 0


@dataclass(frozen=True)
class Declaration:
    name: str
    path: str
    line: int
    statement: str
    tokens: frozenset[str]
    lines: int
    binders: tuple[str, ...]
    kind: str = "theorem"               # the command keyword


_OPENERS = {"(": ")", "[": "]", "{": "}", "⦃": "⦄", "⟨": "⟩"}
_CLOSERS = {close: opener for opener, close in _OPENERS.items()}
# A local definition in the stated type owns the next `:=` outside every bracket; the body delimiter comes after it.
_LOCAL = ("let", "have", "haveI", "letI", "let_fun", "let_delayed", "let_tmp")
# A tactic block, a `do` block, alternatives or calculation steps: their end cannot be told without elaboration.
_UNDECIDED = ("by", "do", "match", "calc")
_BAR_OPERATORS = ("<|>", "<|", "|>", "|||", "||")
_SIGNATURE_TOKEN = re.compile(r"<\|>|<\||\|>|\|\|\|?|::|:=|=>|:|[()\[\]{}⦃⦄⟨⟩|`«]|λ|\b(?:where|fun|sorry|"
                              + "|".join(_LOCAL + _UNDECIDED) + r")\b")
_ESCAPED = re.compile(r"«[^«»\n]*»")


def signature(chunk: str):
    """The statement of one source command, up to its body, and its typed binder groups; None when undecided.

    The body starts at the first `:=` or `where` outside every bracket after the colon of the stated type, or at an
    equation alternative `| pattern =>` that opens a line. Bracketed text is opaque and kept verbatim: named
    arguments, set builders, structure instances, default values. Ambiguous term forms are outside the
    reading-packet contract."""
    masked = leanmod.mask_comments_and_strings(chunk)
    masked = _ESCAPED.sub(lambda name: "x" * len(name.group()), masked)     # an escaped name is one opaque word
    stack = []
    binders = []
    start = result = alternative = None     # offsets: the open outer group, after the type colon, a first `|`
    typed = False                           # a colon in the open outer group
    local = 0                               # local definitions of the stated type still waiting for their `:=`
    plain = True                            # no `fun` and no local definition: a bar can only open the equations
    barred = False                          # a bar inside a line: an absolute value, unless an arrow follows it
    for m in _SIGNATURE_TOKEN.finditer(masked):
        tok = m.group()
        if tok in ("`", "«", "sorry"):
            return None
        if tok == "::":
            continue                        # list cons, in a type or in a pattern: never the colon of the type
        if tok in _OPENERS:
            if not stack:
                start, typed = m.start(), False
            stack.append(tok)
        elif tok in _CLOSERS:
            if not stack or stack.pop() != _CLOSERS[tok]:
                return None
            if not stack and result is None and typed and tok != "⟩" and masked[start - 1:start] != "@":
                binders.append(chunk[start:m.end()])
        elif stack:
            typed = typed or tok == ":"
        elif alternative is not None:
            # Only patterns stand between the bar of an alternative and its arrow. A second bar first: an absolute
            # value that opened the line, or two patterns, which the arrow rule below then refuses.
            if tok == "=>":
                return chunk[:alternative].strip(), tuple(binders)
            if tok != "|":
                return None
            alternative, barred = None, True
        elif tok == ":":
            result = m.end() if result is None else result
        elif tok in _BAR_OPERATORS:
            continue
        elif tok == "=>":
            if barred:
                return None                 # alternatives on one line, or of a `fun`: not an absolute value
        elif result is None:
            return None
        elif tok == ":=" and local:
            local -= 1
        elif tok in (":=", "where"):
            return (chunk[:m.start()].strip(), tuple(binders)) if not local else None
        elif tok in ("fun", "λ"):
            plain = False
        elif tok in _LOCAL:
            local, plain = local + 1, False
        elif tok == "|":
            # Alternatives come after a stated type and open a line. Any other bar is an absolute value.
            if masked[masked.rfind("\n", 0, m.start()) + 1:m.start()].strip() or not masked[result:m.start()].strip():
                barred = True
            elif not plain:
                return None                 # `fun` and `let` have alternatives of their own
            else:
                alternative = m.start()
        else:
            return None
    return None


TOKEN = re.compile(r"[^\W\d][\w'!?]*(?:\.[^\W\d][\w'!?]*)*")
FLAGS = {key: [(name, re.compile(pattern, re.M)) for name, pattern in rules]
         for key, rules in (("axioms", ESCAPES), ("build_code", BUILD))}


class Reading:
    """A source index for this invocation. Source indicators have no path into verification records."""

    def __init__(self, project: Checkout, *, source_file: str | None = None, library_scope: bool = False,
                 module_paths: dict[str, str] | None = None):
        self.decls: dict[str, Declaration] = {}
        self.omissions: list[str] = []
        self.imports: dict[str, list[str]] = {}
        self.signals = {key: [] for key in FLAGS}
        self._project = project
        self.module_paths = module_paths
        candidates = defaultdict(list)
        self.scope = "all pinned Lean files"
        self.excluded_by_library_scope = 0
        if module_paths is not None:
            paths = sorted(set(module_paths.values()))
            self.scope = "pinned graph manifest modules"
        elif source_file is not None:
            if not source_file.endswith(".lean") or source_file.startswith(".lake/"):
                raise RecordError("--file needs a pinned project-relative Lean path")
            paths = [source_file]
            self.scope = source_file
        else:
            paths = gitref.ls_files(project.root, project.commit, "")
            config = (gitref.show(project.root, project.commit, "lakefile.toml")
                      if library_scope and "lakefile.lean" not in paths else None)
            try:
                libraries = leanmod.lean_libraries(config) if config is not None else []
            except leanmod.LeanModError:
                libraries = []  # fall back to Git when the Lake layout cannot be read statically
            if libraries:
                library_paths = {path for _, path in library_sources(libraries, paths)}
                self.excluded_by_library_scope = sum(path.endswith(".lean") and not path.startswith(".lake/")
                                                     and path not in library_paths for path in paths)
                paths = sorted(library_paths)
                self.scope = "pinned Lake library source roots"
        self._paths = set(paths)
        for path in paths:
            if not path.endswith(".lean") or path.startswith(".lake/"):
                continue
            try:
                source = project.read(path).decode()
                masked = leanmod.mask_comments_and_strings(source)
                for key, patterns in FLAGS.items():
                    for flag, pattern in patterns:
                        if pattern.search(masked):
                            self.signals[key].append({"file": path, "indicator": flag})
                header = leanmod.parse_header(source)
                self.imports[path] = header.modules
                names = leanmod.theorem_spans(masked, header.end, reading=True)
            except (leanmod.LeanModError, UnicodeError) as exc:
                self.omissions.append(f"{path}: {exc}")
                continue
            for name, declarations in names.items():
                if len(declarations) != 1:
                    candidates[name].extend((path, None) for _ in declarations)
                    continue
                decl = declarations[0]
                a, b = decl.span
                line = decl.line
                sig = signature(source[a:b])
                if sig is None:
                    self.omissions.append(f"{path}:{line} {name}: unsupported signature")
                    candidates[name].append((path, None))
                    continue
                statement, binders = sig
                candidates[name].append((path, Declaration(name, path, line, statement,
                    frozenset(TOKEN.findall(masked[a:b])), len(masked[a:b].rstrip().splitlines()), binders,
                    decl.kind)))
        self.ambiguous = {}
        for name, occurrences in candidates.items():
            if len(occurrences) == 1:
                declaration = occurrences[0][1]
                if declaration is not None:
                    self.decls[name] = declaration
                continue
            self.ambiguous[name] = occurrences
            self.omissions.extend(f"{path}: {name}: ambiguous declaration" for path in sorted({p for p, _ in occurrences}))

    def resolve_dump_modules(self, module_hints):
        # The artifact is caller supplied. A hint can distinguish source files, not
        # resolve multiple declarations in one module or prove source correspondence.
        libraries = []
        if self.module_paths is None:
            config = gitref.show(self._project.root, self._project.commit, "lakefile.toml")
            if config is None:
                return
            try:
                libraries = leanmod.lean_libraries(config)
            except leanmod.LeanModError:
                return
        for name, module in module_hints.items():
            if not leanmod.is_valid_module_name(module):
                continue
            paths = (self.local_dump_paths(module) if self.module_paths is not None else
                     {(Path(lib.src_dir) / leanmod.module_to_path(module)).as_posix()
                      for lib in libraries if lib.is_local(module)})
            matches = [decl for path, decl in self.ambiguous[name] if path in paths]
            if len(matches) == 1 and matches[0] is not None:
                decl = matches[0]
                self.decls[name] = decl
                self.omissions.remove(f"{decl.path}: {name}: ambiguous declaration")

    def local_dump_paths(self, module):
        if self.module_paths is not None:
            return {self.module_paths[module]} if module in self.module_paths else set()
        if not leanmod.is_valid_module_name(module):
            return set()
        relative = leanmod.module_to_path(module)
        if self._dump_libraries is None:
            return {relative} if relative in self._paths else set()
        return {path for path in (
            (Path(lib.src_dir) / relative).as_posix()
            for lib in self._dump_libraries if lib.is_local(module)
        ) if path in self._paths}

    def prepare_local_dump(self):
        if self.module_paths is not None:
            return
        config = gitref.show(self._project.root, self._project.commit, "lakefile.toml")
        if config is None:
            # Dynamic layouts are not evaluated. Exact root-relative pinned paths
            # can still establish that the row has a local source counterpart.
            self._dump_libraries = None
            return
        try:
            self._dump_libraries = leanmod.lean_libraries(config)
        except leanmod.LeanModError as exc:
            raise RecordError(f"cannot resolve pinned local module paths: {exc}") from exc

    def source_text(self, path: str) -> str:
        return self._project.read(path).decode()

    def lean_paths(self):
        return sorted(p for p in self._paths if p.endswith(".lean") and not p.startswith(".lake/"))

    def declaration(self, name: str):
        if name not in self.decls:
            paths = sorted({path for path, _ in self.ambiguous.get(name, ())})
            candidates = f"; candidate files: {', '.join(paths)}" if paths else ""
            raise RecordError(f"{name}: no unique supported source declaration at this pin{candidates}")
        return self.decls[name]


OLEAN_DIR = ".lake/build/lib/lean"


def library_sources(libraries, paths):
    """Yield pinned paths and their module names under the declared source directories, using roots and globs."""
    for lib in libraries:
        prefix = "" if lib.src_dir == "." else lib.src_dir + "/"
        for path in paths:
            if not path.endswith(".lean") or path.startswith(".lake/") or not path.startswith(prefix):
                continue
            name = path[len(prefix):-5].replace("/", ".")
            if leanmod.is_valid_module_name(name) and lib.is_local(name):
                yield name, path


def client_sources(source_dir, paths, names):
    """Resolve explicitly selected, standalone module targets under the package source directory."""
    for name in names:
        path = (Path(source_dir) / leanmod.module_to_path(name)).as_posix()
        if path not in paths or path.startswith(".lake/"):
            raise RecordError(f"{name}: expected a pinned module at {path}; name the library module or client")
        yield name, path


def target_modules(project, target):
    """{module: pinned source path} of the in-project import closure of a library or module target, and the imports
    that leave the project. Read from the pinned TOML layout and import headers, without evaluating Lake."""
    config = gitref.show(project.root, project.commit, "lakefile.toml")
    if config is None:
        raise RecordError("graph build estimates and dumps require a static lakefile.toml. "
                          "For lakefile.lean, use the project's Lake targets and extract the compiled graph "
                          "with Lean (see vl docs FOREIGN)")
    try:
        libs = leanmod.lean_libraries(config)
    except leanmod.LeanModError as exc:
        raise RecordError(f"cannot resolve pinned Lake library layout: {exc}") from exc
    files = gitref.ls_files(project.root, project.commit, "")
    modules = {}
    for name, path in library_sources(libs, files):
        if name in modules and modules[name] != path:
            raise RecordError(f"ambiguous module {name}")
        modules[name] = path
    library = next((lib for lib in libs if lib.name == target), None)
    pending = ([m for m, _ in library_sources([library], files)
                if any(leanmod.glob_matches(g, m) for g in library.globs)]
               if library else [target] if target in modules else [])
    if not pending:
        raise RecordError(f"no module or library target {target} in the pinned TOML library layout")
    reached, external = {}, set()
    while pending:
        name = pending.pop()
        if name in reached:
            continue
        reached[name] = modules[name]
        source = project.read(modules[name]).decode()
        try:
            imports = leanmod.parse_header(source, check_body=False).modules
        except leanmod.LeanModError as exc:
            raise RecordError(f"{modules[name]}: cannot read the pinned import header: {exc}") from exc
        for dep in imports:
            if dep in modules:
                pending.append(dep)
            elif any(lib.is_local(dep) for lib in libs):
                raise RecordError(f"{name} imports local module {dep}, whose source is missing from the pin")
            else:
                external.add(dep)
    return dict(sorted(reached.items())), external


def olean(root, module):
    """The compiled file of a local module in the conventional Lake output folder, or None when it is not there."""
    artifact = root / OLEAN_DIR / (module.replace(".", "/") + ".olean")
    return artifact if artifact.resolve().is_relative_to(root) and artifact.is_file() else None


def build_estimate(project, target):
    """Count the pinned module closure and observable local oleans, without evaluating Lake."""
    reached, external = target_modules(project, target)
    return {"target": target, "source_modules": len(reached),
            "missing_oleans": [name for name in reached if olean(project.root, name) is None],
            "external_imports": sorted(external), "cache_scope": "local default Lake output; validity and time unmeasured"}
