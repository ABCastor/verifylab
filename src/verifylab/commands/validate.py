"""`vl validate [--incoming BRANCH]`: check records before integration. Reads records directly.

Exit 1 when any error is found, 0 when there are only warnings or nothing. Warnings never hide errors.
With --incoming, the JSON output also lists `trusted_changes` (configuration, evaluators, Lean build files, the
question fields of existing items and trusted inputs of verified receipts that the branch changes) and
`stale_after_merge` (verified receipts the merge would stale, through an input or the item's question), so that an
integrating command can refuse such a branch without explicit acceptance. The items and reviews the branch adds or
changes are validated as they are on the branch.
"""

from __future__ import annotations

import json
import posixpath
import re

from .. import gitref, machine
from ..adapters.lean_comparator import PROJECT_FILES, witness_problems
from ..config import CONFIG_PATH, TRUSTED_REF_KEY
from ..output import emit
from ..records import QUESTION_FIELDS, RecordError, parse_item, question_fields, review_problems
from ..render import PROPOSAL, Context, body_links, prose_edits, split_ref, trust_data, trust_text
from ..status import (UNDETERMINED, answer_mismatch, counted_verdict, fidelity, probe_gaps, probe_hits,
                      receipt_order)
from . import open_repo
from ..repo import CACHE_DIR, EVALUATORS, EVIDENCE, ITEMS, REVIEWS, TARGETS, Repo, input_file

RELATION_FIELDS = ("uses", "cites", "supersedes", "answers", "refutes")
CITE_FIELDS = ("cites", "uses")
STATUS_KINDS = ("result", "conjecture", "intuition")  # kinds whose truth status an explanation relies on
BAD_FOR_CITATION = ("refuted", "retracted")
PROTECTED_PASS = ("verified", "verified-stale", "pending-admission")   # labels under which a protected pass exists
CACHE_NEVER_VERSIONED = (f"{CACHE_DIR}/ is never versioned: exploratory receipts there never count, and a receipt "
                         "committed there is not admitted")


# Process state written into a record's prose. Status is derived from receipts and reviews, never written: a record
# that says "exploratory check only" or "not yet admitted" is wrong the day the check is admitted. One pattern per
# kind of state, matched case-insensitively in `statement`, `limits`, `assumptions` and the body, outside fenced code
# blocks (a quote on purpose); tests/test_cmd_validate.py plants a phrase for each.
PROCESS_STATE = tuple(re.compile(pattern, re.IGNORECASE) for pattern in (
    r"\bexploratory (?:check|run|pass|receipt)s?\b",
    r"\bexplore[- ]mode\b",
    r"\bno protected receipts?\b",
    r"\bnot (?:yet )?verified(?: yet)?\b",
    r"\bnot (?:yet admitted|admitted yet)\b",
    r"\bproposal until\b",
    r"\bsupersedes (?:is )?not set\b",
    r"\bfidelity reviews?\b",
    r"\bcoordinator['’]?s next steps?\b",
    r"\bnotes from the import\b(?: \(to be reviewed\))?",
))
PROSE_FIELDS = ("statement", "limits", "assumptions", "body")
_FENCE = re.compile(r" {0,3}(`{3,}|~{3,})")


def outside_fences(text: str) -> str:
    """`text` without its fenced code blocks (CommonMark: a line of three or more backticks or tildes opens one, a line
    of at least as many of the same character, and nothing else, closes it; an unclosed one runs to the end)."""
    kept, fence = [], None
    for line in text.splitlines():
        if fence is None:
            opening = _FENCE.match(line)
            if opening:
                fence = opening.group(1)
            else:
                kept.append(line)
        elif re.fullmatch(rf" {{0,3}}{re.escape(fence[0])}{{{len(fence)},}}[ \t]*", line):
            fence = None
    return "\n".join(kept)


# An item a branch deletes or breaks: its question fields are empty, so every receipt of it goes stale.
_GONE = parse_item(b'+++\nid = "gone"\nkind = "source"\ntitle = "-"\nauthor = "agent:vl"\ncreated = "-"\n'
                   b'ref = "-"\naccess = "secondary"\n+++\n', "research/items/gone.md")


