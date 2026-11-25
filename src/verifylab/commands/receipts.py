"""`vl receipts export|import`: move protected receipts between the machines of one person (ADR 0015, proposed).

`export OUT.tar` (or `OUT.tar.gz`, compressed with gzip) bundles the protected receipts the trusted ref does not hold
yet (the new files of research/evidence/), with a manifest that names the machine (`[machine] name` of machine.toml) and the sha256 of every
file. `import FILE --from NAME --trust own|foreign` reads a bundle and checks each receipt as `vl validate` would
(sealed, well-formed, its item on the trusted ref):

* `own`: the machine is one the person controls and runs `vl` on with pinned tools. The receipt is written to
  research/evidence/ like a local one and counts once the integrator admits it (`vl admit`). Every verifier tool the
  receipt names must have the sha256 this machine pins, and the commit it asked its question of must be here, else
  it would go stale: such a receipt is refused, and can be imported as foreign.
* `foreign`: the receipt is shown on the item's card and counted for nothing. It waits in `.vl-cache/foreign/NAME/`
  until `vl replay` reproduces it on this machine, which writes its own receipt to research/evidence/ instead.

The bundle (a tar, plain or compressed with gzip, bzip2 or xz) is read, never extracted: only the manifest and members
named `evidence/<item>/<16 hex>.json` are read, as bytes, whatever the archive says about paths or links, and no more
than MAX_BUNDLE_BYTES of it once uncompressed. Nothing here authenticates a machine: `own` is the integrator's
declaration, as admission is (docs/ARCHITECTURE.md, Trust model)."""

from __future__ import annotations

import bz2
import gzip
import hashlib
import io
import json
import lzma
import os
import stat
import re
import tarfile
import zlib
from contextlib import nullcontext
from pathlib import Path
from typing import Any

from .. import gitref, machine
from ..output import emit, fail
from ..records import ID_RE, canonical_json, pretty_json, receipt_problems, write_new_bytes
from ..repo import EVIDENCE, FOREIGN_DIR, Repo
from ..status import single_kernel
from . import action_flags, open_repo
from .batch import read_ids

SCHEMA = "vl.receipts/1"
MANIFEST = "manifest.json"
MEMBER = re.compile(r"evidence/([^/]+)/([0-9a-f]{16})\.json")
MAX_MEMBER_BYTES = 8 * 1024 * 1024
MAX_MEMBERS = 100_000
MAX_BUNDLE_BYTES = 512 * 1024 * 1024   # all of a bundle once uncompressed: tar headers, manifest and receipts
GZIP_SUFFIXES = (".gz", ".tgz")         # an export to such a name is compressed with gzip
OTHER_COMPRESSION = (".bz2", ".tbz", ".tbz2", ".xz", ".txz", ".zst", ".lz", ".lz4", ".z")
# Magic bytes of the compressed forms `read_bundle` opens, and how.
_DECOMPRESS = ((b"\x1f\x8b", lambda raw: gzip.GzipFile(fileobj=raw)), (b"BZh", bz2.BZ2File),
               (b"\xfd7zXZ\x00", lzma.LZMAFile))


class BundleError(Exception):
    """A bundle that is not read at all: not a tar, larger than MAX_BUNDLE_BYTES, a member that is not a regular file of
    the expected name or size, a missing or wrong manifest, a digest that differs."""


def register(sub):
    p = sub.add_parser("receipts", help="move protected receipts between your machines: export, import")
    actions = p.add_subparsers(dest="action", required=True)
    exp = actions.add_parser("export", help="bundle the protected receipts the trusted ref does not hold yet")
    exp.add_argument("out", metavar="OUT.tar", help="the bundle to write (must not exist): a plain tar, or compressed "
                                                    "with gzip when its name ends in .gz or .tgz")
    exp.add_argument("--ids", metavar="FILE", help="only the items listed in FILE, one id per line")
    imp = actions.add_parser("import", help="read a bundle: own receipts into research/evidence/ (counted once "
                                            "admitted), foreign ones into quarantine (counted after vl replay)")
    imp.add_argument("file", metavar="FILE", help="a bundle written by `vl receipts export`, plain or compressed "
                                                  "(gzip, bzip2, xz)")
    imp.add_argument("--from", dest="source", required=True, metavar="NAME",
                     help="the machine that checked them (must be the name in the bundle)")
    imp.add_argument("--trust", required=True, choices=machine.TRUST_CLASSES,
                     help="own (experimental): a machine you control running vl with the tools this machine pins; "
                          "foreign: shown, not counted until vl replay")
    for action in (exp, imp):
        action_flags(action)
    p.epilog = ("Exit: 0 done, 1 some receipts of the bundle were refused (listed; the others are imported), 2 the "
                "bundle or the call is unusable (nothing written).")
    return p


