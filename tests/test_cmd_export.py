"""`vl export catalog`: every item of the trusted ref as catalog.json and one self-contained page. Receipts here are
synthetic: the Lean side is tested elsewhere."""

from __future__ import annotations

import base64
import hashlib
import json
import re
from html import unescape
from html.parser import HTMLParser
from pathlib import Path

import pytest

from verifylab import catalog, catalog_page
from verifylab.adapters import lean_probes
from verifylab.cli import main
from verifylab.repo import Repo, TrustedRepo
from verifylab.status import derive

from conftest import commit_all, git
from review_helpers import meaning_args
from test_cmd_show import make_item
from test_probe_status import NULL, TARGET, receipt, with_target

ROOT = Path(__file__).resolve().parents[1]
LEAN = {"target": TARGET, "theorems": ["VL.AddZero.main"]}


PAGE_TAGS = {"html", "head", "meta", "title", "style", "body", "main", "h1", "h2", "p", "form", "select", "option",
             "input", "details", "summary", "span", "div", "ul", "li", "code", "pre", "table", "tr", "th", "td", "a",
             "b", "br", "script"}
PAGE_ATTRS = {"lang", "charset", "name", "content", "http-equiv", "id", "hidden", "aria-label", "value", "type",
              "placeholder", "class", "data-status", "data-kind", "data-area", "href"}


class Tags(HTMLParser):
    """The start tags of a page and their attributes, as a browser reads them."""

    def __init__(self):
        super().__init__()
        self.tags, self.attrs = [], []

    def handle_starttag(self, tag, attrs):
        self.tags.append(tag)
        self.attrs += attrs

    @classmethod
    def collect(cls, page):
        parser = cls()
        parser.feed(page)
        return parser.tags, parser.attrs


def written_probes() -> dict:
    """The probe entry of VL.AddZero.main as the probes branch writes it: the metaprogram's tagged lines through
    lean_probes.parse_output (a trivial hit, a definition, a totalized operation, boundary values)."""
    row = {"theorem": "VL.AddZero.main", "trivial_by": "simp", "vacuous_by": None, "prop_hypotheses": 1,
           "limit_reached": {}, "definitions": ["Fixture.gap"],
           "totalized": [{"op": "nat_sub", "term": "n - 1", "in": "Fixture.gap"}]}
    bound = {"theorem": "VL.AddZero.main", "phase": "boundary", "limit_reached": [],
             "boundary": [{"binder": "n", "value": 0, "trivial_by": "simp", "vacuous_by": None},
                          {"binder": "n", "value": 1, "trivial_by": None, "vacuous_by": "intros; omega"}]}
    stdout = "\n".join(f"{lean_probes.MARKER}n {json.dumps(r)}" for r in (row, bound))
    results, problems = lean_probes.parse_output(stdout, "n", ["VL.AddZero.main"])
    assert problems == []
    return results


def vl(capsys, *args):
    rc = main(list(args))
    captured = capsys.readouterr()
    return rc, captured.out, captured.err


def export(capsys, out: Path, *extra):
    return vl(capsys, "export", "catalog", str(out), *extra)


def load(out: Path) -> dict:
    return json.loads((out / "catalog.json").read_text())


def entry(data: dict, item_id: str) -> dict:
    return next(i for i in data["items"] if i["id"] == item_id)


@pytest.fixture
def populated(research_repo, monkeypatch, capsys):
    """A verified result with a trivial probe, a faithful review, a cited source, an area tag and an item that uses it."""
    root = research_repo
    monkeypatch.chdir(root)
    with_target(root)
    make_item(root, "paper", kind="source", ref="https://example.org/paper.pdf", access="citation-only",
              body="A paper.\n")
    make_item(root, "add-zero", lean=LEAN, cites=["paper"], tags=["area:Analysis", "demo"],
              limits=["Only natural numbers."], assumptions=["n is a Nat."],
              body="Imported from example.org, commit abc1234.\n\nMore.\n")
    make_item(root, "user", uses=["add-zero"])
    receipt(root, written_probes())
    commit_all(root)
    assert vl(capsys, "review", "add-zero", "--kind", "fidelity", *meaning_args(), "--verdict", "faithful",
              "--author", "agent:referee", "--acknowledge", "trivial", "--text", "Fine.")[0] == 0
    commit_all(root)
    return root


