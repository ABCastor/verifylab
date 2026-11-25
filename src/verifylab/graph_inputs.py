"""`vl graph LOCK --dump DIR --lean LEAN`: the kernel dependency rows and the compiler declaration ranges of a built
checkout, the two inputs of the removal view, with their digests and the Lean that read them.

The two extractor scripts ship beside this module. They read compiled module files and import nothing from the
checkout. The Lean executable is the one the caller names, never one found on PATH, and it runs outside the
checkout. A compiled file that is missing, written by another Lean, unreadable, or at odds with the pinned source
stops the dump before anything is written.
"""
from __future__ import annotations

import gzip
import hashlib
import io
import os
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from . import gitref, leanmod
from .foreign import olean, read_json, target_modules
from .output import listed
from .reading_order import check_dep_row
from .records import RecordError, sha256_hex, write_new_json
from .removal import check_range_bounds, parse_range_row, parse_type_row

SCHEMA = "vl.graph-inputs/1"
SCRIPTS = Path(__file__).with_name("lean")
OUTPUTS = {"kernel": ("dumpx.lean", "kernel.tsv.gz", "DEP"), "ranges": ("Ranges.lean", "ranges.gz", "R")}
RECORD = "graph-inputs.json"
CONFIG = ("lean-toolchain", "lake-manifest.json", "lakefile.toml", "lakefile.lean")
CHECKED = ("every module of the target closure has a compiled file written by the named Lean; its source and the "
           "Lake configuration in the working tree equal the pin; every declaration the reader indexes there has a "
           "compiled constant whose range holds its line and names it; DEP counts agree with module headers, and "
           "the type rows cover those same constants; every indexed theorem of the source is a theorem "
           "row, every axiom row is an axiom of the source, and a source with theorems has theorem rows. An edit "
           "that keeps every declaration on its line is not detected")
_AXIOM = re.compile(r"(?m)^[ \t]*(?:@\[[^\]]*\]\s*)*(?:(?:private|protected|public|noncomputable|unsafe|nonrec|meta"
                    r"|scoped|local)\s+)*axiom\s+(?:_root_\.)?([^\s:(\[{]+)")
_THEOREM = re.compile(r"(?m)^[ \t]*(?:@\[[^\]]*\]\s*)*(?:(?:private|protected|public|nonrec|scoped|local)\s+)*"
                      r"(?:theorem|lemma)\s")
_VERSION = re.compile(r"version (?P<version>[^\s,]+),.*\bcommit (?P<commit>[0-9a-f]{7,40})\b")


def _environment():
    # LEAN_PATH would add search roots: the extractors import the toolchain's own `Lean` and nothing else.
    return {key: value for key, value in os.environ.items() if key != "LEAN_PATH"}


