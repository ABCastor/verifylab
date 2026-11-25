"""Contextual appraisal contracts: actual classifier output, immutable attribution, separate applicability."""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
import subprocess
import sys

import pytest

from conftest import commit_all, git
from verifylab.appraisals import appraisal_view, load_assessment, manifest
from verifylab.cli import main
from verifylab.records import RecordError, review_problems, seal_review, sha256_hex
from verifylab.repo import Repo, TrustedRepo


def payload(**changes):
    return {"schema": "vl.appraisal.v1", "subject": "connection", "ratings": {"novelty": {"value": "high"}},
            "input_context": "Subject, reduction note and supplied source section 3; no literature search.", **changes}


def write_input(root, value):
    path = root / "assessment.json"
    path.write_text(json.dumps(value))
    return path


def record(root, value=None, author="agent:evaluator", text=None, extra=()):
    path = write_input(root, value or payload())
    args = ["review", "add-zero", "--kind", "appraisal", "--author", author, "--assessment", str(path), "--json", *extra]
    if text is not None:
        args += ["--text", text]
    return main(args)


def reviews(root):
    return sorted((root / "research/reviews/add-zero").glob("*.json"))


def test_classifier_only_and_separately_attributed_synthesis(research_repo, monkeypatch, capsys):
    root = research_repo
    monkeypatch.chdir(root)
    value = payload(provenance={"provider": "external", "model": "declared", "run": "run-1", "role": "classifier"},
                    raw_output={"novelty": "high"})
    assert record(root, value) == 0
    first = json.loads(capsys.readouterr().out)["review"]
    assert first["text"] == "" and review_problems(first) == []
    assessment = first["assessment"]
    assert assessment["raw_output"] == value["raw_output"]
    assert assessment["basis"]["input_context"] == value["input_context"]
    assert record(root, payload(review_references=[first["review_id"]]), "agent:writer", "A potentially useful mapping.") == 0
    second = json.loads(capsys.readouterr().out)["review"]
    assert second["author"] == "agent:writer" and second["assessment"]["review_references"] == [first["review_id"]]
    assert len(reviews(root)) == 2


def test_opposed_authors_and_repeated_judgments_all_survive(research_repo, monkeypatch, capsys):
    monkeypatch.chdir(research_repo)
    for author, label in [("agent:one", "high"), ("agent:two", "low"), ("agent:one", "medium")]:
        assert record(research_repo, payload(ratings={"novelty": {"value": label}}), author) == 0
        capsys.readouterr()
    assert len(Repo.open(research_repo).reviews("add-zero")) == 3


def test_context_changes_only_appraisal_applicability(research_repo, monkeypatch, capsys):
    root = research_repo
    monkeypatch.chdir(root)
    note = root / "context.md"
    note.write_text("Source mapping A")
    assert record(root, payload(context_files=["context.md"], goal="explain A")) == 0
    capsys.readouterr()
    repo = Repo.open(root)
    item = repo.load_item("add-zero")
    [stored] = repo.reviews(item.id)
    assert appraisal_view(repo, item, stored)["applicability"]["state"] == "current"
    note.write_text("Source mapping B")
    assert appraisal_view(repo, item, stored)["applicability"]["state"] == "stale"
    # Goal, sources, context and rubric do not enter question/meaning contracts.
    from verifylab.records import question_digest
    assert question_digest(item) == question_digest(repo.load_item("add-zero"))
    note.write_text("Source mapping A")
    item_path = root / item.path
    item_path.write_text(item_path.read_text() + "new connection")
    assert "subject revision changed" in appraisal_view(repo, repo.load_item(item.id), stored)["applicability"]["reasons"]