def test_catalog_json_has_every_field_a_reader_needs(populated, tmp_path, capsys):
    root = populated
    out = tmp_path / "out"
    rc, text, _ = export(capsys, out, "--json")
    assert rc == 0 and json.loads(text)["counts"]["items"] == 3
    data = load(out)
    commit = git(root, "rev-parse", "trusted").strip()
    assert data["schema"] == "vl.catalog/1" and data["trust"]["commit"] == commit and data["problems"] == []
    assert [i["id"] for i in data["items"]] == ["add-zero", "paper", "user"]
    item = entry(data, "add-zero")
    assert item["status"]["label"] == "verified" and item["kind"] == "result" and item["claim"] == "formal"
    assert item["limits"] == ["Only natural numbers."] and item["assumptions"] == ["n is a Nat."]
    assert item["statement"] == "Statement of add-zero."
    assert item["target"]["path"] == TARGET and item["target"]["theorems"] == ["VL.AddZero.main"]
    assert re.fullmatch(r"[0-9a-f]{64}", item["target"]["sha256"])
    assert item["proof"]["kernels"] == ["lean", "nanoda"] and item["proof"]["permitted_axioms"] == ["propext"]
    assert item["proof"]["verdict"] == "pass" and item["proof"]["receipt"] == item["status"]["receipt"]
    assert [r["stale"] for r in item["receipts"]] == [False]
    assert item["probes"]["state"] == "trivial" and item["probes"]["counts"]["trivial"] == 1
    assert item["probes"]["theorems"]["VL.AddZero.main"]["trivial_by"] == "simp"
    assert "extra" not in item["probes"]["theorems"]["VL.AddZero.main"]
    assert item["fidelity"]["label"] == "faithful" and item["fidelity"]["by"] == "agent:referee"
    assert item["fidelity"]["created"] and item["fidelity"]["acknowledges"] == ["trivial"]
    assert item["reviews"][0]["kind"] == "fidelity" and item["reviews"][0]["verdict"] == "faithful"
    assert item["areas"] == ["Analysis"] and "demo" in item["tags"]
    prov = item["provenance"]
    assert prov["path"] == "research/items/add-zero.md" and re.fullmatch(r"[0-9a-f]{40}", prov["added_in"])
    assert prov["sources"] == [{"id": "paper", "ref": "https://example.org/paper.pdf", "access": "citation-only"}]
    assert prov["origin"] == "Imported from example.org, commit abc1234."
    assert item["relations"]["cites"] == [{"id": "paper", "status": "source"}]
    assert item["relations"]["used_by"] == [{"id": "user", "status": "unverified"}]
    assert item["replay"] == {"command": f"vl replay add-zero --receipt {item['proof']['receipt']}",
                              "receipt": item["proof"]["receipt"]}
    source = entry(data, "paper")
    assert source["status"]["label"] == "source" and source["replay"] is None and source["probes"] is None
    assert source["provenance"]["ref"] == "https://example.org/paper.pdf"
    assert data["counts"]["status"] == {"unverified": 1, "source": 1, "verified": 1}
    assert data["counts"]["probes"] == {"trivial": 1}
    assert data["policy"]["external_kernels"] is True


