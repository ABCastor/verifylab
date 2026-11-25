"""`vl replay ID`: run the protected check of an admitted receipt again, in a fresh clone, and compare.

The clone is made by git's own transport (`git clone --no-local`): it holds what the repository's branches,
remote-tracking branches and tags reach and nothing of the caller's worktree, index or loose objects, which is what a
stranger's clone holds. Three replay contracts are kept apart:

* `exact`: reconstructs the recorded tool and kernel generation and compares the full recorded basis (`COMPARED` plus
  one-directional `PROBE_HITS`).
* `compatibility`: replays an identified historical receipt profile through a named, justified projection of preserved
  inputs (question recovered from `item_revision`, historical configuration binding recomputed at `trusted_commit`,
  legacy `VLTrusted/` paths, and explicit omission of absent `environment.packages.identity` without inventing
  historical coverage or rewriting the receipt).
* `stronger`: `--contract recheck` checks the same preserved question and inputs under the current policy and kernels
  (which may add `nanoda` to a single-kernel historical receipt) and records separate evidence with an explicit relation
  to the original check, never conflated with an exact replay match.

Nothing is written to the repository except a replay record under `.vl-cache/replay/` (ADR 0009), and for a matching
`exact` or `compatibility` replay of a foreign receipt, this machine's own replay receipt under `research/evidence/`."""

from __future__ import annotations

import copy
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from .. import __version__, gitref, machine
from ..config import (CONFIG_PATH, RULES_INPUT, ConfigError, config_digest,
                      historical_config_binding, parse_config, rules_digest)
from ..output import EXIT_FAIL, EXIT_PASS, EXIT_TOOL, emit, fail
from ..records import (RecordError, canonical_json, historical_kernel_profile, parse_item, question_digest,
                       sha256_hex, write_new_json)
from ..repo import (CACHE_DIR, EVIDENCE, FOREIGN_DIR, ITEMS, Repo, StoredRecord,
                    input_file, question_from_revision)
from ..status import derive, probe_hits
from . import check, open_repo, seconds

REPLAY_DIR = f"{CACHE_DIR}/replay"
RECORD_SCHEMA = "vl.replay/1"
COMPAT_PROFILE = "legacy-historical/v1"
VLTRUSTED_PREFIX = "VLTrusted/"
# Besides branches and tags, the clone takes the repository's remote-tracking branches: in a stranger's clone they
# are the publisher's branches (only the default one is a local branch), the trusted one among them.
PUBLISHED = "+refs/remotes/*:refs/remotes/published/*"

# What a replay must reproduce: the receipt fields, `*` standing for every key of a table. The rest (times, paths,
# the trusted ref's name, the machine policy, memory, logs) describes the run, not what it established. The statement
# probes are compared in one direction only (`differences`).
COMPARED = (
    ("adapter",), ("verdict",), ("question_digest",),
    ("inputs", "digest"), ("inputs", "imports"), ("inputs", "files", "*"), ("inputs", "trusted_files", "*"),
    ("environment", "toolchain"), ("environment", "lean_version"), ("environment", "tools", "*", "sha256"),
    ("environment", "packages", "identity", "revisions", "*", "head"),
    ("environment", "packages", "identity", "traces", "*", "depHash"),
    ("environment", "interpreter"), ("environment", "python_version"),
    ("checked", "kernels"),
)
PACKAGE_IDENTITY_PARTS = {
    ("environment", "packages", "identity", "revisions", "*", "head"),
    ("environment", "packages", "identity", "traces", "*", "depHash"),
}
RECHECK_SKIP_EXACT = {
    ("environment", "toolchain"), ("environment", "lean_version"), ("environment", "tools", "*", "sha256"),
    ("environment", "interpreter"), ("environment", "python_version"), ("checked", "kernels"),
    *PACKAGE_IDENTITY_PARTS,
}
PROBE_HITS = ("vacuous_by", "trivial_by")
CONTRACTS = ("auto", "exact", "compat", "recheck")
_ABSENT = object()


