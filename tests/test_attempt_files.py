"""`vl attempts check|table` (docs/PURPOSE-AND-ATTEMPTS.md): each rule of the convention with a planted violation
next to the clean file, the order of forecast and outcome read from a temporary Git repository, and the table on a
fixture small enough to compute by hand."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from conftest import commit_all, git
from verifylab import attempt_files
from verifylab.cli import main
from verifylab.formalization_yaml import CatalogueError, parse_yaml

ROOT = Path(__file__).resolve().parents[1]

FORECAST = """  - by: model-a/high
    at: 2026-11-22T22:10+01:00
    p_accepted: 0.8
    value:
      reached_lines: [-640, -560]      # change against base
    cost: {agent_hours: [1, 3], usd: [0, 0]}
    basis: one call site
"""
OUTCOME = """outcome:
  status: accepted
  at: 2026-11-22T23:59+01:00
  commit: null
  value: {reached_lines: -615, physical_lines: null}
  cost: {agent_hours: 2.5, usd: 0}
  contract: {statements: same, axioms: [propext, Classical.choice, Quot.sound], comparator: accepted}
  evidence: logs/build.log
"""
SCORES = """scores:
  - what: novelty
    value: 3
    reader: SP
    task: say whether the argument is in the literature
    by: model-a/high
    at: 2026-11-22T23:59+01:00
    basis: "nearest: [Example text, Proposition 2]; search: the two cited texts"
"""
SENTENCE = """scores:
  - what: explanatory
    text: "explains why the converse is not needed: its only user takes one direction"
    by: person:Reader A
    at: 2026-11-25T10:00+01:00
"""
UNVERIFIED = """outcome:
  status: unverified
  by: model-a/high
  at: 2026-11-22T23:59+01:00
  commit: 1dd94f8
  value: {reached_lines: -615}
  cost: {agent_hours: 2.5, build_minutes: null}
  contract: {statements: null, axioms: null, comparator: null}