def run(args) -> int:
    try:
        return export(args) if args.action == "export" else import_(args)
    except BundleError as exc:
        return fail(str(exc))


# Export -------------------------------------------------------------------------------------------------------------


def export(args) -> int:
    repo = open_repo(args)
    loaded = machine.load()
    if not loaded.name:
        return fail(f"this machine has no name: set [machine] name in {loaded.path} (it is recorded in the bundle and "
                    "in the receipts checked from then on)")
    if os.path.lexists(args.out):
        return fail(f"{args.out} exists; the bundle is written only to a new file")
    suffix = Path(args.out).suffix.lower()
    if suffix in OTHER_COMPRESSION:
        return fail(f"{args.out}: a bundle is a plain tar, or compressed with gzip; name it OUT.tar or OUT.tar.gz")
    try:
        ids = read_ids(args.ids) if args.ids else repo.record_folders(EVIDENCE)
    except ValueError as exc:
        return fail(str(exc))
    files: dict[str, bytes] = {}
    refused = 0
    other_origins = []
    for item_id in ids:
        for record in repo.receipts(item_id):
            if record.admitted or not record.path.startswith(repo.rel(EVIDENCE) + "/"):
                continue          # on the trusted ref already, or exploratory
            if record.problems or record.data.get("assurance") != "protected":
                refused += 1
                continue
            declared = declared_machine(record.data)
            if declared is None:
                refused += 1
                continue
            if declared.get("name") not in (None, loaded.name):
                other_origins.append({"receipt": record.path, "origin": declared["name"]})
                continue
            files[f"evidence/{item_id}/{Path(record.path).name}"] = (repo.root / record.path).read_bytes()
    manifest = {"schema": SCHEMA, "machine": {"name": loaded.name, "trust": loaded.trust},
                "receipts": {name: hashlib.sha256(data).hexdigest() for name, data in sorted(files.items())}}
    out = Path(args.out)
    try:
        handle = out.open("xb")
    except OSError as exc:              # nothing created here (another process may have made it since): nothing to remove
        return fail(f"cannot write {out}: {exc}")
    compressed = suffix in GZIP_SUFFIXES
    try:
        with (handle,
              (gzip.GzipFile(filename="", fileobj=handle, mode="wb", mtime=0) if compressed
               else nullcontext(handle)) as stream,
              tarfile.open(fileobj=stream, mode="w", format=tarfile.GNU_FORMAT) as tar):
            for name, data in [(MANIFEST, pretty_json(manifest).encode()), *sorted(files.items())]:
                info = tarfile.TarInfo(name)
                info.size, info.mode = len(data), 0o644
                tar.addfile(info, io.BytesIO(data))
    except OSError as exc:
        out.unlink(missing_ok=True)
        return fail(f"cannot write {out}: {exc}")
    text = f"wrote {out} ({'tar, gzip' if compressed else 'plain tar'}): {len(files)} protected receipts not yet on the trusted ref, from machine {loaded.name}"
    if refused:
        text += f"\nskipped {refused} receipts that are malformed or not protected (vl validate lists them)"
    if other_origins:
        text += "\nForward the original bundles for receipts from other machines:"
        text += "".join(f"\n  {entry['receipt']}: {entry['origin']}" for entry in other_origins)
    emit(args, text, {"out": str(out), "machine": manifest["machine"], "receipts": sorted(files),
                      "skipped": refused + len(other_origins), "other_origins": other_origins})
    return 0


# Import -------------------------------------------------------------------------------------------------------------


class _Bounded(io.RawIOBase):
    """A stream that refuses to give more than `limit` bytes: the whole of a bundle, once uncompressed, so that neither
    its members nor its headers (a long name or pax header is read whole) can make the import hold more."""

    def __init__(self, stream, limit: int, path: str):
        self.stream, self.left, self.path = stream, limit, path

    def readable(self) -> bool:
        return True

    def readinto(self, buffer) -> int:
        data = self.stream.read(min(len(buffer), self.left + 1))
        if len(data) > self.left:
            raise BundleError(f"{self.path}: larger than {MAX_BUNDLE_BYTES} bytes once uncompressed")
        self.left -= len(data)
        buffer[:len(data)] = data
        return len(data)


