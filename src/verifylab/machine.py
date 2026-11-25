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
      build_cache = false    # do not reuse the trusted build store (default: reuse it)
      reuse_solution_builds = true   # reuse the untrusted solution build of the same inputs (default: off)

      [sandbox]
      class = "workstation"  # or "disposable": a purpose-built VM that is destroyed afterwards (ADR 0012, proposed);
                             # third-party Lake packages (packages.py) are built and checked only on a disposable one

      [machine]              # who this computer is, for receipts moved between machines (ADR 0015, proposed)
      name = "worker-a"      # recorded in every receipt this machine writes; `vl receipts export` needs it
      trust = "own"          # "own" (default) or "foreign": how this machine's receipts are classed elsewhere

      [queue]                # the job queue (queue.py): what a heavy job waits for
      threads = 4            # CPUs one check may use (LEAN_NUM_THREADS, CPUQuota of its slice); default 4, at most the CPUs
      slots = 3              # jobs at once; default the number of CPUs divided by threads
      keep_free = "3G"       # MemAvailable never planned below this
      default_reservation = "2G"
      max_wait = 3600        # seconds a job waits before giving up; default: no limit

* `$XDG_DATA_HOME/verifylab/tools/<toolchain>/` (default `~/.local/share/verifylab/tools/`): copies made by
  `vl init --tools`, with their sha256 in `REVISIONS` (`sha256sum -c REVISIONS` re-checks them). A protected check
  runs a tool only when a `REVISIONS` file next to it pins its sha256, wherever the tool was named.

* `$XDG_DATA_HOME/verifylab/lean-builds/`: the trusted build store (ADR 0005), Lake's products of the
  challenge side of earlier protected checks (adapters/lean_build_cache.py); only `vl` writes it.

* `$XDG_DATA_HOME/verifylab/lean-solution-builds/`: with `[check] reuse_solution_builds = true`, the solution
  side of earlier protected checks, untrusted, one entry per set of inputs (ADR 0008); only `vl` writes it,
  and a check mounts only the entry of its own inputs, read-only.

* The system programs `vl` starts (bubblewrap, systemd-run, systemctl, env, stdbuf, sh, git) come from
  `[launchers]` in machine.toml (absolute paths), else from the first of SYSTEM_BIN that has them; never from the
  caller's `PATH`, which could put a wrapper in front of the jail or of git.

