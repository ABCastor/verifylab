"""`vl jobs export | missing | shard`: a job list read from the trusted commit alone, which jobs an admitted receipt
answers, and one ids file per machine. Receipts here are synthetic: the Lean side is tested elsewhere."""

from __future__ import annotations

import json
from argparse import Namespace
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from verifylab import cli, jobs
from verifylab.commands import batch, check
from verifylab.commands import jobs as jobs_cmd
from verifylab.records import sha256_hex, write_new_json
from verifylab.repo import Repo, TrustedRepo

from batch_helpers import add_lean_items
from conftest import commit_all, git, item_question, synthetic_receipt, write_item

G = 2**30
IDS = ["add-zero", "t1", "t2", "t3", "t4"]


def vl(capsys, *argv):
    """`vl` in this process: (exit code, stdout, stderr); an argparse refusal is exit 2 as well."""
    try:
        rc = cli.main(list(argv))
    except SystemExit as exc:
        rc = exc.code
    out = capsys.readouterr()
    return rc, out.out, out.err


@pytest.fixture
def root(research_repo, monkeypatch):
    add_lean_items(research_repo, "t1", "t2", "t3", "t4")
    monkeypatch.chdir(research_repo)
    return research_repo


def put(root, item_id, peak=None, seconds=None, finished="2026-10-11T10:00:00+00:00", question=None, commit=None,
        assurance="protected", verdict="pass"):
    """A receipt of the item (a protected pass unless told) that recorded `peak` bytes and ran `seconds`, written to
    the worktree; `question` and `commit` replace the ones it would answer."""
    lean = "Fixture/Basic.lean"
    end = datetime.fromisoformat(finished)
    extra = {"memory_peak_bytes": peak} if peak else {}
    inputs = {"digest": "d", "files": {lean: sha256_hex((root / lean).read_bytes())}}
    fields = {}
    if commit:       # a receipt checked at another commit has its target from that commit too
        inputs["trusted_commit"] = commit
        fields["target"] = {"path": "ReceiptFixture.lean", "sha256": sha256_hex((root / "ReceiptFixture.lean").read_bytes()),
                            "source": "trusted-commit", "commit": commit, "theorems": ["Fixture.receipt"]}
    receipt = synthetic_receipt(root, dict(
        item=item_id, item_revision="a" * 40, question_digest=question or item_question(root, item_id),
        adapter="lean-comparator", assurance=assurance, verdict=verdict, reasons=[], inputs=inputs, environment={},
        checked={}, command=["vl"], started_at=(end - timedelta(seconds=seconds or 1)).isoformat(),
        finished_at=end.isoformat(), tool_version="vl 0.1.0", **fields, **({"extra": extra} if extra else {})))
    path = root / "research" / "evidence" / item_id / f"{receipt['receipt_id'][:16]}.json"
    write_new_json(path, receipt)
    return path


def export(capsys, root, *extra, name="jobs.json"):
    rc, out, err = vl(capsys, "jobs", "export", str(root / name), *extra)
    assert rc == 0, err
    return json.loads((root / name).read_text())


def trusted(root) -> str:
    return git(root, "rev-parse", "trusted").strip()


# Export -----------------------------------------------------------------------------------------------------------

def test_export_names_each_job_by_item_commit_and_question(root, capsys):
    data = export(capsys, root, "--all")
    commit = trusted(root)
    assert data["schema"] == "vl.jobs/1" and data["trust"]["commit"] == commit and data["project"] == "fixture"
    assert [j["item"] for j in data["jobs"]] == IDS and {j["adapter"] for j in data["jobs"]} == {"lean-comparator"}
    for job in data["jobs"]:
        assert job["trusted_commit"] == commit and job["question_digest"] == item_question(root, job["item"])
        assert job["job"] == jobs.job_id(job["item"], commit, job["question_digest"])
        assert job["reservation_bytes"] is None and job["expected_seconds"] is None
    assert jobs.load((root / "jobs.json").read_text()) == data
    export(capsys, root, "--all", name="again.json")
    assert (root / "again.json").read_bytes() == (root / "jobs.json").read_bytes()      # the same records, the same bytes