def test_the_export_reads_the_trusted_ref_never_the_worktree(populated, tmp_path, capsys):
    """Planted: a protected pass only the worktree has, an edited target, an edited item. The catalog is what the
    trusted commit says; a Repo that read the worktree (the guard disabled) says otherwise. The control: committing
    the same edit changes the catalog."""
    root = populated
    first = tmp_path / "first"
    assert export(capsys, first)[0] == 0
    # a worktree-only receipt for `user` (a result, so it can be checked) and an edit of what add-zero was checked on
    make_item(root, "user", uses=["add-zero"], limits=["Edited, uncommitted."])
    import test_probe_status
    lean = "Fixture/Basic.lean"
    from conftest import item_question, synthetic_receipt
    from verifylab.records import sha256_hex, write_new_json
    data = synthetic_receipt(root, dict(
        item="user", item_revision="a" * 40, question_digest=item_question(root, "user"),
        adapter="lean-comparator", assurance="protected", verdict="pass", reasons=[],
        inputs={"digest": "d", "files": {lean: sha256_hex((root / lean).read_bytes())}}, environment={},
        checked={}, command=["vl"], started_at="t0", finished_at="t1", tool_version="vl 0.1.0"))
    write_new_json(root / "research" / "evidence" / "user" / f"{data['receipt_id'][:16]}.json", data)
    (root / "Fixture" / "Basic.lean").write_text("-- edited\n")
    assert test_probe_status.status(root).label == "verified-stale"          # the worktree's view of add-zero
    repo = Repo.open(root)
    items, _ = repo.load_items()
    assert derive(repo, items["user"], items).label == "pending-admission"   # what a worktree reader would say
    second = tmp_path / "second"
    assert export(capsys, second)[0] == 0
    for name in ("catalog.json", "index.html"):
        assert (first / name).read_bytes() == (second / name).read_bytes(), name
    assert entry(load(second), "user")["status"]["label"] == "unverified"
    assert entry(load(second), "add-zero")["status"]["label"] == "verified"
    commit_all(root)
    third = tmp_path / "third"
    assert export(capsys, third)[0] == 0
    assert entry(load(third), "user")["status"]["label"] == "verified-stale"      # its receipt's file is now edited
    assert (third / "catalog.json").read_bytes() != (first / "catalog.json").read_bytes()
    assert entry(load(third), "add-zero")["status"]["label"] == "verified-stale"
    assert isinstance(TrustedRepo.open(root), Repo)


def test_the_same_records_give_the_same_bytes(populated, tmp_path, capsys):
    outs = [tmp_path / name for name in ("a", "b")]
    for out in outs:
        assert export(capsys, out)[0] == 0
    for name in ("catalog.json", "index.html"):
        assert (outs[0] / name).read_bytes() == (outs[1] / name).read_bytes()
    text = (outs[0] / "catalog.json").read_text()
    assert not re.search(r"generated|\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d.*generated", text)
    assert export(capsys, outs[0])[0] == 0                                         # overwriting is the same bytes
    assert sorted(p.name for p in outs[0].iterdir()) == ["catalog.json", "index.html"]


def test_the_page_is_self_contained_escaped_and_readable_without_script(populated, tmp_path, capsys):
    root = populated
    make_item(root, "evil", title="<script>alert(1)</script>", statement='"><img src=x onerror=alert(1)>',
              limits=["<b>bold</b>"], tags=["area:<i>x</i>"], body="<script>alert(2)</script>\n")
    make_item(root, "src", kind="source", ref="javascript:alert(3)", access="citation-only")
    commit_all(root)
    out = tmp_path / "page"
    assert export(capsys, out)[0] == 0
    page = (out / "index.html").read_text()
    # nothing is loaded or linked from outside, and nothing the records say is markup: the page's own tags, none of
    # them a resource, and every link inside the page
    tags, attrs = Tags.collect(page)
    assert set(tags) <= PAGE_TAGS and "<img" not in page.replace("&lt;img", "") and page.count("<script") == 1
    assert {n for n, _ in attrs} <= PAGE_ATTRS and not [v for n, v in attrs if n == "href" and not v.startswith("#")]
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page and "&quot;&gt;&lt;img src=x onerror=alert(1)&gt;" in page
    assert "<b>bold</b>" not in page and "&lt;b&gt;bold&lt;/b&gt;" in page and "<i>x</i>" not in page
    assert "<code>javascript:alert(3)</code>" in page
    # the policy allows exactly the inline style and script
    style = re.search(r"<style>(.*?)</style>", page, re.S).group(1)
    script = re.search(r"<script>(.*?)</script>", page, re.S).group(1)
    csp = unescape(re.search(r'Content-Security-Policy" content="([^"]*)"', page).group(1))
    sha = lambda text: "'sha256-" + base64.b64encode(hashlib.sha256(text.encode()).digest()).decode() + "'"  # noqa: E731
    assert csp == f"default-src 'none'; style-src {sha(style)}; script-src {sha(script)}"
    # readable without the script: every item is HTML, and the filters appear only when the script runs
    assert 'id="filters" hidden' in page and "Only natural numbers." in page and "VL.AddZero.main" in page
    assert 'name="viewport"' in page and "prefers-color-scheme:dark" in page
    # no resource of a clean catalog either
    assert page.count("<script") == 1 and "http://" not in page