class Findings:
    def __init__(self):
        self.errors: list[dict[str, str]] = []
        self.warnings: list[dict[str, str]] = []
        self.info: list[dict[str, str]] = []

    def error(self, where: str, message: str) -> None:
        self.errors.append({"where": where, "message": message})

    def warn(self, where: str, message: str) -> None:
        self.warnings.append({"where": where, "message": message})

    def note(self, where: str, message: str) -> None:
        self.info.append({"where": where, "message": message})


def register(sub):
    p = sub.add_parser("validate", help="check references, receipts, reviews, targets and explanations")
    p.add_argument("--incoming", metavar="BRANCH",
                   help="also inspect what BRANCH changes relative to the trusted ref")
    return p


def _references(repo: Repo, ctx: Context, broken: set[str], f: Findings) -> None:
    for item in ctx.items.values():
        refs = [(name, ref) for name in RELATION_FIELDS for ref in getattr(item, name)]
        refs += [("body link", f"{i}@{r}" if r else i) for i, r in body_links(item.body)]
        for name, ref in refs:
            ref_id, rev = split_ref(ref)
            if ref_id in broken:
                continue  # its parse error is already reported
            target = ctx.items.get(ref_id)
            if target is None:
                f.error(item.path, f"{name} '{ref}': no item '{ref_id}' exists")
            elif rev and not target.revision.startswith(rev.lower()):
                f.warn(item.path, f"{name} '{ref}' pins a revision that is not the current one "
                                  f"({target.revision[:12]}); re-read {ref_id} before relying on it")


def _answers(ctx: Context, f: Findings) -> None:
    """An item answers a question with a `[lean]` target only with the same target and theorems, and one with a
    `[python]` evaluator only with the same evaluator and entry: otherwise, once verified, it would make the question
    `answered` by a proof of another statement, or by a pass under another judge."""
    for item in ctx.items.values():
        for ref in item.answers:
            question = ctx.items.get(split_ref(ref)[0])
            mismatch = answer_mismatch(question, item) if question is not None else None
            if mismatch:
                f.error(item.path, f"answers '{ref}': {mismatch}")


def _cycles(ctx: Context, f: Findings) -> None:
    """`refutes` and `answers` among the worktree's items must not form a cycle: once integrated, no status on it
    could be derived. (Status itself follows the trusted ref's relations, so a cycle there is `undetermined`.)"""
    edges: dict[str, list[str]] = {}
    for item in ctx.items.values():
        for name in ("refutes", "answers"):
            for ref in getattr(item, name):
                edges.setdefault(split_ref(ref)[0], []).append(item.id)
    state: dict[str, int] = {}
    reported: set[str] = set()

    def visit(node: str, path: list[str]) -> None:
        state[node] = 1
        for nxt in edges.get(node, []):
            if state.get(nxt) == 1:
                cycle = path[path.index(nxt):] + [nxt]
                if nxt not in reported and nxt in ctx.items:
                    reported.add(nxt)
                    f.error(ctx.items[nxt].path, "refutes/answers relations form a cycle: " + " <- ".join(cycle)
                            + "; no status on it can be derived")
            elif nxt not in state:
                visit(nxt, path + [nxt])
        state[node] = 2

    for node in sorted(edges):
        if node not in state:
            visit(node, [node])


def _target(repo: Repo, ctx: Context, f: Findings) -> None:
    prefix = repo.rel(TARGETS) + "/"
    for item in ctx.items.values():
        if "target" not in item.lean:
            continue
        target = item.lean["target"]
        if not isinstance(target, str) or not target.strip():
            f.error(item.path, "lean.target must be a repo-relative path string")
            continue
        if not target.startswith(prefix) or ".." in target.split("/"):
            f.error(item.path, f"lean.target '{target}' must be under {prefix}")
            continue
        committed = repo.read_trusted(target)
        local = repo.root / target
        if committed is None:
            if not local.is_file():
                f.error(item.path, f"lean.target '{target}' exists neither in the worktree nor on the trusted ref")
            else:
                f.warn(item.path, f"target not admitted on the trusted ref: needs integration and a fidelity review "
                                  f"({target})")
        elif local.is_file() and local.read_bytes() != committed:
            f.warn(item.path, f"{target} differs from the trusted ref; protected checks use the trusted version, "
                              "a change needs integration and a fidelity review")
        theorems = item.lean.get("theorems")
        for problem in witness_problems(item.lean, tuple(theorems) if isinstance(theorems, list) else ()):
            f.error(item.path, problem)