"""
HEAD = """---
schema: vl.attempt/2
id: 2026-11-22-one-direction
purpose: shorten
base: 36468ee
claim: one direction of the equivalence is enough for its only user
"""
VALID = HEAD + "forecasts:\n" + FORECAST + OUTCOME + SCORES + "---\nWhat was tried.\n"


def run(capsys, *args):
    rc = main(["attempts", *map(str, args)])
    captured = capsys.readouterr()
    return rc, captured.out, captured.err


def folder(tmp_path: Path, *texts: str) -> Path:
    directory = tmp_path / "attempts"
    directory.mkdir(exist_ok=True)
    for number, text in enumerate(texts, 1):
        (directory / f"2026-11-22-attempt-{number}.md").write_text(text)
    return directory


def test_a_file_that_follows_the_convention_is_clean_without_a_verifylab_project(tmp_path, monkeypatch, capsys):
    folder(tmp_path, VALID, VALID.replace(OUTCOME, "").replace(SCORES, ""))        # the second one is open
    (tmp_path / "attempts" / "README.md").write_text("Not an attempt.\n")
    monkeypatch.chdir(tmp_path)                                                    # no vl.toml, no Git: DIR defaults
    rc, out, err = run(capsys, "check")
    assert rc == 0 and out == "attempts: 2 attempt files, 1 open, 0 with problems\n"
    assert err.count("was not checked") == 1 and "not in a Git repository" in err


RULES = [
    ("schema: vl.attempt/2", "schema: vl.attempt/1", "'schema' must be vl.attempt/2"),
    ("id: 2026-11-22-one-direction\n", "", "'id' is required"),
    ("purpose: shorten\n", "", "'purpose' is required"),
    ("base: 36468ee", "base: 1234567", "'base' is required: a non-empty text (put a value made of digits in quotes)"),
    ("claim: one direction of the equivalence is enough for its only user", "claim: \"\"", "'claim' is required"),
    ("claim: one", "notes: later\nclaim: one", "unknown key 'notes'"),
    ("forecasts:\n" + FORECAST, "", "'forecasts' is required: a list"),
    ("forecasts:\n" + FORECAST, "forecasts: [{by: model-a/high, at: 2026-11-22T22:10+01:00, basis: one call site}]\n",
     "write 'forecasts' as a block list"),
    ("  - by: model-a/high\n    at:", "  - at:", "forecast 1: 'by' is required"),
    ("    at: 2026-11-22T22:10+01:00\n    p_accepted", "    p_accepted", "forecast 1: 'at' is required"),
    ("p_accepted: 0.8", "p_accepted: 1.2", "forecast 1: 'p_accepted' must be a number between 0 and 1"),
    ("[-640, -560]", "[-560, -640]", "forecast 1: value.reached_lines must be [low, high]"),
    ("usd: [0, 0]", "usd: 0", "forecast 1: cost.usd must be [low, high]"),
    ("basis: one call site", "basis: \"\"", "forecast 1: 'basis' is required"),
    ("basis: one call site", "basis: one call site\n    novelty: 5", "forecast 1: unknown key 'novelty'"),
    (OUTCOME, "outcome: null\n", "outcome: omit 'outcome' while open, or use 'status: unverified'"),
    ("status: accepted", "status: null", "outcome: 'status' must be one of accepted, rejected, abandoned, unverified, not-run"),
    ("status: accepted", "status: done", "outcome: 'status' must be one of accepted, rejected, abandoned, unverified, not-run"),
    ("statements: same, ", "statements: null, ", "outcome: 'accepted' requires a non-empty statement reference"),
    ("axioms: [propext, Classical.choice, Quot.sound], ", "axioms: [false], ", "outcome: 'accepted' requires a non-empty statement reference"),
    ("statements: same, ", "statements: {}, ", "outcome: 'accepted' requires a non-empty statement reference"),
    ("statements: same, ", 'statements: " ", ', "outcome: 'accepted' requires a non-empty statement reference"),
    ("axioms: [propext, Classical.choice, Quot.sound], ", "", "outcome: 'accepted' requires a non-empty statement reference"),
    ("  contract: {statements: same, axioms: [propext, Classical.choice, Quot.sound], comparator: accepted}\n", "",
     "a kept change awaiting a check is 'status: unverified'"),
    ("  evidence: logs/build.log\n", "  evidence: logs/build.log\n  by: 7\n", "outcome: 'by' names who measured"),
    ("  evidence: logs/build.log\n", "  evidence: logs/build.log\n  measured_by: model-a/high\n", "outcome: unknown key 'measured_by'"),
    ("reached_lines: -615", "reached_lines: many", "outcome: value.reached_lines must be a measured number or null"),
    ("  evidence: logs/build.log\n", "  evidence: logs/build.log\n  kept: {reached_lines: -30}\n",
     "outcome: 'kept' says what stays of an attempt that ended abandoned or rejected; what an attempt that ended 'accepted' "
     "measured is its 'value'"),
    ("status: accepted\n", "status: rejected\n  kept: {reached_lines: some}\n", "outcome: kept.reached_lines must be a measured number or null"),
    ("status: accepted\n", "status: rejected\n  kept: -30\n", "outcome: 'kept' must be a mapping of dimension to a measured number or null"),
    ("  - what: novelty\n", "  - what: \"\"\n", "score 1: 'what' is required"),
    ("value: 3\n", "value: 11\n", "score 1: 'value' must be an integer from 1 to 10"),
    ("value: 3\n", "value: 3.5\n", "score 1: 'value' must be an integer from 1 to 10"),
    ("    reader: SP\n", "", "score 1: 'reader' is required"),
    ("    task: say whether the argument is in the literature\n", "", "score 1: 'task' is required"),
    ("    by: model-a/high\n    at: 2026-11-22T23:59+01:00\n    basis: \"n", "    at: 2026-11-22T23:59+01:00\n    basis: \"n",
     "score 1: 'by' is required"),
    ("    basis: \"nearest: [Example text, Proposition 2]; search: the two cited texts\"\n", "", "score 1: 'basis' is required"),
    ("value: 3\n", "value: 7\n", "score 1: a 7 or more given by an agent is a forecast"),
    ("what: novelty\n    value: 3\n", "what: readability\n    value: 9\n", "score 1: a 7 or more given by an agent"),
    ("value: 3\n", "value: 3\n    forecast: perhaps\n", "score 1: 'forecast' must be true or false"),
    (SCORES, SENTENCE.replace("    text", "    value: 5\n    text"), "score 1: 'explanatory' takes no 'value'"),
    (SCORES, SENTENCE.replace("explanatory", "elegance").replace("person:Reader A", "model-a/high"),
     "score 1: 'elegance' is signed by a person"),
    (SCORES, "\n".join(line for line in SENTENCE.splitlines() if "text:" not in line) + "\n", "score 1: 'text' is required"),
    ("basis: one call site", "basis: one call site\n    unconditional: sometimes", "forecast 1: 'unconditional' must be true or false"),
    ("basis: one call site", "basis: one call site\n    lane: \"\"", "forecast 1: 'lane' names a line of work of the forecaster in 'by'"),
    ("value: 3\n", "value: 3\n    lane: 7\n", "score 1: 'lane' names a line of work of the forecaster in 'by'"),
    ("claim: one", "claim: one: two", "line 6: unsupported YAML"),
    ("usd: [0, 0]}", "usd: [0, 0]", "line 13: a flow collection must close on its line"),
    ("---\nWhat was tried.\n", "What was tried.\n", "the YAML front matter is not closed"),
]


@pytest.mark.parametrize("old,new,message", RULES, ids=[rule[2] for rule in RULES])
def test_each_rule_reports_its_planted_violation_on_one_line(tmp_path, capsys, old, new, message):
    assert VALID.count(old) == 1
    directory = folder(tmp_path, VALID, VALID.replace(old, new))
    rc, out, _ = run(capsys, "check", directory, "--no-git")
    lines = out.splitlines()
    assert rc == 1 and lines[-1] == f"{directory}: 2 attempt files, 0 open, 1 with problems"
    assert lines[:-1] == [line for line in lines[:-1] if line.startswith(f"{directory}/2026-11-22-attempt-2.md: ")]
    assert any(message in line for line in lines[:-1]), out


ALLOWED = [
    ("usd: [0, 0]}", "usd: [0, 0], cluster_core_hours: [10, 40]}"),          # the convention is open on dimensions
    ("usd: 0}", "usd: 0, cluster_core_hours: 31.5}"),
    ("value: 3\n", "value: 7\n    forecast: true\n"),                           # an agent's 7 says it is a forecast
    ("value: 3\n", "value: 3\n    forecast: true\n"),                           # and may say so at any level
    ("value: 3\n    reader: SP\n    task: say whether the argument is in the literature\n    by: model-a/high",
     "value: 9\n    reader: SP\n    task: say whether the argument is in the literature\n    by: person:Reader A"),
    (SCORES, SENTENCE),                                                       # a sentence signed by a person
    (SCORES, SENTENCE.replace("person:Reader A", "model-a/high\n    forecast: true")),     # or proposed by an agent
    ("basis: one call site", "basis: one call site\n    unconditional: true"),
    ("basis: one call site", "basis: one call site\n    lane: going-up"),       # a line of work of one forecaster
    ("value: 3\n", "value: 3\n    lane: going-up\n"),                           # on a score too
    ("forecasts:\n" + FORECAST, "forecasts: []\n"),                           # a past attempt without a forecast
    ("  at: 2026-11-22T23:59+01:00\n  commit", "  by: person:Reader A\n  at: 2026-11-22T23:59+01:00\n  commit"),    # who measured
    ("  at: 2026-11-22T23:59+01:00\n  commit", "  by: model-a/high\n  commit"),                 # each of the two is optional
    (OUTCOME, UNVERIFIED),                                                    # the change exists, nothing was checked
    ("status: accepted\n", "status: abandoned\n  kept: {reached_lines: -30, named_steps: null}\n"),     # a part of it stays
    ("status: accepted\n", "status: rejected\n  kept: {reached_lines: -30}\n"),
    (OUTCOME, "outcome:\n  status: rejected\n  by: null\n  contract: {statements: null, axioms: null}\n"),    # only accepted needs it
]


@pytest.mark.parametrize("old,new", ALLOWED)
def test_what_the_convention_leaves_open_is_accepted(tmp_path, capsys, old, new):
    assert VALID.count(old) == 1
    assert run(capsys, "check", folder(tmp_path, VALID.replace(old, new)), "--no-git")[0] == 0


def test_a_score_between_two_anchors_is_a_note_and_not_a_problem(tmp_path, capsys):
    directory = folder(tmp_path, VALID.replace("value: 3\n", "value: 4\n"))
    rc, out, _ = run(capsys, "check", directory, "--no-git")
    assert rc == 0 and out.splitlines() == [
        f"{directory}/2026-11-22-attempt-1.md: note: score 1: the value 4 is between two anchors (the anchored levels "
        "are 1, 3, 5, 7, 9)", f"{directory}: 1 attempt files, 0 open, 0 with problems"]
    data = json.loads(run(capsys, "check", directory, "--no-git", "--json")[1])
    assert data["problems"] == [] and len(data["notes"]) == 1
    assert json.loads(run(capsys, "check", folder(tmp_path, VALID), "--no-git", "--json")[1])["notes"] == []    # control


def test_a_markdown_file_that_does_not_start_with_a_front_matter_line_is_not_an_attempt_file(tmp_path, capsys):
    directory = folder(tmp_path, VALID)
    (directory / "BASELINE.md").write_text("# Baseline\n\n---\nschema: vl.attempt/2\n---\n")    # a rule lower down opens nothing
    note = f"{directory}/BASELINE.md: note: not an attempt file: it does not start with a '---' line"
    rc, out, _ = run(capsys, "check", directory, "--no-git")
    assert rc == 0 and out.splitlines() == [note, f"{directory}: 1 attempt files, 0 open, 0 with problems"]
    rc, out, _ = run(capsys, "table", directory, "--no-git")
    lines = out.splitlines()
    assert rc == 0 and lines[0] == f"{directory}: 1 attempt files, 1 with an outcome, 0 open" and lines[-1] == note
    assert out.count("BASELINE.md") == 1 and "Left out" not in out
    data = json.loads(run(capsys, "table", directory, "--no-git", "--json")[1])
    assert data["files"] == 1 and data["skipped_files"] == [] and [n["file"] for n in data["notes"]] == [f"{directory}/BASELINE.md"]
    assert json.loads(run(capsys, "check", directory, "--no-git", "--json")[1])["files"] == 1



def test_accepted_with_an_explicit_empty_axiom_list_resolves_the_forecast(tmp_path, capsys):
    text = VALID.replace("statements: same, axioms: [propext, Classical.choice, Quot.sound]",
                         "statements: [Tiny.truth], axioms: []")
    directory = folder(tmp_path, text)
    assert run(capsys, "check", directory, "--no-git")[0] == 0
    assert table(capsys, directory, "--no-git")["p_accepted"][0]["resolved"] == 1

def test_accepted_without_a_checked_contract_is_a_problem_with_a_forecast_and_a_note_without_one(tmp_path, capsys):
    unchecked = VALID.replace("statements: same, axioms: [propext, Classical.choice, Quot.sound]", "statements: null, axioms: null")
    message = ("outcome: 'accepted' requires a non-empty statement reference (or list of references) and an explicit "
               "list of axiom names, which may be empty; a kept change awaiting a check is 'status: unverified'")
    directory = folder(tmp_path, unchecked, unchecked.replace("forecasts:\n" + FORECAST, "forecasts: []\n"),
                       unchecked.replace("status: accepted", "status: unverified"), VALID)
    rc, out, _ = run(capsys, "check", directory, "--no-git")
    assert rc == 1 and out.splitlines() == [
        f"{directory}/2026-11-22-attempt-1.md: {message}",                  # it would resolve a forecast in the table
        f"{directory}/2026-11-22-attempt-2.md: note: {message}",            # a past attempt: nothing rests on it
        f"{directory}: 4 attempt files, 0 open, 1 with problems"]
    data = json.loads(run(capsys, "table", directory, "--no-git", "--json")[1])
    assert data["skipped_files"] == ["2026-11-22-attempt-1.md"]
    assert data["p_accepted"] == [{"by": "model-a/high", "resolved": 1, "mean_forecast": 0.8, "share_accepted": 1,
                                   "brier": pytest.approx(0.04)}]             # the checked one only; the unverified one no
    assert [(row["kind"], row["dimension"], row["resolved"]) for row in data["intervals"]] == [
        ("value", "reached_lines", 1), ("cost", "agent_hours", 2), ("cost", "usd", 2)]     # its cost counts, its value no


def test_a_missing_folder_is_a_usage_error_and_json_names_its_schema(tmp_path, capsys):
    assert run(capsys, "check", tmp_path / "nowhere")[0] == 2
    rc, out, _ = run(capsys, "check", folder(tmp_path, VALID.replace("status: accepted", "status: done")), "--no-git", "--json")
    data = json.loads(out)
    assert rc == 1 and data["schema"] == "vl.attempts.check/1" and data["git_order"] == "not-checked"
    assert [p["file"].endswith("attempt-1.md") and "'status'" in p["message"] for p in data["problems"]] == [True]


# Order in Git ---------------------------------------------------------------------------------------------------

SECOND = FORECAST.replace("model-a/high", "model-b/low").replace("[-640, -560]", "[-700, -650]")


@pytest.fixture
def repository(tmp_path):
    git(tmp_path, "init", "-q")
    git(tmp_path, "config", "user.name", "Test")
    git(tmp_path, "config", "user.email", "test@example.com")
    return tmp_path


def table(capsys, directory, *extra):
    rc, out, _ = run(capsys, "table", directory, "--json", *extra)
    assert rc == 0
    return json.loads(out)


def test_a_forecast_committed_before_its_outcome_is_clean(repository, capsys):
    directory = folder(repository, VALID.replace(OUTCOME, "").replace(SCORES, ""))
    commit_all(repository, "forecast")
    folder(repository, VALID)
    assert run(capsys, "check", directory) == (0, f"{directory}: 1 attempt files, 0 open, 0 with problems\n", "")
    commit_all(repository, "outcome")                       # the same answer before and after the outcome is committed
    git(repository, "mv", "attempts/2026-11-22-attempt-1.md", "attempts/2026-11-22-renamed.md")
    commit_all(repository, "rename")                        # a rename keeps the history of the file
    assert run(capsys, "check", directory)[0] == 0
    data = table(capsys, directory)
    assert data["git_order"] == "checked" and data["excluded_forecasts"] == []
    assert [(row["by"], row["dimension"], row["resolved"]) for row in data["intervals"]] == [
        ("model-a/high", "reached_lines", 1), ("model-a/high", "agent_hours", 1), ("model-a/high", "usd", 1)]


def test_selected_attempt_check_is_scoped_and_still_checks_git_history(repository, capsys):
    directory = folder(repository, VALID.replace(OUTCOME, "").replace(SCORES, ""), VALID)
    first, late = sorted(directory.glob("*.md"))
    commit_all(repository, "freeze one forecast; the other arrived with its outcome")
    first.write_text(VALID)
    assert run(capsys, "check", directory)[0] == 1
    code, out, err = run(capsys, "check", first)
    assert (code, err) == (0, "") and "(selected file)" in out
    code, out, err = run(capsys, "check", first, "--json")
    data = json.loads(out)
    assert (code, err) == (0, "")
    assert (data["scope"], data["files"], data["git_order"], data["problems"]) == (
        {"file": str(first)}, 1, "checked", [])
    code, out, _ = run(capsys, "check", late)
    assert code == 1 and "late forecast" in out
    readme = directory / "README.md"
    readme.write_text("Notes about the attempts.\n")
    code, _, err = run(capsys, "check", readme)
    assert code == 2 and "expected a Markdown attempt file" in err


def test_selected_attempt_refuses_a_file_outside_its_git_repository(repository, tmp_path_factory, capsys):
    directory = folder(repository, VALID.replace(OUTCOME, "").replace(SCORES, ""))
    external = tmp_path_factory.mktemp("external") / "external.md"
    external.write_text(VALID)
    link = directory / "external.md"
    link.symlink_to(external)
    code, _, err = run(capsys, "check", link)
    assert code == 2 and "selected attempt resolves outside the Git repository" in err
    assert run(capsys, "check", external, "--no-git")[0] == 0


def test_a_forecast_first_committed_with_or_after_the_outcome_is_late_and_leaves_the_table(repository, capsys):
    directory = folder(repository, VALID.replace(OUTCOME, "").replace(SCORES, ""), VALID)
    commit_all(repository, "one forecast, and one file whose forecast arrives with its outcome")
    folder(repository, VALID.replace(OUTCOME, SECOND + OUTCOME))      # the outcome, and a second forecaster with it
    commit_all(repository, "outcome")
    rc, out, _ = run(capsys, "check", directory)
    assert rc == 1 and out.splitlines() == [
        f"{directory}/2026-11-22-attempt-1.md: late forecast by model-b/low: forecast not committed before the outcome "
        "(a past attempt without a forecast keeps 'forecasts: []')",
        f"{directory}/2026-11-22-attempt-2.md: late forecast by model-a/high: forecast not committed before the outcome "
        "(a past attempt without a forecast keeps 'forecasts: []')",
        f"{directory}: 2 attempt files, 0 open, 2 with problems"]
    data = table(capsys, directory)
    assert data["excluded_forecasts"] == [
        {"file": "2026-11-22-attempt-1.md", "by": "model-b/low", "reason": "late"},
        {"file": "2026-11-22-attempt-2.md", "by": "model-a/high", "reason": "late"}]
    assert {row["by"] for row in data["intervals"]} == {"model-a/high"}            # the forecast that was on time
    assert {row["resolved"] for row in data["intervals"]} == {1} and data["skipped_files"] == []
    rc, out, err = run(capsys, "check", directory, "--no-git")                     # control: the rule lives in Git
    assert rc == 0 and "was not checked (--no-git)" in err



def test_a_duplicate_forecast_can_only_resolve_once(repository, capsys):
    directory = folder(repository, VALID.replace(OUTCOME, "").replace(SCORES, ""))
    attempt = next(directory.glob("*.md"))
    commit_all(repository, "freeze one forecast")
    attempt.write_text(VALID)
    commit_all(repository, "record its outcome")
    assert run(capsys, "check", attempt)[0] == 0
    assert table(capsys, directory)["p_accepted"][0]["resolved"] == 1

    attempt.write_text(VALID.replace(FORECAST, FORECAST * 2))
    code, out, _ = run(capsys, "check", attempt)
    assert code == 1 and "late forecast" in out
    result = table(capsys, directory)
    assert result["p_accepted"][0]["resolved"] == 1
    assert result["excluded_forecasts"] == [{"file": attempt.name, "by": "model-a/high", "reason": "late"}]

    cosmetic_copy = FORECAST.replace("basis: one call site", "basis: one call site # same forecast")
    attempt.write_text(VALID.replace(FORECAST, FORECAST + cosmetic_copy))
    code, out, _ = run(capsys, "check", attempt, "--no-git")
    assert code == 1 and "duplicate forecast" in out
    result = table(capsys, directory, "--no-git")
    assert result["p_accepted"][0]["resolved"] == 1
    assert result["excluded_forecasts"][0]["reason"] == "duplicate"

def test_a_forecast_edited_after_the_outcome_is_a_problem_and_leaves_the_table(repository, capsys):
    directory = folder(repository, VALID.replace(OUTCOME, "").replace(SCORES, ""))
    commit_all(repository, "forecast")
    folder(repository, VALID)
    commit_all(repository, "outcome")
    folder(repository, VALID.replace("[-640, -560]", "[-640, -600]"))
    commit_all(repository, "a narrower interval, after seeing -615")
    rc, out, _ = run(capsys, "check", directory)
    assert rc == 1 and out.splitlines()[0].startswith(
        f"{directory}/2026-11-22-attempt-1.md: forecast edited after the outcome: the entry by model-a/high")
    assert len(out.splitlines()) == 2
    data = table(capsys, directory)
    assert data["intervals"] == [] and data["p_accepted"] == []
    assert data["excluded_forecasts"] == [{"file": "2026-11-22-attempt-1.md", "by": "model-a/high", "reason": "edited"}]
    folder(repository, VALID.replace("forecasts:\n" + FORECAST, "forecasts: []\n"))     # removing it is an edit too
    rc, out, _ = run(capsys, "check", directory)
    assert rc == 1 and "forecast edited after the outcome: the entry by model-a/high" in out


def test_in_a_repository_without_a_commit_every_forecast_of_a_closed_attempt_is_late(repository, capsys):
    directory = folder(repository, VALID, VALID.replace(OUTCOME, "").replace(SCORES, ""))
    rc, out, _ = run(capsys, "check", directory)
    assert rc == 1 and out.splitlines()[0].startswith(f"{directory}/2026-11-22-attempt-1.md: late forecast by model-a/high")
    assert out.splitlines()[-1] == f"{directory}: 2 attempt files, 1 open, 1 with problems"


def test_not_run_is_not_an_outcome_for_the_order_in_git(repository, capsys):
    """An option that was priced and not chosen is recorded with its forecast in one commit. Nothing is late, also
    when the option is tried afterwards: its forecast was committed, with `not-run`, before the outcome."""
    not_run = VALID.replace(SCORES, "").replace(OUTCOME, "outcome:\n  status: not-run\n")
    directory = folder(repository, not_run, not_run.replace("status: not-run", "status: rejected"))
    commit_all(repository, "two options priced and closed in one commit, one as not-run")
    rc, out, _ = run(capsys, "check", directory)
    assert rc == 1 and out.splitlines() == [
        f"{directory}/2026-11-22-attempt-2.md: late forecast by model-a/high: forecast not committed before the outcome "
        "(a past attempt without a forecast keeps 'forecasts: []')",          # control: any other status is an outcome
        f"{directory}: 2 attempt files, 0 open, 1 with problems"]
    data = table(capsys, directory)
    assert data["intervals"] == [] and data["p_accepted"] == [] and data["with_outcome"] == 2
    assert data["excluded_forecasts"] == [{"file": "2026-11-22-attempt-2.md", "by": "model-a/high", "reason": "late"}]
    folder(repository, VALID)                                                 # the first option is tried after all
    rc, out, _ = run(capsys, "check", directory)
    assert rc == 1 and len(out.splitlines()) == 2 and "attempt-2.md: late forecast" in out
    assert [(row["by"], row["resolved"]) for row in table(capsys, directory)["p_accepted"]] == [("model-a/high", 1)]
    folder(repository, VALID.replace("[-640, -560]", "[-640, -600]"))         # and its forecast is frozen from then on
    assert "attempt-1.md: forecast edited after the outcome: the entry by model-a/high" in run(capsys, "check", directory)[1]


# The table ------------------------------------------------------------------------------------------------------

def flow(mapping: dict) -> str:
    return "{" + ", ".join(f"{key}: {json.dumps(value)}" for key, value in mapping.items()) + "}"


def attempt(name: str, forecasts: list[tuple], status: str | None, value: dict | None = None, cost: dict | None = None,
            scores: list[tuple] = ()) -> str:
    """Forecasts are (by, p_accepted, value intervals, cost intervals[, unconditional]); scores are (what, value,
    reader, task, by), in the order they were written."""
    lines = ["---", "schema: vl.attempt/2", f"id: {name}", "purpose: shorten", "base: 36468ee", "claim: c",
             "forecasts:" if forecasts else "forecasts: []"]
    for by, p, f_value, f_cost, *unconditional in forecasts:
        lines += [f"  - by: {by}", "    at: 2026-11-22T22:10+01:00", f"    p_accepted: {json.dumps(p)}",
                  f"    value: {flow(f_value)}", f"    cost: {flow(f_cost)}", "    basis: b"]
        lines += ["    unconditional: true"] if unconditional else []
    if status:
        lines += ["outcome:", f"  status: {status}", f"  value: {flow(value or {})}", f"  cost: {flow(cost or {})}"]
        lines += ["  contract: {statements: same, axioms: [propext]}"] if status == "accepted" else []
    lines += ["scores:"] if scores else []
    for what, level, reader, task, by in scores:
        lines += [f"  - what: {what}", f"    value: {level}", f"    reader: {reader}", f"    task: {task}", f"    by: {by}",
                  "    at: 2026-11-23T10:00+01:00", "    basis: b"]
        lines += ["    forecast: true"] if level >= 7 and not by.startswith("person:") else []
    return "\n".join(lines + ["---", ""])


def test_the_table_counts_resolved_forecasts_and_their_errors(tmp_path, capsys):
    """By hand, for forecaster a:
    value.reached_lines  accepted attempts only: [-640, -560] against -615 (inside, -615 - -600 = -15) and
                         [-300, -100] against -50 (outside, -50 - -200 = +150): 2 resolved, 1 inside, median 67.5.
                         The rejected attempt measured 0 and resolves no value.
    cost.agent_hours     [1, 3] against 2.5 (inside, +0.5) and, rejected, [2, 4] against 6 (outside, +3): 2 resolved,
                         1 inside, median 1.75. The third attempt measured null.
    p_accepted           0.8 accepted, 0.6 rejected, 0.5 accepted: mean 1.9 / 3, accepted 2 / 3,
                         Brier (0.04 + 0.36 + 0.25) / 3. The not-run and the open attempt resolve nothing.
    For forecaster b: [-700, -650] against -615: outside, -615 - -675 = +60; no p_accepted, so no row there.
    For forecaster c, who priced value "including zero if rejected" (unconditional): the rejected attempt resolves
    [-500, 0] with 0 (inside, 0 - -250 = +250) whatever it measured, and the accepted one [-400, -200] with -615
    (outside, -615 - -300 = -315): 2 resolved, 1 inside, median -32.5. p_accepted 0.4 rejected: Brier 0.16."""
    directory = folder(
        tmp_path,
        attempt("one", [("a", 0.8, {"reached_lines": [-640, -560]}, {"agent_hours": [1, 3]}),
                        ("b", None, {"reached_lines": [-700, -650]}, {}),
                        ("c", None, {"reached_lines": [-400, -200]}, {}, True)],
                "accepted", {"reached_lines": -615}, {"agent_hours": 2.5}),
        attempt("two", [("a", 0.6, {"reached_lines": [-1000, -800]}, {"agent_hours": [2, 4]}),
                        ("c", 0.4, {"reached_lines": [-500, 0]}, {}, True)],
                "rejected", {"reached_lines": -77}, {"agent_hours": 6}),
        attempt("three", [("a", 0.5, {"reached_lines": [-300, -100]}, {"agent_hours": [1, 2]})],
                "accepted", {"reached_lines": -50}, {"agent_hours": None}),
        attempt("four", [("a", 0.3, {"reached_lines": [-10, 0]}, {"agent_hours": [1, 2]}),
                         ("c", 0.3, {"reached_lines": [-10, 0]}, {}, True)],
                "not-run", {"reached_lines": -5}, {"agent_hours": 1}),
        attempt("five", [("a", 0.9, {"reached_lines": [-10, 0]}, {"agent_hours": [1, 2]})], None))
    rc, out, _ = run(capsys, "table", directory, "--no-git", "--json")
    data = json.loads(out)
    assert rc == 0 and data["schema"] == "vl.attempts.table/1"
    assert (data["files"], data["with_outcome"], data["open"], data["git_order"]) == (5, 4, 1, "not-checked")
    assert data["intervals"] == [
        {"by": "a", "kind": "value", "dimension": "reached_lines", "resolved": 2, "inside": 1, "median_error": 67.5},
        {"by": "a", "kind": "cost", "dimension": "agent_hours", "resolved": 2, "inside": 1, "median_error": 1.75},
        {"by": "b", "kind": "value", "dimension": "reached_lines", "resolved": 1, "inside": 0, "median_error": 60},
        {"by": "c", "kind": "value", "dimension": "reached_lines", "resolved": 2, "inside": 1, "median_error": -32.5}]
    assert data["p_accepted"] == [
        {"by": "a", "resolved": 3, "mean_forecast": pytest.approx(1.9 / 3), "share_accepted": pytest.approx(2 / 3),
         "brier": pytest.approx(0.65 / 3)},
        {"by": "c", "resolved": 1, "mean_forecast": 0.4, "share_accepted": 0, "brier": pytest.approx(0.16)}]
    assert data["score_pairs"] == []
    rc, out, _ = run(capsys, "table", directory, "--no-git")
    rows = [line.split() for line in out.splitlines()]
    assert rc == 0 and ["a", "value.reached_lines", "2", "1", "+67.5"] in rows and ["a", "3", "0.63", "0.67", "0.217"] in rows
    assert "No agent score has a later score by a person on the same what, reader and task yet." in out


def test_one_forecaster_is_one_row_whatever_its_lanes_and_the_lanes_can_be_shown_under_it(tmp_path, capsys):
    """By hand. Model m priced three attempts, two in a named lane and one in none; model n priced a fourth.
    m, p_accepted        0.8 accepted (taylor), 0.6 rejected (going-up), 0.5 accepted (no lane): one row, 3 resolved,
                         mean 1.9 / 3, accepted 2 / 3, Brier (0.04 + 0.36 + 0.25) / 3. Its lanes: 0.36, 0.04, 0.25.
    m, value             [-100, -50] against -70 in the two accepted attempts: 2 resolved, 2 inside, median +5.
    m, cost.agent_hours  [1, 3] against 2 (taylor, 0), 5 (going-up, outside, +3) and 2.5 (no lane, +0.5): 3 resolved,
                         2 inside, median +0.5.
    n                    one forecast without a lane: a row, and no lane under it.
    Planted: the same forecasts with the lane written into `by`, as one model signed in the field, are three
    forecasters of one forecast each."""
    cases = (("one", 0.8, "accepted", 2, "taylor"), ("two", 0.6, "rejected", 5, "going-up"), ("three", 0.5, "accepted", 2.5, None))

    def texts(sign):
        return [sign(attempt(name, [("m", p, {"reached_lines": [-100, -50]}, {"agent_hours": [1, 3]})], status,
                             {"reached_lines": -70}, {"agent_hours": hours}), lane) for name, p, status, hours, lane in cases
                ] + [attempt("four", [("n", 0.5, {}, {"agent_hours": [1, 3]})], "accepted", {}, {"agent_hours": 1})]

    directory = folder(tmp_path, *texts(lambda text, lane: text.replace("    basis: b", f"    lane: {lane}\n    basis: b") if lane else text))
    assert run(capsys, "check", directory, "--no-git")[0] == 0
    data = json.loads(run(capsys, "table", directory, "--no-git", "--json")[1])
    assert data["p_accepted"] == [
        {"by": "m", "resolved": 3, "mean_forecast": pytest.approx(1.9 / 3), "share_accepted": pytest.approx(2 / 3),
         "brier": pytest.approx(0.65 / 3)},
        {"by": "n", "resolved": 1, "mean_forecast": 0.5, "share_accepted": 1, "brier": 0.25}]
    assert [(row["by"], row["lane"], row["resolved"], row["share_accepted"], row["brier"]) for row in data["p_accepted_by_lane"]] == [
        ("m", "going-up", 1, 0, pytest.approx(0.36)), ("m", "taylor", 1, 1, pytest.approx(0.04)), ("m", None, 1, 1, 0.25)]
    assert data["intervals"] == [
        {"by": "m", "kind": "value", "dimension": "reached_lines", "resolved": 2, "inside": 2, "median_error": 5},
        {"by": "m", "kind": "cost", "dimension": "agent_hours", "resolved": 3, "inside": 2, "median_error": 0.5},
        {"by": "n", "kind": "cost", "dimension": "agent_hours", "resolved": 1, "inside": 1, "median_error": -1}]
    assert [(row["by"], row["dimension"], row["lane"], row["resolved"], row["inside"], row["median_error"])
            for row in data["intervals_by_lane"]] == [
        ("m", "reached_lines", "taylor", 1, 1, 5), ("m", "reached_lines", None, 1, 1, 5),
        ("m", "agent_hours", "going-up", 1, 0, 3), ("m", "agent_hours", "taylor", 1, 1, 0), ("m", "agent_hours", None, 1, 1, 0.5)]
    plain = run(capsys, "table", directory, "--no-git")[1]
    assert "taylor" not in plain and ["m", "3", "0.63", "0.67", "0.217"] in [line.split() for line in plain.splitlines()]
    shown = run(capsys, "table", directory, "--no-git", "--lanes")[1].splitlines()
    start = shown.index("p_accepted")
    assert shown[start + 1:start + 7] == [
        "forecaster   resolved  mean forecast  accepted  Brier",
        "m                   3           0.63      0.67  0.217",
        "  going-up          1           0.60      0.00  0.360",
        "  taylor            1           0.80      1.00  0.040",
        "  (no lane)         1           0.50      1.00  0.250",
        "n                   1           0.50      1.00  0.250"]
    assert "  going-up   cost.agent_hours            1       0                           +3" in shown
    (tmp_path / "planted").mkdir()
    planted = folder(tmp_path / "planted", *texts(lambda text, lane: text.replace("  - by: m", f"  - by: m {lane} lane") if lane else text))
    data = json.loads(run(capsys, "table", planted, "--no-git", "--json")[1])
    assert [(row["by"], row["resolved"]) for row in data["p_accepted"]] == [
        ("m", 1), ("m going-up lane", 1), ("m taylor lane", 1), ("n", 1)]
    assert data["p_accepted_by_lane"] == [] and data["intervals_by_lane"] == []


def test_a_focused_probe_and_a_full_build_are_two_dimensions_that_the_table_never_pools(tmp_path, capsys):
    """By hand. Forecaster a priced, for two attempts, one focused invocation at [1, 4] minutes and the cold build of
    the roots at [13, 16]. Attempt one measured both, 2 and 14; attempt two ran probes only, 3, and no build.
    cost.probe_minutes   2 and 3: 2 resolved, 2 inside, median of -0.5 and +0.5 = 0.
    cost.build_minutes   14: 1 resolved, 1 inside, -0.5.
    Planted: the field form, where the probe of attempt two is written as `build_minutes`. The build row then counts
    2, with 1 inside and a median of -0.5 and -11.5 = -6: a probe read against the forecast of a build."""
    def texts(probe):
        prices = {"probe_minutes": [1, 4], "build_minutes": [13, 16]}
        return (attempt("one", [("a", None, {}, prices)], "accepted", {}, {"probe_minutes": 2, "build_minutes": 14}),
                attempt("two", [("a", None, {}, prices)], "accepted", {}, probe))

    data = json.loads(run(capsys, "table", folder(tmp_path, *texts({"probe_minutes": 3, "build_minutes": None})), "--no-git", "--json")[1])
    assert [(row["dimension"], row["resolved"], row["inside"], row["median_error"]) for row in data["intervals"]] == [
        ("build_minutes", 1, 1, -0.5), ("probe_minutes", 2, 2, 0)]
    data = json.loads(run(capsys, "table", folder(tmp_path, *texts({"build_minutes": 3})), "--no-git", "--json")[1])
    assert [(row["dimension"], row["resolved"], row["inside"], row["median_error"]) for row in data["intervals"]] == [
        ("build_minutes", 2, 1, -6), ("probe_minutes", 1, 1, -0.5)]


def test_an_open_attempt_has_no_outcome_and_a_placeholder_hides_it_or_makes_its_forecast_late(repository, capsys):
    """Three live candidates committed with their forecasts. The first has no `outcome` key: it is open. The second
    holds the place with `not-run`: nothing is reported, and it is no longer counted as open. The third holds it with
    `unverified`: that is an outcome committed together with the forecast, which is late."""
    live = VALID.replace(OUTCOME, "").replace(SCORES, "")
    directory = folder(repository, live, live.replace("---\nWhat", "outcome:\n  status: not-run\n---\nWhat"),
                       live.replace("---\nWhat", "outcome:\n  status: unverified\n---\nWhat"))
    commit_all(repository, "three forecasts")
    rc, out, _ = run(capsys, "check", directory)
    assert rc == 1 and out.splitlines() == [
        f"{directory}/2026-11-22-attempt-3.md: late forecast by model-a/high: forecast not committed before the outcome "
        "(a past attempt without a forecast keeps 'forecasts: []')",
        f"{directory}: 3 attempt files, 1 open, 1 with problems"]
    data = table(capsys, directory)
    assert (data["open"], data["with_outcome"]) == (1, 2) and data["p_accepted"] == [] and data["intervals"] == []


KEPT_TITLE = "kept of attempts that ended abandoned or rejected (it resolves no forecast), under the base it is a change against"
BASES = ["A measured value is a change against the base of its attempt: values with different bases are not additive",
         "(--measured lists every one under its base)."]


def test_what_was_kept_of_an_abandoned_attempt_is_reported_apart_under_its_base_and_resolves_no_forecast(tmp_path, capsys):
    """By hand. Attempt one, against base 36468ee, gave up its route and kept a part of it: -381 reached lines and -37
    declarations. Its forecasts resolve on the claim, which failed: a's 0.2 as not accepted (Brier 0.04) and no value;
    c, who priced value including zero if rejected, [-2500, 0] with 0 (inside, 0 - -1250 = +1250) and not with the
    -381 that was kept. Attempt two, against base 1dd94f8, was accepted with -70: inside a's [-100, -50], +5.
    Control: without `kept` the tables of forecasts are the same and nothing is listed as kept."""
    def texts(kept):
        return (attempt("one", [("a", 0.2, {"reached_lines": [-2500, -100]}, {}), ("c", None, {"reached_lines": [-2500, 0]}, {}, True)],
                        "abandoned", {"reached_lines": 0, "physical_lines": None}).replace("status: abandoned\n", "status: abandoned\n" + kept),
                attempt("two", [("a", 0.9, {"reached_lines": [-100, -50]}, {})], "accepted", {"reached_lines": -70}
                        ).replace("base: 36468ee", "base: 1dd94f8"))

    directory = folder(tmp_path, *texts("  kept: {reached_lines: -381, reached_declarations: -37, named_steps: null}\n"))
    assert run(capsys, "check", directory, "--no-git")[0] == 0
    data = json.loads(run(capsys, "table", directory, "--no-git", "--json")[1])
    forecasts = {key: data[key] for key in ("intervals", "p_accepted", "p_accepted_by_band", "p_accepted_by_purpose")}
    assert data["intervals"] == [
        {"by": "a", "kind": "value", "dimension": "reached_lines", "resolved": 1, "inside": 1, "median_error": 5},
        {"by": "c", "kind": "value", "dimension": "reached_lines", "resolved": 1, "inside": 1, "median_error": 1250}]
    assert data["p_accepted"] == [{"by": "a", "resolved": 2, "mean_forecast": pytest.approx(0.55), "share_accepted": 0.5,
                                   "brier": pytest.approx(0.025)}]
    one, two = ({"file": f"2026-11-22-attempt-{number}.md", "status": status, "base": base}
                for number, status, base in ((1, "abandoned", "36468ee"), (2, "accepted", "1dd94f8")))
    assert data["measured"] == [
        {**one, "kind": "value", "dimension": "reached_lines", "measured": 0},
        {**one, "kind": "kept", "dimension": "reached_lines", "measured": -381},
        {**one, "kind": "kept", "dimension": "reached_declarations", "measured": -37},
        {**two, "kind": "value", "dimension": "reached_lines", "measured": -70}]
    lines = run(capsys, "table", directory, "--no-git")[1].splitlines()
    start = lines.index(KEPT_TITLE)
    assert lines[start + 1:start + 4] == [
        "base 36468ee", "  2026-11-22-attempt-1.md  abandoned  kept  reached_lines -381, reached_declarations -37", ""]
    assert lines[start + 7:start + 9] == BASES and "base 1dd94f8" not in lines
    lines = run(capsys, "table", directory, "--no-git", "--measured")[1].splitlines()
    start = lines.index("base 36468ee")
    assert lines[start - 2].startswith("measured changes of each attempt, under the base they are changes against") and lines[start:start + 6] == [
        "base 36468ee",
        "  2026-11-22-attempt-1.md  abandoned  value  reached_lines +0",
        "  2026-11-22-attempt-1.md  abandoned  kept   reached_lines -381, reached_declarations -37",
        "base 1dd94f8",
        "  2026-11-22-attempt-2.md  accepted   value  reached_lines -70", ""]
    assert lines[start + 9:start + 11] == BASES and KEPT_TITLE not in lines
    control = folder(tmp_path, *texts(""))
    data = json.loads(run(capsys, "table", control, "--no-git", "--json")[1])
    assert {key: data[key] for key in forecasts} == forecasts and [row["kind"] for row in data["measured"]] == ["value", "value"]
    lines = run(capsys, "table", control, "--no-git")[1].splitlines()
    assert KEPT_TITLE not in lines and "base 36468ee" not in lines and lines[lines.index(BASES[0]) + 1] == BASES[1]


def test_the_table_gives_p_accepted_by_band_of_the_forecast_and_by_purpose(tmp_path, capsys):
    """By hand, for forecaster a, eight resolved forecasts: one mean (4.36 / 8 = 0.545 against 5 / 8 accepted, Brier
    1.471 / 8) hides that the two unlikely ones failed and that the likely ones mostly held.
    band 0.0-0.2   0 and 0.12, both rejected: 2 resolved, mean 0.06, accepted 0, Brier 0.0144 / 2.
    band 0.2-0.4   0.2 accepted (a band holds its lower edge): 1, 0.2, 1, Brier 0.64.
    band 0.4-0.6   nothing resolved, so no row: 0.5 is unverified and 0.45 is open. Control: with `accepted` in place
                   of `unverified` the band has one forecast.
    band 0.6-0.8   0.7 and 0.75 accepted, 0.79 rejected: 3, mean 2.24 / 3, accepted 2 / 3, Brier 0.7766 / 3.
    band 0.8-1.0   0.8 and 1 accepted (the last band holds 1): 2, mean 0.9, accepted 1, Brier 0.04 / 2.
    new-route      0.12 rejected: Brier 0.0144.
    shorten        0 rejected, 0.2 accepted, 0.7 accepted, 0.79 rejected, 1 accepted: 5, mean 2.69 / 5, accepted 3 / 5,
                   Brier (0.64 + 0.09 + 0.6241) / 5.
    understand     0.75 and 0.8 accepted: 2, mean 0.775, accepted 1, Brier (0.0625 + 0.04) / 2.
    Forecaster b has one forecast, 0.3 accepted: one band, one purpose."""
    cases = ((0, "rejected", "shorten"), (0.12, "rejected", "new-route"), (0.2, "accepted", "shorten"), (0.7, "accepted", "shorten"),
             (0.75, "accepted", "understand"), (0.79, "rejected", "shorten"), (0.8, "accepted", "understand"), (1, "accepted", "shorten"),
             (0.5, "unverified", "shorten"), (0.45, None, "shorten"))

    def texts(unverified):
        return [attempt(f"a{number}", [("a", p, {}, {})] + ([("b", 0.3, {}, {})] if p == 1 else []),
                        unverified if status == "unverified" else status).replace("purpose: shorten", f"purpose: {purpose}")
                for number, (p, status, purpose) in enumerate(cases)]

    directory = folder(tmp_path, *texts("unverified"))
    assert run(capsys, "check", directory, "--no-git")[0] == 0
    data = json.loads(run(capsys, "table", directory, "--no-git", "--json")[1])
    assert data["p_accepted"] == [
        {"by": "a", "resolved": 8, "mean_forecast": pytest.approx(0.545), "share_accepted": 0.625, "brier": pytest.approx(1.471 / 8)},
        {"by": "b", "resolved": 1, "mean_forecast": 0.3, "share_accepted": 1, "brier": pytest.approx(0.49)}]
    assert [tuple(row.values()) for row in data["p_accepted_by_band"]] == [
        ("a", "0.0-0.2", 2, pytest.approx(0.06), 0, pytest.approx(0.0072)),
        ("a", "0.2-0.4", 1, 0.2, 1, pytest.approx(0.64)),
        ("a", "0.6-0.8", 3, pytest.approx(2.24 / 3), pytest.approx(2 / 3), pytest.approx(0.7766 / 3)),
        ("a", "0.8-1.0", 2, pytest.approx(0.9), 1, pytest.approx(0.02)),
        ("b", "0.2-0.4", 1, 0.3, 1, pytest.approx(0.49))]
    assert list(data["p_accepted_by_band"][0]) == ["by", "band", "resolved", "mean_forecast", "share_accepted", "brier"]
    assert [tuple(row.values()) for row in data["p_accepted_by_purpose"]] == [
        ("a", "new-route", 1, 0.12, 0, pytest.approx(0.0144)),
        ("a", "shorten", 5, pytest.approx(0.538), 0.6, pytest.approx(1.3541 / 5)),
        ("a", "understand", 2, pytest.approx(0.775), 1, pytest.approx(0.05125)),
        ("b", "shorten", 1, 0.3, 1, pytest.approx(0.49))]
    lines = run(capsys, "table", directory, "--no-git")[1].splitlines()
    start = lines.index("p_accepted by band of the forecast (a band holds its lower edge, the last one also 1)")
    assert lines[start + 1:start + 7] == [
        "forecaster  band     resolved  mean forecast  accepted  Brier",
        "a           0.0-0.2         2           0.06      0.00  0.007",
        "a           0.2-0.4         1           0.20      1.00  0.640",
        "a           0.6-0.8         3           0.75      0.67  0.259",
        "a           0.8-1.0         2           0.90      1.00  0.020",
        "b           0.2-0.4         1           0.30      1.00  0.490"]
    start = lines.index("p_accepted by purpose of the attempt")
    assert lines[start + 1:start + 6] == [
        "forecaster  purpose     resolved  mean forecast  accepted  Brier",
        "a           new-route          1           0.12      0.00  0.014",
        "a           shorten            5           0.54      0.60  0.271",
        "a           understand         2           0.78      1.00  0.051",
        "b           shorten            1           0.30      1.00  0.490"]
    data = json.loads(run(capsys, "table", folder(tmp_path, *texts("accepted")), "--no-git", "--json")[1])      # control
    assert [(row["band"], row["resolved"], row["mean_forecast"]) for row in data["p_accepted_by_band"] if row["by"] == "a"][2] == (
        "0.4-0.6", 1, 0.5)


def test_an_unverified_attempt_resolves_cost_and_nothing_else_and_the_table_says_what_it_counts(tmp_path, capsys):
    """By hand. Attempt one is unverified: the change exists, its contract was not checked. It resolves the costs
    it measured and no p_accepted and no value, also for c, who priced value unconditionally.
    a, cost.agent_hours  [1, 3] against 2 (inside, 0) and, abandoned, [2, 4] against 5 (outside, +2): 2 resolved,
                         1 inside, median 1.
    a, p_accepted        the abandoned attempt only: 0.6 not accepted, Brier 0.36. Not the 0.8 of the unverified one.
    c, cost.agent_hours  [0, 1] against 2: outside, 2 - 0.5 = +1.5. No value row and no p_accepted row for c.
    Control: with `accepted` in place of `unverified`, attempt one also resolves a's p (0.8 and 0.6: Brier
    (0.04 + 0.36) / 2) and the value intervals: a [-100, -50] against -70 (inside, +5), c [-100, 0] (inside, -20)."""
    def texts(status):
        return (attempt("one", [("a", 0.8, {"reached_lines": [-100, -50]}, {"agent_hours": [1, 3]}),
                                ("c", 0.5, {"reached_lines": [-100, 0]}, {"agent_hours": [0, 1]}, True)],
                        status, {"reached_lines": -70}, {"agent_hours": 2}),
                attempt("two", [("a", 0.6, {"reached_lines": [-40, -20]}, {"agent_hours": [2, 4]})],
                        "abandoned", {"reached_lines": None}, {"agent_hours": 5}))
    directory = folder(tmp_path, *texts("unverified"))
    assert run(capsys, "check", directory, "--no-git")[0] == 0
    data = json.loads(run(capsys, "table", directory, "--no-git", "--json")[1])
    assert (data["files"], data["with_outcome"], data["open"], data["skipped_files"]) == (2, 2, 0, [])
    assert data["intervals"] == [
        {"by": "a", "kind": "cost", "dimension": "agent_hours", "resolved": 2, "inside": 1, "median_error": 1},
        {"by": "c", "kind": "cost", "dimension": "agent_hours", "resolved": 1, "inside": 0, "median_error": 1.5}]
    assert data["p_accepted"] == [{"by": "a", "resolved": 1, "mean_forecast": 0.6, "share_accepted": 0, "brier": pytest.approx(0.36)}]
    out = run(capsys, "table", directory, "--no-git")[1]
    lines = out.splitlines()
    legend = lines.index("p_accepted by purpose of the attempt") + 3            # under the last table of forecasts
    assert "a                  1           0.60      0.00  0.360" in lines and lines[legend:legend + 4] == [
        "",
        "p_accepted is resolved by the attempts that ended accepted, rejected or abandoned (the last two as not accepted);",
        "a value interval only by an accepted one (a forecast marked 'unconditional': also rejected and abandoned, as 0);",
        "a cost interval by any run attempt that measured it. 'unverified' and 'not-run' resolve no acceptance and no value."]
    assert lines[legend + 4:legend + 6] == BASES
    data = json.loads(run(capsys, "table", folder(tmp_path, *texts("accepted")), "--no-git", "--json")[1])       # control
    assert [(row["by"], row["kind"], row["resolved"], row["inside"], row["median_error"]) for row in data["intervals"]] == [
        ("a", "value", 1, 1, 5), ("a", "cost", 2, 1, 1), ("c", "value", 1, 1, -20), ("c", "cost", 1, 0, 1.5)]
    assert [(row["by"], row["resolved"], row["share_accepted"]) for row in data["p_accepted"]] == [("a", 2, 0.5), ("c", 1, 1)]
    assert data["p_accepted"][0]["brier"] == pytest.approx(0.2)


def test_the_table_pairs_an_agent_score_with_a_later_score_by_a_person(tmp_path, capsys):
    """By hand, on novelty for the same reader and task. Agent a: 7 against Ada's 3 (+4) in the first file; 5
    against Ada's 7 (-2) and against Bo's 1 (+4) in the second: 3 pairs, mean 6 / 3 = +2. Agent b: 5 against Ada's 3:
    1 pair, +2. Not pairs: a's 7 with b's 5 (b is no person); readability, where Ada scored for another reader;
    generality, where she scored another task; clarity, where her score was written first; Ada's 7 with Bo's 1
    (two persons)."""
    task, other = "say whether the argument is in the literature", "rebuild the proof from a summary"
    ada, bo = "person:Reader A", "person:Reader B"
    directory = folder(
        tmp_path,
        attempt("one", [], None, scores=[("novelty", 7, "SP", task, "a"), ("readability", 5, "SP", other, "a"),
                                         ("novelty", 5, "SP", task, "b"), ("generality", 3, "SP", task, "a"),
                                         ("novelty", 3, "SP", task, ada), ("readability", 5, "AG", other, ada),
                                         ("generality", 5, "SP", other, ada)]),
        attempt("two", [], None, scores=[("clarity", 3, "SP", task, ada), ("clarity", 5, "SP", task, "a"),
                                         ("novelty", 5, "SP", task, "a"), ("novelty", 7, "SP", task, ada),
                                         ("novelty", 1, "SP", task, bo)]))
    data = json.loads(run(capsys, "table", directory, "--no-git", "--json")[1])
    assert data["score_pairs"] == [{"by": "a", "what": "novelty", "pairs": 3, "mean_difference": 2},
                                   {"by": "b", "what": "novelty", "pairs": 1, "mean_difference": 2}]
    out = run(capsys, "table", directory, "--no-git")[1]
    assert ["a", "novelty", "3", "+2"] in [line.split() for line in out.splitlines()]


def test_the_table_leaves_out_a_file_with_problems_and_says_so(tmp_path, capsys):
    directory = folder(tmp_path, VALID, VALID.replace("status: accepted", "status: done"))
    rc, out, _ = run(capsys, "table", directory, "--no-git")
    assert rc == 0 and "Left out, files with problems (vl attempts check): 2026-11-22-attempt-2.md" in out
    rows = [line.split() for line in out.splitlines()]
    assert ["model-a/high", "value.reached_lines", "1", "1", "-15"] in rows
    assert ["model-a/high", "cost.agent_hours", "1", "1", "+0.5"] in rows        # the other file adds nothing to it


# The YAML that people and dumpers write --------------------------------------------------------------------------

READ = [
    ("a: one line\n  and its continuation\n\n  after a blank line\nb: 2\n", {"a": "one line and its continuation\nafter a blank line", "b": 2}),
    ("a:\n- by: x\n  at: y\n- by: z\nb:\n  c:\n  - -2900\n  - -45\n", {"a": [{"by": "x", "at": "y"}, {"by": "z"}], "b": {"c": [-2900, -45]}}),
    ("a:\n  - - 1\n    - 2\n  -\n    - 3\n    - k: v\n      l: w\n", {"a": [[1, 2], [3, {"k": "v", "l": "w"}]]}),
    ("a: 'it''s: one # line\n  and a second\n\n  third'   # a comment\nb: '2026-11-22'\nc: '7'\n",
     {"a": "it's: one # line and a second\nthird", "b": "2026-11-22", "c": "7"}),
    ('a: "tab\\there \\"q\\" \\u00e9\\x41 \\\\ joined\\\n  \\ on and\n  folded"\n', {"a": 'tab\there "q" éA \\ joined on and folded'}),
    ("a: >\n  folded\n  text\n\n  second paragraph\nb: >-\n  no final\n  line break\n", {"a": "folded text\nsecond paragraph\n", "b": "no final line break"}),
    ("a: |\n  kept\n    # not a comment\n  k: v\n\nb: |-\n  one\n  two\nc: |+\n  x\n\n", {"a": "kept\n  # not a comment\nk: v\n", "b": "one\ntwo", "c": "x\n\n"}),
    ("a: |2-\n   starts with a space\nb:\n  - >-\n    in a\n    list\n", {"a": " starts with a space", "b": ["in a list"]}),
    ("a:\n  the text starts on the next line\n  and goes on\nb:\n  'quoted'\n", {"a": "the text starts on the next line and goes on", "b": "quoted"}),
    ("a: 1.0e-05\nb: 1234e56\nc: 0.5 # half\nd: 007\ne: text # comment\n", {"a": 1e-05, "b": "1234e56", "c": 0.5, "d": "007", "e": "text"}),
    ('a: ["caf\\xE9 \\u2200", \'it\'\'s\', "\\ud83d\\ude00", {b: "x, y"}]\n', {"a": ["café ∀", "it's", "\U0001f600", {"b": "x, y"}]}),
]


@pytest.mark.parametrize("text,value", READ, ids=[text.splitlines()[0] for text, _ in READ])
def test_the_typed_reader_reads_what_people_and_dumpers_write(text, value):
    assert parse_yaml(text, typed=True) == value


REFUSED = [
    ("a: &anchor 1\n", "line 1: anchors and aliases are not supported"),
    ("a: 1\nb: *anchor\n", "line 2: anchors and aliases are not supported"),
    ("a: !!str 1\n", "line 1: tags are not supported"),
    ("a: 1\n---\nb: 2\n", "line 2: document markers and directives are not supported"),
    ("a: [1,\n  2]\n", "line 1: a flow collection must close on its line"),
    ("a: {b: 1,\n  c: 2}\n", "line 1: a flow collection must close on its line"),
    ("a: [1, 'text that goes\n  on]\n", "line 1: a flow collection must close on its line"),
    ('a: "half \\ud83d"\n', "line 1: half of a surrogate pair"),
    ("a: " + "[" * 3000 + "\n", "lists or mappings nested too deeply to read"),
    ("- " * 3000 + "x\n", "lists or mappings nested too deeply to read"),
    ("a: text\n  b: 1\n", "line 2: 'key: value' inside a text that runs over several lines"),
    ("a:\n  b: 1\n   c: 2\n", "line 3: 'key: value' inside a text that runs over several lines"),
    ("a:\n  b:\n    - 1\n   c: 2\n", "line 4: unexpected indentation"),
    ("a: text\n  # a comment ends it\n  more\n", "line 3: unexpected indentation"),
    ("a: 'never closed\nb: 1\n", "line 1: this quoted scalar is not closed"),
    ("a: 'closed' and more\n", "line 1: unexpected text after the closing quote"),
    ('a: "a \\q escape"\n', "line 1: unsupported escape"),
    ("a: |x\n  text\n", "line 1: unsupported block scalar header"),
    ("a:\n\tb: 1\n", "line 2: tabs are not supported"),
    ("a: 1\na: 2\n", "line 2: duplicate key 'a'"),
    ("'a': 1\n", "line 1: expected 'key: value'"),
]


@pytest.mark.parametrize("text,message", REFUSED, ids=[f"{text[:12]!r} {message}" for text, message in REFUSED])
def test_the_typed_reader_refuses_the_rest_with_its_line(text, message):
    with pytest.raises(CatalogueError) as raised:
        parse_yaml(text, typed=True)
    assert str(raised.value).startswith(message)


def test_the_catalogue_reader_still_refuses_what_only_the_typed_reader_reads():
    for text, _ in [READ[index] for index in (0, 2, 3, 4, 7, 8)]:
        with pytest.raises(CatalogueError, match="line"):
            parse_yaml(text)
    assert parse_yaml("a: -800\nb: >-\n  one\n  two\n") == {"a": "-800", "b": "one two"}       # and reads as before


REAL = ROOT / "tests" / "fixtures" / "attempt-files-real"
REGISTRY = ("ambient", "components", "cosupport", "finite-contact", "rees")      # written by an agent with a dumper


def test_attempt_files_written_by_an_agent_from_the_convention_alone_are_read(capsys):
    """Nine synthetic field-shaped files: five in the style of a YAML dumper (text over several lines, lists at the
    indentation of their key, intervals as nested lists) and four outcome records. They parse; what `check` then
    reports is what the five really lack: a numeric score without `reader` and `task`."""
    for name in REGISTRY:
        attempt = attempt_files.read(REAL / f"2026-11-22-{name}.md")
        forecast = attempt.data["forecasts"][0]
        assert attempt.data["claim"] == attempt.path.read_text().split("\n---\n", 1)[1].strip()    # the prose repeats it
        assert forecast["by"] == "model-fixture/high" and forecast["at"].startswith("2026-11-22T2")
        assert forecast["basis"].endswith(("no paid calls planned.", "no paid calls.")) and "\n" not in forecast["basis"]
        assert all(len(pair) == 2 for kind in ("value", "cost") for pair in forecast[kind].values())
        assert len(attempt.entries) == 1 and attempt.entries[0].startswith("- by: model-fixture/high\n")
    finite = attempt_files.read(REAL / "2026-11-22-finite-contact.md").data
    assert finite["forecasts"][0]["value"] == {"reached_lines": [-2900, -45]} and finite["forecasts"][0]["p_accepted"] == 0.12
    assert finite["forecasts"][0]["cost"] == {"agent_hours": [1, 3], "build_minutes": [2, 50], "peak_ram_gb": [1, 10],
                                              "usd": [0, 0]}
    assert finite["outcome"] == {"status": "not-run"} and finite["scores"][0]["value"] == 4
    rc, out, _ = run(capsys, "check", REAL, "--no-git")
    lines = [line.removeprefix(f"{REAL}/2026-11-22-") for line in out.splitlines()]
    missing = [f"{name}.md: score 1: '{key}' is required: a non-empty text" for name in REGISTRY for key in ("reader", "task")]
    between = [f"{name}.md: note: score 1: the value {value} is between two anchors (the anchored levels are 1, 3, 5, 7, 9)"
               for name, value in (("components", 2), ("finite-contact", 4), ("rees", 4))]
    assert rc == 1 and lines == missing + between + [f"{REAL}: 9 attempt files, 0 open, 5 with problems"]
    rc, out, _ = run(capsys, "table", REAL, "--no-git", "--json")
    data = json.loads(out)
    assert rc == 0 and data["files"] == 9 and data["with_outcome"] == 4 and data["intervals"] == []     # nothing was run
    assert data["skipped_files"] == [f"2026-11-22-{name}.md" for name in REGISTRY]


USABILITY = ROOT / "tests" / "fixtures" / "attempt-files-usability"
UNCHECKED = ("outcome: 'accepted' requires a non-empty statement reference (or list of references) and an explicit "
             "list of axiom names, which may be empty; a kept change awaiting a check is 'status: unverified'")


def test_the_files_of_two_agents_without_a_build_and_three_of_the_real_history_are_told_what_they_lack(tmp_path, capsys):
    """Seven files with anonymized historical examples. Two agents had only the documents and a toy repository where no build was possible: the
    change that one kept is recorded as `abandoned` (remove-unused-helper) and the one of the other as `accepted`
    with `axioms: null` (direct-dependencies); each also priced an option it did not take, `not-run`. Three files of
    a real history say `accepted` with an empty contract and `forecasts: []`.
    By hand: the abandoned attempt resolves the 0.85 of agent:codex as not accepted, Brier 0.85 ** 2 = 0.7225, which
    surprised its author; no interval, since value needs an acceptance and both costs are null. The `accepted` one
    is left out. With `unverified` in both, as the convention now asks, the folder is clean and resolves nothing."""
    rc, out, _ = run(capsys, "check", USABILITY, "--no-git")
    history = ("v1-12-verifylab-reading-controls", "v3-36-use-lookup", "v4-37-check-overlap")
    assert rc == 1 and out.splitlines() == [
        f"{USABILITY}/2026-11-22-direct-dependencies.md: {UNCHECKED}",
        *(f"{USABILITY}/2026-11-22-{name}.md: note: {UNCHECKED}" for name in history),
        f"{USABILITY}: 7 attempt files, 0 open, 1 with problems"]
    data = json.loads(run(capsys, "table", USABILITY, "--no-git", "--json")[1])
    assert data["skipped_files"] == ["2026-11-22-direct-dependencies.md"] and data["intervals"] == []
    assert data["p_accepted"] == [{"by": "agent:codex", "resolved": 1, "mean_forecast": 0.85, "share_accepted": 0,
                                   "brier": pytest.approx(0.7225)}]
    directory = tmp_path / "attempts"
    directory.mkdir()
    for path in USABILITY.glob("*.md"):
        (directory / path.name).write_text(path.read_text().replace("status: abandoned", "status: unverified")
                                           .replace("status: accepted\n  at: 2026-11-22T23:40", "status: unverified\n  at: 2026-11-22T23:40"))
    rc, out, _ = run(capsys, "check", directory, "--no-git")
    assert rc == 0 and out.count(": note: ") == 3 and out.splitlines()[-1] == f"{directory}: 7 attempt files, 0 open, 0 with problems"
    data = json.loads(run(capsys, "table", directory, "--no-git", "--json")[1])
    assert data["skipped_files"] == [] and data["intervals"] == [] and data["p_accepted"] == [] and data["with_outcome"] == 7
    assert "No forecast is resolved yet" in run(capsys, "table", directory, "--no-git")[1]


DUMPED = VALID.replace("forecasts:\n" + FORECAST, "forecasts:\n" + """- by: model-a/high
  at: '2026-11-22T22:10+01:00'
  p_accepted: 0.8
  value:
    reached_lines:
    - -640
    - -560
  cost:
    agent_hours:
    - 1
    - 3
  basis: 'one call site, and a text long enough for a dumper
    to break: it goes on here'
