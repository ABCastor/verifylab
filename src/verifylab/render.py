"""Result cards, briefs and impact views of `vl show`; `Context` (items and memoised statuses) also serves `vl find`
and `vl validate`.

Everything here reads records directly (items, receipts, reviews) and derives status with
`status.derive`; nothing comes from the index. The order of a card is the order that prevents
misuse: corrections and retractions first, then limits and assumptions, then the statement (these three as
committed on the trusted ref, with a differing worktree text set apart as an unreviewed proposal), status, proof
(kernels, axioms, statement probes) and meaning (fidelity) as two separate facts, evidence, reviews, relations and
body.
"""

from __future__ import annotations

import json
import re
from typing import Any

from .records import Item
from .repo import Repo, StoredRecord
from .status import UNDETERMINED, Status, derive, fidelity, receipt_order, review_order, meaning, meaning_digest

LINK_RE = re.compile(r"\[\[([a-z0-9][a-z0-9-]{1,63})(?:@([0-9a-fA-F]{4,40}))?\]\]")
RELATIONS = (
    ("uses", "used by"),
    ("cites", "cited by"),
    ("answers", "answered by"),
    ("refutes", "refuted by"),
    ("supersedes", "superseded by"),
)
CORRECTION_KINDS = ("retraction", "correction")
KIND_NOTES = {
    "conjecture": "a conjecture is not a result: do not use it as proved",
    "intuition": "an intuition is an attributed lead, never evidence",
    "explanation": "an explanation is prose: what it claims is only as good as the items it cites",
    "source": "a source is what someone else wrote; citing it does not verify its claims",
}


def split_ref(ref: str) -> tuple[str, str | None]:
    """'id' or 'id@rev' -> (id, rev or None)."""
    item_id, _, rev = ref.partition("@")
    return item_id, (rev or None)


def body_links(body: str) -> list[tuple[str, str | None]]:
    return [(m.group(1), m.group(2)) for m in LINK_RE.finditer(body)]


def short(rev: str | None) -> str:
    return (rev or "?")[:12]


class Context:
    """All items plus memoised derived statuses for one command run. Relations are those of the worktree's items;
    `admitted` holds the ones the trusted ref's items also state, the only ones that change a status."""

    def __init__(self, repo: Repo, items: dict[str, Item]):
        self.repo = repo
        self.items = items
        self._status: dict[str, Status] = {}
        self.reverse: dict[str, dict[str, list[str]]] = {name: {} for name, _ in RELATIONS}
        self.admitted: set[tuple[str, str, str]] = {
            (name, item.id, split_ref(ref)[0]) for item in repo.trusted_items()[0].values()
            for name, _ in RELATIONS for ref in getattr(item, name)}
        self.linked_from: dict[str, list[str]] = {}
        for item in items.values():
            for name, _ in RELATIONS:
                for ref in getattr(item, name):
                    self.reverse[name].setdefault(split_ref(ref)[0], []).append(item.id)
            for target, _ in body_links(item.body):
                if item.id not in self.linked_from.setdefault(target, []):
                    self.linked_from[target].append(item.id)

    @classmethod
    def load(cls, repo: Repo) -> "Context":
        items, _ = repo.load_items()
        return cls(repo, items)

    def status(self, item_id: str) -> Status | None:
        if item_id not in self.items:
            return None
        if item_id not in self._status:
            self._status[item_id] = derive(self.repo, self.items[item_id], self.items)
        return self._status[item_id]

    def label(self, item_id: str) -> str:
        status = self.status(item_id)
        return status.label if status else "missing"


# Card data ------------------------------------------------------------------------------------

CONFIGURED = "git config vl.trustedRef"


def trust_data(repo: Repo) -> dict[str, Any]:
    """The trust anchor every derived status of this command depends on: the trusted ref, where its name came from
    and the commit it resolved to. `configured` is False for an anchor the caller chose (--trusted-ref) or the
    default `main` of a repository without `git config vl.trustedRef`."""
    config = repo.config
    return {"ref": config.trusted_ref, "source": config.trusted_ref_source, "commit": repo.trusted_commit,
            "configured": config.trusted_ref_source == CONFIGURED}


def trust_text(trust: dict[str, Any]) -> str | None:
    """One line naming a trust anchor that is not the repository's configured one; None for the configured one."""
    if trust["configured"]:
        return None
    commit = (trust["commit"] or "none")[:12]
    return (f"trusted ref {trust['ref']} at {commit} from {trust['source']}, not the repository's {CONFIGURED}: "
            "statuses here hold for that ref only")


