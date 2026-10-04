"""Derived status. Nothing here is ever written into a record: it is recomputed from receipts and reviews."""

from __future__ import annotations

import json
import posixpath
from dataclasses import dataclass, field
from datetime import datetime, timezone

from . import leanmod
from .records import Item, canonical_json, sha256_hex
from .repo import Repo, StoredRecord

CHECKABLE_CLAIMS = ("formal", "computation")
UNDETERMINED = "undetermined"
CYCLE_NOTE = "status cannot be derived: refutes/answers relations form a cycle; run vl validate"


@dataclass(frozen=True)
class Status:
    label: str
    notes: tuple[str, ...] = ()
    receipt: str | None = None


@dataclass
class Graph:
    """Reverse `refutes` and `answers` relations, as committed on the trusted ref: (source id, pinned revision or
    None) per target id. A relation only a worktree holds is a proposal and never changes a status."""

    refuted_by: dict[str, list[tuple[str, str | None]]] = field(default_factory=dict)
    answered_by: dict[str, list[tuple[str, str | None]]] = field(default_factory=dict)

    @classmethod
    def build(cls, items: dict[str, Item]) -> "Graph":
        graph = cls()
        for item in items.values():
            for name, target in (("refutes", graph.refuted_by), ("answers", graph.answered_by)):
                for ref in getattr(item, name):
                    other, _, rev = ref.partition("@")
                    target.setdefault(other, []).append((item.id, rev.lower() or None))
        return graph


def trusted_graph(repo: Repo) -> Graph:
    """The relation graph of the items on the trusted ref (memoised on the repository object)."""
    graph = getattr(repo, "_vl_graph", None)
    if graph is None:
        graph = Graph.build(repo.trusted_items()[0])
        repo._vl_graph = graph
    return graph


def _worktree_graph(repo: Repo, items: dict[str, Item]) -> Graph:
    cached = getattr(repo, "_vl_worktree_graph", None)
    if cached is None or cached[0] is not items:
        cached = (items, Graph.build(items))
        repo._vl_worktree_graph = cached
    return cached[1]


# Among receipts that finished at the same instant, the more adverse verdict counts as the newer one.
_ADVERSE = {"pass": 0, "unsupported": 1, "error": 2, "fail": 3}


def _instant(raw) -> tuple[int, float, str]:
    """A recorded time, at full precision: (1, POSIX seconds) for ISO 8601 (without a zone, UTC), else (0, its text),
    which sorts before every real time."""
    try:
        moment = datetime.fromisoformat(raw)
    except (TypeError, ValueError):
        return (0, 0.0, str(raw))
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return (1, moment.timestamp(), "")


def _when(record: StoredRecord) -> tuple[int, float, str]:
    """When a receipt finished (`finished_at`), at full precision."""
    return _instant(record.data.get("finished_at", ""))


def receipt_order(record: StoredRecord) -> tuple:
    """Newest last: by time, then (a tie) the more adverse verdict, then the path, so the order is deterministic."""
    return (_when(record), _ADVERSE.get(record.data.get("verdict"), 0), record.path)


def review_order(record: StoredRecord) -> tuple:
    """Newest last: by `created` as a time at full precision; among reviews of the same instant, a fidelity verdict
    other than `faithful` counts as the newer (an adverse judgement is never lost to a tie), then the path decides."""
    data = record.data
    adverse = data.get("kind") == "fidelity" and data.get("verdict") != "faithful"
    return (_instant(data.get("created", "")), adverse, record.path)


def _latest(records: list[StoredRecord]) -> StoredRecord | None:
    return max(records, key=receipt_order, default=None)


def probe_hits(receipt: dict, key: str) -> list[tuple[str, str]]:
    """(theorem, tactic) for every target theorem whose statement probe `key` ("vacuous_by" or "trivial_by")
    closed, from a receipt's `checked.probes`. Receipts without probes have none."""
    probes = (receipt.get("checked") or {}).get("probes")
    if not isinstance(probes, dict):
        return []
    return [(name, entry[key]) for name, entry in sorted(probes.items())
            if isinstance(entry, dict) and isinstance(entry.get(key), str)]


def vacuity_notes(receipt: dict) -> list[str]:
    return [f"{name}: `{tactic}` derives False from its hypotheses, so no instance satisfies them and the "
            "theorem says nothing" for name, tactic in probe_hits(receipt, "vacuous_by")]


