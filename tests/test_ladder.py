"""The verdict ladder (ADR 0013): five separate rows, each `not assessed` until a receipt or review assessed it; a
`correspondence` review fills the last one; text for people never shows `verified` as the verdict. Receipts here are
synthetic: the Lean side is tested elsewhere. Each guard has its planted case and a clean control."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from verifylab import ladder, repo as repo_module
from verifylab.cli import main
from verifylab.records import CORRESPONDENCE_OUTCOMES, review_problems, seal_review, sha256_hex, write_new_json
from verifylab.repo import Repo
from verifylab.status import correspondence, meaning, meaning_digest

from conftest import commit_all, item_question, synthetic_receipt
from review_helpers import meaning_args
from test_cmd_show import make_item
from test_probe_status import NULL, TARGET, receipt, with_target

ROWS = ["lean_kernel", "second_kernel", "axioms", "statement_fidelity", "paper_correspondence"]
NOT = "not assessed"


def vl(capsys, *args):
    rc = main(list(args))
    captured = capsys.readouterr()
    return rc, captured.out, captured.err


def card(capsys, *extra):
    rc, out, _ = vl(capsys, "show", "add-zero", "--json", *extra)
    assert rc == 0
    return json.loads(out)["items"][0]


def states(capsys):
    data = card(capsys)
    return {r["row"]: r["state"] for r in data["ladder"]}


def lean_receipt(root: Path, kernels=("lean", "nanoda"), verdict="pass", assurance="protected",
                 kernel_results=None) -> None:
    lean = "Fixture/Basic.lean"
    data = synthetic_receipt(root, dict(
        item="add-zero", item_revision="a" * 40, question_digest=item_question(root, "add-zero"),
        adapter="lean-comparator", assurance=assurance, verdict=verdict,
        reasons=[] if verdict == "pass" else ["theorem not proved"],
        inputs={"digest": "d", "files": {lean: sha256_hex((root / lean).read_bytes())}},
        environment={}, checked={"kernels": list(kernels), "permitted_axioms": ["propext"],
                                 **({"kernel_results": kernel_results} if kernel_results else {})},
        command=["vl"], started_at="t0", finished_at="t1", tool_version="vl 0.1.0"))
    write_new_json(root / "research" / "evidence" / "add-zero" / f"{data['receipt_id'][:16]}.json", data)


@pytest.fixture
def passed(research_repo, monkeypatch):
    """A result with a Lean target and an admitted protected pass by both kernels, and no review."""
    monkeypatch.chdir(research_repo)
    with_target(research_repo)
    receipt(research_repo, {"VL.AddZero.main": NULL})
    commit_all(research_repo)
    return research_repo


def review_correspondence(capsys, verdict, statement=None, extra=()):
    args = ["review", "add-zero", "--kind", "correspondence", *meaning_args(), "--verdict", verdict,
            "--author", "agent:referee", "--text", f"The paper's statement is {verdict} to the target.", *extra]
    return vl(capsys, *args, *(["--statement", statement] if statement else []))


# The ladder from receipts ------------------------------------------------------------------------------------------

def test_a_passing_item_without_reviews_assesses_only_the_three_check_rows(passed, capsys):
    data = card(capsys)
    assert [r["row"] for r in data["ladder"]] == ROWS
    assert states(capsys) == {"lean_kernel": "passed", "second_kernel": "passed", "axioms": "passed",
                              "statement_fidelity": NOT, "paper_correspondence": NOT}
    assert data["ladder"][2]["detail"] == "axioms within propext" and "uninformative" not in data["ladder"][2]
    assert data["status"]["label"] == "verified"              # the derived label stays for compatibility


def test_under_foundations_the_axioms_row_says_first_that_it_is_uninformative(research_repo, monkeypatch, capsys):
    """Planted: the probes found set-theoretic foundations in the target, where the card's PROOF section says the
    axiom list is uninformative. The ladder row, all a header or the catalog summary shows, must say so too; its
    state stays `passed`. The control, the clean pass, has no such word."""
    from test_probe_status import AC, CARD
    monkeypatch.chdir(research_repo)
    with_target(research_repo)
    receipt(research_repo, {"VL.AddZero.main": {**NULL, "definitions": [], "totalized": [], "foundations": [CARD, AC]}})
    commit_all(research_repo)
    row = card(capsys)["ladder"][2]
    assert row["state"] == "passed" and row["uninformative"] and row["detail"].startswith("uninformative")
    assert "axioms passed (uninformative)" in vl(capsys, "show", "add-zero")[1].splitlines()[0]


def test_probe_fields_of_another_shape_in_a_sealed_receipt_do_not_break_the_card(research_repo, monkeypatch, capsys):
    """Planted: a sealed receipt whose probe entry has `limit_reached` true and lists that are numbers. The card and
    the brief still print; the control, the well-formed entry of the other tests, shows its findings."""
    monkeypatch.chdir(research_repo)
    with_target(research_repo)
    receipt(research_repo, {"VL.AddZero.main": {**NULL, "limit_reached": True, "boundary": 5, "totalized": 3,
                                                "foundations": "x"}})
    commit_all(research_repo)
    for extra in ((), ("--brief",)):
        rc, out, err = vl(capsys, "show", "add-zero", *extra)
        assert rc == 0 and "internal error" not in err and "VL.AddZero.main" in out


def test_text_for_people_leads_with_the_ladder_and_never_shows_verified_as_the_verdict(passed, capsys):
    out = vl(capsys, "show", "add-zero")[1]
    header = out.splitlines()[0]
    assert "status: check passed;" in header and "status: verified" not in out
    assert "ladder: Lean kernel passed · second kernel passed · axioms passed · fidelity not assessed · " \
           "paper not assessed" in header
    assert out.index("VERDICT LADDER") < out.index("STATUS (derived") < out.index("PROOF")
    assert "derived label `verified`, kept for compatibility" in out
    brief = vl(capsys, "show", "add-zero", "--brief")[1]
    assert "ladder:\n  Lean kernel: passed" in brief and "status: verified" not in brief
    found = vl(capsys, "find", "small")[1]
    assert "status: check passed]" in found and "status: verified" not in found


def test_with_the_label_mapping_removed_the_badge_comes_back(passed, capsys, monkeypatch):
    """The planted case: without `human_label`, text shows the bare `verified` the ladder exists to replace."""
    monkeypatch.setattr(ladder, "HUMAN_LABEL", {})
    assert "status: verified;" in vl(capsys, "show", "add-zero")[1].splitlines()[0]


@pytest.mark.parametrize("kernels,second", [(("lean", "nanoda"), "passed"), (("lean",), NOT)])
def test_the_second_kernel_row_needs_nanoda_in_the_receipt(research_repo, monkeypatch, capsys, kernels, second):
    monkeypatch.chdir(research_repo)
    with_target(research_repo)
    lean_receipt(research_repo, kernels)
    commit_all(research_repo)
    assert states(capsys)["lean_kernel"] == "passed" and states(capsys)["second_kernel"] == second


@pytest.mark.parametrize("make,expected", [
    (lambda root: lean_receipt(root, verdict="fail"), (NOT, NOT, NOT)),
    (lambda root: lean_receipt(root, verdict="fail", kernel_results={"lean": "passed", "nanoda": "failed"}),
     ("passed", "failed", NOT)),
    (lambda root: lean_receipt(root, assurance="exploratory"), (NOT, NOT, NOT)),
    (lambda root: lean_receipt(root, verdict="error"), (NOT, NOT, NOT)),
])
def test_a_check_that_did_not_pass_only_assesses_observed_kernels(research_repo, monkeypatch, capsys, make, expected):
    monkeypatch.chdir(research_repo)
    with_target(research_repo)
    make(research_repo)
    commit_all(research_repo)
    assert tuple(states(capsys)[k] for k in ROWS[:3]) == expected


def test_a_receipt_not_on_the_trusted_ref_assesses_nothing_and_an_edited_input_makes_it_stale(
        research_repo, monkeypatch, capsys):
    root = research_repo
    monkeypatch.chdir(root)
    with_target(root)
    lean_receipt(root)                                               # only in the worktree: a proposal
    assert states(capsys)["lean_kernel"] == NOT
    assert card(capsys)["ladder"][0]["detail"] == "the receipt is not on the trusted ref"
    commit_all(root)
    assert states(capsys)["lean_kernel"] == "passed"                  # control: the same receipt, admitted
    (root / "Fixture" / "Basic.lean").write_text("-- changed\n")
    commit_all(root, "edit an input")
    assert [states(capsys)[k] for k in ROWS[:3]] == ["stale"] * 3


def test_a_python_check_assesses_no_lean_row(research_repo, monkeypatch, capsys):
    monkeypatch.chdir(research_repo)
    assert states(capsys) == {k: NOT for k in ROWS}
    assert card(capsys)["ladder"][0]["detail"] == "no receipt"


# Fidelity and correspondence ---------------------------------------------------------------------------------------

def test_reviews_fill_their_rows_and_change_no_status(passed, capsys):
    assert vl(capsys, "review", "add-zero", "--kind", "fidelity", *meaning_args(), "--verdict", "faithful",
              "--author", "agent:referee", "--text", "States n + 0 = n.")[0] == 0
    commit_all(passed)
    assert states(capsys)["statement_fidelity"] == "faithful" and states(capsys)["paper_correspondence"] == NOT
    before = card(capsys)["status"]
    assert review_correspondence(capsys, "weaker", "Theorem 2.3")[0] == 0
    assert states(capsys)["paper_correspondence"] == NOT              # written but not admitted: a proposal
    commit_all(passed)
    row = card(capsys)["ladder"][4]
    assert row["state"] == "weaker" and row["statements"][0]["statement"] == "Theorem 2.3"
    assert row["statements"][0]["by"] == "agent:referee" and row["record"].startswith("research/reviews/add-zero/")
    assert card(capsys)["status"] == before and states(capsys)["statement_fidelity"] == "faithful"
    assert "paper correspondence: weaker (Theorem 2.3: weaker by agent:referee)" in vl(capsys, "show", "add-zero")[1]


def test_the_newest_review_of_a_statement_counts_and_disagreeing_statements_are_mixed(passed, capsys):
    assert review_correspondence(capsys, "same", "Theorem 1")[0] == 0
    commit_all(passed)
    assert states(capsys)["paper_correspondence"] == "same"
    assert review_correspondence(capsys, "stronger", "Theorem 2")[0] == 0
    commit_all(passed)
    row = card(capsys)["ladder"][4]
    assert row["state"] == "mixed" and [s["outcome"] for s in row["statements"]] == ["same", "stronger"]
    assert row["record"] is None
    assert review_correspondence(capsys, "stronger", "Theorem 1")[0] == 0           # renews Theorem 1
    commit_all(passed)
    assert states(capsys)["paper_correspondence"] == "stronger"                    # both statements now agree


def test_an_edit_of_what_a_review_read_makes_both_judgements_stale(passed, capsys):
    assert vl(capsys, "review", "add-zero", "--kind", "fidelity", *meaning_args(), "--verdict", "faithful",
              "--author", "agent:referee", "--text", "States n + 0 = n.")[0] == 0
    assert review_correspondence(capsys, "same")[0] == 0
    commit_all(passed)
    assert states(capsys)["statement_fidelity"] == "faithful" and states(capsys)["paper_correspondence"] == "same"
    (passed / TARGET).write_text((passed / TARGET).read_text().replace("n + 0 = n", "n + 1 = n + 1"))
    commit_all(passed, "edit the target")
    assert states(capsys)["statement_fidelity"] == "stale" and states(capsys)["paper_correspondence"] == "stale"
    assert "target changed since review" in card(capsys)["ladder"][4]["detail"]


def test_a_review_of_another_item_text_is_not_a_correspondence_of_this_one(passed, capsys):
    """Planted: the item's statement changes after the review; the control is the same review before the change."""
    assert review_correspondence(capsys, "same")[0] == 0
    commit_all(passed)
    assert states(capsys)["paper_correspondence"] == "same"
    item = passed / "research" / "items" / "add-zero.md"
    item.write_text(item.read_text().replace("For every n, n + 0 = n.", "For every n, n + 1 = n + 1."))
    commit_all(passed, "new statement")
    assert states(capsys)["paper_correspondence"] == "stale"