def register(sub):
    p = sub.add_parser("replay", help="run an admitted (or foreign, imported) receipt's protected check again in a "
                                      "fresh clone and compare")
    p.add_argument("id", help="item id")
    p.add_argument("--receipt", metavar="RID",
                   help="a prefix of the receipt_id of an admitted receipt or of a foreign one imported by `vl receipts "
                        "import` (default: the receipt that decides the item's status, the one `vl show` names; else "
                        "its newest admitted protected pass, else its newest admitted protected receipt, else its "
                        "newest foreign one)")
    p.add_argument("--contract", choices=CONTRACTS, default="auto",
                   help="replay contract: auto (exact for current receipts, compatibility for recognized legacy "
                        "profiles), exact, compat, or recheck (independent rerun recorded separately)")
    p.add_argument("--answer-rev", metavar="REV",
                   help="published revision holding the checked answer; its inputs must match the receipt")
    p.add_argument("--timeout", type=seconds, default=1800.0, help="seconds for the check (default 1800)")
    p.add_argument("--max-wait", type=seconds, help="give up (exit 2) after waiting this many seconds in the queue")
    p.add_argument("--keep-scratch", action="store_true", help="keep the clone and the check's scratch for debugging")
    p.epilog = ("The clone and the check run under $XDG_CACHE_HOME/verifylab/scratch, deleted afterwards; a replay "
                "record goes to .vl-cache/replay/<id>/. Exit: 0 match (or independent recheck succeeded), 1 mismatch "
                "(the differences are listed), 3 the replay could not compare (no clone, an inconclusive check), "
                "2 usage (no such item, receipt or applicable compatibility profile) or a problem found before the "
                "check ran (no checker, --max-wait reached). A foreign receipt that matches under exact or "
                "compatibility replay leaves quarantine, and the receipt of this replay's check is written to "
                "research/evidence/, where it counts once the integrator admits it (`vl admit`); rechecks "
                "never auto-promote foreign receipts.")
    return p


def _pick(data: Any, parts: tuple[str, ...], shown: str = "") -> Iterator[tuple[str, Any]]:
    """(dotted name, value) of the fields of `data` at `parts`; `*` names every key of a table, shown in brackets."""
    if not parts:
        yield shown, data
        return
    if not isinstance(data, dict):
        return
    head, rest = parts[0], parts[1:]
    for key in (sorted(data) if head == "*" else [head] if head in data else []):
        name = f"{shown}[{key}]" if head == "*" else f"{shown}.{key}" if shown else key
        yield from _pick(data[key], rest, name)


def _show(value: Any) -> str:
    return "absent" if value is _ABSENT else str(value)


def _compare_fields(recorded: dict[str, Any], replayed: dict[str, Any],
                    skip: set[tuple[str, ...]] = frozenset()) -> list[str]:
    found = []
    for parts in COMPARED:
        if parts in skip:
            continue
        before, after = dict(_pick(recorded, parts)), dict(_pick(replayed, parts))
        for name in sorted(before.keys() | after.keys()):
            old, new = before.get(name, _ABSENT), after.get(name, _ABSENT)
            if old != new:
                found.append(f"{name}: receipt {_show(old)}, replay {_show(new)}")
    for key in PROBE_HITS:     # a hit the replay finds and the receipt lacks; not the reverse (the battery's reach)
        recorded_hits = dict(probe_hits(recorded, key))
        found += [f"checked.probes[{name}].{key}: receipt absent, replay {tactic}"
                  for name, tactic in probe_hits(replayed, key) if name not in recorded_hits]
    return found


def differences(recorded: dict[str, Any], replayed: dict[str, Any]) -> list[str]:
    """Each COMPARED field the replay's receipt holds otherwise than the receipt it replays, absent on either side
    included: `name: receipt X, replay Y`. Empty when they agree."""
    return _compare_fields(recorded, replayed)