def test_the_page_lists_every_item_with_its_filters_and_facts(populated, tmp_path, capsys):
    out = tmp_path / "page"
    assert export(capsys, out)[0] == 0
    page = (out / "index.html").read_text()
    items = re.findall(r'<details class="item" id="([^"]+)" data-status="([^"]+)" data-kind="([^"]+)" '
                       r'data-area="([^"]+)"', page)
    assert sorted(items) == [("add-zero", "verified", "result", "|Analysis|"), ("paper", "source", "source", "|(none)|"),
                             ("user", "unverified", "result", "|(none)|")]
    assert '<select name="status"' in page and '<select name="kind"' in page and '<select name="area"' in page
    assert "verified (" in page
    for fact in ("kernels: lean, nanoda", "axioms the check permitted: propext", "trivial by simp",
                 "totalized operations:", "boundary values:", "definitions:", "Fixture.gap", "`n - 1`",
                 "at n = 1 `intros; omega` refutes its hypotheses", "faithful by agent:referee", "vl replay add-zero --receipt", "https://example.org/paper.pdf",
                 "added in commit", "Imported from example.org, commit abc1234."):
        assert fact in page, fact
    for tab in re.findall(r'id="([^"]+)"', page):
        assert page.count(f'id="{tab}"') == 1


def test_without_any_area_the_page_has_no_area_filter(research_repo, monkeypatch, tmp_path, capsys):
    monkeypatch.chdir(research_repo)
    out = tmp_path / "page"
    assert export(capsys, out)[0] == 0
    page = (out / "index.html").read_text()
    assert '<select name="status"' in page and 'name="area"' not in page


def test_a_correction_and_a_retraction_are_shown_and_change_the_status(populated, tmp_path, capsys):
    root = populated
    assert vl(capsys, "review", "add-zero", "--kind", "correction", "--author", "human:ada", "--human-approved",
              "--text", "Wrong for integers.")[0] == 0
    commit_all(root)
    out = tmp_path / "page"
    assert export(capsys, out)[0] == 0
    item = entry(load(out), "add-zero")
    assert item["corrections"][0]["text"] == "Wrong for integers." and item["corrections"][0]["author"] == "human:ada"
    assert "Wrong for integers." in (out / "index.html").read_text()
    assert vl(capsys, "review", "add-zero", "--kind", "retraction", "--author", "human:ada", "--human-approved",
              "--text", "Withdrawn.")[0] == 0
    commit_all(root)
    assert export(capsys, out)[0] == 0
    assert entry(load(out), "add-zero")["status"]["label"] == "retracted"


def test_the_replay_compares_with_the_items_own_receipt_not_the_record_a_status_borrows(populated, tmp_path, capsys):
    """Planted: a retraction (a refutation borrows the refuting item's receipt the same way) makes the status point to
    another record. The control, a verified item, compares with the receipt its status rests on."""
    root = populated
    out = tmp_path / "page"
    assert export(capsys, out)[0] == 0
    item = entry(load(out), "add-zero")
    assert item["replay"]["receipt"] == item["status"]["receipt"] == item["proof"]["receipt"]
    assert vl(capsys, "review", "add-zero", "--kind", "retraction", "--author", "human:ada", "--human-approved",
              "--text", "Withdrawn.")[0] == 0
    commit_all(root)
    assert export(capsys, out)[0] == 0
    item = entry(load(out), "add-zero")
    assert item["status"]["label"] == "retracted" and item["status"]["receipt"] == item["corrections"][0]["id"]
    assert item["replay"]["receipt"] == item["proof"]["receipt"] != item["status"]["receipt"]