def test_a_changed_question_is_a_new_job_and_the_others_stay(root, capsys):
    before = export(capsys, root, "--all")
    item = root / "research" / "items" / "t2.md"
    item.write_text(item.read_text().replace('["VL.hard", "VL.easy"]', '["VL.easy"]'))
    commit_all(root, "t2 asks another question")
    after = export(capsys, root, "--all", name="after.json")
    changed = {a["item"] for a, b in zip(after["jobs"], before["jobs"]) if a["question_digest"] != b["question_digest"]}
    assert changed == {"t2"} and after["digest"] != before["digest"]
    # the trusted commit is part of the name, so every job of the new list is a different job
    assert not {j["job"] for j in after["jobs"]} & {j["job"] for j in before["jobs"]}


def test_ids_keep_their_order_and_must_be_checkable_on_the_trusted_ref(root, capsys, tmp_path):
    write_item(root, "plain")                                           # no checker table
    commit_all(root, "plain")
    ids = tmp_path / "ids.txt"
    ids.write_text("t3\n# skip\nt1  # why\nt3\n")
    assert [j["item"] for j in export(capsys, root, "--ids", str(ids))["jobs"]] == ["t3", "t1"]
    for bad in ("plain", "nowhere"):
        ids.write_text(f"t1\n{bad}\n")
        rc, _, err = vl(capsys, "jobs", "export", str(root / "no.json"), "--ids", str(ids))
        assert rc == 2 and bad in err and not (root / "no.json").exists()
    assert vl(capsys, "jobs", "export", str(root / "no.json"), "--ids", str(tmp_path / "gone.txt"))[0] == 2
    assert vl(capsys, "jobs", "export", str(root / "no.json"))[0] == 2                 # one of --all, --stale, --ids
    assert vl(capsys, "jobs", "export", str(root / "no.json"), "--all", "--stale")[0] == 2


def test_stale_and_the_list_come_from_the_trusted_ref_never_the_worktree(root, capsys):
    """Planted: a protected pass only the worktree has. The trusted view still wants t1 checked; a plain Repo, which
    reads the worktree (the guard disabled), calls it pending admission and leaves it out. The control: committed, it
    is done for both."""
    put(root, "t1")
    ask = Namespace(ids=None, stale=True)
    assert "t1" in jobs_cmd.select(ask, TrustedRepo.open())
    assert "t1" not in batch.select(ask, Repo.open(), Repo.open().load_items()[0], check.load_adapters)
    assert "t1" in [j["item"] for j in export(capsys, root, "--stale")["jobs"]]
    commit_all(root, "admit t1")
    assert "t1" not in jobs_cmd.select(ask, TrustedRepo.open())
    assert "t1" not in [j["item"] for j in export(capsys, root, "--stale")["jobs"]]


def test_the_expected_reservation_is_what_a_check_would_reserve(root, capsys):
    put(root, "t1", peak=1 * G, seconds=10, finished="2026-10-11T10:00:00+00:00")
    put(root, "t1", peak=3 * G, seconds=30, finished="2026-10-11T11:00:00+00:00")
    for hour in range(6):                       # t4: the 5 newest peaks count, so the oldest, largest one does not
        put(root, "t4", peak=(9 if hour == 0 else 1 + hour) * G, seconds=5 + hour,
            finished=f"2026-10-11T{10 + hour}:00:00+00:00")
    put(root, "t2", verdict="fail")             # a receipt that recorded no peak
    commit_all(root, "receipts")
    by_item = {j["item"]: j for j in export(capsys, root, "--all")["jobs"]}
    repo = TrustedRepo.open()
    for item_id, job in by_item.items():        # parity with what `vl check` reserves
        assert job["reservation_bytes"] == check.last_peak(repo, item_id, "lean-comparator"), item_id
    assert by_item["t1"]["reservation_bytes"] == 3 * G and by_item["t1"]["expected_seconds"] == 30
    assert by_item["t4"]["reservation_bytes"] == 6 * G and by_item["t4"]["expected_seconds"] == 10
    assert by_item["t2"]["reservation_bytes"] == 9 * G and by_item["t2"]["expected_seconds"] == 1    # the checker's largest
    assert by_item["t3"]["reservation_bytes"] == 9 * G and by_item["t3"]["expected_seconds"] is None