def _process_state(ctx: Context, f: Findings) -> None:
    """A warning per prose field that writes process state (PROCESS_STATE), naming the phrases found."""
    for item in ctx.items.values():
        for name in PROSE_FIELDS:
            value = getattr(item, name)
            texts = value if isinstance(value, tuple) else [value or ""]
            found = list(dict.fromkeys(m.group(0) for text in texts for pattern in PROCESS_STATE
                                       for m in pattern.finditer(outside_fences(text))))
            if found:
                phrases = ", ".join(f"'{phrase}'" for phrase in found)
                f.warn(item.path, f"{name}: process state written into the record ({phrases}): status is derived from "
                                  "receipts and reviews, and this goes stale; remove it (quote it in a fenced code "
                                  "block if it is meant as a quote)")


def _records(repo: Repo, ctx: Context, broken: set[str], f: Findings) -> tuple[int, int]:
    counts = {}
    for folder, load, id_key in ((EVIDENCE, repo.receipts, "receipt_id"), (REVIEWS, repo.reviews, "review_id")):
        n = 0
        for name in repo.record_folders(folder):     # in the worktree or on the trusted ref
            if name not in ctx.items and name not in broken:
                f.error(repo.rel(folder, name), f"records filed under '{name}', which is not an item")
            for record in load(name):
                n += 1
                if record.problems:
                    f.error(record.path, "; ".join(record.problems))
                    continue
                expected = f"{record.data[id_key][:16]}.json"
                if record.path.rsplit("/", 1)[-1] != expected:
                    f.warn(record.path, f"file name does not match its {id_key} (expected {expected})")
        counts[folder] = n
    return counts[EVIDENCE], counts[REVIEWS]


def _cache(repo: Repo, f: Findings) -> None:
    """Every file git tracks under the cache, in the index or on the trusted ref, is an error."""
    where: dict[str, list[str]] = {}
    for path in gitref.tracked_files(repo.root, CACHE_DIR):
        where.setdefault(path, []).append("tracked in the index")
    for path in repo.trusted_paths(CACHE_DIR):
        where.setdefault(path, []).append(f"committed on the trusted ref '{repo.config.trusted_ref}'")
    for path, places in sorted(where.items()):
        f.error(path, f"{' and '.join(places)}; {CACHE_NEVER_VERSIONED}: git rm --cached it")


def _receipt_data(ctx: Context, item_id: str, path: str | None) -> dict:
    record = next((r for r in ctx.repo.receipts(item_id) if r.path == path and not r.problems), None)
    return record.data if record is not None else {}


def _witnesses(item) -> set[str]:
    raw = item.lean.get("witnesses") if item.lean else None
    return {w for w in raw if isinstance(w, str)} if isinstance(raw, list) else set()


def _witness_requirement(item, receipt: dict, witnesses: set[str], f: Findings) -> None:
    """A target theorem with Prop hypotheses needs a non-vacuity witness: another theorem of the target, listed in
    `[lean] witnesses`, that instantiates them. Whether it really does is for the fidelity review to judge."""
    probes = (receipt.get("checked") or {}).get("probes")
    if not isinstance(probes, dict) or witnesses:
        return
    needing = [f"{name} ({entry['prop_hypotheses']})" for name, entry in sorted(probes.items())
               if isinstance(entry, dict) and isinstance(entry.get("prop_hypotheses"), int)
               and entry["prop_hypotheses"] > 0]
    if needing:
        f.warn(item.path, f"target theorems with Prop hypotheses: {', '.join(needing)}; add a non-vacuity witness, "
                          "a theorem of the target that instantiates them, to [lean] theorems and [lean] witnesses")


