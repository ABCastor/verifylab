"""What statement-probe findings do to a result: a vacuity hit makes it `vacuous`, never `verified`; a triviality
hit keeps it verified but `vl validate` warns until a fidelity review of the current target acknowledges it; the
card states proof and meaning separately. Receipts here are synthetic: the Lean side is tested elsewhere."""

from __future__ import annotations

import json
from pathlib import Path

from verifylab.cli import main
from review_helpers import meaning_args
from verifylab.records import review_problems, seal_receipt, seal_review, sha256_hex, write_new_json
from verifylab.repo import Repo
from verifylab.status import derive

from conftest import synthetic_receipt, commit_all, item_question

TARGET = "research/targets/add-zero.lean"
NULL = {"trivial_by": None, "vacuous_by": None, "prop_hypotheses": 0}


def vl(capsys, *args):
    rc = main(list(args))
    captured = capsys.readouterr()
    return rc, captured.out, captured.err


def with_target(root: Path) -> None:
    """Give add-zero a Lean target on the trusted ref, so fidelity reviews can bind to it."""
    item = root / "research" / "items" / "add-zero.md"
    item.write_text(item.read_text().replace(
        "+++\nBody", f'[lean]\ntarget = "{TARGET}"\ntheorems = ["VL.AddZero.main"]\n+++\nBody'))
    target = root / TARGET
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("namespace VL.AddZero\ntheorem main (n : Nat) : n + 0 = n := sorry\nend VL.AddZero\n")
    commit_all(root, "target")


def receipt(root: Path, probes: dict | None, finished: str = "t1", assurance: str = "protected",
            lints: list | None = None) -> Path:
    lean = "Fixture/Basic.lean"
    checked = {"kernels": ["lean", "nanoda"], "permitted_axioms": ["propext"]}
    if lints is not None:
        checked["lints"] = lints
    if probes is not None:
        checked.update(probes=probes, probe_run={"battery": ["simp"], "heartbeats_per_attempt": 5000, "problems": []})
    data = synthetic_receipt(root, dict(
        item="add-zero", item_revision="a" * 40, question_digest=item_question(root, "add-zero"),
        adapter="lean-comparator", assurance=assurance, verdict="pass",
        reasons=[], inputs={"digest": "d", "files": {lean: sha256_hex((root / lean).read_bytes())}},
        environment={}, checked=checked, command=["vl"], started_at="t0", finished_at=finished,
        tool_version="vl 0.1.0"))
    path = root / "research" / "evidence" / "add-zero" / f"{data['receipt_id'][:16]}.json"
    write_new_json(path, data)
    return path


def status(root: Path):
    repo = Repo.open(root)
    items, _ = repo.load_items()
    return derive(repo, items["add-zero"], items)


def warnings(capsys) -> list[str]:
    rc, out, _ = vl(capsys, "validate")
    return [line for line in out.splitlines() if line.startswith("WARNING")]


def test_a_vacuous_target_is_never_verified(research_repo):
    root = research_repo
    receipt(root, {"VL.AddZero.main": {**NULL, "vacuous_by": "intros; omega", "prop_hypotheses": 2}})
    assert status(root).label == "pending-admission"
    assert any("`intros; omega` derives False" in n for n in status(root).notes)
    commit_all(root)
    result = status(root)
    assert result.label == "vacuous" and result.receipt.endswith(".json")
    assert any("VL.AddZero.main: `intros; omega` derives False from its hypotheses" in n for n in result.notes)


def test_guard_without_probe_findings_the_same_receipt_verifies(research_repo):
    """The planted control: the identical receipt with clean probes (or none recorded) is verified."""
    root = research_repo
    receipt(root, {"VL.AddZero.main": NULL})
    receipt(root, None, finished="t0")
    commit_all(root)
    assert status(root).label == "verified"


def test_validate_reports_vacuity(research_repo, monkeypatch, capsys):
    root = research_repo
    monkeypatch.chdir(root)
    receipt(root, {"VL.AddZero.main": {**NULL, "vacuous_by": "decide", "prop_hypotheses": 1}})
    commit_all(root)
    assert any("vacuous: VL.AddZero.main: `decide` derives False" in w and "Not verified" in w
               for w in warnings(capsys))


