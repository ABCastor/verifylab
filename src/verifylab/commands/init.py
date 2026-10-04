"""`vl init`: create `research/` and `research/vl.toml` in the current git repository, and record the trusted ref
in the repository's own git config (`vl.trustedRef`), where no branch can change it.

Never overwrites: refuses when `research/vl.toml` exists, and leaves `AGENTS.md` alone unless
`--write-agents` is given (then appends the snippet once, between markers).

`vl init --tools --comparator PATH ...` sets up this machine instead: it copies the given verifier binaries into the
tools store of a Lean toolchain (`verifylab.machine`) and pins their sha256 in its `REVISIONS` file; protected
checks on that toolchain then run those copies and refuse them once they change.
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from string import Template

import verifylab

from .. import gitref, machine
from ..config import CONFIG_NAME, DEFAULT_RESEARCH_DIR, TRUSTED_REF_KEY, parse_config
from ..output import emit, fail
from ..repo import EVALUATORS, EVIDENCE, ITEMS, REVIEWS, TARGETS

FOLDERS = (ITEMS, TARGETS, EVIDENCE, REVIEWS, EVALUATORS)
BEGIN, END = "<!-- vl:begin -->", "<!-- vl:end -->"
CACHE_LINE = ".vl-cache/"
SKIP_DIRS = {".git", ".lake", "lake-packages", "node_modules", "build", "__pycache__", DEFAULT_RESEARCH_DIR}


def template(name: str) -> str:
    package = Path(verifylab.__file__).resolve().parent
    for folder in (package / "templates", package.parents[1] / "templates"):
        if (folder / name).is_file():
            return (folder / name).read_text(encoding="utf-8")
    raise FileNotFoundError(f"template '{name}' not found next to the package; the installation is incomplete")


def guess_roots(root: Path) -> list[str]:
    """Top-level folders that contain at least one .lean file."""
    roots = []
    for top in sorted(p for p in root.iterdir() if p.is_dir()):
        if top.name in SKIP_DIRS or top.name.startswith("."):
            continue
        for folder, dirs, names in os.walk(top):
            if any(n.endswith(".lean") for n in names):
                roots.append(top.name)
                break
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".")]
    return roots


def register(sub):
    p = sub.add_parser("init", help="create research/ and research/vl.toml in this git repository")
    p.add_argument("--trusted-ref", help="ref whose committed receipts, reviews and targets count, written to "
                                         f"git config {TRUSTED_REF_KEY} (default: its current value, else the "
                                         "current branch)")
    p.add_argument("--write-agents", action="store_true",
                   help="append the vl section to AGENTS.md between markers (only if not already there)")
    p.add_argument("--tools", action="store_true",
                   help="set up this machine instead: copy the verifier binaries given below into the tools store of "
                        "the toolchain and pin their sha256 in its REVISIONS file")
    for name in machine.TOOL_NAMES:
        p.add_argument(f"--{name}", metavar="PATH", help=f"with --tools: the {name} binary to copy")
    p.add_argument("--toolchain", help="with --tools: the Lean toolchain the binaries were built for "
                                       "(default: the lean-toolchain file of this repository)")
    return p


def install_tools(args) -> int:
    sources = {name: Path(getattr(args, name)).expanduser() for name in machine.TOOL_NAMES if getattr(args, name)}
    if not sources:
        return fail("--tools needs at least one of " + ", ".join(f"--{n} PATH" for n in machine.TOOL_NAMES))
    toolchain = args.toolchain
    if not toolchain:
        try:
            file = gitref.toplevel(Path.cwd()) / "lean-toolchain"
        except gitref.GitError:
            file = Path.cwd() / "lean-toolchain"
        if not file.is_file():
            return fail("no lean-toolchain here: pass --toolchain TOOLCHAIN")
        toolchain = file.read_text(encoding="utf-8").strip()
    try:
        store = machine.store_root() / machine.toolchain_key(toolchain)
    except ValueError as exc:
        return fail(str(exc))
    pins = machine.read_revisions(store)
    plan: dict[str, str] = {}
    for name, source in sorted(sources.items()):          # check everything before copying anything
        if not source.is_file() or not os.access(source, os.X_OK):
            return fail(f"--{name} {source}: not an executable file")
        sha = machine.file_sha256(source)
        dest = store / name
        if dest.exists() and (machine.file_sha256(dest) != sha or pins.get(name, sha) != sha):
            return fail(f"{dest} already holds a different {name}; vl init never overwrites a pinned tool: "
                        "remove it (and its REVISIONS line) to replace it")
        plan[name] = sha
    store.mkdir(parents=True, exist_ok=True)
    copied, kept = [], []
    for name, sha in plan.items():
        dest = store / name
        if dest.exists():
            kept.append(name)
        else:
            tmp = store / f".{name}.tmp"
            shutil.copyfile(sources[name], tmp)
            tmp.chmod(0o555)
            tmp.replace(dest)
            if machine.file_sha256(dest) != sha:
                dest.unlink()
                return fail(f"{sources[name]} changed while it was copied; run again")
            copied.append(name)
        pins[name] = sha
    machine.write_revisions(store, pins)
    try:
        overriding = sorted(set(plan) & set(machine.load().tools))
    except machine.MachineError:
        overriding = []
    lines = [f"tools store for {toolchain}: {store}",
             *(f"  {name}: sha256 {plan[name]} ({'copied from ' + str(sources[name]) if name in copied else 'already there'})"
               for name in plan),
             f"pinned in {store / machine.REVISIONS} (re-check with: sha256sum -c REVISIONS)"]
    if overriding:
        lines.append(f"note: {machine.config_path()} [tools] names {', '.join(overriding)}, which takes precedence over "
                     "the store; remove those entries to use the pinned copies")
    emit(args, "\n".join(lines), {"toolchain": toolchain, "store": str(store), "sha256": plan, "copied": copied,
                                  "kept": kept, "overridden_by_machine_toml": overriding})
    return 0


def run(args) -> int:
    if args.tools:
        return install_tools(args)
    given = [f"--{name}" for name in (*machine.TOOL_NAMES, "toolchain") if getattr(args, name)]
    if given:
        return fail(f"{', '.join(given)} only apply with --tools")
    root = gitref.toplevel(Path.cwd())
    research = root / DEFAULT_RESEARCH_DIR
    if (research / CONFIG_NAME).exists():
        return fail(f"{DEFAULT_RESEARCH_DIR}/{CONFIG_NAME} already exists; vl init never overwrites")
    previous = gitref.local_config(root, TRUSTED_REF_KEY)
    trusted = args.trusted_ref or previous or gitref.current_branch(root)
    if not trusted:
        return fail("HEAD is detached; pass --trusted-ref REF")
    if trusted.startswith("-"):
        return fail(f"trusted ref '{trusted}' starts with '-'; git would read it as an option")
    try:
        snippet = template("AGENTS-snippet.md")
        config_template = template("vl.toml")
    except FileNotFoundError as exc:
        return fail(str(exc))
    roots = guess_roots(root)
    config_text = Template(config_template).substitute(
        name=json.dumps(root.name), roots=json.dumps(roots))
    parse_config(root, config_text)  # never write a configuration vl itself cannot read

    created = []
    for folder in FOLDERS:
        path = research / folder
        path.mkdir(parents=True, exist_ok=True)
        if not any(path.iterdir()):
            (path / ".gitkeep").touch()
            created.append(f"{DEFAULT_RESEARCH_DIR}/{folder}/.gitkeep")
    with (research / CONFIG_NAME).open("x", encoding="utf-8") as handle:
        handle.write(config_text)
    created.insert(0, f"{DEFAULT_RESEARCH_DIR}/{CONFIG_NAME}")
    gitref.set_local_config(root, TRUSTED_REF_KEY, trusted)
    ref_set = (f"git config {TRUSTED_REF_KEY} = {trusted}"
               + (f" (was '{previous}')" if previous and previous != trusted else ""))

    gitignore = root / ".gitignore"
    lines = gitignore.read_text(encoding="utf-8").splitlines() if gitignore.exists() else []
    if any(line.strip() in (CACHE_LINE, CACHE_LINE.rstrip("/"), "/" + CACHE_LINE) for line in lines):
        ignore_note = f".gitignore already ignores {CACHE_LINE}"
    else:
        existing = gitignore.read_text(encoding="utf-8") if gitignore.exists() else ""
        prefix = "" if not existing or existing.endswith("\n") else "\n"
        gitignore.write_text(existing + prefix + CACHE_LINE + "\n", encoding="utf-8")
        ignore_note = f".gitignore: added {CACHE_LINE}"

    agents = root / "AGENTS.md"
    if not args.write_agents:
        agents_note = "AGENTS.md: not modified (pass --write-agents to append the section below)"
    elif agents.exists() and BEGIN in agents.read_text(encoding="utf-8"):
        agents_note = "AGENTS.md: already has a vl section; left unchanged"
    else:
        existing = agents.read_text(encoding="utf-8") if agents.exists() else ""
        sep = "" if not existing else ("\n" if existing.endswith("\n") else "\n\n")
        agents.write_text(existing + sep + snippet, encoding="utf-8")
        agents_note = "AGENTS.md: appended the vl section between markers"

    ref_note = None
    if not gitref.ref_exists(root, trusted):
        ref_note = (f"WARNING: trusted ref '{trusted}' does not resolve to a commit yet; nothing counts as admitted "
                    "and vl validate reports it until it does")
    text = "\n".join([
        f"initialised {DEFAULT_RESEARCH_DIR}/ in {root}",
        f"trusted ref: {trusted} ({ref_set}) · lean roots: {', '.join(roots) or 'none found (edit [lean] roots)'}",
        *([ref_note] if ref_note else []),
        "created: " + ", ".join(created),
        ignore_note,
        agents_note,
        "",
        snippet.rstrip("\n"),
    ])
    emit(args, text, {"root": str(root), "trusted_ref": trusted, "trusted_ref_config": ref_set,
                      "lean_roots": roots, "created": created,
                      "gitignore": ignore_note, "agents": agents_note, "agents_snippet": snippet,
                      "warnings": [ref_note] if ref_note else []})
    return 0