def _undeclared_hypotheses(ctx: Context, f: Findings) -> None:
    """An item whose target theorems have Prop hypotheses, by the probes of its newest admitted receipt that has them
    (witnesses excepted: they instantiate the hypotheses), while its `assumptions` is empty: the record hides what the
    result rests on."""
    for item in ctx.items.values():
        if item.assumptions:
            continue
        probed = [r for r in ctx.repo.receipts(item.id) if not r.problems and r.admitted
                  and isinstance((r.data.get("checked") or {}).get("probes"), dict)]
        newest = max(probed, key=receipt_order, default=None)
        if newest is None:
            continue
        witnesses = _witnesses(item)
        counts = [(name, entry["prop_hypotheses"]) for name, entry in sorted(newest.data["checked"]["probes"].items())
                  if name not in witnesses and isinstance(entry, dict) and type(entry.get("prop_hypotheses")) is int
                  and entry["prop_hypotheses"] > 0]
        if counts:
            parts = [f"{n} {'hypothesis' if n == 1 else 'hypotheses'} in {name}" for name, n in counts]
            f.warn(item.path, f"the target has {' and '.join(parts)}; state them in assumptions, in plain words "
                              f"(by the probes of {newest.path})")


def _probes(ctx: Context, item, status, f: Findings) -> None:
    """Statement-probe findings of the receipt the status comes from: vacuity is the status itself; a target
    theorem closed by automation alone needs a fidelity review of the current target that acknowledges it."""
    receipt = _receipt_data(ctx, item.id, status.receipt)
    if status.label == "vacuous":
        f.warn(item.path, "vacuous: " + "; ".join(n for n in status.notes if "derives False" in n)
               + ". Not verified: the target needs new hypotheses (record a fidelity review with --verdict vacuous)")
    if status.label not in PROTECTED_PASS:
        return
    gaps = probe_gaps(receipt)
    if gaps:
        f.warn(item.path, f"probes incomplete ({'; '.join(gaps)}): vacuity and triviality of the target were not "
                          "ruled out; run vl check --protected again")
    witnesses = _witnesses(item)
    _witness_requirement(item, receipt, witnesses, f)
    trivial = [(name, tactic) for name, tactic in probe_hits(receipt, "trivial_by") if name not in witnesses]
    if not trivial:          # a witness is expected to be easy: an instance, often closed by `decide`
        return
    state = fidelity(ctx.repo, item)
    if state is not None and "trivial" in state.acknowledges:
        return
    names = ", ".join(f"{name} by `{tactic}`" for name, tactic in trivial)
    f.warn(item.path, f"automation alone closes {names}: if that is intended, record a fidelity review of the "
                      "current target with --acknowledge trivial; otherwise the target is too weak")


def _unreviewed_prose(ctx: Context, item, status, f: Findings) -> None:
    """A verified item whose worktree statement, limits or assumptions differ from the item as committed on the
    trusted ref: status and fidelity hold for the committed text, which the card shows, never for the edit."""
    if status.label != "verified":
        return
    edited = prose_edits(ctx.repo.trusted_items()[0].get(item.id), item)
    if edited:
        f.warn(item.path, f"{PROPOSAL}: its {', '.join(edited)} differ from the item as committed on the trusted ref "
                          f"'{ctx.repo.config.trusted_ref}'; status and fidelity hold for the committed text, which "
                          "the card shows; integrate the edit and record a new fidelity review, or revert it")


def _statuses(ctx: Context, f: Findings) -> None:
    for item in ctx.items.values():
        status = ctx.status(item.id)
        if status.label == UNDETERMINED and not any(e["where"] == item.path and "form a cycle" in e["message"]
                                                    for e in f.errors):
            f.error(item.path, "; ".join(status.notes))
        _probes(ctx, item, status, f)
        _unreviewed_prose(ctx, item, status, f)
        if status.label in PROTECTED_PASS:
            state = fidelity(ctx.repo, item)
            if state is not None and state.label != "faithful":
                f.warn(item.path, f"{status.label} against a target whose fidelity is '{state.label}': record "
                                  "vl review --kind fidelity after reading the admitted target")
            elif state is not None and state.legacy:
                f.warn(item.path, "the faithful fidelity review predates meaning binding: it binds the target file "
                                  "only, not the definitions it imports, the theorems, the claim, the limits or the "
                                  "assumptions; review again")
            elif state is not None and state.unbound:
                f.warn(item.path, f"the faithful fidelity review predates the binding of {' and '.join(state.unbound)}: "
                                  "removing a limit or an assumption would not make it stale; review again")
            elif state is not None and state.self_review:
                f.warn(item.path, f"the faithful fidelity review is by the item's author ({state.by}); "
                                  "an independent review is stronger")
        if status.label == "verified-stale":
            stale = [n for n in status.notes if "inputs changed" in n]
            f.warn(item.path, "admitted receipt has stale inputs; re-run vl check --protected: " + " | ".join(stale))
        if item.kind != "explanation":
            continue
        seen = set()
        for name in CITE_FIELDS:
            for ref in getattr(item, name):
                ref_id = split_ref(ref)[0]
                if ref_id in seen or ref_id not in ctx.items:
                    continue
                seen.add(ref_id)
                label = ctx.label(ref_id)
                if label in BAD_FOR_CITATION:
                    f.error(item.path, f"explanation {name} '{ref_id}', which is {label}")
                elif ctx.items[ref_id].kind in STATUS_KINDS and label != "verified":
                    f.warn(item.path, f"explanation {name} '{ref_id}', which is not verified (status: {label})")