def lean_identity(lean, cwd):
    """(version, commit, the reported line) of the named executable."""
    try:
        proc = subprocess.run([lean, "--version"], cwd=cwd, env=_environment(), capture_output=True, text=True,
                              timeout=120, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RecordError(f"{lean} --version: {exc}") from exc
    reported = proc.stdout.strip().splitlines()[:1]
    found = _VERSION.search(reported[0]) if reported and proc.returncode == 0 else None
    if found is None:
        raise RecordError(f"{lean} --version did not report a Lean version and commit")
    return found["version"], found["commit"], reported[0]


def olean_identity(path):
    """(Lean version, commit) from the header of a compiled module file."""
    with path.open("rb") as stream:
        header = stream.read(82)
    if len(header) < 82 or header[:5] != b"olean":
        raise RecordError(f"{path} is not a compiled Lean module file")
    return tuple(part.split(b"\0", 1)[0].decode("ascii", "replace") for part in (header[7:40], header[40:82]))


def _extract(lean, script, listing, cwd, out, see):
    """Run one extractor, gzip its rows as they come and show each, split on tabs, to `see`. Returns the problem
    of a run that did not exit 0, for the caller to report after the rows that say more."""
    try:
        with (tempfile.TemporaryFile() as errors,
              subprocess.Popen([lean, "--run", script, listing], cwd=cwd, env=_environment(),
                               stdout=subprocess.PIPE, stderr=errors) as child,
              gzip.GzipFile(filename="", fileobj=out, mode="wb", mtime=0) as packed):
            for number, raw in enumerate(child.stdout, 1):
                packed.write(raw)
                location = f"{script.name}:{number}"
                try:
                    row = raw.decode("utf-8").rstrip("\r\n").split("\t")
                except UnicodeDecodeError as exc:
                    raise RecordError(f"{location}: extractor output is not UTF-8") from exc
                see(row, location)
            code = child.wait()
            if code:
                errors.seek(max(0, errors.tell() - 2000))
                message = errors.read().decode(errors="replace").strip()
                return f"{lean} --run {script.name} exited {code}: {message or 'no message'}"
    except OSError as exc:
        raise RecordError(f"{lean} --run {script.name}: {exc}") from exc
    return None


def _unproved(modules, pinned, constants):
    """Rows that hold no proof: a theorem dumped as an axiom, or no theorem row at all for a source with theorems.
    That is what the public part of a module-system file gives, where Lean keeps the type of a theorem only."""
    problems, theorems = [], False
    for module, path in modules.items():
        try:
            masked = leanmod.mask_comments_and_strings(pinned[path].decode())
        except (leanmod.LeanModError, UnicodeError):
            continue
        theorems = theorems or _THEOREM.search(masked) is not None
        declared = set(_AXIOM.findall(masked))
        for name, kind in constants.get(module, {}).items():
            if kind == "axiom" and not any(name == own or name.endswith("." + own) for own in declared):
                problems.append(f"{name} ({module}) is recorded as an axiom and {path} declares no such axiom")
    rows = sum(kind == "thm" for kinds in constants.values() for kind in kinds.values())
    if theorems and not rows and not problems:
        problems.append("the pinned source has theorems and the rows hold none")
    return problems


def _stale(modules, pinned, constants, ranges):
    """Declarations of the pinned source that the compiled files do not hold where the source has them, or hold as
    something else than the theorem the source states."""
    problems = []
    for module, path in modules.items():
        try:
            source = pinned[path].decode()
            masked = leanmod.mask_comments_and_strings(source)
            found = leanmod.theorem_spans(masked, leanmod.parse_header(source).end, reading=True)
        except (leanmod.LeanModError, UnicodeError):
            continue                    # the reader omits this file too: nothing of it enters a view
        lines = masked.split("\n")
        local = constants.get(module, {})
        private = {re.sub(r"^_private\." + re.escape(module) + r"\.\d+\.", "", name): name
                   for name in local if name.startswith("_private.")}
        for name, declarations in found.items():
            for decl in declarations:
                compiled = name if name in local else private.get(name)
                where = f"{name} ({path}:{decl.line})"
                span = ranges.get((module, compiled))
                if compiled is None:
                    problems.append(f"{where} has no compiled constant in {module}")
                elif span is None:
                    problems.append(f"{where} has no compiled declaration range")
                elif not (span[0] <= decl.line <= span[1] and 1 <= span[2] <= len(lines)
                          and name.rsplit(".", 1)[-1] in lines[span[2] - 1]):
                    problems.append(f"{where} was compiled at lines {span[0]}-{span[1]}")
                elif decl.kind in ("theorem", "lemma") and local[compiled] != "thm":
                    problems.append(f"{where} is a {decl.kind} in the source and a row of kind {local[compiled]}")
    return problems


@dataclass(frozen=True)
class ArtifactSnapshot:
    path: Path
    data: bytes

    @property
    def suffix(self) -> str:
        return self.path.suffix

    def __str__(self) -> str:
        return str(self.path)

    def open(self, mode: str) -> io.BytesIO:
        assert mode == "rb"
        return io.BytesIO(self.data)


def read_manifest(path, project):
    """Read both artifacts once and verify the exact bytes the readers will use."""
    record = read_json(path)
    if not isinstance(record, dict) or record.get("schema") != SCHEMA:
        raise RecordError(f"{path}: expected a {SCHEMA} manifest")
    if record.get("commit") != project.commit or record.get("toolchain") != project.read("lean-toolchain").decode().strip():
        raise RecordError(f"{path}: graph dump and pin must have the same commit and toolchain")
    files = record.get("files")
    result = {}
    for kind, (_, filename, _) in OUTPUTS.items():
        entry = files.get(kind) if isinstance(files, dict) else None
        if not isinstance(entry, dict) or entry.get("path") != filename:
            raise RecordError(f"{path}: expected {filename} in the manifest")
        artifact = path.parent / filename
        data = artifact.read_bytes()
        if len(data) != entry.get("bytes") or sha256_hex(data) != entry.get("sha256"):
            raise RecordError(f"{artifact}: bytes differ from the graph manifest; regenerate the dump")
        result[kind] = ArtifactSnapshot(artifact, data)
    modules = None
    if "module_paths" in record or "input_sha256" in record:
        targets = record.get("targets")
        if not isinstance(targets, list) or not targets or not all(
                isinstance(target, str) and leanmod.is_valid_module_name(target) for target in targets):
            raise RecordError(f"{path}: expected pinned graph targets")
        modules = target_closure(project, targets)
        if record.get("module_paths") != modules:
            raise RecordError(f"{path}: module paths differ from the pinned target closure")
        if record.get("input_sha256") != {name: sha256_hex(data)
                                          for name, data in pinned_inputs(project, modules).items()}:
            raise RecordError(f"{path}: source and Lake input digests differ from the pin")
    return result, modules


def target_closure(project, targets):
    modules = {}
    for target in targets:
        modules.update(target_modules(project, target)[0])
    return dict(sorted(modules.items()))


def pinned_inputs(project, modules):
    tracked = set(gitref.ls_files(project.root, project.commit, ""))
    return {path: project.read(path) for path in (*sorted(tracked.intersection(CONFIG)), *modules.values())}


def compiled_digests(root, compiled):
    digests = {}
    for base in compiled.values():
        for name in (base, base.with_suffix(".olean.server"), base.with_suffix(".olean.private")):
            if name != base and not (name.exists() or name.is_symlink()):
                continue
            if not name.resolve().is_relative_to(root) or not name.is_file():
                raise RecordError(f"{name}: expected a compiled file inside the build tree")
            with name.open("rb") as stream:
                digests[name.relative_to(root).as_posix()] = hashlib.file_digest(stream, "sha256").hexdigest()
    return digests


def dump(project, toolchain, targets, lean, out_dir, *, built_tree=None):
    """Write kernel.tsv.gz, ranges.gz and graph-inputs.json under `out_dir`; returns the record."""
    lean = Path(os.path.abspath(lean))          # as named: a link keeps the name its target answers to
    if not lean.is_file() or not os.access(lean, os.X_OK):
        raise RecordError(f"--lean must be an executable file: {lean}")
    targets = list(dict.fromkeys(targets))
    if not targets:
        raise RecordError("the lock names no default target: name one with --build")
    existing = [name for name in (*(output[1] for output in OUTPUTS.values()), RECORD) if (out_dir / name).exists()]
    if existing:
        raise RecordError(f"{out_dir} already holds {', '.join(existing)}: choose a new folder")
    if out_dir.is_symlink() or (out_dir.exists() and (not out_dir.is_dir() or any(out_dir.iterdir()))):
        raise RecordError(f"{out_dir}: choose a new or empty folder")
    root = project.root if built_tree is None else built_tree.resolve()
    if not root.is_dir():
        raise RecordError(f"{root}: expected a prepared Lake build directory")
    modules = target_closure(project, targets)
    pinned = pinned_inputs(project, modules)
    if "lakefile.lean" not in pinned and (root / "lakefile.lean").exists():
        raise RecordError(f"{root / 'lakefile.lean'} is present in the build tree and absent from the pin")
    compiled = {module: olean(root, module) for module in modules}
    missing = [module for module, artifact in compiled.items() if artifact is None]
    if missing:
        raise RecordError(f"{len(missing)} of {len(modules)} compiled files are missing: {listed(missing, 5)}. "
                          f"Build {' '.join(targets)} first")
    for path, data in pinned.items():
        try:
            same = (root / path).read_bytes() == data
        except OSError:
            same = False
        if not same:
            context = "working tree" if built_tree is None else f"prepared build {root}"
            raise RecordError(f"{path} in the {context} differs from the lock: "
                              "the compiled files are not those of the pin")
    out_dir.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="vl-graph-inputs-") as scratch, \
            tempfile.TemporaryDirectory(prefix=".vl-dump-", dir=out_dir.parent) as staging:
        version, commit, reported = lean_identity(lean, scratch)
        object_sha256 = compiled_digests(root, compiled)
        for artifact in compiled.values():
            written, digest = olean_identity(artifact)
            shorter, longer = sorted((digest, commit), key=len)
            if written != version or not shorter or not longer.startswith(shorter):
                raise RecordError(f"{artifact} was written by Lean {written} ({digest[:12]}), not by {lean}: "
                                  f"{reported}. Name the Lean of the checkout's toolchain, or rebuild")
        listing = Path(scratch) / "modules.tsv"
        listing.write_text("".join(f"{artifact}\t{module}\n" for module, artifact in compiled.items()),
                           encoding="utf-8")
        constants, ranges, closed, unread = {}, {}, set(), []
        names = set()
        active = None
        expected = 0
        line_counts = {module: len(pinned[path].decode("utf-8", "replace").splitlines())
                       for module, path in modules.items()}
        counts = dict.fromkeys(OUTPUTS, 0)
        types = set()

        def kernel_row(row, location):
            nonlocal active, expected
            if row[0] in ("FAIL", "BADLINE"):
                unread.append(" ".join(row[1:]))
            elif row[0] == "MOD":
                if (len(row) != 4 or active is not None or row[1] not in modules or row[1] in closed
                        or re.fullmatch(r"[0-9]{1,20}", row[2]) is None):
                    raise RecordError(f"{location}: invalid MOD header")
                active, expected = row[1], int(row[2])
                constants[active] = {}
            elif row[0] == "DEP":
                check_dep_row(row, location)
                _, kind, module, name, *_ = row
                if active != module or not name or name in names:
                    raise RecordError(f"{location}: misplaced or duplicate DEP row for {name} ({module})")
                constants[module][name] = kind
                names.add(name)
                counts["kernel"] += 1
            elif row[0] == "MODEND":
                if len(row) != 2 or active != row[1]:
                    raise RecordError(f"{location}: MODEND must close the open module")
                observed = len(constants[active])
                if observed != expected:
                    raise RecordError(f"{location}: {active} declares {expected} DEP rows but has {observed}")
                closed.add(active)
                active = None
            else:
                raise RecordError(f"{location}: unexpected kernel row {row[0]!r}")

        def range_row(row, location):
            if row[0] == "T":
                module, name, _ = parse_type_row(row, location)
                if name not in constants.get(module, {}) or name in types:
                    raise RecordError(f"{location}: unknown or duplicate T row for {name} ({module})")
                types.add(name)
                return
            if row[0] != "R":
                raise RecordError(f"{location}: unexpected range row {row[0]!r}")
            module, name, first, last, selection = parse_range_row(row, location)
            if module not in modules:
                raise RecordError(f"{location}: range for unexpected module {module}")
            check_range_bounds(modules[module], name, first, last, line_counts[module])
            span = (first, last, selection)
            if ranges.setdefault((module, name), span) != span:
                raise RecordError(f"{location}: conflicting ranges for {name} ({module})")
            counts["ranges"] += 1

        script, name, _ = OUTPUTS["kernel"]
        with (Path(staging) / name).open("wb") as out:
            failure = _extract(lean, SCRIPTS / script, listing, scratch, out, kernel_row)
        if unread:
            raise RecordError(f"Lean could not read {len(unread)} compiled files: {listed(unread, 3)}")
        if failure:
            raise RecordError(failure)
        absent = [module for module in modules if module not in closed]
        if absent:
            raise RecordError(f"the kernel rows of {len(absent)} modules are missing or cut short: {listed(absent, 5)}")
        unproved = _unproved(modules, pinned, constants)
        if unproved:
            raise RecordError(f"the kernel rows hold no proof ({len(unproved)}): {listed(unproved, 3)}. "
                              "A dependency graph made of them would stop at the statements")
        script, name, _ = OUTPUTS["ranges"]
        with (Path(staging) / name).open("wb") as out:
            failure = _extract(lean, SCRIPTS / script, listing, scratch, out, range_row)
        if failure:
            raise RecordError(failure)
        if missing_types := names - types:
            raise RecordError(f"missing T rows for {len(missing_types)} constants: {listed(sorted(missing_types), 5)}")
        stale = _stale(modules, pinned, constants, ranges)
        if stale:
            raise RecordError(f"the compiled files do not match the pinned source in {len(stale)} declarations: "
                              f"{listed(stale, 3)}. Rebuild {' '.join(targets)} at the pin")
        files = {}
        for kind, (script, name, row) in OUTPUTS.items():
            data = (Path(staging) / name).read_bytes()
            files[kind] = {"path": name, "sha256": sha256_hex(data), "bytes": len(data), "rows": counts[kind],
                           "row_kind": row, "extractor": script,
                           "extractor_sha256": sha256_hex((SCRIPTS / script).read_bytes())}
        record = {"schema": SCHEMA, "commit": project.commit, "toolchain": toolchain,
                  "lean": {"executable": str(lean), "version": version, "commit": commit, "reported": reported},
                  "targets": targets, "modules": len(modules), "module_paths": modules,
                  "input_sha256": {path: sha256_hex(data) for path, data in pinned.items()},
                  "olean_sha256": object_sha256,
                  "files": files, "checked": CHECKED}
        try:
            write_new_json(Path(staging) / RECORD, record)
            Path(staging).rename(out_dir)
        except OSError as exc:
            raise RecordError(f"cannot write the graph inputs: {exc}") from exc
    return record
