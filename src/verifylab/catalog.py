"""The data of `vl export catalog`: every item of the trusted ref as plain data (schema `vl.catalog/1`).

Everything is derived from the trusted commit alone (`TrustedRepo`): admitted receipts and reviews, the items as
committed, statuses as `status.derive` gives them. No time, host or path of this machine is in it, so the same
records give the same bytes. An entry is built from the card of `vl show --json`, so the two cannot disagree.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from . import gitref
from .ladder import ROWS
from .render import RELATIONS, Context, card_data, trust_data
from .repo import ITEMS, TrustedRepo
from .status import CHECKABLE_CLAIMS

SCHEMA = "vl.catalog/1"
PROBE_STATES = ("vacuous", "trivial", "incomplete", "skipped", "clean")     # worst first; "none": no probe result
PROBE_FIELDS = ("trivial_by", "vacuous_by", "prop_hypotheses", "vacuity_skipped", "limit_reached", "error")
# The lists ADR 0010 adds to a probe entry, under the keys the probes write (adapters/lean_probes.py): the keys of a
# finding the file keeps, and None for a list of names.
PROBE_LISTS = {"definitions": None, "totalized": ("op", "term", "in", "why"),
               "boundary": ("binder", "value", "trivial_by", "vacuous_by")}
ORIGIN_CHARS = 400
AREA_TAG = "area:"


def _stem(path: str) -> str:
    """The 16 hex characters that name a receipt or review file: its id."""
    return path.rsplit("/", 1)[-1][:-5]


def _rows(rows: list[Any], keys: tuple[str, ...] | None) -> list[Any]:
    """The names (strings) or the findings (tables, cut to `keys`) of a probe list; anything else is left out."""
    if keys is None:
        return [r for r in rows if isinstance(r, str)]
    return [{k: r[k] for k in keys if k in r} for r in rows if isinstance(r, dict)]


def probe_summary(proof: dict[str, Any] | None, witnesses: list[Any]) -> dict[str, Any] | None:
    """What the statement probes of the receipt behind the status found, per theorem and for the item. A theorem is
    `vacuous` or `trivial` when the battery says so, `incomplete` when it gave no result, `skipped` when the
    premise test did not run, else `clean`; the item takes the
    worst, and `incomplete` also when the run recorded problems. None for a check that has no probes (Python). A
    theorem lists the fields its probe entry set, its `definitions`, `totalized` operations and `boundary` values
    (well-formed rows only, as the card shows them), and under `extra` the fields beyond these."""
    if proof is None or proof["adapter"] != "lean-comparator":
        return None
    probes, run = proof.get("probes"), proof.get("probe_run") or {}
    theorems = {}
    for name, entry in sorted((probes if isinstance(probes, dict) else {}).items()):
        entry = entry if isinstance(entry, dict) else {}
        state = ("vacuous" if entry.get("vacuous_by") else "trivial" if entry.get("trivial_by")
                 else "incomplete" if entry.get("prop_hypotheses") is None
                 else "skipped" if entry.get("vacuity_skipped") else "clean")
        extra = {k: v for k, v in sorted(entry.items()) if k not in (*PROBE_FIELDS, *PROBE_LISTS)}
        theorems[name] = {"state": state, "witness": name in witnesses,
                          **{k: entry[k] for k in PROBE_FIELDS if entry.get(k) is not None},
                          **{k: _rows(entry[k], keys) for k, keys in PROBE_LISTS.items()
                             if isinstance(entry.get(k), list)},
                          **({"extra": extra} if extra else {})}
    problems = [p for p in run.get("problems") or [] if isinstance(p, str)]
    skipped = run.get("skipped") if isinstance(run.get("skipped"), str) else None
    states = {t["state"] for t in theorems.values()} | ({"incomplete"} if problems else set()) \
        | ({"skipped"} if skipped else set())
    counts = {s: sum(t["state"] == s for t in theorems.values()) for s in PROBE_STATES}
    unfinished = [t for t in run.get("boundary_unfinished") or [] if isinstance(t, str)]
    return {"state": next((s for s in PROBE_STATES if s in states), "none"),
            "receipt": _stem(proof["receipt"]), "skipped": skipped, "boundary_unfinished": unfinished,
            "problems": problems, "counts": counts, "theorems": theorems, "data_inhabitation": "not assessed"}


def _ladder(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The five rows of the card, with a record named by its id (the 16 hex characters of its file)."""
    return [{**row, "record": _stem(row["record"]) if row["record"] else None,
             **({"statements": [{**x, "review": _stem(x["review"])} for x in row["statements"]]}
                if "statements" in row else {})} for row in rows]


