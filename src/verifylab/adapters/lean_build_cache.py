"""The trusted build store: Lake's build products of the challenge side of protected checks, reused across checks.

A protected check compiles the challenge (the trusted target and the in-project modules it imports, read from the
trusted ref) before any candidate code is compiled (`prebuild_challenge`). Those products depend on trusted inputs
only, so `vl` keeps a copy of them here and copies them back into the next check's fresh project before its challenge
build: Lake finds them up to date and replays them instead of compiling the modules again.

A module's entry is named by a key, the digest of everything that determines its build: the environment (toolchain
and Lean version, the generated lakefile and manifest, each Lake package's revision and the trace of each dependency
module the trusted side imports) and the module's name, the sha256 of its source and the keys of the in-project
modules it imports. A changed definition therefore changes the key of every module above it. Lake still compares
each restored module's trace with its inputs, and rebuilds what does not match; it does not read a replayed
module's olean, so an entry whose files no longer match the sha256 stored with them is never restored.

Only `vl` writes the store, from its own process, right after a challenge build that compiled trusted code only; no
jail mounts it, so candidate code cannot write it. A process of the same user outside the jail can, as it can write
the Lake packages: the documented limit (docs/adr/0005-trusted-build-store.md).

The same machinery keeps, in a store of its own, the solution side of protected checks (ADR 0008): what
Comparator's build of the solution compiled, untrusted, kept as one entry under the digest of every input of that
build. A check whose inputs are exactly those mounts the entry read-only under its build folder; the kernels still
replay everything the solution declares.
"""

from __future__ import annotations

import graphlib
import json
import os
import secrets
import shutil
import time
from pathlib import Path
from typing import Any

from .. import leanmod
from ..machine import file_sha256
from ..records import sha256_hex

FORMAT = 1                        # part of every key: a new layout or basis never reads an old entry
BUILD_DIRS = ("lib/lean", "ir")   # under a project's .lake/build: olean, ilean, trace and hashes; C code, setup
SUMS = "SHA256SUMS"               # in each entry, `sha256sum -c` format: its files and what they held when stored
TREE = "build"                    # in a solution entry: the files, laid out as under .lake/build
STALE_TMP = 3600                  # seconds after which an entry being written aside (`.tmp-*`) was left by a crash


def _sweep(store: Path) -> None:
    """Remove what a writer killed mid-save left in `store`: `.tmp-*` folders older than STALE_TMP, far longer than
    any save takes, so a writer at work keeps its own."""
    for tmp in store.glob(".tmp-*"):
        try:
            if time.time() - tmp.lstat().st_mtime > STALE_TMP:
                shutil.rmtree(tmp, ignore_errors=True)
        except OSError:
            pass


def _digest(value: Any) -> str:
    return sha256_hex(json.dumps(value, sort_keys=True, separators=(",", ":")).encode())


def module_keys(environment: dict[str, Any], sources: dict[str, bytes]) -> dict[str, str]:
    """module -> key of its build products, for every module of `sources` (each module the challenge build compiles,
    by name, with its source). Raises ValueError on an import cycle or a source that is not UTF-8 Lean."""
    env = _digest({"format": FORMAT, **environment})
    graph = {module: [m for m in leanmod.imports_of(leanmod.decode(data, module)) if m in sources]
             for module, data in sources.items()}
    keys: dict[str, str] = {}
    for module in graphlib.TopologicalSorter(graph).static_order():
        keys[module] = _digest({"environment": env, "module": module, "source": sha256_hex(sources[module]),
                                "imports": sorted(keys[m] for m in graph[module])})
    return keys


def _stem(module: str) -> Path:
    return Path(leanmod.module_to_path(module)).with_suffix("")


def _products(build: Path, module: str, listed: dict[Path, dict[str, list[Path]]]) -> list[tuple[str, Path]]:
    """(folder under `build`, file) of each regular file Lake wrote for `module` under `build` (.lake/build).
    `listed` keeps each folder's files by the stem of their name, so that a folder of many modules is listed once."""
    stem = _stem(module)
    found = []
    for sub in BUILD_DIRS:
        folder = build / sub / stem.parent
        if folder not in listed:
            listed[folder] = {}
            for path in sorted(folder.iterdir()) if folder.is_dir() else ():
                if "." in path.name and path.is_file() and not path.is_symlink():
                    listed[folder].setdefault(path.name.split(".", 1)[0], []).append(path)
        found += [(sub, path) for path in listed[folder].get(stem.name, ())]
    return found


def _entry_files(entry: Path, stem: str) -> list[str] | None:
    """The files of a stored entry ("lib/lean/X.olean", ...) as its SHA256SUMS lists them, or None when there is no
    entry or it is damaged: a file missing, or not holding what was stored. Lake replays a restored module from its
    trace without reading its olean, so a damaged one would reach every later check of that key."""
    try:
        lines = (entry / SUMS).read_text().splitlines()
    except (OSError, UnicodeDecodeError):
        return None
    files = []
    for line in lines:
        digest, _, rel = line.partition("  ")
        sub, _, name = rel.rpartition("/")
        path = entry / rel
        if (sub not in BUILD_DIRS or not name.startswith(stem + ".") or path.is_symlink() or not path.is_file()
                or sha256_hex(path.read_bytes()) != digest):
            return None
        files.append(rel)
    return files or None