def compatibility_features(receipt: dict[str, Any]) -> list[str]:
    """Named historical-format differences on `receipt`, detected structurally (never by a timestamp).
    Empty when `receipt` follows the current format or is ineligible (e.g. binds `#verdict-rules` or carries
    `environment.packages.identity`)."""
    inputs = receipt.get("inputs") if isinstance(receipt.get("inputs"), dict) else {}
    trusted = inputs.get("trusted_files") if isinstance(inputs.get("trusted_files"), dict) else {}
    env = receipt.get("environment") if isinstance(receipt.get("environment"), dict) else {}
    packages = env.get("packages") if isinstance(env.get("packages"), dict) else {}
    checked = receipt.get("checked") if isinstance(receipt.get("checked"), dict) else {}
    if RULES_INPUT in trusted or "identity" in packages:
        return []
    features: list[str] = []
    if not isinstance(receipt.get("question_digest"), str) and isinstance(receipt.get("item_revision"), str):
        features.append("missing_question_digest")
    if historical_config_binding(trusted) is not None:
        features.append("legacy_config_binding")
    if any(isinstance(k, str) and k.startswith(VLTRUSTED_PREFIX) for k in trusted):
        features.append("legacy_vltrusted_trusted_files")
    if (isinstance(checked.get("trusted_modules"), list)
            and any(isinstance(m, str) and m.startswith(VLTRUSTED_PREFIX) for m in checked["trusted_modules"])):
        features.append("legacy_vltrusted_modules")
    if (receipt.get("adapter") == "lean-comparator" and checked.get("kernels") == ["lean"]
            and historical_kernel_profile(receipt)):
        features.append("historical_single_kernel")
    if not features:
        return []
    if receipt.get("adapter") == "lean-comparator" and (packages or trusted):
        features.append("missing_packages_identity")
    return features


def resolve_contract(receipt: dict[str, Any], requested: str) -> tuple[str, list[str], str | None]:
    """(contract, features, error reason) where contract is `exact`, `compatibility`, or `stronger`."""
    features = compatibility_features(receipt)
    if requested == "exact":
        return "exact", [], None
    if requested == "compat":
        if not features:
            return "compatibility", [], ("--contract compat applies only to recognized historical receipt profiles "
                                         "(this receipt uses the modern schema; use exact or recheck)")
        return "compatibility", features, None
    if requested == "recheck":
        return "stronger", features, None
    if features:
        return "compatibility", features, None
    return "exact", [], None


def is_foreign(record: StoredRecord) -> bool:
    return record.path.startswith(f"{FOREIGN_DIR}/")


def choose(repo: Repo, item_id: str, prefix: str | None) -> StoredRecord | str:
    """The admitted protected receipt to replay (else a foreign one, ADR 0015, proposed), or why there is none.
    Without a prefix, the receipt that decides the item's status (the one `vl show` names) when it is one of them, else
    the newest pass, else the newest receipt; with no admitted one, the same order over the foreign ones."""
    admitted = [r for r in repo.receipts(item_id) if r.admitted and r.data.get("assurance") == "protected"]
    foreign = [r for r in repo.foreign_receipts(item_id) if r.data.get("assurance") == "protected"]
    if prefix is not None:
        named = [r for r in admitted + foreign if str(r.data.get("receipt_id", "")).startswith(prefix.lower())]
        if len(named) != 1:
            return (f"{len(named)} admitted protected receipts of '{item_id}' (or foreign ones) have a receipt_id "
                    f"starting '{prefix}'; name exactly one")
        if named[0].problems:
            return f"{named[0].path} is not a valid receipt: {'; '.join(named[0].problems)}"
        return named[0]

    def valid(pool: list[StoredRecord]) -> list[StoredRecord]:       # the last is the newest pass, else receipt
        return sorted((r for r in pool if not r.problems),
                      key=lambda r: (r.data["verdict"] == "pass", r.data["finished_at"], r.path))
    if counted := valid(admitted):
        items, _ = repo.load_items()
        status = derive(repo, items[item_id], items) if item_id in items else None
        return next((r for r in counted if status and r.path == status.receipt), counted[-1])
    if quarantined := valid(foreign):
        return quarantined[-1]
    return (f"'{item_id}' has no valid admitted protected receipt on the trusted ref '{repo.config.trusted_ref}', "
            "and no foreign one")


def promote(repo: Repo, record: StoredRecord, replayed: dict[str, Any]) -> str:
    """Take a foreign receipt that a replay reproduced out of quarantine: the receipt of this machine's replay goes to
    research/evidence/, where it is a new record like a local one and counts once `vl admit` commits it. Not the
    foreign one: a replay compares preserved/reconstructed fields, and this machine's replay receipt records only what
    this machine actually produced. Returns its path."""
    target = repo.path(EVIDENCE, replayed["item"], f"{replayed['receipt_id'][:16]}.json")
    if not target.exists():
        write_new_json(target, replayed)
    (repo.root / record.path).unlink()
    return str(target.relative_to(repo.root))