def _relations(card: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """The relations of the card as `uses`, `used_by`, ...: the ids with their derived status, and the revision a
    relation is pinned to. Every one is stated by an item of the trusted ref."""
    out = {}
    for forward, backward in RELATIONS:
        for name, rows in ((forward, card["relations"][forward]), (backward.replace(" ", "_"),
                                                                    sorted(card["relations"][backward],
                                                                           key=lambda r: r["id"]))):
            out[name] = [{k: r[k] for k in ("id", "status", "pinned_revision") if k in r} for r in rows]
    return out


def _origin(body: str) -> str | None:
    """The first paragraph of the body: where an import or a note says what the item came from (free text)."""
    paragraph = " ".join((body.strip().split("\n\n") or [""])[0].split())
    return (paragraph if len(paragraph) <= ORIGIN_CHARS else paragraph[:ORIGIN_CHARS - 1] + "…") or None


def _provenance(ctx: Context, card: dict[str, Any], added: dict[str, tuple[str, ...]]) -> dict[str, Any]:
    """Where the item comes from: its file and the commit that added it, the source items it cites or uses (their
    `ref` is a DOI, URL or path) and, free text, the first paragraph of its body."""
    item = ctx.items[card["id"]]
    cited = {r["id"] for name in ("cites", "uses") for r in card["relations"][name]}
    sources = [{"id": s.id, "ref": s.ref, "access": s.access}
               for s in sorted((ctx.items[i] for i in cited if i in ctx.items), key=lambda s: s.id)
               if s.kind == "source"]
    commits = added.get(item.path, ())
    return {"path": item.path, "revision": item.revision, "added_in": commits[-1] if commits else None,
            "author": item.author, "recorded_by": item.recorded_by, "created": item.created,
            "ref": item.ref, "access": item.access, "sources": sources, "origin": _origin(item.body)}


def _replay(item_id: str, claim: str | None, card: dict[str, Any]) -> dict[str, Any] | None:
    """The command that runs the item's own receipt again in a clean clone (`vl replay`, ADR 0009): the receipt the
    card shows as its proof, not the record a status borrows (the refuting item's receipt of a `refuted` item, the
    retraction of a `retracted`). Only a protected receipt can be replayed."""
    proof = card["proof"]
    if claim not in CHECKABLE_CLAIMS or not (card["lean"] or card["python"]) or proof is None \
            or proof["assurance"] != "protected":
        return None
    receipt = _stem(proof["receipt"])
    return {"command": f"vl replay {item_id} --receipt {receipt}", "receipt": receipt}


def _entry(ctx: Context, item_id: str, added: dict[str, tuple[str, ...]]) -> dict[str, Any]:
    card = card_data(ctx, ctx.items[item_id])
    lean, python = card["lean"], card["python"]
    meaning = card["meaning"] or {}
    target = lean.get("target") or python.get("evaluator")
    witnesses = lean.get("witnesses") if isinstance(lean.get("witnesses"), list) else []
    proof = card["proof"]
    fidelity = card["fidelity"]
    reviews = [{"id": _stem(r["path"]), "kind": r["kind"], "author": r["author"], "created": r["created"],
                "verdict": r["verdict"], "at_current_revision": r["at_current_revision"],
                **({key: r[key] for key in ("assessment", "applicability", "text", "review_id",
                                            "admitted", "problems", "path")} if r["kind"] == "appraisal" else {})}
               for r in sorted(card["reviews"], key=lambda r: (r["created"] or "", r["path"]), reverse=True)
               if not r["problems"]]
    rejected = [{"path": r["path"], "problems": r["problems"]} for r in [*card["receipts"], *card["reviews"],
                                                                         *card["corrections"]] if r["problems"]]
    return {
        "id": card["id"], "title": card["title"], "kind": card["kind"], "claim": card["claim"],
        "status": {"label": card["status"]["label"], "notes": card["status"]["notes"],
                   "receipt": _stem(card["status"]["receipt"]) if card["status"]["receipt"] else None},
        "statement": card["statement"], "limits": card["limits"], "assumptions": card["assumptions"],
        "tags": card["tags"], "areas": sorted(t[len(AREA_TAG):] for t in card["tags"] if t.startswith(AREA_TAG)),
        "target": {"path": target, "sha256": (meaning.get("files") or {}).get(target) if target else None,
                   "theorems": lean.get("theorems") or [], "witnesses": witnesses, "solution": lean.get("solution"),
                   "evaluator": python.get("evaluator"), "entry": python.get("entry")},
        "proof": None if proof is None else {
            "receipt": _stem(proof["receipt"]), "adapter": proof["adapter"], "assurance": proof["assurance"],
            "verdict": proof["verdict"], "kernels": proof["kernels"], "permitted_axioms": proof["permitted_axioms"]},
        "probes": probe_summary(proof, witnesses),
        "ladder": _ladder(card["ladder"]),
        "receipts": [{"id": _stem(r["path"]), "adapter": r["adapter"], "assurance": r["assurance"],
                      "verdict": r["verdict"], "finished_at": r["finished_at"], "stale": bool(r["stale_inputs"])}
                     for r in card["receipts"] if not r["problems"]],
        "fidelity": None if fidelity is None else {
            "label": fidelity["label"], "review": _stem(fidelity["review"]) if fidelity["review"] else None,
            "by": fidelity["by"], "created": fidelity["created"], "self_review": fidelity["self_review"],
            "acknowledges": fidelity["acknowledges"], "legacy": fidelity["legacy"], "unbound": fidelity["unbound"]},
        "reviews": reviews,
        "corrections": [{"id": _stem(r["path"]), "kind": r["kind"], "author": r["author"], "created": r["created"],
                         "text": r["text"]} for r in card["corrections"] if not r["problems"]],
        "rejected_records": rejected,
        "provenance": _provenance(ctx, card, added),
        "statement_provenance": card["statement_provenance"],
        "origin": card["origin"],
        "imports": card["imports"],
        "relations": _relations(card),
        "replay": _replay(item_id, card["claim"], card),
    }


def build(repo: TrustedRepo) -> dict[str, Any]:
    """The catalog of the trusted ref: policy, counts and one entry per item, by id."""
    items, problems = repo.trusted_items()
    commit = repo.trusted_commit
    ctx = Context(repo, items)
    added = gitref.added(str(repo.root), commit, repo.rel(ITEMS))
    entries = [_entry(ctx, item_id, added) for item_id in sorted(items)]
    lean = repo.config.lean
    return {"schema": SCHEMA, "project": repo.config.name, "trust": trust_data(repo),
            "policy": {"permitted_axioms": sorted(set(lean.permitted_axioms)), "external_kernels": lean.external_kernels},
            "counts": {"items": len(entries), "status": dict(Counter(e["status"]["label"] for e in entries)),
                       "kind": dict(Counter(e["kind"] for e in entries)),
                       "probes": dict(Counter(e["probes"]["state"] for e in entries if e["probes"])),
                       "ladder": {key: dict(Counter(e["ladder"][i]["state"] for e in entries))
                                  for i, (key, _, _) in enumerate(ROWS)}},
            "problems": sorted(problems), "items": entries}