# The review command and record shape -------------------------------------------------------------------------------

def test_the_command_refuses_what_a_correspondence_review_cannot_be(passed, capsys):
    base = ["review", "add-zero", "--author", "agent:x", "--text", "x"]
    assert "needs --verdict in" in vl(capsys, *base, "--kind", "correspondence", *meaning_args())[2]
    assert "needs --verdict in" in vl(capsys, *base, "--kind", "correspondence", "--verdict", "equal")[2]
    assert "--statement is for --kind correspondence" in vl(
        capsys, *base, "--kind", "fidelity", "--verdict", "faithful", "--statement", "T", *meaning_args())[2]
    assert "--statement is for --kind correspondence" in vl(
        capsys, *base, "--kind", "correspondence", "--verdict", "same", "--statement", "a\nb")[2]
    rc, _, err = vl(capsys, *base, "--kind", "correspondence", "--verdict", "same")
    assert rc == 2 and "a correspondence write requires --expected-meaning-digest" in err
    rc, _, err = vl(capsys, *base, "--kind", "correspondence", "--verdict", "same",
                    "--expected-meaning-digest", "sha256:" + "0" * 64)
    assert rc == 2 and "differs from the current trusted meaning" in err
    assert vl(capsys, *base, "--kind", "understanding", "--verdict", "same", *meaning_args())[0] == 2
    assert vl(capsys, *base, "--kind", "correspondence", "--verdict", "same", "--dry-run")[0] == 0   # control
    old = Repo.open().trusted_item("add-zero").revision
    rc, _, err = vl(capsys, "review", f"add-zero@{old[:7]}", "--kind", "correspondence", "--verdict", "same",
                    "--author", "agent:x", "--text", "x", *meaning_args())
    assert rc == 0                                                    # the current revision named in full is fine
    rc, _, err = vl(capsys, "review", "add-zero@deadbeef", "--kind", "correspondence", "--verdict", "same",
                    "--author", "agent:x", "--text", "x", *meaning_args())
    assert rc == 2