def probe_gaps(receipt: dict) -> list[str]:
    """Why the statement probes of a receipt did not finish: skipped, crashed, timed out, or a theorem without a
    result. Empty for a receipt whose probes all ran, and for one without a probe run (not a Lean receipt)."""
    checked = receipt.get("checked") or {}
    run = checked.get("probe_run")
    if not isinstance(run, dict):
        return []
    gaps = [f"skipped: {run['skipped']}"] if isinstance(run.get("skipped"), str) else []
    gaps += [p for p in run.get("problems") or [] if isinstance(p, str)]
    probes = checked.get("probes")
    if not gaps and isinstance(probes, dict):
        missing = sorted(name for name, entry in probes.items()
                         if not isinstance(entry, dict) or entry.get("prop_hypotheses") is None)
        if missing:
            gaps.append(f"no probe result for {', '.join(missing)}")
    return gaps


def probe_notes(receipt: dict) -> list[str]:
    gaps = probe_gaps(receipt)
    return [f"probes incomplete ({'; '.join(gaps)}): vacuity and triviality of the target were not ruled out"] \
        if gaps else []


SINGLE_KERNEL = ("a pass replayed by the Lean kernel only, while [lean] external_kernels is on (nanoda was not "
                 "installed on the machine that checked it): it does not count; check again where nanoda is installed")


def single_kernel(repo: Repo, receipt: dict) -> bool:
    """A protected Lean pass that only Lean's own kernel replayed while the trusted rules ask for external kernels:
    it does not count as a pass. (Protected checks refuse to run without nanoda then; older receipts may have one.)"""
    kernels = (receipt.get("checked") or {}).get("kernels")
    return (receipt.get("adapter") == "lean-comparator" and receipt.get("assurance") == "protected"
            and receipt.get("verdict") == "pass" and (not isinstance(kernels, list) or "nanoda" not in kernels)
            and repo.config.lean.external_kernels)


def counted_verdict(repo: Repo, record: StoredRecord) -> str:
    """The verdict a receipt counts for: a single-kernel pass the rules do not accept counts as `unsupported`."""
    return "unsupported" if single_kernel(repo, record.data) else record.data["verdict"]


def policy_notes(receipt: dict) -> list[str]:
    """The machine policy a receipt ran under, when the caller's environment chose it (XDG variables)."""
    policy = (receipt.get("environment") or {}).get("machine_policy")
    overridden = policy.get("overridden_by") if isinstance(policy, dict) else None
    if not isinstance(overridden, dict) or not overridden:
        return []
    moved = ", ".join(f"{k}={v}" for k, v in sorted(overridden.items()))
    return [f"checked under a machine policy the caller's environment chose ({moved}), not the account's default "
            f"machine.toml and tools store ({policy.get('config')})"]


def _newer_fail(repo: Repo, item: Item, valid: list[StoredRecord], passed: StoredRecord) -> StoredRecord | None:
    """An admitted protected fail, not older than `passed` (a fail of the same instant counts as newer), whose inputs
    are still those of the item now: the same question and files judged again, and rejected."""
    for record in sorted(valid, key=receipt_order, reverse=True):
        data = record.data
        if (record.admitted and data["assurance"] == "protected" and data["verdict"] == "fail"
                and _when(record) >= _when(passed) and not repo.stale_inputs(data, item, admitted=True)):
            return record
    return None