def _asked(repo: Repo, clone: Path, commit: str, item_id: str, trusted: str) -> str | None:
    """The question digest of the item as committed at `commit` in the clone, its question read from `trusted`: what
    a protected check of that commit binds its receipt to (None when the item is not there or does not parse)."""
    rel = repo.rel(ITEMS, f"{item_id}.md")
    data = gitref.show(clone, commit, rel)
    try:
        asked = check.question_item(repo, parse_item(data, rel), "protected", trusted)[0] if data else None
    except RecordError:
        return None
    return question_digest(asked) if asked else None


def resolve_recorded_question(repo: Repo, clone: Path, receipt: dict[str, Any],
                              contract: str) -> tuple[str | None, str, str | None]:
    """(question_digest, source label, unrecoverable error) for `receipt`."""
    if isinstance(receipt.get("question_digest"), str):
        return receipt["question_digest"], "question_digest", None
    if contract == "exact":
        return None, "absent", None
    rel = repo.rel(ITEMS, f"{receipt['item']}.md")
    digest, err = question_from_revision(clone, rel, receipt.get("item_revision"))
    if err is not None:
        return None, "item_revision", err
    return digest, f"item_revision:{str(receipt.get('item_revision'))[:12]}", None


def answer_revisions(repo: Repo, clone: Path, record: StoredRecord, revision: str | None
                     ) -> Iterator[tuple[str, str]]:
    if revision is not None:
        yield gitref.rev_parse(clone, revision), "--answer-rev"
        return
    candidate = record.data["inputs"].get("candidate_head")
    if isinstance(candidate, str) and gitref.is_full_sha(candidate) and gitref.ref_exists(clone, candidate):
        yield candidate, "candidate_head"
    trusted = repo.trusted_commit
    if trusted and gitref.ref_exists(clone, trusted):
        if record.admitted:
            raw = gitref.show(clone, trusted, record.path)
            history = gitref.git(clone, "--literal-pathspecs", "log", "--first-parent", "--no-show-signature",
                                 "--format=%H", trusted, "--", record.path).stdout.decode().splitlines()
            admitted = next((rev for rev in reversed(history)
                             if raw is not None and gitref.show(clone, rev, record.path) == raw), None)
            if admitted and admitted != candidate:
                yield admitted, "the commit that admits the receipt"
        yield trusted, "the current trusted commit"


def answer_commit(repo: Repo, clone: Path, record: StoredRecord, revision: str | None,
                  expected_question: str | None = None) -> tuple[str | None, str, list[str]]:
    """Find the recorded bytes and answer binding together at one revision in the fresh clone."""
    receipt = record.data
    inputs = receipt["inputs"]
    files = inputs.get("files", {})
    wanted = {f"inputs.files[{p}]": d for p, d in files.items()}
    if isinstance(expected_question, str):
        wanted["question_digest"] = expected_question
    differences = []
    examined = set()
    for commit, name in answer_revisions(repo, clone, record, revision):
        if commit in examined:
            continue
        examined.add(commit)
        held = {f"inputs.files[{p}]": repo.digest(p, data) if (data := gitref.show(clone, commit, input_file(p)))
                is not None else None for p in files}
        if "question_digest" in wanted:
            held["question_digest"] = _asked(repo, clone, commit, receipt["item"], inputs["trusted_commit"])
        if held == wanted:
            return commit, name, []
        differences.extend(f"{key}: receipt {value}, git holds {held.get(key) or 'absent'} at {name}"
                           for key, value in sorted(wanted.items()) if held.get(key) != value)
    return None, "", differences or ["no answer revision was available in the clone"]


def clone(repo: Repo, folder: Path, trusted: str) -> Path:
    """A fresh clone of the repository in `folder`, nothing checked out; GitError when it cannot be made or lacks the
    trusted commit `trusted`."""
    target = folder / "clone"
    gitref.git(folder, "clone", "--quiet", "--no-local", "--no-checkout", "-c", f"remote.origin.fetch={PUBLISHED}",
               "--", str(gitref.common_dir(repo.root)), str(target))
    if not gitref.ref_exists(target, trusted):
        raise gitref.GitError(f"commit {trusted} is not in a clone of the repository: no branch, remote-tracking "
                              "branch or tag reaches it")
    return target