The default locations come from the account database, not from `$HOME`. The XDG variables are honoured, so a
caller can choose another machine file and tools store for one command; when one points elsewhere than the
account's default, every receipt names the variable (`environment.machine_policy.overridden_by`) and the status of
a result verified by such a receipt says so.
"""

from __future__ import annotations

import contextlib
import hashlib
import math
import os
import platform
import pwd
import re
import tomllib
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Iterator

from . import fsio

MACHINE_FILE = "machine.toml"
REVISIONS = "REVISIONS"
TOOL_NAMES = ("comparator", "landrun", "lean4export", "nanoda")
LAUNCHER_NAMES = ("bwrap", "systemd-run", "systemctl", "env", "stdbuf", "sh", "git")
SYSTEM_BIN = ("/usr/bin", "/bin", "/usr/sbin", "/sbin")
_XDG_DEFAULTS = {"XDG_CONFIG_HOME": ".config", "XDG_DATA_HOME": ".local/share"}
CHECK_KEYS = ("memory_total", "build_cache", "reuse_solution_builds")
MACHINE_KEYS = ("name", "trust")
TRUST_CLASSES = ("own", "foreign")
MACHINE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
QUEUE_KEYS = ("slots", "threads", "keep_free", "default_reservation", "max_wait")
DEFAULT_THREADS = 4   # CPUs one check may use, unless [queue] threads says otherwise
SANDBOX_CLASSES = ("workstation", "disposable")      # the machine's own word for what it is (ADR 0012, proposed)
MEMORY_TOTAL_SHARE = 0.7   # default cap of all vl runs together: this share of physical RAM
_REVISION_LINE = re.compile(r"^([0-9a-f]{64}) [ *](\S+)$")
_SIZE = re.compile(r"^(\d+(?:\.\d+)?)([KMGT]?)$", re.IGNORECASE)


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


def build_store_root() -> Path:
    """The trusted build store of protected Lean checks (adapters/lean_build_cache.py), next to the tools store."""
    return _xdg("XDG_DATA_HOME", _XDG_DEFAULTS["XDG_DATA_HOME"]) / "verifylab" / "lean-builds"


def solution_store_root() -> Path:
    """The store of untrusted solution builds (adapters/lean_build_cache.py, ADR 0008), apart from the
    trusted one: no entry of one ever reaches the other."""
    return _xdg("XDG_DATA_HOME", _XDG_DEFAULTS["XDG_DATA_HOME"]) / "verifylab" / "lean-solution-builds"


def queue_state_root() -> Path:
    """Where the job queue (queue.py) keeps its places: machine-wide state, outside every repository."""
    return _xdg("XDG_STATE_HOME", ".local/state") / "verifylab" / "queue"


def overrides() -> dict[str, str]:
    """The XDG variables of this call that move the machine file or the tools store away from the account's
    default locations: a machine policy the caller chose, which receipts and statuses name."""
    found = {}
    for variable, default in _XDG_DEFAULTS.items():
        if _xdg(variable, default) != home() / default:
            found[variable] = os.environ[variable]
    return found


def policy_record(loaded: "Machine | None" = None) -> dict:
    """What a receipt records of the machine policy a check ran under, and of which machine it was (when named)."""
    loaded = loaded if loaded is not None else load()
    return {"config": str(loaded.path), "sha256": loaded.sha256, "tools_store": str(store_root()),
            "overridden_by": overrides(), "launchers": {n: launcher(n, loaded) for n in LAUNCHER_NAMES},
            **({"machine": {"name": loaded.name, "trust": loaded.trust}} if loaded.name else {})}


def cpu_model(cpuinfo: Path = Path("/proc/cpuinfo")) -> str | None:
    """The first `model name` of /proc/cpuinfo (x86), else what `platform` knows; None when neither says."""
    try:
        for line in cpuinfo.read_text().splitlines():
            if line.startswith("model name") and ":" in line:
                return line.split(":", 1)[1].strip() or None
    except OSError:
        pass
    return platform.processor() or None


def host_class(loaded: "Machine | None" = None) -> dict[str, object]:
    """What a receipt records of the computer it ran on, so that receipts of different machines can be compared (no
    verdict depends on it): the sandbox class, kernel release, architecture, CPU model, logical cores and RAM. A
    field that cannot be read is left out."""
    loaded = loaded if loaded is not None else load()
    uname = os.uname()
    found = {"sandbox_class": loaded.sandbox_class, "kernel": uname.release, "arch": uname.machine,
             "cpu_model": cpu_model(), "cores": os.cpu_count(), "ram_bytes": meminfo_field("MemTotal")}
    return {key: value for key, value in found.items() if value is not None}


def environment_class(adapter: str, assurance: str, adapter_env: dict[str, object],
                      memory_cap: dict[str, object] | None = None,
                      loaded: "Machine | None" = None) -> dict[str, object]:
    """Deterministic class of the execution environment a receipt ran under, excluding time, host name, local paths
    and machine names: two runs on equivalent setups share `environment_class['digest']`."""
    import json
    host = host_class(loaded)
    basis: dict[str, object] = {
        "adapter": adapter,
        "assurance": assurance,
        **{k: host[k] for k in ("sandbox_class", "arch", "kernel") if k in host},
    }
    cap = (memory_cap or {}).get("memory_cap")
    if isinstance(cap, str) and cap:
        basis["memory_cap"] = cap
    if isinstance(adapter_env.get("toolchain"), str):
        basis["toolchain"] = adapter_env["toolchain"]
    if isinstance(adapter_env.get("lean_version"), str):
        basis["lean_version"] = adapter_env["lean_version"]
    tools = adapter_env.get("tools")
    if isinstance(tools, dict):
        pinned = {name: info["sha256"] for name, info in sorted(tools.items())
                  if isinstance(info, dict) and isinstance(info.get("sha256"), str)}
        if pinned:
            basis["tools"] = pinned
    packages = adapter_env.get("packages")
    if isinstance(packages, dict) and isinstance(packages.get("identity"), str):
        basis["packages_identity"] = packages["identity"]
    if isinstance(adapter_env.get("python_version"), str):
        basis["python_version"] = adapter_env["python_version"]
    if isinstance(adapter_env.get("bwrap"), str):
        basis["bwrap"] = adapter_env["bwrap"]
    canonical = json.dumps(basis, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return {**basis, "digest": "sha256:" + hashlib.sha256(canonical).hexdigest()}


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
    total = meminfo_field("MemTotal", meminfo)
    return None if total is None else f"{int(total // 1024 * MEMORY_TOTAL_SHARE)}K"


def meminfo_field(name: str, meminfo: Path = Path("/proc/meminfo")) -> int | None:
    """A field of /proc/meminfo (`MemTotal`, `MemAvailable`) in bytes; None when it cannot be read."""
    try:
        for line in meminfo.read_text().splitlines():
            if line.startswith(f"{name}:"):
                return int(line.split()[1]) * 1024
    except (OSError, ValueError, IndexError):
        return None
    return None


def size_bytes(text: str, meminfo: Path = Path("/proc/meminfo")) -> int | None:
    """A size as systemd writes one (`2G`, `512M`, `1048576`, base 1024; `70%` of MemTotal) in bytes; None for
    `infinity`. Raises ValueError for anything else."""
    text = text.strip()
    if text == "infinity":
        return None
    if text.endswith("%") and text[:-1].replace(".", "", 1).isdigit():
        total = meminfo_field("MemTotal", meminfo)
        if total is None:
            raise ValueError(f"cannot read MemTotal for {text!r}")
        return int(total * float(text[:-1]) / 100)
    if (match := _SIZE.match(text)) is None:
        raise ValueError(f"not a size: {text!r} (a number of bytes, or with K, M, G or T)")
    return int(float(match.group(1)) * 1024 ** " KMGT".index(match.group(2).upper() or " "))


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
    fsio.replace_files(folder, {REVISIONS: "\n".join(lines) + "\n"})


@dataclass(frozen=True)
class Machine:
    path: Path
    sha256: str | None                     # None: there is no machine file
    tools: dict[str, str] = field(default_factory=dict)
    elan_home: str | None = None
    caches: dict[str, str] = field(default_factory=dict)
    memory_total: str | None = None        # [check] memory_total as written; None: not set
    build_cache: bool = True               # [check] build_cache: reuse the trusted build store
    reuse_solution_builds: bool = False    # [check] reuse_solution_builds (ADR 0008)
    launchers: dict[str, str] = field(default_factory=dict)
    queue: dict[str, object] = field(default_factory=dict)   # [queue], checked; queue.py applies the defaults
    sandbox_class: str = "workstation"     # [sandbox] class (ADR 0012, proposed)
    name: str | None = None                # [machine] name: this computer, recorded in its receipts (ADR 0015, proposed)
    trust: str = "own"                     # [machine] trust: how other machines class this one's receipts

    def threads(self) -> int:
        """The CPUs one check may use (`[queue] threads`, else DEFAULT_THREADS, at most the CPUs there are): its Lean
        and Lake worker threads and the CPU quota of its job's slice. The queue's default slots divide the CPUs by it."""
        return int(self.queue.get("threads") or min(DEFAULT_THREADS, os.cpu_count() or 1))

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

    def pinned_sha256(self, tool: str, toolchain: str) -> str | None:
        """The sha256 this machine pins for `tool` under `toolchain`: the REVISIONS file next to the tool where a
        protected check would find it (machine.toml `[tools]`, else the tools store); None when nothing pins it."""
        try:
            path = Path(self.tools.get(tool) or self.store(toolchain) / tool)
        except ValueError:
            return None
        return read_revisions(path.parent).get(path.name)


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


@contextlib.contextmanager
def replay_policy(clone: Path, source: Path) -> Iterator[Machine]:
    """The machine policy of `vl replay` while it checks in its fresh clone `clone`: the build cache this machine sets
    for the repository it was cloned from (`source`, that repository's main checkout), no trusted build store
    (ADR 0005) and no stored solution build (ADR 0008), so both sides are built anew from the sources git holds. The
    machine file is unchanged, and so is its record."""
    loaded = load()
    cache = loaded.cache_for(source)
    _LOADED[loaded.path] = replace(loaded, build_cache=False, reuse_solution_builds=False,
                                   caches={**loaded.caches, **({str(clone.resolve()): cache} if cache else {})})
    try:
        yield _LOADED[loaded.path]
    finally:
        _LOADED[loaded.path] = loaded


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
    launchers_raw, queue_raw = raw.get("launchers", {}), raw.get("queue", {})
    sandbox_raw, machine_raw = raw.get("sandbox", {}), raw.get("machine", {})
    if not all(isinstance(t, dict)
               for t in (tools_raw, caches_raw, check_raw, launchers_raw, queue_raw, sandbox_raw, machine_raw)):
        raise MachineError(f"{path}: [tools], [caches], [check], [launchers], [machine], [queue] and [sandbox] must be "
                           "tables")
    if set(sandbox_raw) - {"class"}:
        raise MachineError(f"{path}: unknown [sandbox] key(s) {sorted(set(sandbox_raw) - {'class'})}; known: ['class']")
    sandbox_class = sandbox_raw.get("class", "workstation")
    if sandbox_class not in SANDBOX_CLASSES:
        raise MachineError(f"{path}: [sandbox] class must be one of {list(SANDBOX_CLASSES)}, not {sandbox_class!r}")
    name, trust = _machine(path, machine_raw)
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
    flags = {key: check_raw.get(key, default) for key, default in (("build_cache", True),
                                                                    ("reuse_solution_builds", False))}
    for key, value in flags.items():
        if not isinstance(value, bool):
            raise MachineError(f"{path}: [check] {key} must be true or false")
    unknown = sorted(set(tools_raw) - {*TOOL_NAMES, "elan_home"})
    if unknown:
        raise MachineError(f"{path}: unknown [tools] key(s) {unknown}; known: {[*TOOL_NAMES, 'elan_home']}")
    tools = {name: _absolute(value, f"[tools] {name}") for name, value in tools_raw.items() if name != "elan_home"}
    elan = _absolute(tools_raw["elan_home"], "[tools] elan_home") if "elan_home" in tools_raw else None
    caches = {_absolute(key, "a [caches] key"): _absolute(value, f"[caches] '{key}'") for key, value in caches_raw.items()}
    return Machine(path, hashlib.sha256(data).hexdigest(), tools, elan, caches, total, flags["build_cache"],
                   flags["reuse_solution_builds"], launchers, _queue(path, queue_raw), sandbox_class, name, trust)


def _machine(path: Path, raw: dict) -> tuple[str | None, str]:
    unknown = sorted(set(raw) - set(MACHINE_KEYS))
    if unknown:
        raise MachineError(f"{path}: unknown [machine] key(s) {unknown}; known: {list(MACHINE_KEYS)}")
    name, trust = raw.get("name"), raw.get("trust", "own")
    if name is not None and (not isinstance(name, str) or not MACHINE_NAME.fullmatch(name)):
        raise MachineError(f"{path}: [machine] name must be 1-64 letters, digits, '.', '_' or '-'")
    if trust not in TRUST_CLASSES:
        raise MachineError(f"{path}: [machine] trust must be one of {list(TRUST_CLASSES)}")
    return name, trust


def _queue(path: Path, raw: dict) -> dict[str, object]:
    unknown = sorted(set(raw) - set(QUEUE_KEYS))
    if unknown:
        raise MachineError(f"{path}: unknown [queue] key(s) {unknown}; known: {list(QUEUE_KEYS)}")
    for key in ("slots", "threads", "max_wait"):
        value = raw.get(key)
        whole = key != "max_wait"
        if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float))
                                  or not 0 < value < math.inf or (whole and not isinstance(value, int))):
            kind = "a positive whole number" if whole else "a finite, positive number of seconds"
            raise MachineError(f"{path}: [queue] {key} must be {kind}")
    for key in ("keep_free", "default_reservation"):
        if key in raw:
            try:
                size = size_bytes(raw[key]) if isinstance(raw[key], str) else None
            except ValueError:
                size = None
            if size is None or (key == "default_reservation" and size == 0):   # a run capped at 0 cannot start
                raise MachineError(f"{path}: [queue] {key} must be a size such as \"3G\"")
    return dict(raw)