def test_the_largest_peak_of_each_checker_is_found_once_for_the_whole_list(root, capsys, monkeypatch):
    """Items without a peak of their own reserve their checker's largest: found once per list, not once per item."""
    put(root, "t1", peak=2 * G)
    commit_all(root, "receipt")
    calls = []
    real = jobs.largest_peaks
    monkeypatch.setattr(jobs, "largest_peaks", lambda found: calls.append(1) or real(found))
    assert {j["reservation_bytes"] for j in export(capsys, root, "--all")["jobs"]} == {2 * G}
    assert len(calls) == 1


def test_an_unresolvable_trusted_ref_exports_nothing(root, capsys):
    rc, _, err = vl(capsys, "jobs", "export", str(root / "no.json"), "--all", "--trusted-ref", "nowhere")
    assert rc == 2 and "nothing to export" in err and not (root / "no.json").exists()


# Missing ----------------------------------------------------------------------------------------------------------

def test_missing_counts_only_admitted_protected_receipts_for_that_question_at_that_commit(root, capsys):
    data = export(capsys, root, "--all")
    commit = trusted(root)
    put(root, "add-zero")                                               # admitted, answers the job: done
    put(root, "t3", question="sha256:" + "0" * 64)                      # admitted, for another question
    put(root, "t4", commit="f" * 40)                                    # admitted, checked at another commit
    commit_all(root, "admit")
    assert all(not r.problems for i in ("add-zero", "t3", "t4") for r in TrustedRepo.open().receipts(i))   # valid, so only the key differs
    put(root, "t2", commit=commit)                                      # a proposal: in the worktree only
    rc, out, err = vl(capsys, "jobs", "missing", str(root / "jobs.json"), "--out", str(root / "left.json"))
    assert rc == 0 and out.split() == ["t1", "t2", "t3", "t4"] and "4 of 5 jobs" in err
    left = jobs.load((root / "left.json").read_text())
    assert [j["item"] for j in left["jobs"]] == ["t1", "t2", "t3", "t4"] and left["digest"] != data["digest"]
    assert left["jobs"] == [j for j in data["jobs"] if j["item"] != "add-zero"]
    # the control: admitting the proposal and answering the other two as asked leaves them done, at a later commit
    put(root, "t3", commit=commit)
    put(root, "t4", commit=commit)
    put(root, "t1", commit=commit)
    commit_all(root, "admit more")
    assert trusted(root) != commit
    rc, out, err = vl(capsys, "jobs", "missing", str(root / "jobs.json"), "--json")
    assert rc == 0 and json.loads(out)["missing"] == [] and "0 of 5 jobs" in err


def test_a_failed_admitted_check_is_an_answer_too(root, capsys):
    data = export(capsys, root, "--all")
    put(root, "t1", verdict="fail")
    commit_all(root, "admit a fail")
    assert "t1" not in vl(capsys, "jobs", "missing", str(root / "jobs.json"))[1].split()
    assert len(jobs.missing(TrustedRepo.open(), data)) == 4


# A list that was edited -------------------------------------------------------------------------------------------

def edited(root, tmp_path, change):
    data = json.loads((root / "jobs.json").read_text())
    change(data)
    path = tmp_path / "edited.json"
    path.write_text(json.dumps(data))
    return str(path)