def evidence_status(repo: Repo, item: Item, receipts: list[StoredRecord]) -> Status:
    valid = [r for r in receipts if not r.problems]
    notes = [f"{r.path}: rejected receipt ({'; '.join(r.problems)})" for r in receipts if r.problems]
    notes += [f"{r.path}: {SINGLE_KERNEL}" for r in valid if single_kernel(repo, r.data)]
    passes = [r for r in valid if counted_verdict(repo, r) == "pass"]
    protected = [r for r in passes if r.data["assurance"] == "protected"]

    admitted = sorted((r for r in protected if r.admitted), key=receipt_order, reverse=True)
    fresh = []
    for record in admitted:
        stale = repo.stale_inputs(record.data, item, admitted=True)
        if stale:
            notes.append(f"{record.path}: inputs changed since check: {', '.join(stale)}")
        else:
            fresh.append(record)
    if fresh:
        record = fresh[0]
        failed = _newer_fail(repo, item, valid, record)
        if failed is not None:
            return Status("failed", tuple(notes + [
                f"{failed.path}: a newer admitted protected check of the same inputs failed, so the pass in "
                f"{record.path} no longer holds: " + "; ".join(failed.data["reasons"])]), failed.path)
        # A proof of an empty statement is never `verified`; vacuity shown by any admitted pass of these same inputs
        # stands, whatever a later run whose probes did not finish says.
        vacuous = [(r, vacuity_notes(r.data)) for r in fresh if vacuity_notes(r.data)]
        if vacuous:
            shown, found = vacuous[0]
            return Status("vacuous", tuple(notes + found), shown.path)
        return Status("verified", tuple(notes + probe_notes(record.data) + policy_notes(record.data)), record.path)
    if admitted:
        newest = admitted[0]
        return Status("verified-stale", tuple(notes + vacuity_notes(newest.data)), newest.path)
    if protected:
        latest = _latest(protected)
        notes.append("protected pass not yet committed on the trusted ref")
        return Status("pending-admission", tuple(notes + vacuity_notes(latest.data) + probe_notes(latest.data)),
                      latest.path)
    if passes:
        latest = _latest(passes)
        return Status("explored", tuple(notes + vacuity_notes(latest.data)), latest.path)
    latest = _latest(valid)
    if latest is not None and counted_verdict(repo, latest) == "fail":
        return Status("failed", tuple(notes + [f"{latest.path}: " + "; ".join(latest.data["reasons"])]), latest.path)
    if latest is not None:
        return Status(f"check-{counted_verdict(repo, latest)}", tuple(notes), latest.path)
    return Status("unverified", tuple(notes))


def derive(repo: Repo, item: Item, items: dict[str, Item], _seen: frozenset[str] = frozenset()) -> Status:
    """The status of `item` (its worktree version; `items` are the worktree's items). What the item is (kind and
    claim) and which items refute or answer it come from the trusted ref; the worktree's own relations are shown as
    proposals and change nothing. A relation pinned to a revision (`id@rev`) applies only while the trusted item
    is at that revision."""
    if item.id in _seen:
        return Status(UNDETERMINED, (CYCLE_NOTE,))
    seen = _seen | {item.id}
    trusted_items = repo.trusted_items()[0]
    trusted = trusted_items.get(item.id)
    reviews = repo.reviews(item.id)
    retractions = [r for r in reviews if not r.problems and r.data["kind"] == "retraction"]
    admitted_retractions = [r for r in retractions if r.admitted]
    notes: list[str] = [f"{r.path}: retraction proposed by {r.data['author']} (not admitted)"
                        for r in retractions if not r.admitted]
    if admitted_retractions:
        newest = max(admitted_retractions, key=review_order)
        return Status("retracted", tuple(notes), newest.path)

    basis = trusted or item
    if trusted is not None and (trusted.kind, trusted.claim) != (item.kind, item.claim):
        notes.append(f"the worktree makes this item a {_kind(item)}; the trusted ref's {_kind(trusted)} decides "
                     "its status until the change is integrated")
    graph, proposals = trusted_graph(repo), _worktree_graph(repo, items)

    def related(relation: str) -> list[str]:
        """Items that `relation` (refuted_by, answered_by) links to this item on the trusted ref and that apply
        to its trusted revision; notes for those that do not, and for proposals."""
        linked = []
        admitted = {source for source, _ in getattr(graph, relation).get(item.id, [])}
        for source, pin in getattr(graph, relation).get(item.id, []):
            if source not in items and source not in trusted_items:
                continue
            if pin and not basis.revision.startswith(pin):
                notes.append(f"{source} {_VERB[relation]} revision {pin} of this item, not its trusted revision "
                             f"{basis.revision[:12]}")
                continue
            linked.append(source)
        for source, _ in getattr(proposals, relation).get(item.id, []):
            if source not in admitted:
                notes.append(f"{source} {_VERB[relation]} this item in the worktree only: a proposal, not on the "
                             "trusted ref")
        return linked

    def status_of(other: str) -> Status:
        return derive(repo, items.get(other) or trusted_items[other], items, seen)

    if basis.kind in ("result", "conjecture"):
        for refuter in related("refuted_by"):
            sub = status_of(refuter)
            if sub.label == UNDETERMINED:
                return sub
            if sub.label == "verified":
                return Status("refuted", tuple(notes + [f"refuted by {refuter}"]), sub.receipt)
            notes.append(f"refutation proposed by {refuter} ({sub.label})")
        if basis.kind == "result" and basis.claim in CHECKABLE_CLAIMS:
            status = evidence_status(repo, item, repo.receipts(item.id))
            return Status(status.label, tuple(notes) + status.notes, status.receipt)
        if basis.kind == "result":
            return Status(f"recorded-{basis.claim}", tuple(notes))
        return Status("open", tuple(notes))

    if basis.kind == "question":
        answers = []
        for source in related("answered_by"):
            mismatch = answer_mismatch(basis, trusted_items.get(source) or items[source])
            if mismatch:
                notes.append(f"{source} does not answer this question: {mismatch}")
            else:
                answers.append(source)
        verified = [i for i in answers if status_of(i).label == "verified"]
        if verified:
            return Status("answered", tuple(notes + [f"answered by {', '.join(verified)}"]))
        if answers:
            notes.append(f"candidate answers: {', '.join(answers)}")
        return Status("open", tuple(notes))

    return Status(basis.kind, tuple(notes))