def _trusted_reason(repo: Repo, path: str) -> str | None:
    """Why `path` is trusted by every protected check, or None when it is not such a file."""
    if path == CONFIG_PATH:
        return "the project configuration (permitted axioms, external kernels, Lean project and roots)"
    if path.startswith(repo.rel(EVALUATORS) + "/"):
        return "an evaluator, which judges python results"
    project = posixpath.normpath(repo.config.lean.project.strip() or ".")
    if path in {name if project == "." else f"{project}/{name}" for name in PROJECT_FILES}:
        return "a Lean build file (lakefile, manifest or toolchain)"
    return None


def _verified_receipts(repo: Repo, ctx: Context):
    """Admitted protected passes whose inputs are still fresh: what a merge could silently stale."""
    for item_id in sorted(ctx.items):
        for record in repo.receipts(item_id):
            data = record.data
            if (not record.problems and record.admitted and data["assurance"] == "protected"
                    and counted_verdict(repo, record) == "pass"
                    and not repo.stale_inputs(data, ctx.items[item_id], admitted=True)):
                yield item_id, record


def _stale_after_merge(repo: Repo, ctx: Context, branch: str, changed: dict[str, str], f: Findings,
                       trusted: dict[str, dict[str, str]]) -> list[dict]:
    """Verified receipts with an input the branch changes, compared digest by digest with the branch's version."""
    rows = []
    for item_id, record in _verified_receipts(repo, ctx):
        inputs = record.data["inputs"]
        hit = []
        for kind in ("trusted_files", "files"):
            for p, digest in inputs.get(kind, {}).items():
                path = input_file(p)
                if path not in changed or repo.input_digest(p, branch) == digest:
                    continue
                hit.append(p + (" (trusted)" if kind == "trusted_files" else ""))
                if kind == "trusted_files":
                    trusted.setdefault(path, {"path": path, "change": changed[path],
                                              "why": f"a trusted input of the verified receipt {record.path}"})
        item_path = repo.rel(ITEMS, f"{item_id}.md")
        if item_path in changed and repo.question_stale(record.data, _branch_item(repo, branch, item_path)):
            hit.append(f"{item_path}#question")
        if hit:
            rows.append({"receipt": record.path, "item": item_id, "inputs": hit})
            f.warn(record.path, f"verified receipt of '{item_id}' goes stale if {branch} is merged: it changes "
                                f"{', '.join(hit)}; re-run vl check --protected after merging")
    return rows


def _branch_item(repo: Repo, branch: str, path: str):
    """The item at `path` as it is on `branch`, or a placeholder whose question is empty when it is gone or broken."""
    data = gitref.show(repo.root, branch, path)
    try:
        return parse_item(data, path) if data is not None else _GONE
    except RecordError:
        return _GONE


def _question_change(repo: Repo, path: str, branch: str) -> list[str]:
    """The question fields (records.QUESTION_FIELDS) of an existing item that `branch` changes, as `[table] key`."""
    versions = []
    for data in (repo.read_trusted(path), gitref.show(repo.root, branch, path)):
        try:
            versions.append(question_fields(parse_item(data, path)) if data is not None else None)
        except RecordError:
            versions.append(None)
    old, new = versions
    if old is None or new is None:
        return []
    return [f"[{table}] {key}" for table, keys in QUESTION_FIELDS.items() for key in keys
            if old[table].get(key) != new[table].get(key)]


REVIEW_EFFECTS = {"retraction": "merging it retracts the item",
                  "correction": "merging it puts a correction at the top of the item's card",
                  "fidelity": "merging it decides whether the target means what the question asks"}