def _open_bundle(raw, path: str) -> _Bounded:
    """The bundle's tar bytes, uncompressed by its magic bytes (gzip, bzip2, xz, else a plain tar), bounded."""
    magic = raw.read(6)
    raw.seek(0)
    opener = next((opener for prefix, opener in _DECOMPRESS if magic.startswith(prefix)), None)
    return _Bounded(opener(raw) if opener else raw, MAX_BUNDLE_BYTES, path)


def read_bundle(path: str) -> tuple[dict[str, Any], dict[str, bytes]]:
    """(manifest, {member name: bytes}) of a bundle, plain or compressed, or BundleError. Members are read as bytes
    and named, never extracted; the whole is read once, as a stream, up to MAX_BUNDLE_BYTES uncompressed."""
    try:
        raw = open(path, "rb")
    except OSError as exc:
        raise BundleError(f"{path}: cannot read ({exc.strerror or exc})") from None
    members: dict[str, bytes] = {}
    with raw:
        try:
            tar = tarfile.open(fileobj=_open_bundle(raw, path), mode="r|")
        except (tarfile.TarError, OSError, EOFError, zlib.error, lzma.LZMAError) as exc:
            raise BundleError(f"{path}: not a tar bundle, plain or compressed with gzip, bzip2 or xz ({exc})") from None
        try:
            for index, info in enumerate(tar):
                if index >= MAX_MEMBERS:
                    raise BundleError(f"{path}: more than {MAX_MEMBERS} members")
                if not info.isreg() or info.size > MAX_MEMBER_BYTES or info.name in members:
                    raise BundleError(f"{path}: member {info.name!r} is not a single regular file of at most "
                                      f"{MAX_MEMBER_BYTES} bytes")
                named = MEMBER.fullmatch(info.name)
                if info.name != MANIFEST and (named is None or not ID_RE.match(named.group(1))):
                    raise BundleError(f"{path}: member {info.name!r} is neither {MANIFEST} nor "
                                      "evidence/<item>/<16 hex>.json")
                members[info.name] = tar.extractfile(info).read()
        except (tarfile.TarError, OSError, EOFError, zlib.error, lzma.LZMAError) as exc:
            raise BundleError(f"{path}: damaged bundle ({exc})") from None
        finally:
            tar.close()
    try:
        manifest = json.loads(members.pop(MANIFEST))
        assert manifest["schema"] == SCHEMA and machine.MACHINE_NAME.fullmatch(manifest["machine"]["name"])
        listed = manifest["receipts"]
        assert isinstance(listed, dict) and all(isinstance(v, str) for v in listed.values())
    except (KeyError, ValueError, TypeError, AssertionError):
        raise BundleError(f"{path}: no valid {MANIFEST} ({SCHEMA}: machine name and receipt digests)") from None
    if set(listed) != set(members):
        raise BundleError(f"{path}: the members are not those the manifest lists")
    wrong = sorted(n for n, data in members.items() if hashlib.sha256(data).hexdigest() != listed[n])
    if wrong:
        raise BundleError(f"{path}: {len(wrong)} members differ from the digests in the manifest "
                          f"(altered after export): {', '.join(wrong[:5])}")
    return manifest, members


def tool_problems(loaded: machine.Machine, environment: Any) -> list[str]:
    """Each verifier tool a receipt names whose sha256 this machine does not pin (or pins otherwise)."""
    tools = environment.get("tools") if isinstance(environment, dict) else None
    toolchain = environment.get("toolchain") if isinstance(environment, dict) else None
    if not isinstance(tools, dict) or not isinstance(toolchain, str):
        return []
    found = []
    for name, identity in sorted(tools.items()):
        pinned = loaded.pinned_sha256(name, toolchain)
        sha = identity.get("sha256") if isinstance(identity, dict) else None
        if pinned is None or pinned != sha:
            found.append(f"{name} sha256 {str(sha)[:12]} is " + (
                f"not the {pinned[:12]} this machine pins" if pinned else "not pinned on this machine"))
    return found