def test_admitted_appraisals_read_trusted_context_not_local_files(research_repo, monkeypatch, capsys):
    root = research_repo
    monkeypatch.chdir(root)
    (root / "context.md").write_text("A")
    assert record(root, payload(context_files=["context.md"])) == 0
    capsys.readouterr()
    commit_all(root)
    repo = TrustedRepo(Repo.open(root).config)
    item = repo.trusted_item("add-zero")
    [stored] = repo.reviews(item.id)
    (root / "context.md").write_text("uncommitted B")
    assert appraisal_view(repo, item, stored)["applicability"]["state"] == "current"
    commit_all(root)
    repo = TrustedRepo(Repo.open(root).config)
    assert appraisal_view(repo, repo.trusted_item(item.id), repo.reviews(item.id)[0])["applicability"]["state"] == "stale"


def test_manifest_changes_keep_historical_record_valid(research_repo, monkeypatch, capsys):
    root = research_repo
    monkeypatch.chdir(root)
    assert record(root) == 0
    capsys.readouterr()
    config = root / "research/vl.toml"
    config.write_text(config.read_text() + '\n[appraisal]\nversion=2\n[appraisal.criteria.novelty]\nquestion="What changed?"\nlabels=["high","medium","low"]\nordered=true\ndescription="Reversed rubric"\n')
    commit_all(root)
    repo = Repo.open(root)
    [stored] = repo.reviews("add-zero")
    assert stored.problems == ()
    assert review_problems(stored.data) == []
    assert "appraisal manifest changed" in appraisal_view(repo, repo.load_item("add-zero"), stored)["applicability"]["reasons"]
    assert manifest(repo)["criteria"]["novelty"]["labels"] == ["high", "medium", "low"]


def test_unknown_abstain_and_nominal_categories_are_explicit(research_repo, monkeypatch, capsys):
    root = research_repo
    monkeypatch.chdir(root)
    assert record(root, payload(ratings={"novelty": {"abstention": "unknown"}, "usefulness": {"abstention": "abstain"}}, goal="explain A")) == 0
    first = json.loads(capsys.readouterr().out)["review"]
    assert "value" not in first["assessment"]["ratings"]["novelty"]
    config = root / "research/vl.toml"
    config.write_text(config.read_text() + '\n[appraisal.criteria.action]\nquestion="Next action?"\nlabels=["read","test","pause"]\nordered=false\n')
    commit_all(root)
    assert record(root, payload(ratings={"action": {"value": "read"}})) == 0
    assert manifest(Repo.open(root))["criteria"]["action"]["ordered"] is False


def test_optional_distribution_and_native_confidence_retained(research_repo, monkeypatch, capsys):
    monkeypatch.chdir(research_repo)
    rating = {"value": "high", "distribution": {"low": .1, "medium": .2, "high": .7}, "confidence": 3.5}
    assert record(research_repo, payload(ratings={"novelty": rating})) == 0
    assert json.loads(capsys.readouterr().out)["review"]["assessment"]["ratings"]["novelty"] == rating


@pytest.mark.parametrize("change", [
    {"ratings": None}, {"ratings": {"novelty": "high"}}, {"ratings": {}}, {"ratings": {"unknown": {"value": "high"}}},
    {"ratings": {"novelty": {"value": "unknown"}}}, {"ratings": {"novelty": {"abstention": "error"}}},
    {"ratings": {"novelty": {"value": "high", "abstention": "abstain"}}},
    {"ratings": {"novelty": {"value": "high", "distribution": {"high": 1}}}},
    {"ratings": {"novelty": {"value": "high", "distribution": {"low": 0, "medium": 0, "high": .9}}}},
    {"ratings": {"novelty": {"value": "high", "confidence": True}}},
    {"ratings": {"usefulness": {"value": "low"}}}, {"input_context": ""}, {"goal": 7}, {"subject": "unsupported"},
    {"raw_output": float("nan")}, {"provenance": {"made_up": "x"}}, {"review_references": ["abc"]},
    {"sources": [{"note": "missing locator"}]}, {"context_files": ["../secret"]}, {"context_files": ["/etc/passwd"]},
    {"context_files": ["missing"]}, {"context_files": ["context.md", "context.md"]}, {"extras": {}},
])
def test_malformed_input_fails_closed(research_repo, monkeypatch, capsys, change):
    monkeypatch.chdir(research_repo)
    assert record(research_repo, payload(**change)) == 2
    assert not reviews(research_repo)
    assert "internal error" not in capsys.readouterr().err


