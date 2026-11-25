"""The page of `vl export catalog`: one self-contained HTML file for a catalog (catalog.py).

No external resource: the style and the filter script are inline and a Content-Security-Policy that allows only
their sha256 forbids everything else, so text of the records can neither load nor run anything; it is escaped, and
a `ref` stays text, never a link. Every item is in the page as HTML, so it reads without the script; the script only
hides the items a filter excludes. Light or dark follows the reader's system.
"""

from __future__ import annotations

import base64
import hashlib
import json
from html import escape
from typing import Any

from .ladder import human_label, summary as ladder_summary
from .render import appraisal_lines, boundary_text, finding_text, provenance_lines, trust_text

NO_AREA = "(none)"
GOOD, BAD, WARN = ("verified", "answered"), ("failed", "refuted", "vacuous", "retracted", "check-error",
                                              "check-unsupported", "undetermined"), \
    ("verified-stale", "pending-admission", "explored", "unverified")
PROBE_BAD, PROBE_WARN = ("vacuous", "trivial"), ("incomplete", "skipped")

STYLE = """
:root{color-scheme:light dark;--bg:#fff;--fg:#1f2328;--muted:#59636e;--line:#d1d9e0;--panel:#f6f8fa;
--good:#1a7f37;--bad:#cf222e;--warn:#9a6700;--link:#0969da}
@media (prefers-color-scheme:dark){:root{--bg:#0d1117;--fg:#e6edf3;--muted:#9198a1;--line:#3d444d;--panel:#151b23;
--good:#3fb950;--bad:#f85149;--warn:#d29922;--link:#4493f8}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:16px/1.5 system-ui,sans-serif}
main{max-width:60rem;margin:0 auto;padding:1rem}
h1{font-size:1.4rem;margin:.2rem 0}h2{font-size:1rem;margin:1rem 0 .3rem}
a{color:var(--link)}code,pre{font:.85rem/1.4 ui-monospace,monospace}
pre{background:var(--panel);padding:.5rem;overflow-x:auto;white-space:pre-wrap;overflow-wrap:anywhere;margin:.3rem 0}
.muted{color:var(--muted)}.warning{border:1px solid var(--bad);padding:.5rem;margin:.5rem 0}
#filters{display:flex;flex-wrap:wrap;gap:.5rem;margin:1rem 0 .3rem}
#filters select,#filters input{font:inherit;color:inherit;background:var(--panel);border:1px solid var(--line);
border-radius:.3rem;padding:.3rem;min-width:0;flex:1 1 9rem}
.item{border:1px solid var(--line);border-radius:.4rem;margin:.4rem 0;background:var(--panel)}
.item>summary{cursor:pointer;padding:.5rem;overflow-wrap:anywhere;min-width:0;display:flex;flex-wrap:wrap;gap:.2rem .5rem;align-items:baseline}
.item .detail{padding:0 .75rem .75rem;border-top:1px solid var(--line);overflow-wrap:anywhere}
.badge{border:1px solid currentColor;border-radius:.3rem;padding:0 .35rem;font-size:.8rem;white-space:nowrap}
.s-good{color:var(--good)}.s-bad{color:var(--bad)}.s-warn{color:var(--warn)}
.item>summary>span{min-width:0}.id{font-weight:600}ul{margin:.2rem 0;padding-left:1.2rem}table{border-collapse:collapse;width:100%}
td,th{border-top:1px solid var(--line);padding:.15rem .4rem;text-align:left;vertical-align:top;overflow-wrap:normal}
td code{white-space:nowrap}
.scroll{overflow-x:auto}
"""

SCRIPT = """
(function(){
var items=[].slice.call(document.querySelectorAll('.item')),form=document.getElementById('filters');
var text=items.map(function(el){return el.textContent.toLowerCase()}),shown=document.getElementById('shown');
function apply(){
var v={status:'',kind:'',area:''},q=form.q.value.trim().toLowerCase(),n=0;
for(var k in v){if(form[k])v[k]=form[k].value}
items.forEach(function(el,i){
var ok=(!v.status||el.dataset.status===v.status)&&(!v.kind||el.dataset.kind===v.kind)&&
(!v.area||el.dataset.area.indexOf('|'+v.area+'|')>=0)&&(!q||text[i].indexOf(q)>=0);
el.hidden=!ok;if(ok)n++});
shown.textContent=n+' of '+items.length+' shown'}
function open_(){var el=document.getElementById(decodeURIComponent(location.hash.slice(1)));
if(el&&el.classList.contains('item')){el.hidden=false;el.open=true;el.scrollIntoView()}}
form.hidden=false;form.addEventListener('input',apply);form.addEventListener('submit',function(e){e.preventDefault()});
window.addEventListener('hashchange',open_);apply();open_()})();
"""