def refusal(repo: Repo, loaded: machine.Machine, item_id: str, name: str, data: Any, args) -> str | None:
    """Why this receipt is not imported as `args.trust` from `args.source`, or None."""
    if problems := receipt_problems(data):
        return "; ".join(problems)
    if data["item"] != item_id or data["receipt_id"][:16] != name:
        return "filed under another item or name than the receipt says"
    if data["assurance"] != "protected":
        return "not a protected receipt"
    if item_id not in repo.trusted_items()[0]:
        return f"item '{item_id}' is not on the trusted ref '{repo.config.trusted_ref}'"
    declared = declared_machine(data)
    if declared is None:
        return "environment.machine_policy.machine is not a table of the strings name and trust"
    if declared.get("name") not in (None, args.source):
        return f"the receipt says machine {declared['name']}, not {args.source}"
    if args.trust == "own":
        if declared.get("trust") == "foreign":
            return f"machine {args.source} classes its own receipts as foreign ([machine] trust there)"
        commit = data["inputs"].get("trusted_commit")
        if isinstance(commit, str) and not (gitref.is_full_sha(commit) and gitref.ref_exists(repo.root, commit)):
            return f"the commit {commit[:12]} it asked its question of is not in this repository (fetch it first)"
        if mismatched := tool_problems(loaded, data["environment"]):
            return "; ".join(mismatched) + " (import as foreign and `vl replay` it)"
        if single_kernel(repo, data):
            return ("single-kernel Lean pass does not count under [lean] external_kernels "
                    "(import as foreign and `vl replay` it)")
    return None


def declared_machine(data: dict[str, Any]) -> dict[str, str] | None:
    policy = data["environment"].get("machine_policy") or {}
    declared = (policy.get("machine") or {}) if isinstance(policy, dict) else None
    if isinstance(declared, dict) and all(isinstance(declared.get(k, ""), str) for k in ("name", "trust")):
        return declared
    return None


def existing_receipt(repo: Repo, own: Path, place: Path, data: dict[str, Any]) -> bool:
    """Refuse an occupied receipt name that contains anything but the same sealed record."""
    for path in dict.fromkeys((own, place)):
        try:
            mode = path.lstat().st_mode
        except FileNotFoundError:
            continue
        if not stat.S_ISREG(mode):
            raise BundleError(f"existing receipt {path.relative_to(repo.root)} is not a regular file")
        raw = path.read_bytes()
        if not same_receipt(raw, data):
            raise BundleError(f"existing receipt {path.relative_to(repo.root)} is incomplete or differs")
        return True
    rel = own.relative_to(repo.root).as_posix()
    raw = repo.read_trusted(rel)
    if raw is None:
        return False
    if not same_receipt(raw, data):
        raise BundleError(f"admitted receipt {rel} differs")
    return True


def same_receipt(raw: bytes, data: dict[str, Any]) -> bool:
    try:
        return canonical_json(json.loads(raw)) == canonical_json(data)
    except (ValueError, RecursionError):
        return False


def import_(args) -> int:
    if not machine.MACHINE_NAME.fullmatch(args.source):
        return fail(f"--from {args.source!r}: a machine name is 1-64 letters, digits, '.', '_' or '-'")
    repo = open_repo(args)
    loaded = machine.load()
    manifest, members = read_bundle(args.file)
    if manifest["machine"]["name"] != args.source:
        return fail(f"the bundle comes from machine {manifest['machine']['name']}, not {args.source} (--from)")
    imported, present, refused = [], [], []
    for member, raw in sorted(members.items()):
        item_id, name = MEMBER.fullmatch(member).groups()
        try:
            data = json.loads(raw)
        except (ValueError, RecursionError):
            refused.append({"receipt": member, "reason": "not valid JSON"})
            continue
        if (why := refusal(repo, loaded, item_id, name, data, args)) is not None:
            refused.append({"receipt": member, "reason": why})
            continue
        own = repo.path(EVIDENCE, item_id, f"{name}.json")
        place = own if args.trust == "own" else repo.root / FOREIGN_DIR / args.source / item_id / f"{name}.json"
        rel = place.relative_to(repo.root).as_posix()
        try:
            if existing_receipt(repo, own, place, data):
                present.append(rel)
                continue
            try:
                write_new_bytes(place, raw)
            except FileExistsError:
                if not existing_receipt(repo, own, place, data):
                    raise
                present.append(rel)
                continue
        except (OSError, BundleError) as exc:
            refused.append({"receipt": member, "reason": str(exc)})
            continue
        imported.append(rel)
    where = "research/evidence/" if args.trust == "own" else f"{FOREIGN_DIR}/{args.source}/"
    lines = [f"{len(imported)} receipts from machine {args.source} imported as {args.trust} into {where}; "
             f"{len(present)} already here; {len(refused)} refused"]
    lines += [f"  refused {r['receipt']}: {r['reason']}" for r in refused]
    if imported:
        lines.append("next: `vl validate`, then `vl admit` (they count once admitted)" if args.trust == "own" else
                     "next: `vl replay ID --receipt RID` for each (they count after a matching replay is admitted)")
    emit(args, "\n".join(lines), {"from": args.source, "trust": args.trust, "imported": imported, "present": present,
                                  "refused": refused})
    return 1 if refused else 0