def test_triviality_warns_until_a_fidelity_review_of_the_current_target_acknowledges_it(
        research_repo, monkeypatch, capsys):
    root = research_repo
    monkeypatch.chdir(root)
    with_target(root)
    receipt(root, {"VL.AddZero.main": {**NULL, "trivial_by": "simp"}})
    commit_all(root)
    assert status(root).label == "verified"
    message = "automation alone closes VL.AddZero.main by `simp`"
    assert any(message in w for w in warnings(capsys))

    # A faithful review that does not acknowledge the triviality silences the fidelity warning only.
    assert vl(capsys, "review", "add-zero", "--kind", "fidelity", *meaning_args(), "--verdict", "faithful", "--author",
              "agent:referee", "--text", "States n + 0 = n.")[0] == 0
    commit_all(root)
    assert any(message in w for w in warnings(capsys))
    rc, out, _ = vl(capsys, "review", "add-zero", "--kind", "fidelity", *meaning_args(), "--verdict", "faithful", "--author",
                    "agent:referee", "--acknowledge", "trivial", "--text", "A definitional fact; trivial by design.",
                    "--json")
    assert rc == 0 and json.loads(out)["review"]["acknowledges"] == ["trivial"]
    commit_all(root)
    assert not any(message in w for w in warnings(capsys))

    # Editing the target makes that acknowledgement stale: the warning is back.
    (root / TARGET).write_text((root / TARGET).read_text() + "-- edited\n")
    commit_all(root)
    assert any(message in w for w in warnings(capsys))


def test_acknowledge_is_only_for_fidelity_reviews(research_repo, monkeypatch, capsys):
    monkeypatch.chdir(research_repo)
    rc, _, err = vl(capsys, "review", "add-zero", "--kind", "understanding", "--author", "agent:x",
                    "--acknowledge", "trivial", "--text", "x")
    assert rc == 2 and "--acknowledge only applies to --kind fidelity" in err
    forged = seal_review(dict(item="add-zero", item_revision="a" * 40, kind="understanding", author="agent:x",
                              text="x", created="t", acknowledges=["trivial"]))
    assert "only a fidelity review can acknowledge probe findings" in review_problems(forged)
    odd = seal_review(dict(item="add-zero", item_revision="a" * 40, kind="fidelity", author="agent:x",
                           text="x", created="t", verdict="faithful", acknowledges=["vacuous"]))
    assert any("'acknowledges' must be" in p for p in review_problems(odd))


def test_card_states_proof_and_meaning_separately(research_repo, monkeypatch, capsys):
    root = research_repo
    monkeypatch.chdir(root)
    with_target(root)
    receipt(root, {"VL.AddZero.main": {**NULL, "trivial_by": "simp", "prop_hypotheses": 1}})
    commit_all(root)
    rc, out, _ = vl(capsys, "show", "add-zero")
    order = ["STATUS", "PROOF (kernels, axioms, statement probes)", "protected pass", "kernels lean, nanoda",
             "axioms within propext", "- VL.AddZero.main: TRIVIAL: closed by `simp` alone; 1 Prop hypothesis, not refuted",
             "MEANING", "  fidelity: not reviewed", "EVIDENCE"]
    positions = [out.index(marker) for marker in order]
    assert positions == sorted(positions), out
    vl(capsys, "review", "add-zero", "--kind", "fidelity", *meaning_args(), "--verdict", "faithful", "--author", "agent:referee",
       "--acknowledge", "trivial", "--text", "Trivial by design.")
    commit_all(root)
    out = vl(capsys, "show", "add-zero")[1]
    meaning = out[out.index("MEANING"):out.index("EVIDENCE")]
    assert "fidelity: faithful (by agent:referee) on 20" in meaning
    assert "probe findings acknowledged by a review of the current target: trivial" in meaning
    card = json.loads(vl(capsys, "show", "add-zero", "--json")[1])["items"][0]
    assert card["proof"]["probes"]["VL.AddZero.main"]["trivial_by"] == "simp"
    assert card["fidelity"]["acknowledges"] == ["trivial"]
    assert "model" not in json.dumps(card).lower()          # no model-family field anywhere
    brief = vl(capsys, "show", "add-zero", "--brief", "--budget", "100000")[1]
    assert brief.index("proof:") < brief.index("meaning:") < brief.index("evidence:")


# Witness: a target theorem with Prop hypotheses needs a non-vacuity witness listed in [lean] witnesses -----------