def _csp() -> str:
    def digest(text: str) -> str:
        return "'sha256-" + base64.b64encode(hashlib.sha256(text.encode()).digest()).decode() + "'"
    return f"default-src 'none'; style-src {digest(STYLE)}; script-src {digest(SCRIPT)}"


def _tone(label: str) -> str:
    return "s-good" if label in GOOD else "s-bad" if label in BAD else "s-warn" if label in WARN else ""


def _badge(label: str, tone: str | None = None) -> str:
    return f'<span class="badge {_tone(label) if tone is None else tone}">{escape(human_label(label))}</span>'


def _list(values: list[Any], empty: str = "none stated") -> str:
    return "<ul>" + "".join(f"<li>{escape(str(v))}</li>" for v in values) + "</ul>" if values else \
        f'<p class="muted">{escape(empty)}</p>'


def _section(title: str, content: str) -> str:
    return f"<h2>{escape(title)}</h2>{content}"


def _code(text: Any) -> str:
    return f"<code>{escape(str(text))}</code>"


def _table(header: list[str], rows: list[list[str]]) -> str:
    head = "".join(f"<th>{escape(h)}</th>" for h in header)
    body = "".join("<tr>" + "".join(f"<td>{cell}</td>" for cell in row) + "</tr>" for row in rows)
    return f'<div class="scroll"><table><tr>{head}</tr>{body}</table></div>'


def _proof(item: dict[str, Any]) -> str:
    proof = item["proof"]
    if proof is None:
        return '<p class="muted">no receipt</p>'
    kernels = ", ".join(proof["kernels"] or []) or "not recorded"
    axioms = ", ".join(proof["permitted_axioms"]) if proof["permitted_axioms"] is not None else "not recorded"
    head = (f"<p>{_badge(proof['verdict'], 's-good' if proof['verdict'] == 'pass' else 's-bad')} "
            f"{escape(proof['assurance'])} {escape(proof['adapter'])}, receipt {_code(proof['receipt'])}<br>"
            f"kernels: {escape(kernels)}<br>axioms the check permitted: {escape(axioms)}</p>")
    rows = [[_code(r["id"]), escape(r["adapter"]), escape(r["assurance"]), escape(str(r["verdict"])),
             escape(str(r["finished_at"])), "stale" if r["stale"] else "fresh"] for r in item["receipts"]]
    return head + _table(["receipt", "adapter", "assurance", "verdict", "finished", "inputs"], rows)


def _ladder(item: dict[str, Any]) -> str:
    rows = [[escape(r["title"]), escape(r["state"]), escape(r["detail"] or ""), _code(r["record"]) if r["record"] else ""]
            for r in item["ladder"]]
    return _table(["fact", "state", "detail", "record"], rows)


def _probes(item: dict[str, Any]) -> str:
    probes = item["probes"]
    if probes is None:
        return '<p class="muted">no statement probes for this check</p>'
    tone = "s-bad" if probes["state"] in PROBE_BAD else "s-warn" if probes["state"] in PROBE_WARN else \
        "s-good" if probes["state"] == "clean" else ""
    out = [f"<p>{_badge(probes['state'], tone)} receipt {_code(probes['receipt'])}"
           + (f"; skipped: {escape(probes['skipped'])}" if probes["skipped"] else "") + "</p>",
           f"<p>Inhabitation of data binders: {escape(probes['data_inhabitation'])}.</p>"]
    out.append(_list(probes["problems"], "no probe problems recorded") if probes["problems"] else "")
    if probes["boundary_unfinished"]:
        out.append("<p>boundary values not probed (the run ended first): "
                   + ", ".join(_code(t) for t in probes["boundary_unfinished"]) + "</p>")
    rows = []
    for name, t in probes["theorems"].items():
        found = [f"vacuous by {t['vacuous_by']}" if "vacuous_by" in t else "",
                 f"trivial by {t['trivial_by']}" if "trivial_by" in t else "",
                 f"premise test skipped: {t['vacuity_skipped']}" if "vacuity_skipped" in t else "",
                 f"error: {t['error']}" if "error" in t else "",
                 f"{t['prop_hypotheses']} Prop hypotheses" if "prop_hypotheses" in t else "",
                 *(f"{k}: {v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)}"
                   for k, v in t.get("extra", {}).items())]
        lists = [(label, texts) for label, texts in (
            ("definitions", t.get("definitions", [])),
            ("boundary values", [boundary_text(b) for b in t.get("boundary", [])]),
            ("totalized operations", [finding_text(x, "term") for x in t.get("totalized", [])])) if texts]
        rows.append([_code(name) + (" (witness)" if t["witness"] else ""), _badge(t["state"], ""),
                     escape("; ".join(x for x in found if x)) + "".join(
                         f"<br>{label}:" + _list(texts) for label, texts in lists)])
    return "".join(out) + (_table(["theorem", "probe", "found"], rows) if rows else "")


