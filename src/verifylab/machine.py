"""Machine-local configuration: this computer's verifier tools, Lean toolchains, build caches and memory budget.

A protected check takes the programs that judge a candidate from here, never from the caller's environment
(`COMPARATOR_*`, `ELAN_HOME`, `PATH`) and never from a file a branch can commit. Two places:

* `$XDG_CONFIG_HOME/verifylab/machine.toml` (default `~/.config/verifylab/machine.toml`; `templates/machine.toml`
  is a commented example)::

      [tools]
      comparator = "/opt/verifylab/comparator"   # absolute paths only
      lean4export = "/opt/verifylab/lean4export"
      landrun = "/opt/verifylab/landrun"
      nanoda = "/opt/verifylab/nanoda_bin"
      elan_home = "/opt/elan"                    # where Lean toolchains live (default: the account's ~/.elan)

      [caches]
      # The built Lake directory (its packages/ holds Mathlib) of a project, by the path of its main checkout.
      "/srv/research/project" = "/srv/research/project/.lake"

      [check]
      memory_total = "20G"   # cap of all vl runs together (the systemd slice vl.slice); default 70% of RAM

* `$XDG_DATA_HOME/verifylab/tools/<toolchain>/` (default `~/.local/share/verifylab/tools/`): copies made by
  `vl init --tools`, with their sha256 in `REVISIONS` (`sha256sum -c REVISIONS` re-checks them). A protected check
  runs a tool only when a `REVISIONS` file next to it pins its sha256, wherever the tool was named.

* The system programs `vl` starts (bubblewrap, systemd-run, systemctl, env, stdbuf, sh, git) come from
  `[launchers]` in machine.toml (absolute paths), else from the first of SYSTEM_BIN that has them; never from the
  caller's `PATH`, which could put a wrapper in front of the jail or of git.

The default locations come from the account database, not from `$HOME`. The XDG variables are honoured, so a
caller can choose another machine file and tools store for one command; when one points elsewhere than the
account's default, every receipt names the variable (`environment.machine_policy.overridden_by`) and the status of
a result verified by such a receipt says so.
"""

from __future__ import annotations

import hashlib
import os
import pwd
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

MACHINE_FILE = "machine.toml"
REVISIONS = "REVISIONS"
TOOL_NAMES = ("comparator", "landrun", "lean4export", "nanoda")
LAUNCHER_NAMES = ("bwrap", "systemd-run", "systemctl", "env", "stdbuf", "sh", "git")
SYSTEM_BIN = ("/usr/bin", "/bin", "/usr/sbin", "/sbin")
_XDG_DEFAULTS = {"XDG_CONFIG_HOME": ".config", "XDG_DATA_HOME": ".local/share"}
CHECK_KEYS = ("memory_total",)
MEMORY_TOTAL_SHARE = 0.7   # default cap of all vl runs together: this share of physical RAM
_REVISION_LINE = re.compile(r"^([0-9a-f]{64}) [ *](\S+)$")


class MachineError(ValueError):
    pass


def home() -> Path:
    """The account's home directory from the password database (`$HOME` is the caller's to set)."""
    try:
        return Path(pwd.getpwuid(os.getuid()).pw_dir)
    except KeyError:
        return Path.home()


def _xdg(variable: str, default: str) -> Path:
    value = os.environ.get(variable, "")
    return Path(value) if os.path.isabs(value) else home() / default


def config_path() -> Path:
    return _xdg("XDG_CONFIG_HOME", _XDG_DEFAULTS["XDG_CONFIG_HOME"]) / "verifylab" / MACHINE_FILE


def store_root() -> Path:
    return _xdg("XDG_DATA_HOME", _XDG_DEFAULTS["XDG_DATA_HOME"]) / "verifylab" / "tools"


def overrides() -> dict[str, str]:
    """The XDG variables of this call that move the machine file or the tools store away from the account's
    default locations: a machine policy the caller chose, which receipts and statuses name."""
    found = {}
    for variable, default in _XDG_DEFAULTS.items():
        if _xdg(variable, default) != home() / default:
            found[variable] = os.environ[variable]
    return found


def policy_record(loaded: "Machine | None" = None) -> dict:
    """What a receipt records of the machine policy a check ran under."""
    loaded = loaded if loaded is not None else load()
    return {"config": str(loaded.path), "sha256": loaded.sha256, "tools_store": str(store_root()),
            "overridden_by": overrides(), "launchers": {n: launcher(n, loaded) for n in LAUNCHER_NAMES}}


def launcher(name: str, loaded: "Machine | None" = None) -> str | None:
    """Absolute path of the system program `name` (LAUNCHER_NAMES): machine.toml [launchers], else the first
    executable in SYSTEM_BIN; never the caller's PATH. None when there is none."""
    loaded = loaded if loaded is not None else load()
    configured = loaded.launchers.get(name)
    if configured:
        return configured if os.access(configured, os.X_OK) else None
    for folder in SYSTEM_BIN:
        path = os.path.join(folder, name)
        if os.path.isfile(path) and os.access(path, os.X_OK):
            return path
    return None


def default_memory_total(meminfo: Path = Path("/proc/meminfo")) -> str | None:
    """MEMORY_TOTAL_SHARE of physical RAM (MemTotal), in systemd's K unit; None when it cannot be read."""
    try:
        for line in meminfo.read_text().splitlines():
            if line.startswith("MemTotal:"):
                return f"{int(int(line.split()[1]) * MEMORY_TOTAL_SHARE)}K"
    except (OSError, ValueError, IndexError):
        return None
    return None