def _normalize_vltrusted(trusted_files: dict[str, Any]) -> tuple[dict[str, str], dict[str, str], list[str]]:
    """Map `VLTrusted/<path>` keys in `trusted_files` to repository paths; report hash collisions cleanly."""
    normalized: dict[str, str] = {}
    mapped_from: dict[str, str] = {}
    problems: list[str] = []
    for path, digest in sorted(trusted_files.items()):
        if isinstance(path, str) and path.startswith(VLTRUSTED_PREFIX):
            repo_path = path[len(VLTRUSTED_PREFIX):]
            if repo_path in trusted_files and trusted_files[repo_path] != digest:
                problems.append(f"inputs.trusted_files[{path}]: conflicts with {repo_path}")
                continue
            normalized[repo_path] = digest
            mapped_from[repo_path] = path
        else:
            normalized[path] = digest
    return normalized, mapped_from, problems


def _project_historical_inputs(clone: Path, receipt: dict[str, Any], replayed: dict[str, Any],
                               expected_question: str,
                               question_source: str) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], list[str]]:
    """Return `(projected_recorded, projected_replayed, normalizations, projection_problems)` for compatibility and
    stronger-recheck comparisons without modifying `receipt`."""
    rec = copy.deepcopy(receipt)
    rep = copy.deepcopy(replayed)
    norm: dict[str, Any] = {}
    problems: list[str] = []

    if not isinstance(receipt.get("question_digest"), str):
        rec["question_digest"] = expected_question
        norm["question_digest"] = {"source": question_source, "digest": expected_question}

    rec_trusted = dict((receipt.get("inputs") or {}).get("trusted_files") or {})
    rep_trusted = dict((replayed.get("inputs") or {}).get("trusted_files") or {})
    norm_rec_trusted, mapped_from, path_problems = _normalize_vltrusted(rec_trusted)
    problems.extend(path_problems)
    if mapped_from:
        norm["trusted_file_paths"] = {orig: repo_p for repo_p, orig in sorted(mapped_from.items())}

    cfg_key = historical_config_binding(norm_rec_trusted)
    projected_rep_for_cmp = dict(rep_trusted)
    projected_rep_for_digest = {mapped_from.get(k, k): v for k, v in rep_trusted.items() if k != RULES_INPUT}
    if cfg_key is not None:
        trusted_commit = receipt["inputs"]["trusted_commit"]
        cfg_bytes = gitref.show(clone, trusted_commit, CONFIG_PATH)
        if cfg_bytes is None:
            problems.append(f"inputs.trusted_files[{cfg_key}]: receipt {norm_rec_trusted[cfg_key]}, "
                            f"trusted_commit holds absent")
        else:
            try:
                cfg_obj = parse_config(clone, cfg_bytes.decode("utf-8"))
            except (ConfigError, UnicodeDecodeError) as exc:
                problems.append(f"inputs.trusted_files[{cfg_key}]: unreadable historical config at trusted_commit: {exc}")
            else:
                expected_rules = rules_digest(cfg_obj)
                if RULES_INPUT in rep_trusted and rep_trusted[RULES_INPUT] != expected_rules:
                    problems.append(f"inputs.trusted_files[{RULES_INPUT}]: trusted_commit config gives "
                                    f"{expected_rules}, replay recorded {rep_trusted[RULES_INPUT]}")
                hist_digest = (sha256_hex(cfg_bytes) if cfg_key == CONFIG_PATH
                               else config_digest(cfg_key, cfg_obj))
                projected_rep_for_cmp.pop(RULES_INPUT, None)
                projected_rep_for_cmp[cfg_key] = hist_digest
                projected_rep_for_digest[cfg_key] = hist_digest
                norm["config_binding"] = {"recorded_key": cfg_key, "replayed_key": RULES_INPUT,
                                          "trusted_commit": trusted_commit}

    rec_inputs_basis = {"files": (receipt.get("inputs") or {}).get("files", {}), "trusted_files": rec_trusted,
                        "target": receipt.get("target", {})}
    if "imports" in (receipt.get("inputs") or {}):
        rec_inputs_basis["imports"] = receipt["inputs"]["imports"]
    expected_rec_digest = "sha256:" + sha256_hex(canonical_json(rec_inputs_basis).encode("utf-8"))
    if (receipt.get("inputs") or {}).get("digest") != expected_rec_digest:
        problems.append(f"inputs.digest (recorded self-check): receipt {_show((receipt.get('inputs') or {}).get('digest'))}, "
                        f"expected {expected_rec_digest}")

    rep_inputs_basis = {"files": (replayed.get("inputs") or {}).get("files", {}),
                        "trusted_files": projected_rep_for_digest, "target": replayed.get("target", {})}
    if "imports" in (replayed.get("inputs") or {}):
        rep_inputs_basis["imports"] = replayed["inputs"]["imports"]
    projected_rep_digest = "sha256:" + sha256_hex(canonical_json(rep_inputs_basis).encode("utf-8"))
    if cfg_key is not None or mapped_from:
        norm["inputs_digest"] = {"basis": cfg_key or "VLTrusted-normalized", "recorded": receipt["inputs"]["digest"],
                                 "projected_replayed": projected_rep_digest}

    rec["inputs"]["trusted_files"] = norm_rec_trusted
    rep["inputs"]["trusted_files"] = projected_rep_for_cmp
    rep["inputs"]["digest"] = projected_rep_digest

    rec_mods = (receipt.get("checked") or {}).get("trusted_modules")
    rep_checked = replayed.get("checked") or {}
    historical = [m for m in rec_mods or [] if isinstance(m, str) and m.startswith(VLTRUSTED_PREFIX)]
    if historical:
        # The only replayed coverage representation known to map historical VLTrusted/ modules is the shared closure;
        # any other (missing or unknown mode, renamed modules) cannot show the coverage, so it never matches.
        mode = str(rep_checked.get("definitions_mode", ""))
        if not (mode.startswith("shared:") and rep_checked.get("trusted_modules") == []):
            problems.append(f"checked.trusted_modules: historical {historical} cannot be compared with the replayed "
                            f"coverage (definitions_mode {mode or 'missing'})")
        else:
            closure = set(rep_checked.get("trusted_closure") or [])
            missing_mods = [m for m in historical if m[len(VLTRUSTED_PREFIX):] not in closure]
            if missing_mods:
                problems.append(f"checked.trusted_modules: historical {missing_mods} missing from replayed shared "
                                "closure")
            else:
                norm["trusted_modules"] = {m: f"shared:{m[len(VLTRUSTED_PREFIX):]}" for m in historical}

    return rec, rep, norm, problems