def _fidelity(item: dict[str, Any]) -> str:
    fidelity = item["fidelity"]
    if fidelity is None:
        return '<p class="muted">no target or evaluator to review</p>'
    note = f" by {fidelity['by']}" + (" (self-review: not independent)" if fidelity["self_review"] else "") \
        if fidelity["by"] else ""
    when = f" on {fidelity['created']}" if fidelity["created"] else ""
    older = " (an older review: it does not bind everything; review again)" if fidelity["legacy"] or fidelity[
        "unbound"] else ""
    rows = [[escape(r["kind"]), escape(str(r["verdict"] or "")), escape(r["author"]), escape(str(r["created"])),
             "current" if r["at_current_revision"] else "item text edited since", _code(r["id"])]
            for r in item["reviews"]]
    return (f"<p>{escape(fidelity['label'])}{escape(note)}{escape(when)}{escape(older)}</p>"
            + (_table(["kind", "verdict", "author", "date", "revision", "review"], rows) if rows else ""))


def _provenance(item: dict[str, Any]) -> str:
    p = item["provenance"]
    lines = [f"file {_code(p['path'])}, revision {_code(p['revision'][:12])}"
             + (f", added in commit {_code(p['added_in'][:12])}" if p["added_in"] else ""),
             f"author {escape(p['author'])}" + (f", recorded by {escape(p['recorded_by'])}" if p["recorded_by"] else "")
             + f", created {escape(str(p['created']))}"]
    if p["ref"]:
        lines.append(f"source: {_code(p['ref'])}" + (f" (access: {escape(p['access'])})" if p["access"] else ""))
    lines += [f"cites source {_code(s['id'])}: {_code(s['ref'])} (access: {escape(str(s['access']))})"
              for s in p["sources"]]
    lines += [escape(line) for line in provenance_lines({"statement_provenance": item["statement_provenance"],
                                                         "origin": item["origin"]})]
    if p["origin"]:
        lines.append(f"body begins: {escape(p['origin'])}")
    return "<ul>" + "".join(f"<li>{line}</li>" for line in lines) + "</ul>"


def _relations(item: dict[str, Any], known: set[str]) -> str:
    rows = []
    for name, relations in item["relations"].items():
        if relations:
            links = ", ".join((f'<a href="#{escape(r["id"])}">{escape(r["id"])}</a>' if r["id"] in known
                               else escape(r["id"])) + f" ({escape(human_label(r['status']))}"
                              + (f", pinned {escape(r['pinned_revision'])}" if r.get("pinned_revision") else "") + ")"
                              for r in relations)
            rows.append(f"<li>{escape(name.replace('_', ' '))}: {links}</li>")
    return f"<ul>{''.join(rows)}</ul>" if rows else '<p class="muted">no relations</p>'