def set_lean(root: Path, theorems: list[str], witnesses: list[str] | None) -> None:
    item = root / "research" / "items" / "add-zero.md"
    text = item.read_text()
    head, _, body = text.partition("[lean]\n")
    lean = f'[lean]\ntarget = "{TARGET}"\ntheorems = {json.dumps(theorems)}\n'
    if witnesses is not None:
        lean += f"witnesses = {json.dumps(witnesses)}\n"
    item.write_text(head + lean + "+++" + body.split("+++", 1)[1])


def test_hypotheses_without_a_witness_warn_and_a_listed_witness_silences_it(research_repo, monkeypatch, capsys):
    root = research_repo
    monkeypatch.chdir(root)
    with_target(root)
    receipt(root, {"VL.AddZero.main": {**NULL, "prop_hypotheses": 2},
                   "VL.AddZero.witness": {**NULL, "trivial_by": "decide"}})
    set_lean(root, ["VL.AddZero.main", "VL.AddZero.witness"], None)
    commit_all(root)
    missing = "target theorems with Prop hypotheses: VL.AddZero.main (2); add a non-vacuity witness"
    found = warnings(capsys)
    assert any(missing in w for w in found), found
    assert any("automation alone closes VL.AddZero.witness" in w for w in found)   # not yet known as a witness
    set_lean(root, ["VL.AddZero.main", "VL.AddZero.witness"], ["VL.AddZero.witness"])
    commit_all(root)
    found = warnings(capsys)
    assert not any(missing in w for w in found), found
    assert not any("automation alone closes" in w for w in found)          # a witness is expected to be easy
    out = vl(capsys, "show", "add-zero")[1]
    assert "- VL.AddZero.witness (witness): TRIVIAL: closed by `decide` alone" in out


def test_no_hypotheses_no_witness_needed(research_repo, monkeypatch, capsys):
    root = research_repo
    monkeypatch.chdir(root)
    with_target(root)
    receipt(root, {"VL.AddZero.main": NULL})
    commit_all(root)
    assert not any("witness" in w for w in warnings(capsys))


def test_a_witness_must_be_one_of_the_target_theorems(research_repo, monkeypatch, capsys):
    root = research_repo
    monkeypatch.chdir(root)
    with_target(root)
    set_lean(root, ["VL.AddZero.main"], ["VL.AddZero.witness"])
    rc, out, _ = vl(capsys, "validate")
    assert rc == 1 and "[lean] witnesses names 'VL.AddZero.witness', which is not in 'theorems'" in out
    from verifylab.adapters.lean_comparator import parse_spec
    table = {"target": TARGET, "theorems": ["VL.AddZero.main"], "proofs": {"VL.AddZero.main": "rfl"}}
    for witnesses, problem in ((["VL.AddZero.other"], "not in 'theorems'"), ("VL.AddZero.main", "list of theorem"),
                               (["VL.AddZero.main", "VL.AddZero.main"], "twice")):
        spec, problems = parse_spec({**table, "witnesses": witnesses}, "research/targets")
        assert spec is None and any(problem in p for p in problems), problems
    spec, _ = parse_spec({**table, "witnesses": ["VL.AddZero.main"]}, "research/targets")
    assert spec.witnesses == ("VL.AddZero.main",)


def test_card_lists_command_lints_as_warnings(research_repo, monkeypatch, capsys):
    root = research_repo
    monkeypatch.chdir(root)
    finding = {"file": "Fixture/Sol.lean", "line": 3, "kind": "notation", "text": 'local notation "x" => 0',
               "why": "notation can make the same text mean something else (S2)"}
    receipt(root, {"VL.AddZero.main": NULL}, lints=[finding] * 12)
    commit_all(root)
    out = vl(capsys, "show", "add-zero")[1]
    proof = out[out.index("PROOF"):out.index("MEANING")]
    assert "command lints over the files the candidate changed: 12 (warnings, never a verdict)" in proof
    assert proof.count("- Fixture/Sol.lean:3 notation: notation can make") == 10 and "… 2 more in" in proof
    assert status(root).label == "verified"                       # lints never change the status


def reseal(path: Path, probe_run: dict) -> None:
    """Replace a receipt by the same receipt with another probe run, sealed and named as vl check would."""
    data = json.loads(path.read_text())
    data["checked"]["probe_run"] = probe_run
    path.unlink()
    sealed = seal_receipt(data)
    write_new_json(path.parent / f"{sealed['receipt_id'][:16]}.json", sealed)