def _review_entry(record: StoredRecord, current_rev: str) -> dict[str, Any]:
    data = record.data
    rev = data.get("item_revision")
    return {
        "path": record.path,
        "kind": data.get("kind"),
        "author": data.get("author"),
        "item_revision": rev,
        "at_current_revision": rev == current_rev,
        "verdict": data.get("verdict"),
        "compare_with": data.get("compare_with"),
        "text": data.get("text", ""),
        "created": data.get("created"),
        "admitted": record.admitted,
        "problems": list(record.problems),
    }


def _receipt_entry(repo: Repo, record: StoredRecord, current: Item) -> dict[str, Any]:
    data = record.data
    rev = data.get("item_revision")
    current_rev = current.revision
    stale = repo.stale_inputs(data, current, admitted=record.admitted) if not record.problems else []
    return {
        "path": record.path,
        "adapter": data.get("adapter"),
        "assurance": data.get("assurance"),
        "verdict": data.get("verdict"),
        "reasons": data.get("reasons", []),
        "admitted": record.admitted,
        "stale_inputs": stale,
        "item_revision": rev,
        "at_current_revision": rev == current_rev,
        "finished_at": data.get("finished_at"),
        "checked": data.get("checked", {}),
        "problems": list(record.problems),
    }


_PHASES = ("prepare", "preflight", "isolation_probe", "prebuild_challenge", "comparator_start", "build_challenge",
           "export_challenge", "build_solution", "export_solution_and_compare")
_LAST_PHASES = ("kernel_lean", "probes")
LINTS_SHOWN = 10


def _timing(receipts: list[StoredRecord]) -> dict[str, Any] | None:
    """Where the newest Lean check spent its time (receipt `extra`), or None when it did not record phases."""
    lean = [r for r in receipts if not r.problems and r.data.get("adapter") == "lean-comparator"]
    extra = lean[0].data.get("extra") if lean else None
    if not isinstance(extra, dict) or not isinstance(extra.get("phases"), dict):
        return None

    def order(name: str) -> tuple[int, str]:
        if name in _PHASES:
            return (_PHASES.index(name), "")
        return (len(_PHASES) + 1 + _LAST_PHASES.index(name), "") if name in _LAST_PHASES else (len(_PHASES), name)
    modules = extra.get("modules") or []
    return {"receipt": lean[0].path, "seconds": extra.get("seconds"),
            "phases": {k: extra["phases"][k] for k in sorted(extra["phases"], key=order)},
            "slowest_module": modules[0] if modules else None, "memory_peak_bytes": extra.get("memory_peak_bytes")}


def timing_text(timing: dict[str, Any]) -> str:
    parts = [f"{name} {seconds:g} s" for name, seconds in timing["phases"].items()]
    line = f"timing of the newest Lean receipt ({timing['receipt'].rsplit('/', 1)[-1]}): "
    line += (f"{timing['seconds']:g} s total; " if timing["seconds"] is not None else "") + ", ".join(parts)
    if timing["slowest_module"]:
        line += f"; slowest module {timing['slowest_module']['module']} {timing['slowest_module']['ms']} ms"
    if timing["memory_peak_bytes"]:
        peak = timing["memory_peak_bytes"]
        line += f"; peak memory {peak / 2**30:.1f} GiB" if peak >= 2**30 else f"; peak memory {peak / 2**20:.0f} MiB"
    return line


def _fidelity_entry(repo: Repo, item: Item) -> dict[str, Any] | None:
    state = fidelity(repo, item)
    if state is None:
        return None
    return {"label": state.label, "review": state.review, "by": state.by, "self_review": state.self_review,
            "created": state.created, "acknowledges": list(state.acknowledges), "legacy": state.legacy,
            "unbound": list(state.unbound)}


def _proof_entry(status: Status, receipts: list[StoredRecord]) -> dict[str, Any] | None:
    """What the proof side rests on: the receipt the status comes from (else the newest one), its assurance,
    kernels, axioms, statement probes and lints. Meaning is separate (fidelity)."""
    valid = [r for r in receipts if not r.problems]
    record = next((r for r in valid if r.path == status.receipt), valid[0] if valid else None)
    if record is None:
        return None
    data = record.data
    checked = data.get("checked") if isinstance(data.get("checked"), dict) else {}
    return {"receipt": record.path, "adapter": data.get("adapter"), "assurance": data.get("assurance"),
            "verdict": data.get("verdict"), "kernels": checked.get("kernels"),
            "permitted_axioms": checked.get("permitted_axioms"), "probes": checked.get("probes"),
            "probe_run": checked.get("probe_run"), "lints": checked.get("lints")}