def toolchain_key(spec: str) -> str:
    """`leanprover/lean4:v4.34.0-rc2` -> `leanprover--lean4---v4.34.0-rc2` (elan's directory name)."""
    name = spec.strip()
    if ":" not in name:
        name = "leanprover/lean4:" + name
    key = name.replace("/", "--").replace(":", "---")
    if not re.fullmatch(r"[A-Za-z0-9._+-]+", key):
        raise ValueError(f"unsupported toolchain name {spec.strip()!r}")
    return key


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def read_revisions(folder: Path) -> dict[str, str]:
    """name -> sha256 from `folder/REVISIONS` (sha256sum format; `#` lines are comments)."""
    try:
        text = (folder / REVISIONS).read_text(encoding="utf-8")
    except OSError:
        return {}
    pins = {}
    for line in text.splitlines():
        if (match := _REVISION_LINE.match(line.strip())) is not None:
            pins[match.group(2)] = match.group(1)
    return pins


def write_revisions(folder: Path, pins: dict[str, str]) -> None:
    lines = ["# sha256 of the verifier tools in this directory, pinned by `vl init --tools`; a protected check refuses",
             "# a tool that no longer matches. Re-check with: sha256sum -c REVISIONS",
             *(f"{sha}  {name}" for name, sha in sorted(pins.items()))]
    tmp = folder / f".{REVISIONS}.tmp"
    tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    tmp.replace(folder / REVISIONS)


@dataclass(frozen=True)
class Machine:
    path: Path
    sha256: str | None                     # None: there is no machine file
    tools: dict[str, str] = field(default_factory=dict)
    elan_home: str | None = None
    caches: dict[str, str] = field(default_factory=dict)
    memory_total: str | None = None        # [check] memory_total as written; None: not set
    launchers: dict[str, str] = field(default_factory=dict)

    def total_memory_cap(self) -> str | None:
        """The cap of all vl runs together (the systemd slice they share): `[check] memory_total`, else
        MEMORY_TOTAL_SHARE of physical RAM. A machine-wide value, so two projects never set different caps."""
        return self.memory_total or default_memory_total()

    def store(self, toolchain: str) -> Path:
        return store_root() / toolchain_key(toolchain)

    def protected_elan_home(self) -> Path:
        return Path(self.elan_home) if self.elan_home else home() / ".elan"

    def cache_for(self, main_checkout: Path) -> str | None:
        """The build cache configured for the project whose main checkout is `main_checkout`."""
        for key, value in self.caches.items():
            if Path(key).resolve() == main_checkout.resolve():
                return value
        return None

    def record(self) -> dict[str, str] | None:
        return {"path": str(self.path), "sha256": self.sha256} if self.sha256 else None


def _absolute(value, where: str) -> str:
    if not isinstance(value, str) or not value:
        raise MachineError(f"{where} must be a path")
    if not os.path.isabs(value):
        raise MachineError(f"{where} = '{value}' is relative; machine paths must be absolute")
    return value


_LOADED: dict[Path, Machine] = {}


def load(path: Path | None = None) -> Machine:
    """The machine file (an empty configuration when there is none), read once per process: every git call names its
    launcher from it, and one command runs under one machine policy. Raises MachineError when it is malformed (not
    remembered: the next call reads it again)."""
    path = path or config_path()
    if path not in _LOADED:
        _LOADED[path] = _read(path)
    return _LOADED[path]


def forget() -> None:
    """Read the machine file anew at the next `load`: for a process that rewrites it (tests)."""
    _LOADED.clear()


def _read(path: Path) -> Machine:
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        return Machine(path, None)
    except OSError as exc:
        raise MachineError(f"{path}: {exc}") from exc
    try:
        raw = tomllib.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise MachineError(f"{path}: {exc}") from exc
    tools_raw, caches_raw, check_raw = raw.get("tools", {}), raw.get("caches", {}), raw.get("check", {})
    launchers_raw = raw.get("launchers", {})
    if not all(isinstance(t, dict) for t in (tools_raw, caches_raw, check_raw, launchers_raw)):
        raise MachineError(f"{path}: [tools], [caches], [check] and [launchers] must be tables")
    unknown_launchers = sorted(set(launchers_raw) - set(LAUNCHER_NAMES))
    if unknown_launchers:
        raise MachineError(f"{path}: unknown [launchers] key(s) {unknown_launchers}; known: {list(LAUNCHER_NAMES)}")
    launchers = {name: _absolute(value, f"[launchers] {name}") for name, value in launchers_raw.items()}
    unknown_check = sorted(set(check_raw) - set(CHECK_KEYS))
    if unknown_check:
        raise MachineError(f"{path}: unknown [check] key(s) {unknown_check}; known: {list(CHECK_KEYS)}")
    total = check_raw.get("memory_total")
    if total is not None and (not isinstance(total, str) or not total.strip()):
        raise MachineError(f"{path}: [check] memory_total must be a size such as \"20G\"")
    unknown = sorted(set(tools_raw) - {*TOOL_NAMES, "elan_home"})
    if unknown:
        raise MachineError(f"{path}: unknown [tools] key(s) {unknown}; known: {[*TOOL_NAMES, 'elan_home']}")
    tools = {name: _absolute(value, f"[tools] {name}") for name, value in tools_raw.items() if name != "elan_home"}
    elan = _absolute(tools_raw["elan_home"], "[tools] elan_home") if "elan_home" in tools_raw else None
    caches = {_absolute(key, "a [caches] key"): _absolute(value, f"[caches] '{key}'") for key, value in caches_raw.items()}
    return Machine(path, hashlib.sha256(data).hexdigest(), tools, elan, caches, total, launchers)