def test_only_a_protected_receipt_has_a_replay_command():
    """Planted: an admitted exploratory receipt and an item with no receipt, which `vl replay` would refuse; the
    control is the protected receipt."""
    card = {"lean": {"target": "t"}, "python": {}, "proof": {"receipt": "research/evidence/x/0123456789abcdef.json",
                                                              "assurance": "protected"}}
    assert catalog._replay("x", "formal", card) == {"command": "vl replay x --receipt 0123456789abcdef",
                                                   "receipt": "0123456789abcdef"}
    assert catalog._replay("x", "formal", {**card, "proof": {**card["proof"], "assurance": "exploratory"}}) is None
    assert catalog._replay("x", "formal", {**card, "proof": None}) is None
    assert catalog._replay("x", "prose", card) is None


def test_a_review_only_the_worktree_has_is_not_in_the_catalog(populated, tmp_path, capsys):
    """Planted: an uncommitted correction. The control is the committed one of the test above."""
    root = populated
    assert vl(capsys, "review", "add-zero", "--kind", "correction", "--author", "human:ada", "--human-approved",
              "--text", "Only in the worktree.")[0] == 0
    out = tmp_path / "page"
    assert export(capsys, out)[0] == 0
    item = entry(load(out), "add-zero")
    assert item["corrections"] == [] and item["rejected_records"] == []
    assert "Only in the worktree." not in (out / "index.html").read_text()
    assert not [r for r in item["reviews"] if r["kind"] != "fidelity"]


def test_a_failure_to_write_leaves_the_previous_pair(populated, tmp_path, capsys):
    """Planted: a folder where index.html goes. The previous catalog.json is not replaced by a newer one beside an
    older page; the control is the same folder with a file there, overwritten."""
    out = tmp_path / "page"
    assert export(capsys, out)[0] == 0
    before = {n: (out / n).read_bytes() for n in ("catalog.json", "index.html")}
    make_item(populated, "later", kind="source", ref="https://example.org/later", access="citation-only")
    commit_all(populated)
    (out / "index.html").unlink()
    (out / "index.html").mkdir()
    (out / "catalog.json").write_text("previous")
    rc, _, err = export(capsys, out)
    assert rc == 2 and "folder stands where" in err and (out / "catalog.json").read_text() == "previous"
    (out / "index.html").rmdir()
    (out / "index.html").write_text("previous")
    assert export(capsys, out)[0] == 0
    assert (out / "catalog.json").read_bytes() != before["catalog.json"] and "later" in (out / "index.html").read_text()
    assert sorted(p.name for p in out.iterdir()) == ["catalog.json", "index.html"]


def test_an_item_that_does_not_parse_on_the_trusted_ref_is_named_not_listed(populated, tmp_path, capsys):
    root = populated
    (root / "research" / "items" / "broken.md").write_text("no front matter\n")
    commit_all(root)
    out = tmp_path / "page"
    rc, text, _ = export(capsys, out)
    data = load(out)
    assert rc == 0 and "do not parse" in text and "broken" not in [i["id"] for i in data["items"]]
    assert len(data["problems"]) == 1 and "broken.md" in data["problems"][0]
    assert "broken.md" in (out / "index.html").read_text()


def test_the_export_refuses_a_file_for_a_folder_and_an_unresolved_trusted_ref(populated, tmp_path, capsys):
    target = tmp_path / "file"
    target.write_text("x")
    rc, _, err = export(capsys, target)
    assert rc == 2 and "not a folder" in err and target.read_text() == "x"
    rc, _, err = export(capsys, tmp_path / "out", "--trusted-ref", "no-such-ref")
    assert rc == 2 and "does not resolve" in err and not (tmp_path / "out").exists()
    rc, text, _ = export(capsys, tmp_path / "out", "--trusted-ref", "trusted")
    assert rc == 0 and "NOTE: trusted ref trusted" in text
    assert load(tmp_path / "out")["trust"]["configured"] is False


# The probe summary -----------------------------------------------------------------------------------------------

def proof(probes, run=None):
    return {"adapter": "lean-comparator", "receipt": "research/evidence/x/abcd.json", "probes": probes,
            "probe_run": {"battery": ["simp"], "problems": [], **(run or {})}}