def _item(item: dict[str, Any], known: set[str]) -> str:
    kind = item["kind"] + (f"/{item['claim']}" if item["claim"] else "")
    areas = item["areas"] or [NO_AREA]
    parts = []
    for c in item["corrections"]:
        parts.append(f'<p class="warning"><b>{escape(c["kind"])}</b> by {escape(c["author"])}, '
                     f'{escape(str(c["created"]))}: {escape(c["text"])}</p>')
    if item["rejected_records"]:
        parts.append('<p class="warning">rejected records, ignored: ' + escape("; ".join(
            f"{r['path']} ({'; '.join(r['problems'])})" for r in item["rejected_records"])) + "</p>")
    target = item["target"]
    binding = [f"target {_code(target['path'])}" + (f" (sha256 {_code(target['sha256'][:16])})"
                                                      if target["sha256"] else "")] if target["path"] else []
    if target["theorems"]:
        binding.append("theorems " + ", ".join(_code(t) + (" (witness)" if t in target["witnesses"] else "")
                                               for t in target["theorems"]))
    if target["solution"]:
        binding.append(f"solution module {_code(target['solution'])}")
    if target["entry"]:
        binding.append(f"entry {_code(target['entry'])}")
    parts += [_section("Limits", _list(item["limits"])), _section("Assumptions", _list(item["assumptions"])),
              _section("Statement", f"<p>{escape(item['statement'] or '(no statement)')}</p>"
                       + ("<ul>" + "".join(f"<li>{b}</li>" for b in binding) + "</ul>" if binding else "")),
              _section("Verdict ladder (five separate facts; none implies another)", _ladder(item)),
              _section("Derived status (a summary, not a verdict)", f"<p>{_badge(item['status']['label'])}"
                       + (f" from record {_code(item['status']['receipt'])}" if item["status"]["receipt"] else "")
                       + "</p>" + (_list(item["status"]["notes"]) if item["status"]["notes"] else "")),
              _section("Proof", _proof(item)), _section("Statement probes", _probes(item)),
              _section("Meaning (fidelity)", _fidelity(item)), _section("Provenance", _provenance(item)),
              _section("Relations", _relations(item, known))]
    if any(r["kind"] == "appraisal" for r in item["reviews"]):
        parts.append(_section("Contextual appraisals (never proof status)", _list(appraisal_lines(item, details=True))))
    replay = item["replay"]
    if replay:
        parts.append(_section("Replay", f"<pre>{escape(replay['command'])}</pre>"
                              f"<p class=\"muted\">runs receipt {_code(replay['receipt'])} again in a fresh clone and "
                              "compares (docs/REPLAY.md)</p>"))
    probe = (f' {_badge("probes " + item["probes"]["state"], "")}' if item["probes"] and item["probes"]["state"]
             in (*PROBE_BAD, *PROBE_WARN) else "")
    return (f'<details class="item" id="{escape(item["id"])}" data-status="{escape(item["status"]["label"])}" '
            f'data-kind="{escape(item["kind"])}" data-area="|{escape("|".join(areas))}|"><summary>'
            f'<span class="muted">{escape(ladder_summary(item["ladder"]))}</span> '
            f'{_badge(item["status"]["label"])}{probe} <span class="muted">{escape(kind)}</span> '
            f'<span class="id">{escape(item["id"])}</span> <span>{escape(item["title"])}</span></summary>'
            f'<div class="detail">{"".join(parts)}</div></details>')


def _select(name: str, every: str, counts: dict[str, int]) -> str:
    options = "".join(f'<option value="{escape(v)}">{escape(human_label(v))} ({n})</option>' for v, n in sorted(counts.items()))
    return f'<select name="{name}" aria-label="{every}"><option value="">{every}</option>{options}</select>'


def render(data: dict[str, Any]) -> str:
    items = data["items"]
    known = {i["id"] for i in items}
    status, kind = data["counts"]["status"], data["counts"]["kind"]
    areas: dict[str, int] = {}
    for i in items:
        for a in i["areas"] or [NO_AREA]:
            areas[a] = areas.get(a, 0) + 1
    trust = data["trust"]
    anchor = trust_text(trust)
    probes = ", ".join(f"{n} {s}" for s, n in sorted(data["counts"]["probes"].items()))
    policy = data["policy"]
    controls = (_select("status", "all statuses", status) + _select("kind", "all kinds", kind)
                + (_select("area", "all areas", areas) if list(areas) != [NO_AREA] else "")
                + '<input type="search" name="q" placeholder="search id, title, text" aria-label="search">')
    problems = ('<p class="warning">items on the trusted ref that do not parse, not listed: '
                + escape("; ".join(data["problems"])) + "</p>") if data["problems"] else ""
    body = (f'<main><h1>{escape(data["project"])}: catalog</h1>'
            f'<p class="muted">Statuses are derived from the records committed on trusted ref {escape(trust["ref"])} '
            f'at {_code(str(trust["commit"])[:12])}, never stored. Permitted axioms: '
            f'{escape(", ".join(policy["permitted_axioms"]) or "none")}; second kernel '
            f'{"required" if policy["external_kernels"] else "not required"}. Schema {escape(data["schema"])}, '
            f'full commit {_code(trust["commit"])}.</p>'
            + (f'<p class="warning">{escape(anchor)}</p>' if anchor else "")
            + f'<p>{len(items)} items. Derived status: '
            f'{escape(", ".join(f"{n} {human_label(s)}" for s, n in sorted(status.items())))}.'
            + (f" Probes: {escape(probes)}." if probes else "") + f"</p>{problems}"
            f'<form id="filters" hidden>{controls}</form><p class="muted" id="shown"></p>'
            + "".join(_item(i, known) for i in items)
            + '<p class="muted">A passing check establishes its formal contract; whether the statement means what '
              'the question asks is the fidelity review. Imported definitions are the source\'s own.</p></main>')
    return ('<!doctype html>\n<html lang="en"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1">'
            f'<meta http-equiv="Content-Security-Policy" content="{escape(_csp())}">'
            f'<title>{escape(data["project"])} catalog</title><style>{STYLE}</style></head>'
            f"<body>{body}<script>{SCRIPT}</script></body></html>\n")