def test_a_correspondence_review_without_a_meaning_or_a_known_outcome_is_not_a_record(passed, capsys, monkeypatch):
    repo = Repo.open()
    basis = meaning(repo, repo.trusted_item("add-zero"))
    fields = dict(item="add-zero", item_revision=repo.trusted_item("add-zero").revision, kind="correspondence",
                  author="agent:forger", text="Matches.", created="2026-10-11T00:00:00.000000+00:00",
                  target_path=TARGET, target_sha256=basis["files"][TARGET], meaning=basis,
                  meaning_digest=meaning_digest(basis))
    sound = seal_review({**fields, "verdict": "same"})
    assert review_problems(sound) == []                                                      # control
    no_meaning = seal_review({k: v for k, v in {**fields, "verdict": "same"}.items()
                              if k not in ("meaning", "meaning_digest")})
    odd_outcome = seal_review({**fields, "verdict": "identical"})
    statement_on_fidelity = seal_review({**fields, "kind": "fidelity", "verdict": "faithful", "statement": "T"})
    assert any("binds the meaning it read" in p for p in review_problems(no_meaning))
    assert any("needs a 'verdict' in" in p for p in review_problems(odd_outcome))
    assert any("'statement' is for a correspondence review" in p for p in review_problems(statement_on_fidelity))
    assert set(CORRESPONDENCE_OUTCOMES) == {"same", "stronger", "weaker", "different"}

    for forged in (no_meaning, odd_outcome):
        write_new_json(passed / "research" / "reviews" / "add-zero" / f"{forged['review_id'][:16]}.json", forged)
    commit_all(passed, "forged reviews")
    assert states(capsys)["paper_correspondence"] == NOT
    monkeypatch.setattr(repo_module, "review_problems", lambda review: [])                     # the guard disabled
    assert correspondence(Repo.open(), Repo.open().trusted_item("add-zero"))["state"] == "identical"