@pytest.mark.parametrize("entries, run, state", [
    ({"a": {**NULL, "prop_hypotheses": 1}}, None, "clean"),
    ({"a": {**NULL, "prop_hypotheses": 0, "vacuity_skipped": "no Prop hypothesis"}}, None, "skipped"),
    ({"a": {**NULL, "prop_hypotheses": 2, "vacuity_skipped": "the conclusion is False"}}, None, "skipped"),
    ({"a": {**NULL, "prop_hypotheses": 1, "trivial_by": "simp"}}, None, "trivial"),
    ({"a": {**NULL, "prop_hypotheses": 1, "trivial_by": "simp", "vacuous_by": "omega"}}, None, "vacuous"),
    ({"a": {**NULL, "prop_hypotheses": None, "error": "no probe result"}}, None, "incomplete"),
    ({"a": {**NULL, "prop_hypotheses": 1}}, {"problems": ["timed out"]}, "incomplete"),
    ({"a": {**NULL, "prop_hypotheses": 1}, "b": {**NULL, "prop_hypotheses": 1, "trivial_by": "rfl"}}, None, "trivial"),
])
def test_probe_states(entries, run, state):
    summary = catalog.probe_summary(proof(entries, run), [])
    assert summary["state"] == state
    assert summary["data_inhabitation"] == "not assessed"


def test_the_probe_lists_are_carried_under_the_keys_the_probes_write(populated, tmp_path, capsys):
    """Planted: a receipt written through lean_probes (definitions, totalized, boundary). Under `extra`, where a
    catalog that did not know the keys would put them, nothing remains; the control is a field the probes do not
    write, which stays in `extra`."""
    out = tmp_path / "out"
    assert export(capsys, out)[0] == 0
    theorem = entry(load(out), "add-zero")["probes"]["theorems"]["VL.AddZero.main"]
    assert theorem["definitions"] == ["Fixture.gap"]
    assert theorem["totalized"] == [{"op": "nat_sub", "term": "n - 1", "in": "Fixture.gap",
                                     "why": lean_probes.TOTALIZED_KINDS["nat_sub"]}]
    assert theorem["boundary"] == [{"binder": "n", "value": 0, "trivial_by": "simp"},
                                   {"binder": "n", "value": 1, "vacuous_by": "intros; omega"}]
    assert "extra" not in theorem and entry(load(out), "add-zero")["probes"]["boundary_unfinished"] == []
    summary = catalog.probe_summary(proof({"a": {**NULL, "prop_hypotheses": 1, "note": "x"}}), [])
    assert summary["theorems"]["a"]["extra"] == {"note": "x"}
    page = (out / "index.html").read_text()
    assert "definitions:" in page and "Fixture.gap" in page
    assert "Inhabitation of data binders: not assessed" in page


def test_probe_lists_that_are_not_well_formed_are_left_out_and_unfinished_boundaries_are_named():
    """Planted: strings where findings go, a finding that is not a table, a name that is not a string. The control:
    well-formed rows are kept, cut to the keys the probes write."""
    bad = {"a": {**NULL, "prop_hypotheses": 1, "definitions": ["D", 3], "totalized": ["x / 0", {"op": "nat_sub",
           "term": "a - b", "in": "statement", "why": "w", "stray": 1}], "boundary": "n = 0"}}
    summary = catalog.probe_summary(proof(bad, {"boundary_unfinished": ["a", 7]}), [])
    theorem = summary["theorems"]["a"]
    assert theorem["definitions"] == ["D"]
    assert theorem["totalized"] == [{"op": "nat_sub", "term": "a - b", "in": "statement", "why": "w"}]
    assert "boundary" not in theorem and "extra" not in theorem
    assert summary["boundary_unfinished"] == ["a"]


def test_the_probe_lists_are_escaped_on_the_page():
    """Planted: markup in every text the probe lists carry (a definition, a term, a binder, a tactic, an unfinished
    theorem). The control is the same text plain, which appears as written."""
    evil = "<img src=x onerror=alert(1)>"
    bad = {"a": {**NULL, "prop_hypotheses": 1, "definitions": [evil], "totalized": [
        {"op": "nat_sub", "term": evil, "in": evil, "why": evil}], "boundary": [
        {"binder": evil, "value": 0, "trivial_by": evil}]}}
    summary = catalog.probe_summary(proof(bad, {"boundary_unfinished": [evil]}), [])
    page = catalog_page._probes({"probes": summary})
    assert "<img" not in page and page.count("&lt;img src=x onerror=alert(1)&gt;") >= 6
    plain = catalog.probe_summary(proof({"a": {**NULL, "prop_hypotheses": 1, "definitions": ["Fixture.gap"]}}), [])
    assert "<li>Fixture.gap</li>" in catalog_page._probes({"probes": plain})