def fidelity_text(entry: dict[str, Any] | None) -> str | None:
    if entry is None:
        return None
    text = entry["label"]
    if entry["by"]:
        text += f" (by {entry['by']}" + (", self-review: not independent)" if entry["self_review"] else ")")
    if entry.get("legacy"):
        text += (" [older review: it binds the target file only, not the definitions it imports, the theorems, the "
                 "claim, the limits or the assumptions; review again]")
    elif entry.get("unbound"):
        text += f" [older review: it does not bind the {' or the '.join(entry['unbound'])}; review again]"
    return text


def _plain(table: dict[str, Any]) -> dict[str, Any]:
    """TOML tables may hold dates; keep the card JSON-serialisable."""
    return json.loads(json.dumps(table, default=str))


# The prose a fidelity review binds (status.meaning). A card shows it as committed on the trusted ref, where status
# and fidelity are derived; a shown revision whose prose differs is set apart as unreviewed (`proposed`).
BOUND_PROSE = ("statement", "limits", "assumptions")
PROPOSAL = "uncommitted edit, not reviewed"
OLDER_REVISION = "the revision asked for, not reviewed"


def _prose(item: Item) -> dict[str, Any]:
    return {"statement": item.statement, "limits": list(item.limits), "assumptions": list(item.assumptions)}


def prose_edits(trusted: Item | None, item: Item) -> list[str]:
    """The fields of BOUND_PROSE in which `item` differs from the same item as committed on the trusted ref
    (`trusted`; None when it is not there, and then there is nothing to differ from)."""
    if trusted is None:
        return []
    then, now = _prose(trusted), _prose(item)
    return [name for name in BOUND_PROSE if then[name] != now[name]]


def card_data(ctx: Context, item: Item, note: str | None = None) -> dict[str, Any]:
    """The full card as data. `note` is a loud message about which revision is shown. The statement, limits and
    assumptions are those of the item as committed on the trusted ref, the text its status and fidelity hold for
    (the shown item's while it is not there); where the shown revision says otherwise, `proposed` holds its text,
    labelled as not reviewed."""
    repo = ctx.repo
    status = ctx.status(item.id) or Status(UNDETERMINED, ("item not among the parsed items",))
    # "current" is the item's revision now, even when an older blob of it is being shown.
    current_rev = ctx.items[item.id].revision if item.id in ctx.items else item.revision
    trusted = repo.trusted_items()[0].get(item.id)
    basis = trusted or item
    edited = prose_edits(trusted, item)
    proposed = None
    if edited:
        shown = _prose(item)
        proposed = {"label": PROPOSAL if item.revision == current_rev else OLDER_REVISION,
                    "revision": item.revision, "fields": edited, **{name: shown[name] for name in edited}}
    reviews = sorted(repo.reviews(item.id), key=review_order)
    entries = [_review_entry(r, current_rev) for r in reviews]
    receipts = sorted(repo.receipts(item.id), key=receipt_order, reverse=True)

    relations: dict[str, list[dict[str, Any]]] = {}
    for forward, backward in RELATIONS:
        out = []
        for ref in getattr(item, forward):
            ref_id, rev = split_ref(ref)
            entry = {"id": ref_id, "status": ctx.label(ref_id), "admitted": (forward, item.id, ref_id) in ctx.admitted}
            if rev:
                entry["pinned_revision"] = rev
                current = ctx.items.get(ref_id)
                entry["pin_is_current"] = bool(current and current.revision.startswith(rev.lower()))
            out.append(entry)
        relations[forward] = out
        relations[backward] = [{"id": i, "status": ctx.label(i), "admitted": (forward, i, item.id) in ctx.admitted}
                               for i in ctx.reverse[forward].get(item.id, [])]
    relations["links in body"] = [
        {"id": i, "status": ctx.label(i), **({"pinned_revision": r} if r else {})} for i, r in body_links(item.body)
    ]
    relations["linked from"] = [{"id": i, "status": ctx.label(i)} for i in ctx.linked_from.get(item.id, [])]

    current_meaning = meaning(repo, basis)
    return {
        "meaning": current_meaning,
        "meaning_digest": meaning_digest(current_meaning) if current_meaning is not None else None,
        "id": item.id,
        "title": item.title,
        "kind": item.kind,
        "claim": item.claim,
        "author": item.author,
        "recorded_by": item.recorded_by,
        "created": item.created,
        "path": item.path,
        "revision": item.revision,
        "revision_note": note,
        "kind_note": KIND_NOTES.get(item.kind),
        "status": {"label": status.label, "notes": list(status.notes), "receipt": status.receipt},
        "trust": trust_data(repo),
        "fidelity": _fidelity_entry(repo, ctx.items.get(item.id, item)),
        "proof": _proof_entry(status, receipts),
        "corrections": [e for e in entries if e["kind"] in CORRECTION_KINDS],
        "refuted_by": relations["refuted by"],
        "superseded_by": relations["superseded by"],
        "limits": list(basis.limits),
        "assumptions": list(basis.assumptions),
        "statement": basis.statement,
        "proposed": proposed,
        "ref": item.ref,
        "access": item.access,
        "lean": _plain(item.lean),
        "python": _plain(item.python),
        "receipts": [_receipt_entry(repo, r, ctx.items.get(item.id, item)) for r in receipts],
        "timing": _timing(receipts),
        "reviews": [e for e in entries if e["kind"] not in CORRECTION_KINDS],
        "relations": relations,
        "tags": list(item.tags),
        "body": item.body,
    }


