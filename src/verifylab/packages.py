"""The third-party package gate (ADR 0012, proposed).

A Lake package's code runs when the package cache is built (its lakefile is a program, as are its build steps) and
whenever a module that imports it is elaborated (macros, `initialize`). The cache is built by its owner outside vl's
jail, with network and the home folder reachable, so a package from an unknown author must not be built or checked on
a workstation. `[lean] trusted_packages` in `research/vl.toml` on the trusted ref names the packages whose code may
run anywhere; by default Mathlib and the packages it inherits from the Lean organisations (DEFAULT_TRUSTED, by name
and by the owner in the package's URL, so that a fork called `batteries` is not taken for Mathlib's). Every other
package in `lake-manifest.json` is third-party: on a machine whose `[sandbox] class` is `workstation` (the default)
`vl check`, `vl lane new` and `vl import` stop with `unsupported` before anything builds; on a `disposable` machine
(a purpose-built VM destroyed afterwards) they proceed, and the receipt records the class and the packages.
"""

from __future__ import annotations

import json
import posixpath
import re
from typing import Iterable

from . import machine

MANIFEST = "lake-manifest.json"
DEFAULT_TRUSTED = ("mathlib", "batteries", "aesop", "Qq", "plausible", "proofwidgets", "importGraph",
                   "LeanSearchClient", "Cli")
OFFICIAL_OWNERS = ("leanprover", "leanprover-community")
# The whole URL, owner/repository and nothing after: a path with `..`, `%2e` or a query could fetch another owner's code.
_OWNER = re.compile(r"(?:https://github\.com/|git@github\.com:)([A-Za-z0-9-]+)/[A-Za-z0-9_-][A-Za-z0-9._-]*",
                    re.IGNORECASE)


def trusted_package(package: object, trusted: tuple[str, ...] | None) -> bool:
    """Whether the manifest entry `package` may run here. A list the owner wrote trusts by name; the default also
    needs a git package from an official owner."""
    name = package.get("name") if isinstance(package, dict) else None
    if not isinstance(name, str):
        return False
    if trusted is not None:
        return name in trusted
    url = package.get("url")
    owner = _OWNER.fullmatch(url) if package.get("type") == "git" and isinstance(url, str) else None
    return name in DEFAULT_TRUSTED and owner is not None and owner.group(1).lower() in OFFICIAL_OWNERS


def third_party(manifest: bytes, trusted: tuple[str, ...] | None) -> list[str]:
    """The packages `lake-manifest.json` lists that `trusted` (`[lean] trusted_packages`, None: the default) does not
    cover, sorted. Raises ValueError when the manifest cannot be read."""
    try:
        data = json.loads(manifest)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError(f"{MANIFEST}: {exc}") from None
    packages = data.get("packages", []) if isinstance(data, dict) else None
    if not isinstance(packages, list):
        raise ValueError(f"{MANIFEST}: 'packages' must be a list")
    return sorted({str(p.get("name") if isinstance(p, dict) else p) for p in packages if not trusted_package(p, trusted)})


def gate(manifests: Iterable[bytes | None], trusted: tuple[str, ...] | None,
         loaded: machine.Machine) -> tuple[list[str], str | None]:
    """(the third-party packages of `manifests`, why they stop here or None). A workstation refuses them, and refuses a
    manifest it cannot read (it cannot say what would run); a disposable machine allows them."""
    found: set[str] = set()
    for manifest in manifests:
        if manifest is None:
            continue
        try:
            found.update(third_party(manifest, trusted))
        except ValueError as exc:
            if loaded.sandbox_class != "disposable":
                return [], f"{exc}; vl cannot tell which packages it lists"
    names = sorted(found)
    if names and loaded.sandbox_class != "disposable":
        return names, (f"{MANIFEST} lists {len(names)} package(s) outside [lean] trusted_packages: "
                       f"{', '.join(names)}. Their code (lakefiles, build steps, macros of the modules that import "
                       "them) runs when the package cache is built, outside the jail; they must be built and "
                       "checked in a disposable sandbox ([sandbox] class = \"disposable\" in machine.toml, a "
                       f"purpose-built VM destroyed afterwards); this machine is a {loaded.sandbox_class}")
    return names, None


def project_refusal(repo) -> str | None:
    """Why the Lean project of `repo` (its manifest on the trusted ref) is `unsupported` on this machine, or None:
    the gate of the commands that build nothing themselves (`vl lane new`, `vl import`)."""
    rel = posixpath.normpath(posixpath.join(repo.config.lean.project.strip() or ".", MANIFEST))
    return gate([repo.read_trusted(rel)], repo.config.lean.trusted_packages, machine.load())[1]