def _compare_compatibility(clone: Path, receipt: dict[str, Any], replayed: dict[str, Any], expected_question: str,
                           question_source: str, features: list[str]) -> tuple[list[str], dict[str, Any]]:
    rec, rep, norm, problems = _project_historical_inputs(clone, receipt, replayed, expected_question, question_source)
    omitted = (["environment.packages.identity"] if "missing_packages_identity" in features else [])
    skip = PACKAGE_IDENTITY_PARTS if omitted else frozenset()
    found = [*problems, *_compare_fields(rec, rep, skip=skip)]
    note = ("historical compatibility projection: omitted environment.packages.identity was not recorded by the "
            "original receipt, so old package identity/trace coverage is not established") if omitted else (
            "historical compatibility projection of preserved inputs")
    compat = {"profile": COMPAT_PROFILE, "features": features, "normalizations": norm,
              "omitted_historical_fields": omitted, "note": note}
    return found, compat


def _compare_stronger(clone: Path, receipt: dict[str, Any], replayed: dict[str, Any], expected_question: str,
                      question_source: str, features: list[str]) -> tuple[str, list[str], dict[str, Any]]:
    """Compare preserved inputs/question/verdict for a stronger recheck and describe the explicit relation."""
    if features:
        rec, rep, norm, problems = _project_historical_inputs(clone, receipt, replayed, expected_question,
                                                              question_source)
        found = [*problems, *_compare_fields(rec, rep, skip=RECHECK_SKIP_EXACT)]
    else:
        norm = {}
        found = _compare_fields(receipt, replayed, skip=RECHECK_SKIP_EXACT)

    rec_kernels = list((receipt.get("checked") or {}).get("kernels") or [])
    rep_kernels = list((replayed.get("checked") or {}).get("kernels") or [])
    lost_kernels = [k for k in rec_kernels if k not in rep_kernels]
    if lost_kernels:
        found.append(f"checked.kernels: replay dropped kernel(s) {lost_kernels} present in receipt {rec_kernels}")
    added_kernels = [k for k in rep_kernels if k not in rec_kernels]

    rec_env = receipt.get("environment") or {}
    rep_env = replayed.get("environment") or {}
    rec_tools = rec_env.get("tools") or {}
    rep_tools = rep_env.get("tools") or {}
    changed_tools = [
        name for name in sorted(set(rec_tools) | set(rep_tools))
        if (rec_tools.get(name) or {}).get("sha256") != (rep_tools.get(name) or {}).get("sha256")
    ]
    relation = {
        "original_receipt_id": receipt["receipt_id"],
        "original_kernels": rec_kernels,
        "recheck_kernels": rep_kernels,
        "added_kernels": added_kernels,
        "removed_kernels": lost_kernels,
        "original_toolchain": rec_env.get("toolchain"),
        "recheck_toolchain": rep_env.get("toolchain"),
        "changed_tools": changed_tools,
        "normalizations": norm,
        "separate_evidence": True,
    }
    return ("mismatch" if found else "rechecked"), found, relation