""")
DUMPED_SECOND = """- basis: >-
    by: nobody
    a second forecaster, whose dumper sorts the keys
  by: "model-b/low"
  value: {reached_lines: [-700, -650]}
  at: '2026-11-22T22:20+01:00'
"""


def test_the_order_in_git_is_read_from_a_list_at_the_indentation_of_its_key(repository, capsys):
    directory = folder(repository, DUMPED.replace(OUTCOME, "").replace(SCORES, ""))
    commit_all(repository, "forecast")
    folder(repository, DUMPED)
    assert run(capsys, "check", directory) == (0, f"{directory}: 1 attempt files, 0 open, 0 with problems\n", "")
    commit_all(repository, "outcome")
    data = table(capsys, directory)
    assert [(row["by"], row["dimension"], row["inside"]) for row in data["intervals"]] == [
        ("model-a/high", "reached_lines", 1), ("model-a/high", "agent_hours", 1)]
    folder(repository, DUMPED.replace(OUTCOME, DUMPED_SECOND + OUTCOME))                      # a forecaster after the outcome
    rc, out, _ = run(capsys, "check", directory)
    assert rc == 1 and out.splitlines() == [
        f"{directory}/2026-11-22-attempt-1.md: late forecast by model-b/low: forecast not committed before the outcome "
        "(a past attempt without a forecast keeps 'forecasts: []')", f"{directory}: 1 attempt files, 0 open, 1 with problems"]
    assert table(capsys, directory)["excluded_forecasts"] == [
        {"file": "2026-11-22-attempt-1.md", "by": "model-b/low", "reason": "late"}]
    folder(repository, DUMPED.replace("    - -560\n", "    - -600\n"))                       # one line of a nested interval
    rc, out, _ = run(capsys, "check", directory)
    assert rc == 1 and out.splitlines()[0].startswith(
        f"{directory}/2026-11-22-attempt-1.md: forecast edited after the outcome: the entry by model-a/high")
    assert table(capsys, directory)["excluded_forecasts"] == [
        {"file": "2026-11-22-attempt-1.md", "by": "model-a/high", "reason": "edited"}]


# What the repository ships ----------------------------------------------------------------------------------------

def test_the_example_folder_of_the_repository_is_clean(capsys):
    directory = ROOT / "examples" / "toy-attempts" / "attempts"
    rc, out, _ = run(capsys, "check", directory)
    assert rc == 0 and out == f"{directory}: 2 attempt files, 2 open, 0 with problems\n"
    assert (directory.parent / "PURPOSE.md").is_file()


def test_the_example_printed_in_the_convention_follows_it(tmp_path, capsys):
    text = (ROOT / "docs" / "PURPOSE-AND-ATTEMPTS.md").read_text()
    block = text.split("```yaml\n", 1)[1].split("```", 1)[0]
    assert block.startswith("---\nschema: vl.attempt/2\n") and "outcome:" in block and "scores:" in block
    rc, out, _ = run(capsys, "check", folder(tmp_path, block), "--no-git")
    assert rc == 0, out


def test_shallow_clone_cannot_call_an_unseen_forecast_late(repository, tmp_path, capsys):
    directory = folder(repository, VALID.replace(OUTCOME, "").replace(SCORES, ""))
    commit_all(repository, "forecast")
    folder(repository, VALID)
    commit_all(repository, "outcome")
    assert table(capsys, directory)["p_accepted"][0]["resolved"] == 1
    shallow = tmp_path / "shallow"
    git(repository, "clone", "--depth", "1", repository.as_uri(), str(shallow))
    code, out, err = run(capsys, "check", shallow / "attempts", "--json")
    assert code == 0 and json.loads(out)["git_order"] == "partial" and not json.loads(out)["problems"]
    assert "deepen" in err
    data = table(capsys, shallow / "attempts")
    assert data["p_accepted"] == [] and data["excluded_forecasts"][0]["reason"] == "history-unavailable"


def test_squashed_archive_can_validate_the_same_records_without_claiming_chronology(repository, tmp_path, capsys):
    original = folder(repository, VALID.replace(OUTCOME, "").replace(SCORES, ""))
    commit_all(repository, "forecast")
    folder(repository, VALID)
    commit_all(repository, "outcome")
    assert run(capsys, "check", original, "--json")[0] == 0

    archive = tmp_path / "archive"
    archive.mkdir()
    git(archive, "init", "-q")
    git(archive, "config", "user.name", "Test")
    git(archive, "config", "user.email", "test@example.com")
    snapshot = folder(archive, VALID)
    commit_all(archive, "squashed export")
    assert (snapshot / "2026-11-22-attempt-1.md").read_bytes() == (original / "2026-11-22-attempt-1.md").read_bytes()
    code, out, _ = run(capsys, "check", snapshot, "--json")
    assert code == 1 and "late forecast" in json.loads(out)["problems"][0]["message"]
    code, out, err = run(capsys, "check", snapshot, "--no-git", "--json")
    assert code == 0 and json.loads(out)["git_order"] == "not-checked" and not json.loads(out)["problems"]
    assert "--no-git" in err


def test_folder_attempt_audit_reports_external_link_and_checks_other_entries(repository, tmp_path_factory, capsys):
    directory = folder(repository, VALID.replace(OUTCOME, "").replace(SCORES, ""))
    commit_all(repository, "forecast")
    folder(repository, VALID)
    external = tmp_path_factory.mktemp("external-diary") / "diary.md"
    external.write_text(VALID)
    link = directory / "external.md"
    link.symlink_to(external)
    code, out, _ = run(capsys, "check", directory, "--json")
    data = json.loads(out)
    assert code == 1 and data["files"] == 2 and data["open"] == 0
    assert data["problems"] == [{"file": str(link), "message": "attempt resolves outside the checkout"}]
    assert table(capsys, directory)["p_accepted"][0]["resolved"] == 1