# Text rendering -------------------------------------------------------------------------------


def _rev_tag(entry: dict[str, Any]) -> str:
    where = "current" if entry["at_current_revision"] else "item text edited since"
    return f"on rev {short(entry['item_revision'])} ({where})"


def _review_line(e: dict[str, Any]) -> str:
    if e["problems"]:   # a rejected record is shown by its path and problems: its fields may have any type
        return (f"{e['path'].rsplit('/', 1)[-1]}: MALFORMED review, ignored ({'; '.join(e['problems'])})"
                + ("" if e["admitted"] else ", NOT admitted"))
    parts = [f"{e['kind']} by {e['author']}", _rev_tag(e), "admitted" if e["admitted"] else "NOT admitted"]
    if e.get("verdict"):
        parts.append(f"verdict={e['verdict']}")
    if e.get("compare_with"):
        parts.append(f"compared with {e['compare_with']}")
    line = " · ".join(parts) + f": {e['text'].strip()}"
    if e["problems"]:
        line += f"  [MALFORMED, ignored: {'; '.join(e['problems'])}]"
    return line


def _receipt_line(e: dict[str, Any]) -> str:
    name = e["path"].rsplit("/", 1)[-1]
    if e["problems"]:
        return f"{name}: REJECTED ({'; '.join(e['problems'])})"
    parts = [str(e["adapter"]), str(e["assurance"]), str(e["verdict"]),
             "admitted" if e["admitted"] else "NOT admitted (not on the trusted ref)",
             ("STALE inputs: " + ", ".join(e["stale_inputs"])) if e["stale_inputs"] else "inputs fresh",
             _rev_tag(e)]
    line = f"{name}: " + " · ".join(parts)
    if e["verdict"] != "pass" and e["reasons"]:
        line += f" — {'; '.join(map(str, e['reasons']))}"
    return line


def _checked_line(e: dict[str, Any], limit: int = 240) -> str | None:
    if not e["checked"] or e["problems"]:
        return None
    text = json.dumps(e["checked"], ensure_ascii=False, sort_keys=True)
    if len(text) > limit:
        text = text[:limit] + f"… [checked cut at {limit} chars; full list in {e['path']}]"
    return f"checked: {text}"


def _table_line(name: str, table: dict[str, Any]) -> str | None:
    if not table:
        return None
    return f"{name}: " + "; ".join(f"{k}={json.dumps(v, ensure_ascii=False)}" for k, v in sorted(table.items()))


def _statement_lines(d: dict[str, Any]) -> list[str]:
    lines = []
    if d["statement"]:
        lines.append(d["statement"].strip())
    if d["ref"]:
        lines.append(f"source: {d['ref']} (access: {d['access']})")
    for name in ("lean", "python"):
        line = _table_line(name, d[name])
        if line:
            lines.append(line)
    return lines or ["(no statement)"]