# The catalog -------------------------------------------------------------------------------------------------------

def test_the_catalog_carries_the_ladder_and_its_page_has_no_bare_verified_badge(passed, capsys, tmp_path):
    assert review_correspondence(capsys, "different", "Theorem 1")[0] == 0
    commit_all(passed)
    out = tmp_path / "out"
    assert vl(capsys, "export", "catalog", str(out))[0] == 0
    data = json.loads((out / "catalog.json").read_text())
    item = data["items"][0]
    assert [r["row"] for r in item["ladder"]] == ROWS and item["status"]["label"] == "verified"
    assert item["ladder"][4]["state"] == "different" and item["ladder"][4]["record"] == item["reviews"][0]["id"]
    assert item["ladder"][4]["statements"][0]["review"] == item["reviews"][0]["id"]
    assert item["ladder"][0]["record"] == item["proof"]["receipt"]
    assert data["counts"]["ladder"]["paper_correspondence"] == {"different": 1}
    assert data["counts"]["ladder"]["statement_fidelity"] == {NOT: 1}
    page = (out / "index.html").read_text()
    assert "Verdict ladder" in page and "check passed" in page and ">verified<" not in page
    assert 'data-status="verified"' in page                           # the filter keeps the label


def test_the_catalog_page_names_related_items_and_leads_its_summary_with_the_ladder(passed, capsys, tmp_path):
    """Planted: a relation to a verified item, which the page printed as a bare `(verified)`; and the summary line,
    which put the status badge before the ladder (ADR 0013 asks for the ladder first)."""
    make_item(passed, "user", uses=["add-zero"])
    commit_all(passed)
    out = tmp_path / "out"
    assert vl(capsys, "export", "catalog", str(out))[0] == 0
    page = (out / "index.html").read_text()
    assert ">add-zero</a> (check passed)" in page and "(verified)" not in page
    summary = re.search(r'id="add-zero"[^>]*><summary>(.*?)</summary>', page).group(1)
    assert summary.index("Lean kernel passed") < summary.index("check passed")