def test_duplicate_keys_and_context_symlink_escape_rejected(research_repo, monkeypatch, capsys, tmp_path):
    root = research_repo
    monkeypatch.chdir(root)
    path = write_input(root, payload())
    path.write_text('{"schema":"vl.appraisal.v1","schema":"vl.appraisal.v1"}')
    assert main(["review", "add-zero", "--kind", "appraisal", "--author", "agent:x", "--assessment", str(path)]) == 2
    capsys.readouterr()
    outside = tmp_path / "outside"
    outside.write_text("private")
    (root / "context.md").symlink_to(outside)
    assert record(root, payload(context_files=["context.md"])) == 2
    assert not reviews(root)


def test_human_approval_and_synopsis_checks_still_apply(research_repo, monkeypatch, capsys):
    monkeypatch.chdir(research_repo)
    assert record(research_repo, author="human:ada") == 2
    assert "--human-approved" in capsys.readouterr().err
    assert not reviews(research_repo)
    assert record(research_repo, author="human:ada", extra=["--human-approved"]) == 0
    capsys.readouterr()
    assert main(["review", "add-zero", "--kind", "understanding", "--author", "agent:x"]) == 2
    assert "needs --text" in capsys.readouterr().err


def test_all_content_participates_in_deduplication(research_repo, monkeypatch, capsys):
    root = research_repo
    monkeypatch.chdir(root)
    assert record(root) == 0
    capsys.readouterr()
    assert record(root) == 2
    assert "identical review" in capsys.readouterr().err
    for change in [{"provenance": {"run": "different"}}, {"goal": "new goal"}, {"raw_output": "different"},
                   {"input_context": "different actual context"}, {"sources": [{"locator": "source"}]}]:
        assert record(root, payload(**change)) == 0
        capsys.readouterr()
    assert len(reviews(root)) == 6


def test_concurrent_identical_writes_are_deduplicated(research_repo):
    root = research_repo
    path = write_input(root, payload())
    code = ("import sys,time; from verifylab.commands import review; from verifylab.cli import main; "
            "original=review.write_new_json; "
            "review.write_new_json=lambda *a: (time.sleep(.15),original(*a))[-1]; sys.exit(main(sys.argv[1:]))")
    command = [sys.executable, "-c", code, "review", "add-zero", "--kind", "appraisal", "--author", "agent:x", "--assessment", str(path)]
    def invoke(_):
        return subprocess.run(command, cwd=root, capture_output=True, text=True)
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(invoke, range(2)))
    assert sorted(r.returncode for r in results) == [0, 2], [r.stderr for r in results]
    assert len(reviews(root)) == 1


def test_review_lock_cannot_be_accidentally_admitted_in_legacy_repo(research_repo, monkeypatch, capsys):
    root = research_repo
    monkeypatch.chdir(root)
    assert not (root / '.gitignore').exists()
    assert record(root) == 0
    capsys.readouterr()
    assert (root / '.git/vl-review-write.lock').is_file()
    commit_all(root, 'all ordinary research files')
    assert not any('.lock' in path for path in git(root, 'ls-files').splitlines())
    assert main(['validate']) == 0


def test_persisted_malformed_assessment_is_rejected_without_crash(research_repo, monkeypatch, capsys):
    monkeypatch.chdir(research_repo)
    assert record(research_repo) == 0
    first = json.loads(capsys.readouterr().out)["review"]
    for bad in [None, [], {}, {**first["assessment"], "ratings": None},
                {**first["assessment"], "basis": {}}, {**first["assessment"], "basis_digest": "wrong"}]:
        malformed = seal_review({**first, "assessment": bad})
        assert review_problems(malformed)
    assert review_problems(seal_review({**first, "verdict": "great"}))
    assert review_problems(seal_review({**first, "kind": "understanding", "text": "explanation"}))