def _proposed_lines(d: dict[str, Any]) -> list[str]:
    """The prose of the shown revision where it differs from the trusted text the card shows, labelled."""
    p = d.get("proposed")
    if not p:
        return []
    lines = [f"{p['label']}: revision {short(p['revision'])} differs from the item as committed on the trusted ref "
             f"in its {', '.join(p['fields'])}; status, proof and meaning hold for the text above"]
    if "statement" in p:
        lines.append(f"statement: {(p['statement'] or '(none)').strip()}")
    for name in ("limits", "assumptions"):
        if name in p:
            lines.append(f"{name}:" + ("".join(f"\n- {x}" for x in p[name]) if p[name] else " none stated"))
    return lines


def _status_lines(d: dict[str, Any]) -> list[str]:
    status = d["status"]
    lines = [status["label"] + (f" (from {status['receipt']})" if status["receipt"] else "")]
    anchor = trust_text(d["trust"]) if "trust" in d else None
    if anchor:
        lines.append(f"- {anchor}")
    lines += [f"- {n}" for n in status["notes"]]
    return lines


def _probe_line(name: str, entry: Any, witnesses: list[Any]) -> str:
    if name in witnesses:
        name += " (witness)"
    if not isinstance(entry, dict):
        return f"- {name}: no probe result"
    parts = []
    if entry.get("vacuous_by"):
        parts.append(f"VACUOUS: `{entry['vacuous_by']}` derives False from its hypotheses")
    if entry.get("trivial_by"):
        parts.append(f"TRIVIAL: closed by `{entry['trivial_by']}` alone")
    if not parts:
        parts.append("no probe result" + (f" ({entry['error']})" if entry.get("error") else "")
                     if entry.get("prop_hypotheses") is None else "not closed by the battery")
    skipped = entry.get("vacuity_skipped")
    if skipped:
        parts.append(f"premise-vacuity skipped: {skipped}")
    hyps = entry.get("prop_hypotheses")
    if isinstance(hyps, int):
        parts.append(f"{hyps} Prop hypothes{'is' if hyps == 1 else 'es'}"
                     + (", not refuted" if hyps and not skipped and not entry.get("vacuous_by") else ""))
    return f"- {name}: " + "; ".join(parts)


def _proof_lines(d: dict[str, Any]) -> list[str]:
    proof = d.get("proof")
    if proof is None:
        return ["no receipt"]
    head = f"{proof['assurance']} {proof['verdict']} ({proof['receipt'].rsplit('/', 1)[-1]}, {proof['adapter']})"
    if proof.get("kernels"):
        head += f" · kernels {', '.join(map(str, proof['kernels']))}"
    if proof.get("permitted_axioms") is not None:
        head += f" · axioms within {', '.join(map(str, proof['permitted_axioms'])) or 'none'}"
    lines = [head]
    probes, run = proof.get("probes"), proof.get("probe_run") or {}
    if isinstance(probes, dict):
        tactics = len(run.get("battery") or [])
        lines.append(f"statement probes ({tactics} tactic{'' if tactics == 1 else 's'}, "
                     f"{run.get('heartbeats_per_attempt')} heartbeats per attempt):")
        lines.append("vacuity scope: inconsistency of top-level Prop hypotheses; definition adequacy needs review")
        witnesses = d["lean"].get("witnesses") if isinstance(d["lean"].get("witnesses"), list) else []
        lines += [_probe_line(name, entry, witnesses) for name, entry in sorted(probes.items())]
        lines += [f"- probe problem: {p}" for p in run.get("problems") or []]
    elif run.get("skipped"):
        lines.append(f"statement probes skipped: {run['skipped']}")
    elif proof["adapter"] == "lean-comparator" and proof["verdict"] == "pass":
        lines.append("statement probes: none recorded in this receipt")
    lints = proof.get("lints")
    if isinstance(lints, list):
        shown = [x for x in lints if isinstance(x, dict)][:LINTS_SHOWN]
        lines.append(f"command lints over the files the candidate changed: {len(lints) or 'none'}"
                     + (" (warnings, never a verdict)" if lints else ""))
        lines += [f"- {x.get('file')}:{x.get('line')} {x.get('kind')}: {x.get('why')} | {x.get('text')}" for x in shown]
        if len(lints) > len(shown):
            lines.append(f"- … {len(lints) - len(shown)} more in {proof['receipt']}")
    return lines