@pytest.mark.parametrize("change", [
    lambda d: d["jobs"][0].update(question_digest="sha256:" + "0" * 64),    # the id no longer names the content
    lambda d: d["jobs"].reverse(),                                          # the digest covers the order
    lambda d: d["jobs"].pop(),                                              # and the set
    lambda d: d["jobs"].append(d["jobs"][0]),                               # a job listed twice
    lambda d: d.update(digest="sha256:" + "0" * 64),
    lambda d: d.update(schema="vl.jobs/2"),
    lambda d: d["jobs"][0].update(reservation_bytes=-1),
    lambda d: d["jobs"][0].update(reservation_bytes=True),
    lambda d: d["jobs"][0].pop("expected_seconds"),
], ids=["question", "order", "dropped", "repeated", "digest", "schema", "negative", "bool", "key"])
def test_a_job_list_that_was_edited_is_refused_by_every_reader(root, capsys, tmp_path, change):
    export(capsys, root, "--all")
    bad = edited(root, tmp_path, change)
    for argv in (["missing", bad], ["shard", bad, "--weights", "a=1"]):
        rc, _, err = vl(capsys, "jobs", *argv)
        assert rc == 2 and "edited.json" in err, argv
    assert vl(capsys, "jobs", "missing", str(root / "jobs.json"))[0] == 0              # the control: as exported
    assert vl(capsys, "jobs", "shard", str(root / "jobs.json"), "--weights", "a=1")[0] == 0


def test_a_missing_or_foreign_file_is_a_usage_error(root, capsys, tmp_path):
    (tmp_path / "other.json").write_text("{}")
    (tmp_path / "text.json").write_text("nope")
    for path in (tmp_path / "gone.json", tmp_path / "other.json", tmp_path / "text.json"):
        assert vl(capsys, "jobs", "missing", str(path))[0] == 2


# Shard ------------------------------------------------------------------------------------------------------------

def listing(costs, by="reservation"):
    """Jobs named j0, j1, ... with these costs (None: unknown), shaped as a loaded list."""
    key = {"reservation": "reservation_bytes", "seconds": "expected_seconds"}[by]
    return [{"job": f"{n:016x}", "item": f"j{n}", "adapter": "a", "trusted_commit": "c", "question_digest": "q",
             "reservation_bytes": None, "expected_seconds": None, **{key: cost}} for n, cost in enumerate(costs)]


def test_equal_jobs_split_in_proportion_to_the_weights():
    """Planted: 90 equal jobs, weights 3:2:4. A round robin or a split that ignores the weights gives 30:30:30."""
    shards = jobs.shard(listing([G] * 90), {"thinkpad": 3, "hp": 2, "samsung": 4}, "reservation")
    assert {m: len(s) for m, s in shards.items()} == {"thinkpad": 30, "hp": 20, "samsung": 40}


def test_every_job_goes_to_one_machine_and_the_load_follows_the_weights():
    costs = [(n * 7919) % 97 + 1 for n in range(200)]
    weights = {"thinkpad": 3, "hp": 2, "samsung": 4}
    shards = jobs.shard(listing(costs), weights, "reservation")
    assert sorted(j["item"] for s in shards.values() for j in s) == sorted(f"j{n}" for n in range(200))
    load = {m: sum(j["reservation_bytes"] for j in s) for m, s in shards.items()}
    finish = [load[m] / weights[m] for m in weights]
    assert max(finish) - min(finish) <= max(costs) / min(weights.values())            # greedy: within one job
    for s in shards.values():                                                         # biggest first
        assert [j["reservation_bytes"] for j in s] == sorted((j["reservation_bytes"] for j in s), reverse=True)
    assert jobs.shard(listing(costs), weights, "reservation") == shards               # deterministic


def test_jobs_that_cost_nothing_still_spread_over_the_machines():
    free = [{**j, "expected_seconds": 0} for j in listing([G] * 4)]
    assert [len(s) for s in jobs.shard(free, {"a": 1, "b": 1}, "seconds").values()] == [2, 2]
    assert [len(s) for s in jobs.shard(listing([G] * 6), {"a": 1, "b": 2}, "reservation").values()] == [2, 4]