def _added_review(repo: Repo, branch: str, path: str) -> tuple[bool, str]:
    """(well-formed, what a review the branch adds would do once merged), naming its kind, verdict and author."""
    data = gitref.show(repo.root, branch, path)
    try:
        review = json.loads(data) if data is not None else None
    except (ValueError, RecursionError):
        review = None
    problems = review_problems(review) if isinstance(review, dict) else ["not a JSON object"]
    if problems:
        return False, f"a malformed review is added on {branch} ({'; '.join(problems)}): merging would admit it"
    verdict = f" with verdict '{review['verdict']}'" if isinstance(review.get("verdict"), str) else ""
    effect = REVIEW_EFFECTS.get(review["kind"], "merging admits it")
    return True, (f"a {review['kind']} review{verdict} by {review['author']} is added on {branch}: {effect}; "
                  "check who wrote it and why before integrating")


def _incoming_items(repo: Repo, branch: str, paths: list[str], f: Findings) -> None:
    """The items `branch` adds or changes, validated as they are on `branch`: each must parse, and every item it
    relates to or links must exist on `branch`."""
    folder = repo.rel(ITEMS)
    there = {posixpath.basename(p)[:-3] for p in gitref.ls_files(repo.root, branch, folder)
             if posixpath.dirname(p) == folder and p.endswith(".md")}
    for path in paths:
        try:
            item = parse_item(gitref.show(repo.root, branch, path) or b"", path)
        except RecordError as exc:
            f.error(path, f"on {branch}: {exc}")
            continue
        refs = [(name, ref) for name in RELATION_FIELDS for ref in getattr(item, name)]
        refs += [("body link", f"{i}@{r}" if r else i) for i, r in body_links(item.body)]
        for name, ref in refs:
            if split_ref(ref)[0] not in there:
                f.error(path, f"on {branch}: {name} '{ref}': no item '{split_ref(ref)[0]}' exists on {branch}")
        for ref in item.answers:
            question = _branch_question(repo, branch, split_ref(ref)[0]) if split_ref(ref)[0] in there else None
            mismatch = answer_mismatch(question, item) if question is not None else None
            if mismatch:
                f.error(path, f"on {branch}: answers '{ref}': {mismatch}")


def _branch_question(repo: Repo, branch: str, item_id: str):
    """The item `item_id` as it is on `branch`, or None when it does not parse there (reported on its own)."""
    path = repo.rel(ITEMS, f"{item_id}.md")
    try:
        return parse_item(gitref.show(repo.root, branch, path) or b"", path)
    except RecordError:
        return None


def _incoming(repo: Repo, ctx: Context, branch: str, f: Findings) -> tuple[list[dict], list[dict]]:
    """Classify what `branch` changes. Returns the trusted-input changes and the receipts the merge would stale."""
    trusted_ref = repo.trusted_commit
    evidence = repo.rel(EVIDENCE) + "/"
    reviews = repo.rel(REVIEWS) + "/"
    targets = repo.rel(TARGETS) + "/"
    items = repo.rel(ITEMS) + "/"
    cache = CACHE_DIR + "/"
    added_items, changed_items, added_reviews, other = [], [], [], []
    trusted: dict[str, dict[str, str]] = {}
    changed: dict[str, str] = {}
    for status, path in gitref.changed_files(repo.root, trusted_ref, branch):
        code = status[:1]
        verb = {"A": "added", "D": "deleted"}.get(code, "modified")
        changed[path] = verb
        name = path.rsplit("/", 1)[-1]
        reason = _trusted_reason(repo, path)
        if path.startswith(cache):
            if code != "D":
                f.error(path, f"{verb} on {branch}: {CACHE_NEVER_VERSIONED}")
        elif path.startswith(evidence) and name != ".gitkeep":
            if code in ("A", "M", "T"):
                f.error(path, f"{verb} on {branch}: receipts are written only by the integrator's protected check")
            elif code == "D":
                f.error(path, f"deleted on {branch}: records are immutable")
        elif path.startswith(reviews) and name != ".gitkeep":
            if code in ("M", "T", "D"):
                f.error(path, f"{verb} on {branch}: records are immutable")
            elif code == "A":
                added_reviews.append(path)
                ok, message = _added_review(repo, branch, path)
                (f.warn if ok else f.error)(path, message)
        elif path.startswith(targets) and name != ".gitkeep":
            f.warn(path, f"target {verb} on {branch}: after merging, read the admitted target and record "
                         "a fidelity review before relying on any result checked against it")
        elif reason:
            trusted[path] = {"path": path, "change": verb, "why": reason}
            f.warn(path, f"trusted input {verb} on {branch}: {reason}; after merging every protected check "
                         "relies on it, so read the diff before integrating")
        elif path.startswith(items) and path.endswith(".md") and code != "D":
            (added_items if code == "A" else changed_items).append(path)
            fields = _question_change(repo, path, branch) if code != "A" else []
            if fields:
                why = f"the question of an existing item ({', '.join(fields)})"
                trusted[path] = {"path": path, "change": verb, "why": why}
                f.warn(path, f"{branch} changes the question of this item ({', '.join(fields)}): its receipts answer "
                             "the old question and go stale after merging, and the new one needs a fidelity review")
        elif path.startswith(items) and path.endswith(".md"):
            f.warn(path, f"item deleted on {branch}; references to it will dangle")
        elif code == "D":
            f.warn(path, f"deleted on {branch}: merging removes it from the trusted ref")
        else:
            other.append(path)
    _incoming_items(repo, branch, added_items + changed_items, f)
    stale = _stale_after_merge(repo, ctx, branch, changed, f, trusted)
    for label, paths in (("items added", added_items), ("items modified", changed_items),
                         ("reviews added", added_reviews), ("code and other files changed", other)):
        if paths:
            f.note(branch, f"{label}: {', '.join(paths)}")
    return sorted(trusted.values(), key=lambda row: row["path"]), stale