def _meaning_lines(d: dict[str, Any]) -> list[str]:
    fid = d.get("fidelity")
    if fid is None:
        return ["no target or evaluator to review"]
    line = f"fidelity: {fidelity_text(fid)}"
    if fid.get("created"):
        line += f" on {fid['created']}"
    lines = [line + (f" ({fid['review']})" if fid.get("review") else "")]
    if fid.get("acknowledges"):
        lines.append(f"probe findings acknowledged by a review of the current target: {', '.join(fid['acknowledges'])}")
    return lines


def _correction_lines(d: dict[str, Any]) -> list[str]:
    lines = [f"! {_review_line(e)}" for e in d["corrections"]]
    lines += [f"! refuted by {r['id']} ({r['status']})" if r.get("admitted", True) else
              f"~ refutation proposed by {r['id']} ({r['status']}): in the worktree only, not on the trusted ref"
              for r in d["refuted_by"]]
    lines += [f"! superseded by {r['id']} ({r['status']})" for r in d["superseded_by"] if r["status"] == "verified"]
    lines += [f"~ proposed successor {r['id']} ({r['status']}): not a replacement until verified"
              for r in d["superseded_by"] if r["status"] != "verified"]
    return lines


def header(d: dict[str, Any]) -> str:
    """One line that always survives truncation: identity, status, and how many corrections exist."""
    kind = d["kind"] + (f"/{d['claim']}" if d["claim"] else "")
    counts = [(sum(e["kind"] == "retraction" for e in d["corrections"]), "retraction"),
              (sum(e["kind"] == "correction" for e in d["corrections"]), "correction"),
              (sum(r.get("admitted", True) for r in d["refuted_by"]), "refutation"),
              (sum(r["status"] == "verified" for r in d["superseded_by"]), "superseded-by")]
    flags = ", ".join(f"{n} {name}" for n, name in counts if n)
    warn = f"; READ FIRST: {flags}" if flags else ""
    fid = d.get("fidelity")
    fid_text = f"; fidelity: {fid['label']}" + (" (self-review)" if fid["self_review"] else "") if fid else ""
    trust = d.get("trust")
    anchor = (f" under trusted ref {trust['ref']} ({trust['source']})" if trust and not trust["configured"] else "")
    proposed = d.get("proposed")
    edit = f"; {proposed['label']}: {', '.join(proposed['fields'])}" if proposed else ""
    return (f"{d['id']}@{short(d['revision'])} — {d['title']} [{kind}; status: {d['status']['label']}{anchor}"
            f"{fid_text}{edit}{warn}]")


def _indent(lines: list[str], prefix: str = "  ") -> list[str]:
    return [prefix + line if line else line for text in lines for line in text.splitlines() or [""]]


def render_card(d: dict[str, Any]) -> str:
    out = [header(d)]
    out.append(f"  {d['path']} · revision {d['revision']} · author {d['author']}"
               + (f" (recorded by {d['recorded_by']})" if d["recorded_by"] else "") + f" · created {d['created']}")
    if d["revision_note"]:
        out.append(f"  NOTE: {d['revision_note']}")
    if d["kind_note"]:
        out.append(f"  NOTE: {d['kind_note']}")

    def section(title: str, lines: list[str], empty: str) -> None:
        out.append(title)
        out.extend(_indent(lines or [empty]))

    section("CORRECTIONS AND RETRACTIONS", _correction_lines(d), "none")
    section("LIMITS", [f"- {x}" for x in d["limits"]], "none stated")
    section("ASSUMPTIONS", [f"- {x}" for x in d["assumptions"]], "none stated")
    section("STATEMENT", _statement_lines(d), "")
    if d.get("proposed"):
        section("PROPOSED (not on the trusted ref)" if d["proposed"]["label"] == PROPOSAL
                else "TEXT OF THE REVISION ASKED FOR", _proposed_lines(d), "")
    section("STATUS (derived from receipts and reviews)", _status_lines(d), "")
    section("PROOF (kernels, axioms, statement probes)", _proof_lines(d), "")
    section("MEANING (does the target say what the question asks?)", _meaning_lines(d), "")
    receipt_lines = []
    for e in d["receipts"]:
        receipt_lines.append(f"- {_receipt_line(e)}")
        checked = _checked_line(e)
        if checked:
            receipt_lines.append(f"  {checked}")
    if d.get("timing"):
        receipt_lines.append(timing_text(d["timing"]))
    section("EVIDENCE (receipts, newest first)", receipt_lines, "no receipts")
    section("REVIEWS",
            [f"- {_review_line(e)}" for e in d["reviews"]], "none")
    rel_lines = []
    for name, entries in d["relations"].items():
        if not entries:
            continue
        shown = []
        for e in entries:
            text = f"{e['id']} ({e['status']})"
            if e.get("pinned_revision"):
                text = f"{e['id']}@{e['pinned_revision']} ({e['status']}"
                text += ", pin is NOT the current revision)" if e.get("pin_is_current") is False else ")"
            if e.get("admitted") is False:
                text += " [proposed: not on the trusted ref]"
            shown.append(text)
        rel_lines.append(f"{name}: {', '.join(shown)}")
    section("RELATIONS", rel_lines, "none")
    section("BODY", [d["body"].strip()] if d["body"].strip() else [], "(empty)")
    return "\n".join(out)