_VERB = {"refuted_by": "refutes", "answered_by": "answers"}


def answer_mismatch(question: Item, answer: Item) -> str | None:
    """Why `answer` cannot answer `question`, or None. A question with a `[lean]` target asks that target's theorems:
    an answer must state the same target and the same theorems (in any order). A question with a `[python]` evaluator
    asks for a candidate that evaluator judges through the entry it names: an answer must state the same evaluator
    and entry. Else a verified proof of an easier target, or of a part of the theorems, or a pass under a lenient
    evaluator, would make the question `answered`."""
    if question.kind != "question":
        return None
    problems = []
    asked = question.lean.get("target") if question.lean else None
    if asked is not None:
        given = answer.lean.get("target") if answer.lean else None

        def names(table: dict) -> list[str]:
            theorems = table.get("theorems") if table else None
            return sorted(map(str, theorems)) if isinstance(theorems, list) else []
        if given != asked or names(answer.lean) != names(question.lean):
            problems.append(f"it states [lean] target {json.dumps(given, default=str)} and theorems "
                            f"{names(answer.lean)}, the question asks {json.dumps(asked, default=str)} and "
                            f"{names(question.lean)}; an answer needs the same target and theorems")
    evaluator = question.python.get("evaluator") if question.python else None
    if evaluator is not None:
        asked_py = (evaluator, question.python.get("entry"))
        given_py = (answer.python.get("evaluator"), answer.python.get("entry")) if answer.python else (None, None)
        if given_py != asked_py:
            problems.append(f"it states [python] evaluator {json.dumps(given_py[0], default=str)} and entry "
                            f"{json.dumps(given_py[1], default=str)}, the question asks "
                            f"{json.dumps(asked_py[0], default=str)} and {json.dumps(asked_py[1], default=str)}; an "
                            "answer needs the same evaluator and entry")
    return "; ".join(problems) or None


def _kind(item: Item) -> str:
    return item.kind + (f"/{item.claim}" if item.claim else "")


@dataclass(frozen=True)
class Fidelity:
    """Whether a human or agent judged that the trusted target means what the question asks."""

    label: str        # faithful | disputed: <verdict> | review stale: <what> changed since review | not reviewed
                      # | target not admitted
    review: str | None = None
    by: str | None = None
    self_review: bool = False
    created: str | None = None
    acknowledges: tuple[str, ...] = ()   # probe findings a review of the current meaning accepts, e.g. "trivial"
    legacy: bool = False                 # the deciding review binds the target file only (it records no meaning)
    unbound: tuple[str, ...] = ()        # parts of the meaning the deciding review, recorded earlier, does not bind


def question_file(item: Item) -> str | None:
    """The file whose meaning a fidelity review vouches for: the Lean target or the trusted evaluator."""
    target = item.lean.get("target") if item.lean else None
    if isinstance(target, str):
        return target
    evaluator = item.python.get("evaluator") if item.python else None
    return evaluator if isinstance(evaluator, str) else None