def test_probes_skipped_or_missing_and_not_lean():
    summary = catalog.probe_summary(proof(None, {"skipped": "no tools"}), [])
    assert summary["state"] == "skipped" and summary["skipped"] == "no tools" and summary["theorems"] == {}
    assert catalog.probe_summary(proof(None), [])["state"] == "none"
    assert catalog.probe_summary({**proof(None), "adapter": "python-eval"}, []) is None
    assert catalog.probe_summary(None, []) is None
    witness = catalog.probe_summary(proof({"a": {**NULL, "prop_hypotheses": 1}}), ["a"])
    assert witness["theorems"]["a"]["witness"] is True


# The documentation -----------------------------------------------------------------------------------------------

def spec_section() -> str:
    text = (ROOT / "docs" / "SPEC.md").read_text()
    return text[text.index("## The catalog"):text.index("## Versions")]


def test_the_spec_names_every_field_of_the_catalog(populated, tmp_path, capsys):
    out = tmp_path / "out"
    assert export(capsys, out)[0] == 0
    data = load(out)
    section = spec_section()
    item = entry(data, "add-zero")
    theorem = item["probes"]["theorems"]["VL.AddZero.main"]
    names = set(data) | set(item) | set(item["probes"]) | set(theorem) | set(theorem["totalized"][0]) \
        | set(theorem["boundary"][0]) | set(item["proof"]) | set(item["fidelity"]) | set(item["provenance"]) \
        | set(item["replay"]) | set(item["ladder"][0]) | set(item["ladder"][4]) | set(item["target"]) \
        | set(item["receipts"][0]) | set(item["reviews"][0]) \
        | set(data["trust"]) | set(data["policy"]) | set(data["counts"]) | set(item["relations"])
    assert [n for n in sorted(names) if f"`{n}`" not in section] == []
    for state in (*catalog.PROBE_STATES, "none"):
        assert f"`{state}`" in section
    assert f"`{catalog.SCHEMA}`" in section


def test_the_docs_name_the_command_and_the_page_module_is_the_one_that_renders(populated):
    assert "vl export catalog" in (ROOT / "README.md").read_text()
    assert catalog_page.render(catalog.build(TrustedRepo.open())).startswith("<!doctype html>")


def test_a_failure_while_putting_the_pair_in_place_leaves_the_previous_pair(tmp_path, monkeypatch):
    """Planted: the second of the two renames fails. The first file must not stay new beside the old second one; the
    control, a write that does not fail, replaces both. The folder is locked meanwhile, so two exports into it
    cannot interleave their files."""
    import fcntl
    import os
    from verifylab import fsio
    out = tmp_path / "out"
    out.mkdir()
    fsio.replace_files(out, {"catalog.json": "old json", "index.html": "old html"})
    real, calls, locked = os.replace, [], []

    def flaky(src, dst):
        other = os.open(out, os.O_RDONLY | os.O_DIRECTORY)
        try:
            fcntl.flock(other, fcntl.LOCK_EX | fcntl.LOCK_NB)
            locked.append(False)
        except BlockingIOError:
            locked.append(True)
        finally:
            os.close(other)
        calls.append(dst)
        if len(calls) == 2:
            raise OSError("disk full")
        return real(src, dst)
    monkeypatch.setattr(os, "replace", flaky)
    with pytest.raises(OSError, match="disk full"):
        fsio.replace_files(out, {"catalog.json": "new json", "index.html": "new html"})
    monkeypatch.setattr(os, "replace", real)
    assert (out / "catalog.json").read_text() == "old json" and (out / "index.html").read_text() == "old html"
    assert locked[:2] == [True, True] and sorted(p.name for p in out.iterdir()) == ["catalog.json", "index.html"]
    fsio.replace_files(out, {"catalog.json": "new json", "index.html": "new html"})                 # control
    assert (out / "catalog.json").read_text() == "new json" and (out / "index.html").read_text() == "new html"