# Brief: compact context pack with loud truncation ----------------------------------------------


def brief_sections(d: dict[str, Any]) -> list[tuple[str, str]]:
    """(name, text) in priority order: corrections, limits, assumptions, statement, the unreviewed proposal (only
    when the shown revision's prose differs from the trusted text), status, proof, meaning, evidence, body."""

    def block(name: str, lines: list[str], empty: str) -> str:
        if not lines:
            return f"{name}: {empty}"
        return "\n".join([f"{name}:", *_indent(lines)])

    head = header(d)
    if d["revision_note"]:
        head += f"\nNOTE: {d['revision_note']}"
    if d["kind_note"]:
        head += f"\nNOTE: {d['kind_note']}"
    evidence = [_receipt_line(e) for e in d["receipts"]]
    proposed = [("proposed", block("proposed", _proposed_lines(d), ""))] if d.get("proposed") else []
    return [
        ("header", head),
        ("corrections", block("corrections", _correction_lines(d), "none")),
        ("limits", block("limits", [f"- {x}" for x in d["limits"]], "none stated")),
        ("assumptions", block("assumptions", [f"- {x}" for x in d["assumptions"]], "none stated")),
        ("statement", block("statement", _statement_lines(d), "")),
        *proposed,
        ("status", block("status", _status_lines(d), "")),
        ("proof", block("proof", _proof_lines(d), "")),
        ("meaning", block("meaning", _meaning_lines(d), "")),
        ("evidence", block("evidence", [f"- {x}" for x in evidence], "no receipts")),
        ("body", block("body", [d["body"].strip()] if d["body"].strip() else [], "(empty)")),
    ]


def _marker(item_id: str, omitted: int, total: int, partial: list[str], dropped: list[str]) -> str:
    what = []
    if partial:
        what.append("partial: " + ", ".join(partial))
    if dropped:
        what.append("omitted: " + ", ".join(dropped))
    return (f"[TRUNCATED: {omitted} of {total} chars omitted ({'; '.join(what)}); "
            f"run vl show {item_id} for the full card]")


class _Pack:
    """One item's brief while it is cut, grown unit by unit: whole sections, then lines of the next one."""

    def __init__(self, card: dict[str, Any]):
        self.id, self.revision = card["id"], card["revision"]
        self.sections = [(name, text.split("\n")) for name, text in brief_sections(card)]
        self.full = "\n".join("\n".join(lines) for _, lines in self.sections)
        self.whole = 1      # sections kept whole; the header always stays
        self.lines = 0      # lines kept of the next section
        self._history: list[tuple[int, int]] = []
        self.length = len(self.render())

    def complete(self) -> bool:
        return self.whole == len(self.sections)

    def level(self) -> int:
        return self.whole

    def advance(self) -> None:
        """Keep the next unit: a section's title line comes with its first content line."""
        self._history.append((self.whole, self.lines))
        lines = self.sections[self.whole][1]
        self.lines = min(len(lines), self.lines + (2 if self.lines == 0 else 1))
        if self.lines == len(lines):
            self.whole, self.lines = self.whole + 1, 0
        self.length = len(self.render())

    def retreat(self) -> None:
        self.whole, self.lines = self._history.pop()
        self.length = len(self.render())

    def _text(self) -> str:
        parts = ["\n".join(lines) for _, lines in self.sections[:self.whole]]
        if self.lines:
            parts.append("\n".join(self.sections[self.whole][1][:self.lines]))
        return "\n".join(parts)

    def _cut_names(self) -> tuple[list[str], list[str]]:
        if self.complete():
            return [], []
        partial = []
        if self.lines:
            name, lines = self.sections[self.whole]
            partial = [f"{name} ({self.lines} of {len(lines)} lines)"]
        start = self.whole + (1 if self.lines else 0)
        return partial, [name for name, _ in self.sections[start:]]

    def render(self) -> str:
        text = self._text()
        if self.complete():
            return text
        partial, dropped = self._cut_names()
        return text + "\n" + _marker(self.id, len(self.full) - len(text), len(self.full), partial, dropped)

    def meta(self) -> dict[str, Any]:
        partial, dropped = self._cut_names()
        return {"id": self.id, "revision": self.revision, "truncated": not self.complete(),
                "chars": self.length, "omitted_chars": len(self.full) - len(self._text()),
                "partial": partial, "omitted": dropped}