def test_unadmitted_context_file_missing_or_escaping_is_unknown(research_repo, monkeypatch, capsys, tmp_path):
    root = research_repo
    monkeypatch.chdir(root)
    note = root / "context.md"
    note.write_text("local input")
    assert record(root, payload(context_files=["context.md"])) == 0
    capsys.readouterr()
    repo = Repo.open(root)
    item = repo.load_item("add-zero")
    stored = repo.reviews(item.id)[0]
    note.unlink()
    assert appraisal_view(repo, item, stored)["applicability"]["state"] == "unknown"
    outside = tmp_path / "outside-context"
    outside.write_text("local input")
    note.symlink_to(outside)
    assert appraisal_view(repo, item, stored)["applicability"]["state"] == "unknown"


@pytest.mark.parametrize("location", ["trusted", "worktree", "incoming"])
def test_validate_rejects_malformed_manifest_without_any_appraisals(research_repo, monkeypatch, capsys, location):
    root = research_repo
    monkeypatch.chdir(root)
    args = ["validate", "--json"]
    if location == "incoming":
        git(root, "checkout", "-qb", "new-rubric")
        args += ["--incoming", "new-rubric"]
    config = root / "research/vl.toml"
    config.write_text(config.read_text() + '\n[appraisal.criteria.novelty]\nquestion="What changed?"\nlabels=["low","low"]\n')
    if location in ("trusted", "incoming"):
        commit_all(root)
    if location == "incoming":
        git(root, "checkout", "-q", "trusted")
    assert main(args) == 1
    data = json.loads(capsys.readouterr().out)
    assert any("distinct labels" in error["message"] for error in data["errors"])
    assert not reviews(root)


def test_low_appraisal_and_changed_rubric_leave_protected_status_current(research_repo, monkeypatch, capsys):
    from conftest import item_question, synthetic_receipt
    from verifylab.records import write_new_json
    from verifylab.status import derive
    root = research_repo
    monkeypatch.chdir(root)
    candidate = "Fixture/Basic.lean"
    receipt = synthetic_receipt(root, dict(item="add-zero", item_revision=Repo.open(root).load_item("add-zero").revision,
        question_digest=item_question(root), adapter="lean-comparator", assurance="protected", verdict="pass", reasons=[],
        inputs={"files": {candidate: sha256_hex((root / candidate).read_bytes())}}, environment={}, checked={},
        command=["vl"], started_at="t0", finished_at="t1", tool_version="vl 0.1.0"))
    write_new_json(root / "research/evidence/add-zero" / (receipt["receipt_id"][:16] + ".json"), receipt)
    (root / "context.md").write_text("Goal A")
    commit_all(root)
    assert record(root, payload(ratings={"novelty": {"value": "low"}}, context_files=["context.md"])) == 0
    capsys.readouterr()
    commit_all(root)
    def state():
        repo = Repo.open(root)
        items, errors = repo.load_items()
        assert not errors
        return derive(repo, items["add-zero"], items).label
    assert state() == "verified"
    (root / "context.md").write_text("Goal B")
    config = root / "research/vl.toml"
    config.write_text(config.read_text() + '\n[appraisal]\nversion=2\n')
    commit_all(root)
    assert state() == "verified"
    repo = Repo.open(root)
    assert appraisal_view(repo, repo.load_item("add-zero"), repo.reviews("add-zero")[0])["applicability"]["state"] == "stale"


def test_score_only_fractional_output_roundtrips_without_label_or_prose(research_repo, monkeypatch, capsys):
    root = research_repo
    monkeypatch.chdir(root)
    rating = {"score": 1.6, "distribution": {"low": .1, "medium": .2, "high": .7}}
    raw = {"native_score": 1.6, "native_probabilities": [.1, .2, .7]}
    assert record(root, payload(ratings={"novelty": rating}, raw_output=raw)) == 0
    review = json.loads(capsys.readouterr().out)["review"]
    assert review["assessment"]["ratings"]["novelty"] == rating
    assert "value" not in review["assessment"]["ratings"]["novelty"]
    assert review["assessment"]["raw_output"] == raw
    assert review["text"] == "" and review_problems(review) == []
    assert json.loads(reviews(root)[0].read_text())["assessment"]["ratings"]["novelty"] == rating