def replay(repo: Repo, record: StoredRecord, args) -> tuple[str, list[str], dict[str, Any]]:
    """(match, rechecked, mismatch or error; differences or reasons; what the record keeps of the run)."""
    receipt = record.data
    requested = getattr(args, "contract", "auto") or "auto"
    contract, features, contract_err = resolve_contract(receipt, requested)
    trusted = receipt["inputs"].get("trusted_commit")
    run_data: dict[str, Any] = {"contract": contract, "trusted_commit": trusted, "answer_commit": None,
                                "answer_from": None}
    if contract_err is not None:
        return "error", [contract_err], run_data
    if not isinstance(trusted, str) or not gitref.is_full_sha(trusted):    # only a pass must name it in full
        return "error", [f"the receipt names no trusted commit to read the question from (inputs.trusted_commit: "
                         f"{trusted})"], run_data
    folder = Path(tempfile.mkdtemp(prefix=f"replay-{receipt['item']}-", dir=check.scratch_root()))
    try:
        try:
            root = clone(repo, folder, trusted)
            expected_q, q_source, q_err = resolve_recorded_question(repo, root, receipt, contract)
            if q_err is not None:
                return "error", [q_err], run_data
            answer, where, missing = answer_commit(repo, root, record, getattr(args, "answer_rev", None),
                                                   expected_question=expected_q)
            if answer is None:
                return "mismatch", ["git does not hold the checked candidate files and answer at one commit: "
                                    "nothing to run", *missing], run_data
            run_data.update(answer_commit=answer, answer_from=where)
            gitref.git(root, "checkout", "--quiet", "--detach", answer)
            fresh = Repo.open(root, trusted_ref=trusted)
            item = fresh.load_item(receipt["item"])
            peak = receipt.get("extra", {}).get("memory_peak_bytes")
            from .. import imports as vl_imports
            stores = vl_imports.replay_package_stores(repo) if receipt.get("inputs", {}).get("imports") else ()
            with machine.replay_policy(root, gitref.main_worktree(repo.root)):
                outcome, replayed = check.execute(
                    fresh, item, "protected", args.timeout,
                    peak if type(peak) is int and peak > 0 else None, args.max_wait, args.keep_scratch,
                    require_external_kernels=(contract == "stronger"), package_stores=stores,
                    historical_single_kernel=(contract == "compatibility" and "historical_single_kernel" in features))
            run_data["replay"] = replayed
            if replayed is None:
                return "error", [f"the check stopped before reading the candidate: {outcome.verdict}",
                                 *outcome.reasons], run_data
            if replayed["verdict"] in ("error", "unsupported"):     # inconclusive: never a match or a recheck
                return "error", [f"the replay's check was inconclusive: {replayed['verdict']}",
                                 *outcome.reasons], run_data
            if contract == "exact":
                found = differences(receipt, replayed)
                return ("mismatch" if found else "match"), found, run_data
            if contract == "compatibility":
                assert isinstance(expected_q, str)
                found, compat = _compare_compatibility(root, receipt, replayed, expected_q, q_source, features)
                run_data["compatibility"] = compat
                return ("mismatch" if found else "match"), found, run_data
            assert isinstance(expected_q, str)
            result, found, relation = _compare_stronger(root, receipt, replayed, expected_q, q_source, features)
            run_data["relation"] = relation
            return result, found, run_data
        except (gitref.GitError, ConfigError, RecordError) as exc:
            return "error", [str(exc)], run_data
    finally:
        gitref.close_readers()
        if not args.keep_scratch:
            shutil.rmtree(folder, ignore_errors=True)