def restore(store: Path, build: Path, keys: dict[str, str]) -> dict[str, str]:
    """Copy each stored, undamaged module of `keys` into `build`, never linking: a later write into the project must
    not reach the store. Returns the modules restored, with their keys."""
    restored = {}
    for module, key in sorted(keys.items()):
        stem = _stem(module)
        files = _entry_files(store / key, stem.name)
        if files is None:
            continue
        for rel in files:
            sub, name = rel.rsplit("/", 1)
            (build / sub / stem.parent).mkdir(parents=True, exist_ok=True)
            shutil.copy2(store / key / rel, build / sub / stem.parent / name)
        restored[module] = key
    return restored


def save(store: Path, build: Path, keys: dict[str, str]) -> list[str]:
    """Store the products of each module of `keys` that the store lacks and `build` holds (at least its olean and
    trace), with their sha256 (SUMS). An entry appears whole or not at all (written aside, then renamed); a
    concurrent writer of the same entry loses the rename and drops its copy. Returns the modules stored."""
    store.mkdir(parents=True, exist_ok=True)
    _sweep(store)
    saved: list[str] = []
    listed: dict[Path, dict[str, list[Path]]] = {}
    for module, key in sorted(keys.items()):
        entry = store / key
        if entry.exists():
            continue
        products = _products(build, module, listed)
        stem = _stem(module).name
        if not {f"{stem}.olean", f"{stem}.trace"} <= {path.name for _, path in products}:
            continue
        tmp = store / f".tmp-{secrets.token_hex(8)}"
        try:
            for sub, path in products:
                (tmp / sub).mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, tmp / sub / path.name)
            (tmp / SUMS).write_text("".join(f"{sha256_hex((tmp / sub / path.name).read_bytes())}  {sub}/{path.name}\n"
                                            for sub, path in products))
            tmp.rename(entry)
            saved.append(module)
        except OSError:
            shutil.rmtree(tmp, ignore_errors=True)
    return saved


# The solution side (ADR 0008) -----------------------------------------------------------------------------

def tree_key(environment: dict[str, Any], inputs: dict[str, str], modules: list[str]) -> str:
    """The key of the solution side of a check: the environment, every input of that build (path to sha256: the
    candidate's files and the generated solution module) and the modules it compiles. All of them: an entry serves
    only a check whose inputs are exactly these, so no code outside them ever wrote what it holds."""
    return _digest({"format": FORMAT, "side": "solution", "environment": environment, "inputs": inputs,
                    "modules": sorted(modules)})


def save_tree(store: Path, build: Path, key: str, modules: list[str]) -> bool:
    """Store the products of `modules` in `build` as the one entry `key`, with their sha256 (SUMS), when the store
    lacks it and every module has at least its olean and trace (a build that stopped part-way is not stored). Written
    aside, then renamed, as `save`. The candidate's build-time code wrote `build`: a product reached through a
    link (a folder of `build` turned into a link to a folder of this machine) is not stored, so that `vl`, which
    runs outside the jail, never copies a file the jail could not see into an entry a later jail mounts. Returns
    whether it stored the entry."""
    entry = store / key
    if entry.exists():
        return False
    listed: dict[Path, dict[str, list[Path]]] = {}
    products = {module: _products(build, module, listed) for module in modules}
    if not all({f"{_stem(m).name}.olean", f"{_stem(m).name}.trace"} <= {p.name for _, p in found}
               for m, found in products.items()):
        return False
    real = build.resolve()
    if any(path.resolve() != real / path.relative_to(build) for found in products.values() for _, path in found):
        return False
    store.mkdir(parents=True, exist_ok=True)
    _sweep(store)
    tmp = store / f".tmp-{secrets.token_hex(8)}"
    try:
        sums = []
        for found in products.values():
            for _, path in found:
                rel = path.relative_to(build).as_posix()
                (tmp / TREE / rel).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, tmp / TREE / rel, follow_symlinks=False)
                sums.append(f"{file_sha256(tmp / TREE / rel)}  {rel}\n")
        (tmp / SUMS).write_text("".join(sorted(sums)))
        tmp.rename(entry)
        return True
    except OSError:
        shutil.rmtree(tmp, ignore_errors=True)
        return False


def tree(store: Path, key: str, modules: list[str]) -> Path | None:
    """The folder of the entry `key` to mount under a check's .lake/build, or None when there is none or it is not
    exactly what was stored: a file missing, changed or not listed in SUMS, a link or anything but a regular file,
    or a file that is not a product of one of `modules`. That last rule keeps an entry from ever holding a module of
    the challenge side, which the check's own challenge build must provide."""
    entry = store / key
    root = entry / TREE
    try:
        lines = (entry / SUMS).read_text().splitlines()
    except (OSError, UnicodeDecodeError):
        return None
    listed = {rel: digest for digest, _, rel in (line.partition("  ") for line in lines)}
    if entry.is_symlink() or root.is_symlink() or not root.is_dir() or "" in listed or len(listed) != len(lines):
        return None
    present = set()
    for folder, dirs, files in os.walk(root):
        for name in dirs + files:
            path = Path(folder) / name
            if path.is_symlink() or not (path.is_dir() or path.is_file()):
                return None
        present |= {(Path(folder) / name).relative_to(root).as_posix() for name in files}
    if not present or present != set(listed):
        return None
    stems: dict[str, set[str]] = {}
    for module in modules:
        stems.setdefault(_stem(module).parent.as_posix(), set()).add(_stem(module).name)
    for rel, digest in listed.items():
        sub = next((s for s in BUILD_DIRS if rel.startswith(s + "/")), None)
        folder, _, name = rel[len(sub or "") + 1:].rpartition("/")
        if sub is None or not any(name.startswith(stem + ".") for stem in stems.get(folder or ".", ())):
            return None
        if file_sha256(root / rel) != digest:
            return None
    return root