@pytest.mark.parametrize("rating", [
    {"score": -0.1}, {"score": 2.1}, {"score": True}, {"score": "1.5"}, {"score": None}, {"score": []},
    {"score": float("nan")}, {"score": float("inf")}, {"score": -float("inf")},
    {"score": 1.5, "value": "high"}, {"score": 1.5, "abstention": "unknown"},
    {"score": 1.5, "distribution": {"low": .1, "medium": .2, "high": .7}},
])
def test_invalid_or_inconsistent_score_rejected(research_repo, monkeypatch, capsys, rating):
    monkeypatch.chdir(research_repo)
    assert record(research_repo, payload(ratings={"novelty": rating})) == 2
    assert not reviews(research_repo)
    assert "internal error" not in capsys.readouterr().err


def test_score_rejected_for_nominal_criterion(research_repo, monkeypatch, capsys):
    root = research_repo
    monkeypatch.chdir(root)
    config = root / "research/vl.toml"
    config.write_text(config.read_text() + '\n[appraisal.criteria.action]\nquestion="Next action?"\nlabels=["read","test","pause"]\nordered=false\n')
    commit_all(root)
    assert record(root, payload(ratings={"action": {"score": 1}})) == 2
    assert "score requires an ordered criterion" in capsys.readouterr().err
    assert not reviews(root)
    assert record(root, payload(ratings={"action": {"value": "test"}})) == 0


@pytest.mark.parametrize("score", [0, 10, 4.25])
def test_score_uses_positions_in_custom_label_order(research_repo, monkeypatch, capsys, score):
    root = research_repo
    monkeypatch.chdir(root)
    config = root / "research/vl.toml"
    config.write_text(config.read_text() + '\n[appraisal.criteria.novelty]\nquestion="Novelty on supplied rubric?"\nlabels=[' + ','.join(json.dumps(str(i)) for i in range(11)) + ']\nordered=true\n')
    commit_all(root)
    assert record(root, payload(ratings={"novelty": {"score": score}})) == 0
    review = json.loads(capsys.readouterr().out)["review"]
    assert review["assessment"]["ratings"]["novelty"] == {"score": score}


def test_persisted_scores_are_checked_against_the_bound_rubric(research_repo, monkeypatch, capsys):
    root = research_repo
    monkeypatch.chdir(root)
    assert record(root, payload(ratings={"novelty": {"score": .75}})) == 0
    first = json.loads(capsys.readouterr().out)["review"]
    for rating in [{"score": -1}, {"score": 3}, {"score": True},
                   {"score": 1, "distribution": {"low": 0, "medium": 0, "high": 1}}]:
        malformed = seal_review({**first, "assessment": {**first["assessment"], "ratings": {"novelty": rating}}})
        assert review_problems(malformed)
    # Extending the current rubric does not retroactively give historical scores a wider domain.
    config = root / "research/vl.toml"
    config.write_text(config.read_text() + '\n[appraisal.criteria.novelty]\nquestion="Extended?"\nlabels=["low","medium","high","exceptional"]\n')
    commit_all(root)
    assert review_problems(first) == []


@pytest.mark.parametrize("kind", ["understanding", "correction", "retraction", "compare"])
def test_legacy_review_kinds_reject_unsupported_top_level_score(kind):
    review = seal_review({"item": "add-zero", "item_revision": "a" * 40, "kind": kind,
                          "author": "agent:reviewer", "created": "now", "text": "reason", "score": 1.5})
    assert "score is supported only inside appraisal assessment.ratings" in review_problems(review)
    clean = {key: value for key, value in review.items() if key != "score"}
    assert review_problems(seal_review(clean)) == []
