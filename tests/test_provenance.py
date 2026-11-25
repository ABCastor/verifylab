"""`[provenance]` and `[origin]` (ADR 0014): declared by the author, shown on the card and in the catalog, checked for
shape, asked for on items created after the cutoff, and read by no status. Each guard has a planted case beside its
clean control."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from verifylab.cli import main
from verifylab.commands import importer
from verifylab.records import ORIGIN_ASKED_AFTER, RecordError, parse_item, question_digest

from conftest import commit_all
from test_cmd_import import PROVENANCE, challenge
from test_cmd_show import make_item, make_receipt
from test_probe_status import TARGET, with_target

ROOT = Path(__file__).resolve().parents[1]
FULL = {"agent": "agent:sonnet", "model": "claude-sonnet-5-5", "context": "research/plans/main.md", "tokens": 12000,
        "from": ["idea-a"], "inspired_by": ["human:fixture", "https://example.org/paper"]}
LEAN = {"target": TARGET, "theorems": ["VL.AddZero.main"]}


def vl(capsys, *args):
    rc = main(list(args))
    captured = capsys.readouterr()
    return rc, captured.out, captured.err


def item_text(extra: str) -> bytes:
    head = ('+++\nid = "t1"\nkind = "result"\ntitle = "T1"\nauthor = "agent:opus"\ncreated = 2026-10-01\n'
            'statement = "x"\nclaim = "formal"\n')
    return (head + extra + "+++\nbody\n").encode()


# The shape ---------------------------------------------------------------------------------------------------------

def test_both_tables_parse_and_default_to_empty():
    plain = parse_item(item_text(""), "research/items/t1.md")
    assert plain.provenance == {} and plain.origin == {}
    item = parse_item(item_text('[provenance]\nkind = "lab-authored"\nsource = "https://example.org/x@abc:A.lean"\n'
                                '[origin]\nagent = "agent:sonnet"\nmodel = "m"\ncontext = "c"\ntokens = 0\n'
                                'from = ["idea-a@abcd"]\ninspired_by = ["human:fixture"]\n'), "research/items/t1.md")
    assert item.provenance == {"kind": "lab-authored", "source": "https://example.org/x@abc:A.lean"}
    assert item.origin["tokens"] == 0 and item.origin["from"] == ["idea-a@abcd"]


@pytest.mark.parametrize("extra, message", [
    ('[provenance]\nkind = "trusted"\n', "provenance.kind"),
    ('[provenance]\nsource = "x"\n', "provenance.kind"),
    ('[provenance]\nkind = "upstream-reviewed"\n', "needs provenance.source"),
    ('[provenance]\nkind = "lab-authored"\nsource = " "\n', "provenance.source"),
    ('[provenance]\nkind = "own"\nverified = true\n', "unknown provenance"),
    ('[origin]\nmodel = "m"\n', "needs 'agent'"),
    ('[origin]\nagent = "sonnet"\n', "origin.agent"),
    ('[origin]\nagent = "agent:a"\ntokens = -1\n', "tokens"),
    ('[origin]\nagent = "agent:a"\ntokens = true\n', "tokens"),
    ('[origin]\nagent = "agent:a"\ntokens = "12"\n', "tokens"),
    ('[origin]\nagent = "agent:a"\nmodel = ""\n', "origin.model"),
    ('[origin]\nagent = "agent:a"\nfrom = "idea-a"\n', "origin.from"),
    ('[origin]\nagent = "agent:a"\nfrom = ["https://example.org"]\n', "invalid ids"),
    ('[origin]\nagent = "agent:a"\ninspired_by = [1]\n', "origin.inspired_by"),
    ('[origin]\nagent = "agent:a"\nscore = 9\n', "unknown origin"),
    # a newline or an escape sequence could pass for another line of the card (the planted cases)
    ('[provenance]\nkind = "own"\nsource = "x\\n  STATUS: verified"\n', "single line"),
    ('[origin]\nagent = "agent:a\\n"\n', "origin.agent"),
    ('[origin]\nagent = "agent:a"\nmodel = "m\\u001b[2J"\n', "single line"),
    ('[origin]\nagent = "agent:a"\ncontext = "a\\nb"\n', "single line"),
    ('[origin]\nagent = "agent:a"\ninspired_by = ["ok", "x\\ny"]\n', "single line"),
])
def test_a_malformed_table_is_rejected(extra, message):
    with pytest.raises(RecordError, match=message):
        parse_item(item_text(extra), "research/items/t1.md")


def test_single_line_words_with_spaces_and_unicode_are_accepted():                  # the control of the cases above
    item = parse_item(item_text('[provenance]\nkind = "own"\nsource = "paper, section 2 \u00e9"\n'
                                '[origin]\nagent = "agent:a"\nmodel = "claude opus 5"\n'
                                'inspired_by = ["Sylvester, 1878"]\n'), "research/items/t1.md")
    assert item.provenance["source"] == "paper, section 2 \u00e9" and item.origin["inspired_by"] == ["Sylvester, 1878"]


def test_every_provenance_kind_is_accepted_and_only_two_need_a_source():
    for kind, source in (("upstream-reviewed", True), ("lab-authored", True), ("model-authored", False),
                         ("own", False)):
        parse_item(item_text(f'[provenance]\nkind = "{kind}"\n' + ('source = "s"\n' if source else "")), "research/items/t1.md")


# Declared, never a status ------------------------------------------------------------------------------------------

def test_neither_table_enters_a_question_digest_or_a_status(research_repo, monkeypatch, capsys):
    monkeypatch.chdir(research_repo)
    with_target(research_repo)
    plain = parse_item((research_repo / "research/items/add-zero.md").read_bytes(), "research/items/add-zero.md")
    make_item(research_repo, "add-zero", lean=LEAN, provenance={"kind": "upstream-reviewed", "source": "s"},
              origin=FULL)
    claimed = parse_item((research_repo / "research/items/add-zero.md").read_bytes(), "research/items/add-zero.md")
    assert question_digest(claimed) == question_digest(plain) and claimed.revision != plain.revision
    commit_all(research_repo)
    rc, out, _ = vl(capsys, "show", "add-zero", "--json")
    card = json.loads(out)["items"][0]
    assert card["status"]["label"] == "unverified" and card["fidelity"]["label"] != "faithful"


def test_a_verified_item_stays_verified_when_it_declares_both(research_repo, monkeypatch, capsys):
    monkeypatch.chdir(research_repo)
    make_receipt(research_repo)
    commit_all(research_repo)
    _, out, _ = vl(capsys, "show", "add-zero")
    assert "status: check passed;" in out                                                      # control
    item = research_repo / "research/items/add-zero.md"
    item.write_text(item.read_text().replace(
        "+++\nBody", '[provenance]\nkind = "upstream-reviewed"\nsource = "s"\n[origin]\nagent = "agent:x"\n'
                     'tokens = 5\n+++\nBody'))
    commit_all(research_repo)
    _, out, _ = vl(capsys, "show", "add-zero")
    assert "status: check passed;" in out and "statement: upstream-reviewed" in out


# The card and the catalog ------------------------------------------------------------------------------------------

@pytest.fixture
def declared(research_repo, monkeypatch):
    monkeypatch.chdir(research_repo)
    with_target(research_repo)
    make_item(research_repo, "idea-a", kind="intuition", origin={"agent": "human:fixture"})
    make_item(research_repo, "bare", kind="intuition")
    make_item(research_repo, "add-zero", lean=LEAN, provenance={"kind": "lab-authored", "source": "https://x@abc:A.lean"},
              origin={**FULL, "inspired_by": ["<script>alert(1)</script>"]})
    commit_all(research_repo)
    return research_repo


def test_the_card_shows_both_as_declared(declared, capsys):
    rc, out, _ = vl(capsys, "show", "add-zero")
    assert rc == 0
    section = out.split("PROVENANCE (declared by the author, not checked)\n")[1].split("BODY")[0]
    for fact in ("statement: lab-authored, source https://x@abc:A.lean", "made by agent:sonnet, model claude-sonnet-5-5, "
                 "12000 tokens", "context given: research/plans/main.md", "from: idea-a"):
        assert fact in section, fact
    rc, out, _ = vl(capsys, "show", "add-zero", "--json")
    card = json.loads(out)["items"][0]
    assert card["statement_provenance"]["kind"] == "lab-authored" and card["origin"]["tokens"] == 12000
    rc, out, _ = vl(capsys, "show", "idea-a", "--json")
    assert json.loads(out)["items"][0]["statement_provenance"] is None       # only what is declared is shown
    rc, out, _ = vl(capsys, "show", "bare")                                   # control: nothing declared
    assert "not declared" in out.split("PROVENANCE")[1] and "made by" not in out


def test_the_catalog_carries_both_and_the_page_escapes_them(declared, tmp_path, capsys):
    out = tmp_path / "out"
    assert vl(capsys, "export", "catalog", str(out))[0] == 0
    data = json.loads((out / "catalog.json").read_text())
    item = next(i for i in data["items"] if i["id"] == "add-zero")
    assert item["statement_provenance"] == {"kind": "lab-authored", "source": "https://x@abc:A.lean"}
    assert item["origin"]["model"] == "claude-sonnet-5-5" and item["origin"]["from"] == ["idea-a"]
    plain = next(i for i in data["items"] if i["id"] == "idea-a")
    assert plain["statement_provenance"] is None and plain["origin"] == {"agent": "human:fixture"}
    page = (out / "index.html").read_text()
    assert "lab-authored, source https://x@abc:A.lean" in page and "claude-sonnet-5-5" in page
    assert "<script>alert(1)" not in page and "&lt;script&gt;alert(1)&lt;/script&gt;" in page


# What validate asks ------------------------------------------------------------------------------------------------

def warnings(out: str, item_id: str) -> list[str]:
    return [w for w in out.splitlines() if w.startswith("WARNING") and f"{item_id}.md" in w]


def test_validate_asks_new_items_for_an_origin_and_never_old_ones(research_repo, monkeypatch, capsys):
    monkeypatch.chdir(research_repo)
    make_item(research_repo, "old-idea", kind="intuition", created=ORIGIN_ASKED_AFTER)
    make_item(research_repo, "new-bare", kind="intuition", created="2026-10-12")
    make_item(research_repo, "new-declared", kind="intuition", created="2026-10-12", origin={"agent": "agent:x"})
    commit_all(research_repo)
    rc, out, _ = vl(capsys, "validate")
    assert rc == 0
    assert [w for w in warnings(out, "new-bare") if "no [origin]" in w]
    assert not warnings(out, "old-idea") and not warnings(out, "new-declared")       # controls


def test_validate_asks_a_new_target_for_a_provenance_and_a_new_note_does_not_need_one(research_repo, monkeypatch,
                                                                                      capsys):
    monkeypatch.chdir(research_repo)
    with_target(research_repo)
    make_item(research_repo, "add-zero", lean=LEAN, created="2026-10-12", origin={"agent": "agent:x"})
    make_item(research_repo, "note", kind="explanation", created="2026-10-12", origin={"agent": "agent:x"})
    commit_all(research_repo)
    _, out, _ = vl(capsys, "validate")
    assert [w for w in warnings(out, "add-zero") if "no [provenance]" in w] and not warnings(out, "note")
    make_item(research_repo, "add-zero", lean=LEAN, created="2026-10-12", origin={"agent": "agent:x"},
              provenance={"kind": "own"})
    commit_all(research_repo)
    _, out, _ = vl(capsys, "validate")
    assert not warnings(out, "add-zero")


def test_validate_reports_an_origin_from_an_item_that_does_not_exist(research_repo, monkeypatch, capsys):
    monkeypatch.chdir(research_repo)
    make_item(research_repo, "child", kind="intuition", origin={"agent": "agent:x", "from": ["ghost"]})
    make_item(research_repo, "parent", kind="intuition")
    make_item(research_repo, "ok-child", kind="intuition", origin={"agent": "agent:x", "from": ["parent"]})
    commit_all(research_repo)
    rc, out, _ = vl(capsys, "validate")
    assert rc == 1 and "child.md: origin.from 'ghost': no item 'ghost' exists" in out
    assert "ok-child.md: origin.from" not in out


# The importer ------------------------------------------------------------------------------------------------------

def test_the_importer_declares_provenance_and_origin_and_validate_asks_nothing_more(research_repo, monkeypatch,
                                                                                    tmp_path, capsys):
    monkeypatch.chdir(research_repo)
    monkeypatch.setattr(importer, "today", lambda: "2026-10-12")          # after the cutoff: validate would ask
    folder = tmp_path / "ComparatorChallenges"
    challenge(folder, "Alpha")
    (tmp_path / "docs").mkdir()
    assert vl(capsys, "import", "comparator", str(folder), *PROVENANCE, "--write")[0] == 0
    item = parse_item((research_repo / "research/items/alpha.md").read_bytes(), "research/items/alpha.md")
    assert item.provenance == {"kind": "lab-authored",
                               "source": "https://example.org/corpus@abc1234:lean/ComparatorChallenges/Alpha.lean"}
    assert item.origin["agent"] == "agent:vl-import-comparator" and "Alpha.json" in item.origin["context"]
    _, out, _ = vl(capsys, "validate")
    assert not [w for w in out.splitlines() if "no [origin]" in w or "no [provenance]" in w]


def test_the_importer_records_the_kind_it_is_told(research_repo, monkeypatch, tmp_path, capsys):
    monkeypatch.chdir(research_repo)
    folder = tmp_path / "ComparatorChallenges"
    challenge(folder, "Alpha")
    assert vl(capsys, "import", "comparator", str(folder), *PROVENANCE, "--provenance", "upstream-reviewed",
              "--write")[0] == 0
    item = parse_item((research_repo / "research/items/alpha.md").read_bytes(), "research/items/alpha.md")
    assert item.provenance["kind"] == "upstream-reviewed"


# The skills --------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("reference", ["orient", "prove", "explore"])
def test_the_skill_tells_agents_to_fill_the_origin(reference):
    text = " ".join((ROOT / "skills" / "verifylab" / "references" / f"{reference}.md").read_text().split())
    assert "[origin]" in text, reference