# Review adversarial findings ---------------------------------------------------------------------------------------

def test_a_statement_that_is_not_text_is_a_malformed_record_not_a_crash(passed):
    """Planted: a hand-written review with `statement: 5`; the control is the same review with a text statement."""
    repo = Repo.open()
    basis = meaning(repo, repo.trusted_item("add-zero"))
    fields = dict(item="add-zero", item_revision=repo.trusted_item("add-zero").revision, kind="correspondence",
                  author="agent:x", text="Matches.", created="2026-10-11T00:00:00.000000+00:00", verdict="same",
                  target_path=TARGET, target_sha256=basis["files"][TARGET], meaning=basis,
                  meaning_digest=meaning_digest(basis))
    assert review_problems(seal_review({**fields, "statement": "Theorem 1"})) == []
    for odd in (5, ["T"], None):
        problems = review_problems(seal_review({**fields, "statement": odd}))
        assert any("'statement' must be str" in p for p in problems), problems


def test_a_statement_whose_review_went_stale_is_not_dropped_by_a_fresh_review_of_another(passed, capsys):
    """Planted: Theorem 1 was judged `weaker`, the item's statement then changed, and only Theorem 2 was judged
    again (`same`): the ladder must not read `same`. The control is the same two reviews with no edit between."""
    assert review_correspondence(capsys, "weaker", "Theorem 1")[0] == 0
    commit_all(passed)
    item = passed / "research" / "items" / "add-zero.md"
    item.write_text(item.read_text().replace("For every n, n + 0 = n.", "For every n, n + 1 = n + 1."))
    commit_all(passed, "new statement")
    assert review_correspondence(capsys, "same", "Theorem 2")[0] == 0
    commit_all(passed)
    row = card(capsys)["ladder"][4]
    assert row["state"] == "mixed", row
    assert {(s["statement"], s["outcome"]) for s in row["statements"]} == {("Theorem 1", "stale"),
                                                                           ("Theorem 2", "same")}
    assert review_correspondence(capsys, "same", "Theorem 1")[0] == 0               # renewed: no stale row left
    commit_all(passed)
    assert states(capsys)["paper_correspondence"] == "same"