def run(args) -> int:
    repo = open_repo(args, warn=False)
    f = Findings()
    if not gitref.ref_exists(repo.root, repo.config.trusted_ref):
        f.error(f"git config {TRUSTED_REF_KEY}", f"trusted ref '{repo.config.trusted_ref}' (from "
                f"{repo.config.trusted_ref_source}) does not resolve to a commit")
    for where, message in repo.config.warnings:
        f.warn(where, message)
    trust = trust_data(repo)
    if repo.config.trusted_ref_source == "--trusted-ref":
        f.warn("--trusted-ref", trust_text(trust))
    legacy_tools = sorted(set(repo.config.tools) & set(machine.TOOL_NAMES))
    if legacy_tools:
        f.warn(CONFIG_PATH, f"[tools] {', '.join(legacy_tools)}: tool paths in the project configuration are deprecated "
                            f"(any branch can propose them); protected checks read {machine.config_path()} and the "
                            "tools store (`vl init --tools`) first")
    items, problems = repo.load_items()
    for problem in problems:
        where, _, message = problem.partition(": ")
        f.error(where, message or problem)
    files = {p.stem for p in repo.item_files()}
    broken = files - set(items)
    ctx = Context(repo, items)
    _references(repo, ctx, broken, f)
    _answers(ctx, f)
    _cycles(ctx, f)
    _target(repo, ctx, f)
    _process_state(ctx, f)
    n_receipts, n_reviews = _records(repo, ctx, broken, f)
    _cache(repo, f)
    _statuses(ctx, f)
    _undeclared_hypotheses(ctx, f)
    incoming = {}
    if args.incoming and repo.trusted_commit:
        trusted, stale = _incoming(repo, ctx, args.incoming, f)
        incoming = {"trusted_changes": trusted, "stale_after_merge": stale}

    counts = {"items": len(items), "item_files": len(files), "receipts": n_receipts, "reviews": n_reviews}
    lines = [f"ERROR {e['where']}: {e['message']}" for e in f.errors]
    lines += [f"WARNING {w['where']}: {w['message']}" for w in f.warnings]
    lines += [f"incoming {i['where']}: {i['message']}" for i in f.info]
    lines.append(f"{len(f.errors)} errors, {len(f.warnings)} warnings "
                 f"({counts['item_files']} item files, {n_receipts} receipts, {n_reviews} reviews checked"
                 + (f"; incoming {args.incoming} vs {repo.config.trusted_ref}" if args.incoming else "") + ")")
    emit(args, "\n".join(lines), {"ok": not f.errors, "errors": f.errors, "warnings": f.warnings,
                                  "incoming": f.info, **incoming, "counts": counts, "trust": trust})
    return 1 if f.errors else 0