# Item lists a fidelity review binds next to the claim text: what the record says it does not give, and what it rests
# on. Removing a limit widens the claim as much as editing the statement. The title and body explain; nothing binds
# them. A review recorded before these were bound lacks their digests and binds less (Fidelity.unbound).
BOUND_LISTS = ("limits", "assumptions")


def semantic_environment(repo: Repo) -> tuple[dict[str, str], list[str]]:
    """Stable Lean meaning inputs from the pinned trusted commit, excluding cache placement.

    Lake TOML contributes the options the protected check propagates; arbitrary lakefile.lean is bound
    conservatively by bytes. Invalid structured inputs retain their raw hash and a diagnostic.
    """
    project = posixpath.normpath(repo.config.lean.project.strip() or ".")
    roots = repo.config.lean.roots
    environment = {"research/vl.toml#meaning-rules": sha256_hex(canonical_json(
        {"project": project, "roots": sorted(set(roots))}).encode())}
    problems = []
    for name in ("lean-toolchain", "lake-manifest.json", "lakefile.toml", "lakefile.lean"):
        path = name if project == "." else f"{project}/{name}"
        raw = repo.read_trusted(path)
        if raw is None:
            environment[path] = "absent"
            continue
        digest = sha256_hex(raw)
        try:
            if name == "lake-manifest.json":
                manifest = json.loads(raw)
                if not isinstance(manifest, dict):
                    raise ValueError("manifest must be an object")
                # Lake's package-store placement is not a dependency identity. Paths inside package
                # entries remain bound, including path dependencies.
                digest = sha256_hex(canonical_json({k: v for k, v in manifest.items()
                                                   if k != "packagesDir"}).encode())
            elif name == "lakefile.toml":
                package, libraries, _ = leanmod.lakefile_options(raw, roots)
                digest = sha256_hex(canonical_json({"package": package, "libraries": libraries}).encode())
        except (ValueError, TypeError, AttributeError, UnicodeError) as exc:
            problems.append(f"{path}: {exc}")
        environment[path] = digest
    return environment, problems


def meaning(repo: Repo, item: Item) -> dict | None:
    """What a fidelity review vouches for, read from the trusted ref: `files`, the target (or evaluator) and, for Lean,
    every in-project module of its import closure, by sha256; the selected `theorems` and `witnesses`;
    `statement_sha256`, the digest of the item's claim text; and `limits_sha256` and `assumptions_sha256`, those of
    its lists (canonical JSON). Lean also binds stable project semantic-environment inputs.
    None when the target or evaluator is not there."""
    path = question_file(item)
    content = repo.read_trusted(path) if path else None
    if content is None:
        return None
    basis: dict = {"files": {path: sha256_hex(content)},
                   "statement_sha256": sha256_hex((item.statement or "").encode("utf-8"))}
    for name in BOUND_LISTS:
        basis[f"{name}_sha256"] = sha256_hex(canonical_json(list(getattr(item, name))).encode("utf-8"))
    if item.lean and isinstance(item.lean.get("target"), str):
        basis["theorems"] = [str(t) for t in item.lean.get("theorems") or []]
        basis["witnesses"] = [str(w) for w in item.lean.get("witnesses") or []]
        environment, problems = semantic_environment(repo)
        basis["semantic_environment"] = environment
        if problems:
            basis["semantic_environment_error"] = "; ".join(problems)
        roots = repo.config.lean.roots
        project = posixpath.normpath(repo.config.lean.project.strip() or ".")
        prel = (lambda p: p) if project == "." else (lambda p: f"{project}/{p}")
        try:
            header = leanmod.parse_header(leanmod.decode(content, path))
            start = [m for m in header.modules if leanmod.in_roots(m, roots)]
            closure = leanmod.closure(start, roots, lambda p: repo.read_trusted(prel(p)))
            for module, data in closure.modules.items():
                basis["files"][prel(leanmod.module_to_path(module))] = sha256_hex(data)
        except leanmod.LeanModError as exc:
            basis["closure_error"] = str(exc)
    return basis


def meaning_digest(basis: dict) -> str:
    return "sha256:" + sha256_hex(canonical_json(basis).encode("utf-8"))