TIMED_OUT = {"battery": ["simp"], "heartbeats_per_attempt": 5000,
             "problems": ["the probes timed out after 10 s; unfinished theorems record null",
                          "VL.AddZero.main: no probe result"]}


def test_a_probe_crash_or_timeout_never_leaves_a_silent_verified(research_repo, monkeypatch, capsys):
    """The probes did not finish: the result stays verified (Comparator's verdict), but the status says the probes
    are incomplete, vl validate warns and the card shows it, so vacuity is never silently assumed away."""
    root = research_repo
    monkeypatch.chdir(root)
    path = receipt(root, {"VL.AddZero.main": {"trivial_by": None, "vacuous_by": None, "prop_hypotheses": None,
                                              "error": "no probe result"}})
    reseal(path, TIMED_OUT)
    commit_all(root)
    result = status(root)
    assert result.label == "verified"
    assert any(n.startswith("probes incomplete (the probes timed out after 10 s") for n in result.notes), result.notes
    assert any("probes incomplete" in w and "timed out" in w for w in warnings(capsys))
    out = vl(capsys, "show", "add-zero")[1]
    assert "probes incomplete" in out[out.index("STATUS"):out.index("PROOF")]


def test_skipped_probes_or_a_theorem_without_a_result_are_incomplete_too(research_repo, monkeypatch, capsys):
    root = research_repo
    monkeypatch.chdir(root)
    path = receipt(root, {"VL.AddZero.main": {"trivial_by": None, "vacuous_by": None, "prop_hypotheses": None}})
    reseal(path, {"battery": ["simp"], "problems": []})
    commit_all(root)
    assert any("no probe result for VL.AddZero.main" in n for n in status(root).notes)
    assert any("probes incomplete" in w for w in warnings(capsys))


def test_hypotheses_the_item_does_not_state_in_assumptions_warn(research_repo, monkeypatch, capsys):
    root = research_repo
    monkeypatch.chdir(root)
    with_target(root)
    set_lean(root, ["VL.AddZero.main", "VL.AddZero.side", "VL.AddZero.witness"], ["VL.AddZero.witness"])
    receipt(root, {"VL.AddZero.main": {**NULL, "prop_hypotheses": 3}, "VL.AddZero.side": {**NULL, "prop_hypotheses": 1},
                   "VL.AddZero.witness": {**NULL, "prop_hypotheses": 2}})
    message = ("the target has 3 hypotheses in VL.AddZero.main and 1 hypothesis in VL.AddZero.side; state them in "
               "assumptions")
    assert not any("state them in assumptions" in w for w in warnings(capsys))   # not admitted yet
    commit_all(root)
    found = [w for w in warnings(capsys) if "state them in assumptions" in w]
    assert len(found) == 1 and message in found[0], found                        # the witness is an instance
    from verifylab.commands import validate
    with monkeypatch.context() as patch:
        patch.setattr(validate, "_undeclared_hypotheses", lambda ctx, f: None)  # the guard, disabled
        assert not any("state them in assumptions" in w for w in warnings(capsys))
    item = root / "research" / "items" / "add-zero.md"
    item.write_text(item.read_text().replace("limits =", 'assumptions = ["n is a natural number"]\nlimits ='))
    assert not any("state them in assumptions" in w for w in warnings(capsys))   # control: stated


def test_only_the_newest_admitted_probes_count_for_undeclared_hypotheses(research_repo, monkeypatch, capsys):
    root = research_repo
    monkeypatch.chdir(root)
    with_target(root)
    receipt(root, {"VL.AddZero.main": {**NULL, "prop_hypotheses": 2}}, finished="2026-10-01T10:00:00+00:00")
    receipt(root, {"VL.AddZero.main": NULL}, finished="2026-10-01T11:00:00+00:00")
    receipt(root, None, finished="2026-10-01T12:00:00+00:00")                     # no probes: says nothing
    commit_all(root)
    assert not any("state them in assumptions" in w for w in warnings(capsys))
    receipt(root, {"VL.AddZero.main": {**NULL, "prop_hypotheses": 1}}, finished="2026-10-01T13:00:00+00:00")
    commit_all(root)
    assert any("the target has 1 hypothesis in VL.AddZero.main; state them in assumptions" in w
               for w in warnings(capsys))