def render_brief(cards: list[dict[str, Any]], budget: int) -> tuple[str, list[dict[str, Any]]]:
    """Cut the pack to `budget` characters, filling by priority across items. Only the records are packed: a
    source's `ref` and a `[[link]]` stay pointers, and no file they name is ever read into a brief.

    Each step keeps one more unit (a line) of the item whose next unit has the highest priority
    (corrections, then limits, assumptions, statement, proposal, status, proof, meaning, evidence, body; ties in the
    order the ids were given). A unit that does not fit is retried after any other item grows, because a growing
    item's truncation marker shrinks. So a later section of one item is only kept when an earlier
    section of another no longer fits at all. Every item keeps its header, which names its status and
    counts its corrections, and every cut item ends with a loud marker.
    """
    packs = [_Pack(card) for card in cards]
    separators = 2 * (len(packs) - 1)
    while True:
        pending = sorted((p for p in packs if not p.complete()), key=lambda p: (p.level(), packs.index(p)))
        for pack in pending:
            pack.advance()
            if sum(p.length for p in packs) + separators <= budget:
                break
            pack.retreat()
        else:
            break
    return "\n\n".join(p.render() for p in packs), [p.meta() for p in packs]


# Impact ---------------------------------------------------------------------------------------


def impact_data(ctx: Context, item_id: str) -> dict[str, Any]:
    """Items that use this item directly or transitively, and explanations citing any of them."""
    closure: dict[str, str] = {}
    frontier = [item_id]
    while frontier:
        current = frontier.pop(0)
        for user in ctx.reverse["uses"].get(current, []):
            if user != item_id and user not in closure:
                closure[user] = current
                frontier.append(user)
    affected = {item_id, *closure}
    explanations = []
    for other in ctx.items.values():
        if other.kind != "explanation":
            continue
        cited = sorted({split_ref(r)[0] for r in (*other.cites, *other.uses)} & affected)
        linked = sorted({i for i, _ in body_links(other.body)} & affected - set(cited))
        if cited or linked:
            explanations.append({"id": other.id, "status": ctx.label(other.id), "cites": cited, "links": linked})
    users = [{"id": user, "status": ctx.label(user), "via": via, "direct": via == item_id}
             for user, via in closure.items()]
    return {"id": item_id, "status": ctx.label(item_id), "users": users, "explanations": explanations,
            "trust": trust_data(ctx.repo)}


def render_impact(d: dict[str, Any]) -> str:
    out = [f"IMPACT of {d['id']} ({d['status']}): what must be re-examined if it changes or is corrected"]
    anchor = trust_text(d["trust"])
    if anchor:
        out.append(f"NOTE: {anchor}")
    out.append(f"items that use it, directly or transitively ({len(d['users'])}):")
    for u in d["users"]:
        how = "directly" if u["direct"] else f"via {u['via']}"
        out.append(f"  - {u['id']} ({u['status']}) {how}")
    if not d["users"]:
        out.append("  none")
    out.append(f"explanations that cite it or a dependent ({len(d['explanations'])}):")
    for e in d["explanations"]:
        what = []
        if e["cites"]:
            what.append("cites " + ", ".join(e["cites"]))
        if e["links"]:
            what.append("links " + ", ".join(e["links"]))
        out.append(f"  - {e['id']} ({e['status']}) {'; '.join(what)}")
    if not d["explanations"]:
        out.append("  none")
    return "\n".join(out)