def test_a_job_with_no_figure_costs_the_median_and_spreads_out():
    mixed = listing([10, 20, 30, None, None, None, None, None, None])
    assert jobs.cost_of(mixed, "reservation") == {j["job"]: c for j, c in zip(mixed, [10, 20, 30] + [20] * 6)}
    assert set(jobs.cost_of(listing([None, None]), "reservation").values()) == {1.0}
    shards = jobs.shard(listing([None] * 12), {"a": 1, "b": 1, "c": 1}, "reservation")
    assert [len(s) for s in shards.values()] == [4, 4, 4]
    by_time = listing([5, 1, 1, 1, 1, 1, 1])
    by_time = [{**j, "reservation_bytes": 1, "expected_seconds": j["reservation_bytes"]} for j in by_time]
    sizes = {m: len(s) for m, s in jobs.shard(by_time, {"a": 1, "b": 1}, "seconds").items()}
    assert sizes == {"a": 2, "b": 5}                                                   # 6 s against 5 s


def test_the_files_are_ids_files_a_machine_can_check(root, capsys, tmp_path):
    export(capsys, root, "--all")
    out = tmp_path / "shards"
    rc, text, _ = vl(capsys, "jobs", "shard", str(root / "jobs.json"), "--weights", "thinkpad=3,hp=2", "--out", str(out))
    assert rc == 0 and "5 jobs" in text and "thinkpad" in text
    assert sorted(p.name for p in out.iterdir()) == ["hp.txt", "thinkpad.txt"]
    ids = [batch.read_ids(str(out / f"{m}.txt")) for m in ("thinkpad", "hp")]
    assert sorted(i for part in ids for i in part) == IDS and len(ids[0]) == 3
    assert (out / "hp.txt").read_text().startswith("# vl jobs shard of sha256:")
    assert f"\n# trusted commit {trusted(root)}\n" in (out / "hp.txt").read_text()     # the commit it was cut for
    repo = Repo.open(root)
    assert batch.pin_warning(str(out / "hp.txt"), repo) is None
    moved = tmp_path / "moved.txt"
    moved.write_text((out / "hp.txt").read_text().replace(trusted(root), "f" * 40))
    assert "was cut for the trusted commit ffffffffffff" in batch.pin_warning(str(moved), repo)
    assert not (tmp_path / "t1.txt").exists()
    rc, text, _ = vl(capsys, "jobs", "shard", str(root / "jobs.json"), "--weights", "solo=1", "--json", "--by", "seconds")
    data = json.loads(text)                                      # by default, beside the job list
    assert rc == 0 and data["by"] == "seconds" and (root / "solo.txt").is_file()
    assert Counter(batch.read_ids(str(root / "solo.txt"))) == Counter(IDS)
    assert data["shards"][0]["jobs"] == 5 and data["shards"][0]["file"] == str(root / "solo.txt")


@pytest.mark.parametrize("weights", ["../evil=1", "a/b=1", ".hidden=1", "=1", "a=0", "a=-1", "a=nan", "a=inf", "a=x",
                                     "a", "a=1,a=2", "a=1,,b=1", ""])
def test_a_machine_name_that_is_a_path_or_a_weight_that_is_no_number_writes_nothing(root, capsys, tmp_path, weights):
    export(capsys, root, "--all")
    out = tmp_path / "shards"
    rc, _, _ = vl(capsys, "jobs", "shard", str(root / "jobs.json"), "--weights", weights, "--out", str(out))
    assert rc == 2 and not out.exists() and not (tmp_path / "evil.txt").exists()


def test_the_weights_of_the_documented_example_are_accepted():
    assert jobs_cmd.weights("thinkpad=3,hp=2,samsung=4") == {"thinkpad": 3, "hp": 2, "samsung": 4}
    assert jobs_cmd.weights("a.b-c_1=0.5") == {"a.b-c_1": 0.5}