def run(args) -> int:
    repo = open_repo(args)
    repo.load_item(args.id)                   # no such item: a usage problem (exit 2)
    chosen = choose(repo, args.id, args.receipt)
    if isinstance(chosen, str):
        return fail(chosen)
    requested = getattr(args, "contract", "auto") or "auto"
    _, _, contract_err = resolve_contract(chosen.data, requested)
    if contract_err is not None:
        return fail(contract_err)
    started = datetime.now(timezone.utc).isoformat(timespec="microseconds")
    try:
        result, found, kept = replay(repo, chosen, args)
    except check.Refused as exc:            # nothing ran (a wait past --max-wait): exit 2, as `vl check` says
        return fail(str(exc))
    receipt_id = chosen.data["receipt_id"]
    stamp = started.replace("-", "").replace(":", "").replace("+0000", "Z")
    path = repo.root / REPLAY_DIR / args.id / f"{receipt_id[:16]}-{stamp}.json"
    record_body = {
        "schema": RECORD_SCHEMA, "item": args.id, "receipt": chosen.path, "receipt_id": receipt_id,
        "result": result, "differences" if result != "error" else "reasons": found, **kept,
        "started_at": started, "finished_at": datetime.now(timezone.utc).isoformat(timespec="microseconds"),
        "tool_version": f"vl {__version__}"}
    write_new_json(path, record_body)
    rel = str(path.relative_to(repo.root))
    contract = kept.get("contract", "exact")
    promoted = (promote(repo, chosen, kept["replay"])
                if result == "match" and contract in ("exact", "compatibility") and is_foreign(chosen)
                and "historical_single_kernel" not in kept.get("compatibility", {}).get("features", []) else None)
    replayed = kept.get("replay") or {}
    if result == "match":
        extra = ""
        if compat := kept.get("compatibility"):
            omitted = compat.get("omitted_historical_fields") or []
            extra = (f" (historical compatibility profile {compat['profile']}"
                     + (f"; omitted historical field(s): {', '.join(omitted)}" if omitted else "") + ")")
        summary = (f"verdict {replayed['verdict']}, question, {len(replayed['inputs']['files'])} candidate and "
                   f"{len(replayed['inputs']['trusted_files'])} trusted inputs, tools and kernels as recorded{extra}")
    elif result == "rechecked":
        rel_info = kept.get("relation") or {}
        added = rel_info.get("added_kernels") or []
        summary = ("independent recheck evidence: preserved question and inputs, verdict "
                   f"{replayed['verdict']}, kernels {rel_info.get('recheck_kernels')}"
                   + (f" (added {added})" if added else ""))
    else:
        summary = "differences:" if result == "mismatch" else "could not compare:"
    answer = kept["answer_commit"]
    text = "\n".join([
        f"{args.id}: {result.upper()} (contract: {contract}, receipt {chosen.path}, replayed in a fresh clone)",
        f"question from {str(kept['trusted_commit'])[:12]}"
        + (f", answer from {answer[:12]} ({kept['answer_from']})" if answer else ""),
        summary, *[f"  - {line}" for line in found], f"record: {rel}",
        *([f"foreign receipt reproduced: this replay's receipt is {promoted}, counted once admitted (`vl admit`)"]
          if promoted else []),
    ])
    payload: dict[str, Any] = {
        "item": args.id, "receipt": chosen.path, "receipt_id": receipt_id, "contract": contract,
        "result": result, "differences" if result != "error" else "reasons": found,
        "trusted_commit": kept["trusted_commit"], "answer_commit": answer, "answer_from": kept["answer_from"],
        "verdict": replayed.get("verdict"), "record": rel, "promoted": promoted,
    }
    if "compatibility" in kept:
        payload["compatibility"] = kept["compatibility"]
    if "relation" in kept:
        payload["relation"] = kept["relation"]
    emit(args, text, payload)
    return {"match": EXIT_PASS, "rechecked": EXIT_PASS, "mismatch": EXIT_FAIL}.get(result, EXIT_TOOL)