def _changed(old: dict, new: dict, target: str) -> str:
    """What differs between the meaning a review recorded and the meaning now, in words."""
    parts = []
    if old.get("semantic_environment") != new.get("semantic_environment"):
        parts.append("semantic environment")
    files_then, files_now = old.get("files") or {}, new.get("files") or {}
    if files_then.get(target) != files_now.get(target):
        parts.append("target")
    if {k: v for k, v in files_then.items() if k != target} != {k: v for k, v in files_now.items() if k != target}:
        parts.append("definitions")
    if (old.get("theorems"), old.get("witnesses")) != (new.get("theorems"), new.get("witnesses")):
        parts.append("theorems")
    if old.get("statement_sha256") != new.get("statement_sha256"):
        parts.append("claim")
    parts += [name for name in BOUND_LISTS                # a review recorded before they were bound never names them
              if f"{name}_sha256" in old and old[f"{name}_sha256"] != new.get(f"{name}_sha256")]
    return ", ".join(parts) or "meaning"


def _binds_lists(review: StoredRecord) -> bool:
    """Whether a fidelity review recorded the digests of the item's limits and assumptions (BOUND_LISTS)."""
    recorded = review.data.get("meaning")
    return isinstance(recorded, dict) and all(f"{name}_sha256" in recorded for name in BOUND_LISTS)


def fidelity(repo: Repo, item: Item) -> Fidelity | None:
    """The fidelity of `item` (as committed on the trusted ref, when it is there). A review counts while the meaning it
    recorded (meaning_digest) is the meaning now. Lean reviews without the semantic environment remain
    readable but require renewal. Older Python reviews bind less and say what (`unbound`): one recorded
    before limits and assumptions were bound counts while the rest of the meaning is the meaning now, until a review
    that binds them is admitted (else it would come back whenever a newer review goes stale through an edited limit);
    one written before reviews recorded a meaning binds only the target file's sha256 (`legacy`), and counts only
    while no admitted fidelity review of the item is newer, whatever made that one stale (else it would come back with
    every edit of what it never bound)."""
    basis_item = repo.trusted_items()[0].get(item.id) or item
    path = question_file(basis_item)
    if path is None:
        return None
    now = meaning(repo, basis_item)
    if now is None:
        return Fidelity("target not admitted")
    reviews = [r for r in repo.reviews(item.id)
               if not r.problems and r.admitted and r.data["kind"] == "fidelity"]
    without_lists = {k: v for k, v in now.items() if k not in {f"{name}_sha256" for name in BOUND_LISTS}}
    unbound: tuple[str, ...] = ()
    matching = [r for r in reviews if r.data.get("meaning_digest") == meaning_digest(now)]
    if not matching and not any(_binds_lists(r) for r in reviews):
        matching = [r for r in reviews if r.data.get("meaning_digest") == meaning_digest(without_lists)]
        unbound = BOUND_LISTS
    legacy = not matching
    if legacy and "semantic_environment" not in now:
        newest = max((_instant(r.data.get("created")) for r in reviews), default=None)
        matching = [r for r in reviews if "meaning_digest" not in r.data
                    and r.data.get("target_sha256") == now["files"][path]
                    and _instant(r.data.get("created")) == newest]
        unbound = ("definitions", "theorems", "claim", *BOUND_LISTS)
    if not matching:
        if not reviews:
            return Fidelity("not reviewed")
        newest = max(reviews, key=review_order)
        recorded = newest.data.get("meaning")
        if "semantic_environment" in now and (not isinstance(recorded, dict)
                                                or "semantic_environment" not in recorded):
            return Fidelity("review stale: semantic environment not bound; review again",
                            unbound=("semantic environment",))
        what = _changed(recorded, now, path) if isinstance(recorded, dict) else "target"
        return Fidelity(f"review stale: {what} changed since review")
    latest = max(matching, key=review_order)
    verdict = latest.data.get("verdict", "")
    label = "faithful" if verdict == "faithful" else f"disputed: {verdict}"
    by = latest.data["author"]
    # A finding counts as acknowledged when any admitted fidelity review of the current meaning acknowledges it.
    acknowledges = sorted({a for r in matching if isinstance(r.data.get("acknowledges"), list)
                           for a in r.data["acknowledges"]})
    return Fidelity(label, latest.path, by, self_review=(by == basis_item.author), created=latest.data.get("created"),
                    acknowledges=tuple(acknowledges), legacy=legacy, unbound=tuple(unbound))
