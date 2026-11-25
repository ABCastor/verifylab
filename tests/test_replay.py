"""`vl replay`: an admitted receipt's protected check run again in a fresh clone, and compared (ADR 0009).

Fast tests drive the whole command with a recording checker; the Lean test replays a real protected pass of the
planted fixture."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from verifylab import cli, machine
from verifylab.adapters.base import CheckOutcome
from verifylab.commands import check, replay
from verifylab.records import canonical_json, seal_receipt, sha256_hex

from conftest import commit_all, git, synthetic_receipt, write_machine

LEAN_TABLE = '\n[lean]\ntarget = "research/targets/add-zero.lean"\ntheorems = ["VL.hard", "VL.easy"]\n'
ANSWER = "Fixture/Basic.lean"


class Recording:
    """A Lean-named checker that hashes the answer file of the repository it is given and says `verdict`; it
    records the root it ran in and the machine policy it saw."""
    name = "lean-comparator"
    verdict = "pass"
    probes: dict | None = None          # the statement probes this check records, if any
    seen: list[dict] = []

    def applies(self, item):
        return bool(item.lean)

    def check(self, request):
        root = request.repo.root
        loaded = machine.load()
        Recording.seen.append({"root": root, "untracked": (root / "Fixture" / "Untracked.lean").exists(),
                               "build_cache": loaded.build_cache, "cache": loaded.cache_for(root)})
        data = synthetic_receipt(root, {"inputs": {"files": {ANSWER: sha256_hex((root / ANSWER).read_bytes())}}})
        data["target"]["theorems"] = data["checked"]["theorems"] = request.item.lean["theorems"]
        data["target"]["commit"] = request.trusted_commit
        if Recording.probes is not None:
            data["checked"] = {**data["checked"], "probes": Recording.probes}
        return CheckOutcome(Recording.verdict, [] if Recording.verdict == "pass" else ["no"], data["inputs"]["files"],
                            data["inputs"]["trusted_files"], data["target"], data["environment"], data["checked"],
                            ["fake"], "log")


@pytest.fixture
def admitted(research_repo, monkeypatch, tmp_path, capsys):
    """An item checked by the integrator on the trusted checkout and its receipt admitted: (root, receipt)."""
    item = research_repo / "research" / "items" / "add-zero.md"
    head, _, body = item.read_text().partition("+++\nBody")
    item.write_text(head + LEAN_TABLE + "+++\nBody" + body)
    (research_repo / ".gitignore").write_text(".vl-cache/\n")
    commit_all(research_repo, "question")
    monkeypatch.chdir(research_repo)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setattr(check, "load_adapters", lambda: [Recording()])
    Recording.verdict, Recording.probes, Recording.seen = "pass", None, []
    assert cli.main(["check", "add-zero", "--json"]) == 0
    path = research_repo / json.loads(capsys.readouterr().out)["receipt"]
    commit_all(research_repo, "admit the receipt")
    return research_repo, json.loads(path.read_text())


def replayed(capsys, *args) -> tuple[int, dict]:
    code = cli.main(["replay", "add-zero", "--json", *args])
    return code, json.loads(capsys.readouterr().out)


# The comparison --------------------------------------------------------------------------------------------------

RECEIPT = {
    "adapter": "lean-comparator", "verdict": "pass", "question_digest": "sha256:q",
    "inputs": {"digest": "sha256:i", "files": {"A.lean": "a1"}, "trusted_files": {"T.lean": "t1"},
               "candidate_head": "c" * 40, "trusted_ref": "main"},
    "environment": {"toolchain": "leanprover/lean4:v4", "lean_version": "Lean 4",
                    "tools": {"comparator": {"path": "/x/comparator", "sha256": "s1"},
                              "nanoda": {"path": "/x/nanoda", "sha256": "s2"}},
                    "packages": {"dir": "/x/packages", "identity": {
                        "revisions": {"mathlib": {"head": "r1", "manifest": "r1", "matches": True}},
                        "traces": {"Mathlib.Data": {"package": "mathlib", "depHash": "d1", "sha256": "f1"}}}}},
    "checked": {"kernels": ["lean", "nanoda"], "probes": {}},
    "finished_at": "2026-10-11T00:00:00.000000+00:00", "log_tail": "a",
}


def edited(path: tuple[str, ...], value) -> dict:
    data = json.loads(json.dumps(RECEIPT))
    table = data
    for key in path[:-1]:
        table = table[key]
    if value is None:
        del table[path[-1]]
    else:
        table[path[-1]] = value
    return data


def test_a_receipt_agrees_with_itself_and_what_describes_the_run_is_not_compared():
    assert replay.differences(RECEIPT, RECEIPT) == []
    run = edited(("finished_at",), "2027-01-01T00:00:00.000000+00:00")
    for path, value in [(("log_tail",), "b"), (("inputs", "candidate_head"), "d" * 40), (("inputs", "trusted_ref"), "x"),
                        (("environment", "tools", "comparator", "path"), "/elsewhere"),
                        (("environment", "packages", "dir"), "/other"), (("checked", "probes"), {"x": 1})]:
        run = edited(path, value) | {k: v for k, v in run.items() if k == "finished_at"}
        assert replay.differences(RECEIPT, run) == [], path


@pytest.mark.parametrize("path, value, expected", [
    (("verdict",), "fail", "verdict: receipt pass, replay fail"),
    (("question_digest",), "sha256:other", "question_digest: receipt sha256:q, replay sha256:other"),
    (("inputs", "digest"), "sha256:j", "inputs.digest: receipt sha256:i, replay sha256:j"),
    (("inputs", "files"), {"A.lean": "a2"}, "inputs.files[A.lean]: receipt a1, replay a2"),
    (("inputs", "files"), {"A.lean": "a1", "B.lean": "b1"}, "inputs.files[B.lean]: receipt absent, replay b1"),
    (("inputs", "trusted_files"), {}, "inputs.trusted_files[T.lean]: receipt t1, replay absent"),
    (("environment", "toolchain"), "leanprover/lean4:v5", "environment.toolchain: receipt leanprover/lean4:v4, "
                                                          "replay leanprover/lean4:v5"),
    (("environment", "tools", "nanoda", "sha256"), "s3", "environment.tools[nanoda].sha256: receipt s2, replay s3"),
    (("environment", "tools", "nanoda"), None, "environment.tools[nanoda].sha256: receipt s2, replay absent"),
    (("environment", "packages", "identity", "revisions", "mathlib", "head"), "r2",
     "environment.packages.identity.revisions[mathlib].head: receipt r1, replay r2"),
    (("environment", "packages", "identity", "traces", "Mathlib.Data", "depHash"), "d2",
     "environment.packages.identity.traces[Mathlib.Data].depHash: receipt d1, replay d2"),
    (("checked", "kernels"), ["lean"], "checked.kernels: receipt ['lean', 'nanoda'], replay ['lean']"),
])
def test_each_compared_field_that_differs_is_named(path, value, expected):
    assert replay.differences(RECEIPT, edited(path, value)) == [expected]


def probes(**theorems) -> dict:
    return {"checked": {"probes": {name: {"trivial_by": None, "vacuous_by": None, "prop_hypotheses": 1, **hits}
                                   for name, hits in theorems.items()}}}


def test_a_probe_hit_the_replay_finds_and_the_receipt_lacks_is_a_difference_but_not_the_reverse():
    """Planted: the replay's battery closes a theorem the receipt's did not (vacuous, trivial). Controls: the same hit
    on both sides (even by another tactic), none on either, and a hit only the receipt has."""
    clean, vacuous, trivial = probes(a={}), probes(a={"vacuous_by": "omega"}), probes(a={"trivial_by": "simp"})
    assert replay.differences(clean, vacuous) == ["checked.probes[a].vacuous_by: receipt absent, replay omega"]
    assert replay.differences(clean, trivial) == ["checked.probes[a].trivial_by: receipt absent, replay simp"]
    assert replay.differences({"checked": {}}, vacuous) == [        # a receipt with no probes lacks the hit too
        "checked.probes[a].vacuous_by: receipt absent, replay omega"]
    assert replay.differences(vacuous, probes(a={"vacuous_by": "decide"})) == []         # same hit, another tactic
    assert replay.differences(clean, clean) == [] and replay.differences(vacuous, clean) == []
    assert replay.differences(vacuous, {"checked": {}}) == []


# The command -----------------------------------------------------------------------------------------------------

def test_an_admitted_receipt_replays_to_a_match_in_a_clone_that_holds_nothing_of_the_worktree(admitted, capsys):
    root, receipt = admitted
    write_machine(caches={str(root): str(root / ".lake")})
    (root / "Fixture" / "Untracked.lean").write_text("-- only this worktree has it\n")
    before = git(root, "status", "--porcelain", "--ignored")
    code, out = replayed(capsys)
    assert (code, out["result"], out["differences"]) == (0, "match", []), out
    assert out["receipt_id"] == receipt["receipt_id"] and out["answer_commit"] == receipt["inputs"]["candidate_head"]
    seen = Recording.seen[-1]
    assert seen["root"] != root and not seen["root"].is_relative_to(root) and not seen["untracked"]
    assert not seen["root"].exists()                                   # the clone is deleted afterwards
    # the replay builds the trusted side anew and uses the Lake packages the machine sets for the repository
    assert seen["build_cache"] is False and seen["cache"] == str(root / ".lake")
    assert machine.load().build_cache is True and machine.load().cache_for(seen["root"]) is None
    # nothing written but the record, under .vl-cache
    assert git(root, "status", "--porcelain", "--ignored").replace("!! .vl-cache/\n", "") == before.replace(
        "!! .vl-cache/\n", "")
    record = json.loads((root / out["record"]).read_text())
    assert out["record"].startswith(".vl-cache/replay/add-zero/") and record["result"] == "match"
    assert record["replay"]["verdict"] == "pass" and record["receipt_id"] == receipt["receipt_id"]


def test_a_forged_receipt_does_not_replay(admitted, capsys):
    """The integrator admits a fail edited into a pass (resealed): the replay's check fails, a mismatch."""
    root, _ = admitted
    Recording.verdict = "fail"
    assert cli.main(["check", "add-zero", "--json"]) == 1
    path = root / json.loads(capsys.readouterr().out)["receipt"]
    forged = json.loads(path.read_text())
    path.unlink()
    forged = seal_receipt({**forged, "verdict": "pass", "reasons": [], "finished_at": "2099-01-01T00:00:00.000000+00:00"})
    (path.parent / f"{forged['receipt_id'][:16]}.json").write_text(json.dumps(forged))
    commit_all(root, "admit a forged receipt")
    code, out = replayed(capsys)                    # the newest admitted pass is the forged one
    assert out["receipt_id"] == forged["receipt_id"]
    assert (code, out["result"], out["differences"]) == (1, "mismatch", ["verdict: receipt pass, replay fail"]), out
    Recording.verdict = "pass"                      # control: the genuine receipt, named, still replays
    genuine = next(p for p in path.parent.glob("*.json") if p.name != f"{forged['receipt_id'][:16]}.json")
    code, out = replayed(capsys, "--receipt", genuine.name[:8])
    assert (code, out["result"]) == (0, "match"), out


def test_an_input_git_does_not_hold_as_checked_is_a_mismatch_without_running(admitted, capsys):
    """The integrator checked an uncommitted edit and admitted the receipt without it: no commit holds the checked
    bytes, so a stranger cannot reproduce the receipt."""
    root, _ = admitted
    answer = root / ANSWER
    original = answer.read_text()
    answer.write_text(original + "-- an edit only the integrator's worktree had\n")
    assert cli.main(["check", "add-zero", "--json"]) == 0
    dirty = json.loads((root / json.loads(capsys.readouterr().out)["receipt"]).read_text())
    answer.write_text(original)
    commit_all(root, "admit the receipt of an uncommitted edit")
    runs = len(Recording.seen)
    code, out = replayed(capsys, "--receipt", dirty["receipt_id"][:12])
    assert (code, out["result"]) == (1, "mismatch") and len(Recording.seen) == runs, out
    assert out["differences"][0].startswith("git does not hold the checked candidate files")
    assert out["differences"][1] == (f"inputs.files[{ANSWER}]: receipt {dirty['inputs']['files'][ANSWER]}, git holds "
                                     f"{sha256_hex(original.encode())} at candidate_head")


def test_a_receipt_replays_against_its_own_commits_after_a_later_change(admitted, capsys):
    """An answer changed and committed after the receipt: the receipt still replays from the commits it names (its
    status is verified-stale; the replay is of the receipt)."""
    root, receipt = admitted
    (root / ANSWER).write_text("theorem changed : True := trivial\n")
    commit_all(root, "a later answer")
    code, out = replayed(capsys)
    assert (code, out["result"], out["answer_commit"]) == (0, "match", receipt["inputs"]["candidate_head"]), out


def test_a_replay_that_cannot_compare_exits_3(admitted, capsys):
    root, _ = admitted
    Recording.verdict = "error"                     # an inconclusive check never matches a pass
    code, out = replayed(capsys)
    assert (code, out["result"]) == (3, "error") and "inconclusive: error" in out["reasons"][0], out
    Recording.verdict = "pass"
    # a question read from a commit no branch reaches: a stranger's clone cannot have it
    git(root, "checkout", "-q", "-b", "gone")
    git(root, "commit", "-q", "--allow-empty", "-m", "a commit only this object store will keep")
    gone = git(root, "rev-parse", "HEAD").strip()
    git(root, "checkout", "-q", "trusted")
    assert cli.main(["check", "add-zero", "--trusted-ref", "gone", "--json"]) == 0
    receipt = json.loads((root / json.loads(capsys.readouterr().out)["receipt"]).read_text())
    assert receipt["inputs"]["trusted_commit"] == gone
    git(root, "branch", "-q", "-D", "gone")
    commit_all(root, "admit it")
    code, out = replayed(capsys, "--receipt", receipt["receipt_id"][:12])
    assert (code, out["result"]) == (3, "error") and f"commit {gone} is not in a clone" in out["reasons"][0], out
    assert json.loads((root / out["record"]).read_text())["result"] == "error"


def test_only_an_admitted_protected_receipt_is_replayed(admitted, capsys):
    root, receipt = admitted
    assert cli.main(["replay", "add-zero", "--receipt", "ffff"]) == 2
    assert "0 admitted protected receipts" in capsys.readouterr().err
    assert cli.main(["check", "add-zero", "--json"]) == 0        # a proposal, not committed
    pending = json.loads(capsys.readouterr().out)["receipt_id"]
    assert cli.main(["replay", "add-zero", "--receipt", pending[:12]]) == 2
    assert cli.main(["replay", "no-such-item"]) == 2
    shutil.rmtree(root / "research" / "evidence")
    commit_all(root, "no evidence")
    assert cli.main(["replay", "add-zero"]) == 2
    assert "no valid admitted protected receipt" in capsys.readouterr().err


def admit_new_check(root, capsys, code: int) -> dict:
    """Run the protected check once more and commit its receipt; the receipt."""
    assert cli.main(["check", "add-zero", "--json"]) == code
    path = root / json.loads(capsys.readouterr().out)["receipt"]
    commit_all(root, "admit another receipt")
    return json.loads(path.read_text())


def show_names(capsys) -> str:
    assert cli.main(["show", "add-zero", "--json"]) == 0
    return json.loads(capsys.readouterr().out)["items"][0]["status"]["receipt"]


def test_a_probe_hit_only_the_replay_finds_is_a_mismatch_end_to_end(admitted, capsys):
    """Planted: the receipt records no hit and the replay's battery finds the theorem vacuous. The control: the same
    hit on both sides, then a hit only the receipt has, both match."""
    root, receipt = admitted
    Recording.probes = {"VL.hard": {"trivial_by": None, "vacuous_by": "omega", "prop_hypotheses": 2}}
    code, out = replayed(capsys)
    assert (code, out["result"]) == (1, "mismatch")
    assert out["differences"] == ["checked.probes[VL.hard].vacuous_by: receipt absent, replay omega"]
    hit = admit_new_check(root, capsys, 0)                                  # a receipt that records the hit
    code, out = replayed(capsys, "--receipt", hit["receipt_id"][:12])
    assert (code, out["result"]) == (0, "match"), out
    Recording.probes = None                                                 # the replay's battery finds nothing
    code, out = replayed(capsys, "--receipt", hit["receipt_id"][:12])
    assert (code, out["result"]) == (0, "match"), out


def test_by_default_the_receipt_that_decides_the_status_is_replayed(admitted, capsys):
    """Planted: a newer admitted protected fail of the same inputs makes the status `failed`, naming the fail, while
    the newest pass is the older receipt; and a vacuous older pass beside a newer clean one. `vl show` names the
    receipt that decides, and so does the replay. The control: one pass, which is the one."""
    root, first = admitted
    code, out = replayed(capsys)
    assert out["receipt_id"] == first["receipt_id"] and show_names(capsys).endswith(f"{first['receipt_id'][:16]}.json")
    Recording.verdict = "fail"
    failed = admit_new_check(root, capsys, 1)
    assert show_names(capsys).endswith(f"{failed['receipt_id'][:16]}.json")
    code, out = replayed(capsys)
    assert out["receipt_id"] == failed["receipt_id"], out
    code, out = replayed(capsys, "--receipt", first["receipt_id"][:12])         # naming one still works
    assert out["receipt_id"] == first["receipt_id"], out


def test_by_default_the_vacuous_receipt_that_decides_is_replayed_not_the_newest_clean_pass(admitted, capsys):
    root, first = admitted
    Recording.probes = {"VL.hard": {"trivial_by": None, "vacuous_by": "omega", "prop_hypotheses": 2}}
    vacuous = admit_new_check(root, capsys, 0)
    Recording.probes = {"VL.hard": {"trivial_by": None, "vacuous_by": None, "prop_hypotheses": 2}}
    clean = admit_new_check(root, capsys, 0)
    assert cli.main(["show", "add-zero", "--json"]) == 0
    card = json.loads(capsys.readouterr().out)["items"][0]
    assert card["status"]["label"] == "vacuous" and card["status"]["receipt"].endswith(f"{vacuous['receipt_id'][:16]}.json")
    assert clean["finished_at"] > vacuous["finished_at"]
    Recording.probes = None
    code, out = replayed(capsys)
    assert out["receipt_id"] == vacuous["receipt_id"], out


def test_the_replay_policy_is_undone_afterwards(tmp_path):
    write_machine(caches={str(tmp_path / "repo"): str(tmp_path / "lake")}, check={"reuse_solution_builds": True})
    before = machine.load()
    assert before.reuse_solution_builds is True
    with machine.replay_policy(tmp_path / "clone", tmp_path / "repo") as policy:
        assert machine.load() is policy and policy.build_cache is False
        assert policy.reuse_solution_builds is False     # a replay compiles the solution from the sources git holds
        assert policy.cache_for(tmp_path / "clone") == str(tmp_path / "lake")
    assert machine.load() is before


# A real protected Lean pass --------------------------------------------------------------------------------------

@pytest.mark.lean
def test_a_protected_lean_pass_replays_to_a_match(tmp_path, monkeypatch, capsys, pinned_tools):
    from conftest import lean_elan_home
    from lean_helpers import lean_unavailable, load_case, make_repo
    if lean_unavailable():
        pytest.skip(lean_unavailable())
    write_machine(pinned_tools, elan_home=lean_elan_home())
    root = make_repo(tmp_path, load_case("control-original"))
    git(root, "checkout", "-q", "trusted")
    git(root, "merge", "-q", "--ff-only", "candidate")              # the integrator merges, then checks
    monkeypatch.chdir(root)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    assert cli.main(["check", "double-even", "--json"]) == 0, capsys.readouterr()
    receipt = json.loads((root / json.loads(capsys.readouterr().out)["receipt"]).read_text())
    commit_all(root, "admit the receipt")
    code = cli.main(["replay", "double-even", "--json"])
    out = json.loads(capsys.readouterr().out)
    assert (code, out["result"], out["differences"]) == (0, "match", []), out
    record = json.loads((root / out["record"]).read_text())
    assert record["replay"]["checked"]["kernels"] == receipt["checked"]["kernels"] == ["lean", "nanoda"]
    assert "build_cache" in receipt["environment"] and "build_cache" not in record["replay"]["environment"]
    # planted: the same receipt bound to a checked file git does not hold (resealed, admitted)
    path = next((root / "research" / "evidence" / "double-even").glob("*.json"))
    files = dict(receipt["inputs"]["files"])
    name = sorted(files)[0]
    files[name] = "0" * 64
    basis = {"files": files, "trusted_files": receipt["inputs"]["trusted_files"], "target": receipt["target"]}
    inputs = {**receipt["inputs"], "files": files, "digest": "sha256:" + sha256_hex(canonical_json(basis).encode())}
    forged = seal_receipt({**receipt, "inputs": inputs, "finished_at": "2099-01-01T00:00:00.000000+00:00"})
    (path.parent / f"{forged['receipt_id'][:16]}.json").write_text(json.dumps(forged))
    commit_all(root, "admit an edited receipt")
    assert cli.main(["replay", "double-even", "--receipt", forged["receipt_id"][:12], "--json"]) == 1
    out = json.loads(capsys.readouterr().out)
    assert any(d.startswith(f"inputs.files[{name}]: receipt {'0' * 64}") for d in out["differences"]), out


def test_a_stranger_whose_trusted_branch_is_not_the_default_one_replays(admitted, tmp_path, monkeypatch, capsys):
    """A stranger's clone holds the publisher's branches as remote-tracking refs, the default one alone as a local
    branch: the receipt's commits, reachable only from `origin/trusted`, are what the publisher published."""
    root, receipt = admitted
    first = git(root, "rev-list", "--max-parents=0", "HEAD").strip()
    git(root, "checkout", "-q", "-b", "work", first)       # the default branch does not reach the receipt's commits
    stranger = tmp_path / "stranger"
    git(tmp_path, "clone", "-q", str(root), str(stranger))
    git(stranger, "config", "vl.trustedRef", "origin/trusted")
    monkeypatch.chdir(stranger)
    code, out = replayed(capsys)
    assert (code, out["result"], out["receipt_id"]) == (0, "match", receipt["receipt_id"]), out


def test_a_replay_that_waits_past_max_wait_exits_2_like_every_command(admitted, capsys):
    """Exit 2 is the queue wait of every command (README): a script retries it, unlike a replay that cannot compare."""
    from verifylab import queue
    root, _ = admitted
    write_machine(queue={"slots": 1})
    runs = len(Recording.seen)
    with queue.admit("another job"):
        assert cli.main(["replay", "add-zero", "--max-wait", "0.2"]) == 2
    assert "did not start: all 1 slots are busy" in capsys.readouterr().err and len(Recording.seen) == runs
    assert not (root / replay.REPLAY_DIR).exists()
    assert cli.main(["replay", "add-zero", "--max-wait", "5"]) == 0          # control: a free slot


@pytest.mark.parametrize("trusted_commit", [None, "trusted"])
def test_a_receipt_that_names_no_trusted_commit_cannot_be_replayed(admitted, capsys, trusted_commit):
    """Only a protected pass must name its trusted commit in full; an admitted fail that names none, or a branch,
    has no fixed question to replay against: it cannot compare (exit 3), never a crash or a match."""
    root, receipt = admitted
    inputs = {k: v for k, v in receipt["inputs"].items() if k != "trusted_commit"}
    if trusted_commit:
        inputs["trusted_commit"] = trusted_commit
    odd = seal_receipt({**receipt, "verdict": "fail", "reasons": ["no"], "inputs": inputs})
    (root / "research" / "evidence" / "add-zero" / f"{odd['receipt_id'][:16]}.json").write_text(json.dumps(odd))
    commit_all(root, "admit a fail naming no trusted commit")
    runs = len(Recording.seen)
    code, out = replayed(capsys, "--receipt", odd["receipt_id"][:12])
    assert (code, out["result"]) == (3, "error") and "names no trusted commit" in out["reasons"][0], out
    assert len(Recording.seen) == runs
    code, out = replayed(capsys)                    # it is the newer fail that decides the status: the default
    assert (code, out["result"], out["receipt_id"]) == (3, "error", odd["receipt_id"]), out
    code, out = replayed(capsys, "--receipt", receipt["receipt_id"][:12])         # control: the genuine pass beside it
    assert (code, out["result"]) == (0, "match"), out


def test_a_squashed_lane_replays_from_the_trusted_commit_on_the_integrators_machine_too(admitted, capsys):
    """The answer was checked on a lane commit, squashed onto the trusted branch, the lane deleted: its commit lingers
    in this object store but no clone has it, so the replay takes the answer from the trusted commit, as a stranger's
    does."""
    root, _ = admitted
    git(root, "checkout", "-q", "-b", "lane")
    (root / ANSWER).write_text("theorem add_zero' (n : Nat) : n + 0 = n := by simp\n")
    commit_all(root, "a new answer on the lane")
    lane_head = git(root, "rev-parse", "HEAD").strip()
    assert cli.main(["check", "add-zero", "--json"]) == 0
    receipt = json.loads((root / json.loads(capsys.readouterr().out)["receipt"]).read_text())
    assert receipt["inputs"]["candidate_head"] == lane_head
    git(root, "checkout", "-q", "trusted")
    git(root, "merge", "-q", "--squash", "lane")
    commit_all(root, "the lane, squashed, and its receipt")
    git(root, "branch", "-q", "-D", "lane")
    code, out = replayed(capsys, "--receipt", receipt["receipt_id"][:12])
    assert (code, out["result"]) == (0, "match"), out
    assert out["answer_commit"] == git(root, "rev-parse", "trusted").strip()


def test_an_answer_binding_committed_after_the_check_replays_from_the_admitting_commit(admitted, capsys):
    """The item's answer binding (here its imports) was edited before the check and committed with the receipt: the
    candidate head holds the checked files but not that binding, so the answer comes from the admitting commit."""
    root, _ = admitted
    item = root / "research" / "items" / "add-zero.md"
    item.write_text(item.read_text().replace('theorems = ["VL.hard", "VL.easy"]\n',
                                             'theorems = ["VL.hard", "VL.easy"]\nimports = ["Fixture.Basic"]\n'))
    assert cli.main(["check", "add-zero", "--json"]) == 0
    receipt = json.loads((root / json.loads(capsys.readouterr().out)["receipt"]).read_text())
    commit_all(root, "the binding and its receipt")
    code, out = replayed(capsys, "--receipt", receipt["receipt_id"][:12])
    assert (code, out["result"], out["answer_commit"]) == (0, "match", git(root, "rev-parse", "HEAD").strip()), out


def test_replay_finds_admission_after_the_trusted_answer_changes(admitted, capsys):
    root, _ = admitted
    answer = root / ANSWER
    answer.write_text(answer.read_text() + "-- checked edit\n")
    assert cli.main(["check", "add-zero", "--json"]) == 0
    receipt = json.loads((root / json.loads(capsys.readouterr().out)["receipt"]).read_text())
    commit_all(root, "preserve the answer and admit its receipt")
    admitted_at = git(root, "rev-parse", "HEAD").strip()
    receipt_args = ("--receipt", receipt["receipt_id"][:12])
    code, out = replayed(capsys, *receipt_args)
    assert (code, out["answer_commit"]) == (0, admitted_at)
    answer.write_text(answer.read_text() + "-- later edit\n")
    commit_all(root, "change the answer")
    code, out = replayed(capsys, *receipt_args)
    assert (code, out["answer_commit"]) == (0, admitted_at), out

    git(root, "branch", "preserved", admitted_at)
    code, out = replayed(capsys, *receipt_args, "--answer-rev", "origin/preserved")
    assert (code, out["answer_commit"], out["answer_from"]) == (0, admitted_at, "--answer-rev")
    runs = len(Recording.seen)
    code, out = replayed(capsys, *receipt_args, "--answer-rev", "HEAD")
    assert (code, out["result"], len(Recording.seen)) == (1, "mismatch", runs)
    assert out["differences"][1].endswith(" at --answer-rev")


# U3: three replay contracts (exact, compatibility, stronger recheck) and synthetic receipt profiles ---------------------

PROFILE_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "replay-profiles"


def _setup_replay_repo(tmp_path: Path, monkeypatch, *, vl_toml: str | None = None,
                       question_extra: str = "", include_vltrusted_file: bool = False):
    """Build a minimal research repo seeded with the toy fixture item/target/config."""
    root = tmp_path / "replay-fixture"
    (root / "research" / "items").mkdir(parents=True)
    (root / "research" / "targets").mkdir(parents=True)
    (root / "research" / "evidence").mkdir(parents=True)
    (root / "ReplayFixture").mkdir(parents=True)
    (root / "ReplayFixture" / "Support.lean").write_text("def support : Nat := 1\n")
    (root / "lakefile.toml").write_text('name = "ReplayFixture"\n')
    (root / "lake-manifest.json").write_text('{"version": "1.1.0", "packages": []}\n')
    (root / "lean-toolchain").write_text("leanprover/lean4:v4.34.0-rc2\n")
    (root / "research" / "vl.toml").write_text(vl_toml or (PROFILE_FIXTURES / "vl.toml").read_text())
    target_rel = "research/targets/replay-example.lean"
    (root / target_rel).write_text((PROFILE_FIXTURES / "replay-example.lean").read_text())
    md_text = (PROFILE_FIXTURES / "replay-example.md").read_text()
    if question_extra:
        md_text = md_text.replace("\n+++\n", f"\n{question_extra}\n+++\n", 1)
    (root / "research" / "items" / "replay-example.md").write_text(md_text)
    if include_vltrusted_file:
        (root / "VLTrusted" / "ReplayFixture").mkdir(parents=True)
        (root / "VLTrusted" / "ReplayFixture" / "Support.lean").write_text(
            "def support : Nat := 1\n")
    (root / ".gitignore").write_text(".vl-cache/\n")
    git(root, "init", "-q", "-b", "trusted")
    git(root, "config", "vl.trustedRef", "trusted")
    commit_all(root, "replay-fixture baseline")
    write_machine()
    monkeypatch.chdir(root)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    return root


def _make_historical_replay_receipt(root: Path, *, cfg_key: str = "research/vl.toml", kernels: list[str] | None = None,
                                    vltrusted_input: bool = False, include_item_revision: bool = True,
                                    finished_at: str = "2026-10-01T10:06:24+00:00") -> dict:
    from verifylab.config import CONFIG_PATH, config_digest, parse_config
    from verifylab.records import git_blob_sha

    commit = git(root, "rev-parse", "trusted").strip()
    fps = "ReplayFixture/Support.lean"
    target_rel = "research/targets/replay-example.lean"
    cfg_bytes = (root / CONFIG_PATH).read_bytes()
    cfg_digest = (sha256_hex(cfg_bytes) if cfg_key == CONFIG_PATH
                  else config_digest(cfg_key, parse_config(root, cfg_bytes.decode())))
    tf_key = f"VLTrusted/{fps}" if vltrusted_input else fps
    files = {fps: sha256_hex((root / fps).read_bytes()),
             "lake-manifest.json": sha256_hex((root / "lake-manifest.json").read_bytes()),
             "lakefile.toml": sha256_hex((root / "lakefile.toml").read_bytes()),
             "lean-toolchain": sha256_hex((root / "lean-toolchain").read_bytes())}
    trusted_files = {tf_key: files[fps],
                     "lake-manifest.json": files["lake-manifest.json"],
                     "lakefile.toml": files["lakefile.toml"],
                     "lean-toolchain": files["lean-toolchain"],
                     target_rel: sha256_hex((root / target_rel).read_bytes()),
                     cfg_key: cfg_digest}
    target = {"commit": commit, "path": target_rel, "sha256": trusted_files[target_rel],
              "source": "trusted-commit",
              "theorems": ["VL.Replay.first", "VL.Replay.second", "VL.Replay.third", "VL.Replay.fourth"]}
    digest = "sha256:" + sha256_hex(canonical_json(
        {"files": files, "trusted_files": trusted_files, "target": target}
    ).encode())
    item_bytes = (root / "research" / "items" / "replay-example.md").read_bytes()
    body = {
        "schema": "vl.receipt/1",
        "item": "replay-example",
        "item_revision": git_blob_sha(item_bytes) if include_item_revision else "0" * 40,
        "adapter": "lean-comparator",
        "assurance": "protected",
        "verdict": "pass",
        "reasons": ["Comparator accepted"],
        "inputs": {"candidate_head": commit, "candidate_uncommitted": [], "digest": digest,
                   "files": files, "trusted_commit": commit, "trusted_files": trusted_files,
                   "trusted_ref": "trusted"},
        "target": target,
        "environment": {
            "isolation": {"kind": "bwrap+landrun"},
            "toolchain": "leanprover/lean4:v4.34.0-rc2",
            "lean_version": "Lean 4.34.0-rc2",
            "packages": {"dir": "/pkg"},
            "tools": {"comparator": {"path": "/t/comparator", "sha256": "1" * 64},
                      "landrun": {"path": "/t/landrun", "sha256": "2" * 64},
                      "lean4export": {"path": "/t/lean4export", "sha256": "3" * 64},
                      **({"nanoda": {"path": "/t/nanoda", "sha256": "4" * 64}}
                         if kernels is None or "nanoda" in kernels else {})},
        },
        "checked": {"kernels": ["lean", "nanoda"] if kernels is None else kernels,
                    "theorems": target["theorems"],
                    "permitted_axioms": ["propext", "Quot.sound", "Classical.choice"],
                    "trusted_modules": [f"VLTrusted/{fps}"]},
        "command": ["vl"],
        "started_at": finished_at,
        "finished_at": finished_at,
        "tool_version": "vl 0.1.0",
    }
    return seal_receipt(body)


def _install_modern_replay_checker(monkeypatch, *, kernels: list[str] | None = None,
                                   closure: list[str] | None = None, probes: dict | None = None,
                                   verdict: str = "pass"):
    """Stub check.load_adapters so a modern check produces the modern shared-closure profile."""
    from verifylab.config import RULES_INPUT, rules_digest
    from verifylab.records import question_digest

    fps = "ReplayFixture/Support.lean"
    target_rel = "research/targets/replay-example.lean"

    class ReplayFixtureModernChecker:
        name = "lean-comparator"

        def applies(self, item):
            return bool(item.lean)

        def check(self, request):
            r = request.repo.root
            files = {p: sha256_hex((r / p).read_bytes())
                     for p in (fps, "lake-manifest.json", "lakefile.toml", "lean-toolchain")}
            trusted_files = {**files, target_rel: sha256_hex((r / target_rel).read_bytes()),
                             RULES_INPUT: rules_digest(request.repo.config)}
            theorems = request.item.lean["theorems"]
            target = {"commit": request.trusted_commit, "path": target_rel, "sha256": trusted_files[target_rel],
                      "source": "trusted-commit", "theorems": theorems}
            used_kernels = ["lean", "nanoda"] if kernels is None else kernels
            tools = {"comparator": {"path": "/t/comparator", "sha256": "1" * 64},
                     "landrun": {"path": "/t/landrun", "sha256": "2" * 64},
                     "lean4export": {"path": "/t/lean4export", "sha256": "3" * 64}}
            if "nanoda" in used_kernels:
                tools["nanoda"] = {"path": "/t/nanoda", "sha256": "4" * 64}
            env = {
                "isolation": {"kind": "bwrap+landrun"},
                "toolchain": "leanprover/lean4:v4.34.0-rc2",
                "lean_version": "Lean 4.34.0-rc2",
                "packages": {"dir": "/pkg", "identity": {"revisions": {"mathlib": {"head": "m1"}},
                                                          "traces": {"ReplayFixture.Dep": {"depHash": "d1"}}}},
                "tools": tools,
            }
            checked = {
                "kernels": used_kernels,
                "theorems": theorems,
                "permitted_axioms": ["propext", "Quot.sound", "Classical.choice"],
                "definitions_mode": "shared: target closure identical to the trusted one",
                "trusted_closure": [fps] if closure is None else closure,
                "trusted_modules": [],
                "probes": probes if probes is not None else {"VL.Replay.first": {"vacuous_by": None, "trivial_by": None}},
            }
            return CheckOutcome(verdict, [] if verdict == "pass" else ["rejected"], files, trusted_files,
                                target, env, checked, ["vl"], "log")

    monkeypatch.setattr(check, "load_adapters", lambda: [ReplayFixtureModernChecker()])


def test_u3_receipt_profile_detection():
    """The legacy profile triggers compatibility by shape; the modern profile uses the exact contract."""
    old = json.loads((PROFILE_FIXTURES / "legacy.json").read_text())
    new = json.loads((PROFILE_FIXTURES / "modern.json").read_text())
    assert replay.compatibility_features(old) == [
        "missing_question_digest",
        "legacy_config_binding",
        "legacy_vltrusted_modules",
        "missing_packages_identity",
    ]
    assert replay.compatibility_features(new) == []
    assert replay.resolve_contract(old, "auto")[0] == "compatibility"
    assert replay.resolve_contract(new, "auto")[0] == "exact"
    assert replay.resolve_contract(old, "exact")[0] == "exact"
    assert replay.resolve_contract(new, "compat")[2] is not None
    assert replay.resolve_contract(new, "recheck")[0] == "stronger"

    # Proof that profile detection is strictly structural, never timestamp-based:
    old_future = {**old, "started_at": "2099-01-01T00:00:00+00:00", "finished_at": "2099-01-01T00:00:00+00:00"}
    new_past = {**new, "started_at": "2020-01-01T00:00:00+00:00", "finished_at": "2020-01-01T00:00:00+00:00"}
    assert replay.compatibility_features(old_future) == replay.compatibility_features(old)
    assert replay.compatibility_features(new_past) == []

    # The fixture config yields the exact rules digest in the modern profile.
    from verifylab.config import RULES_INPUT, parse_config, rules_digest
    cfg = parse_config(PROFILE_FIXTURES, (PROFILE_FIXTURES / "vl.toml").read_text())
    assert rules_digest(cfg) == new["inputs"]["trusted_files"][RULES_INPUT]


def _replay_json(capsys, *argv) -> tuple[int, dict]:
    code = cli.main(["replay", *argv, "--json"])
    cap = capsys.readouterr()
    assert cap.out, f"replay exited {code} with no stdout; stderr={cap.err!r}"
    return code, json.loads(cap.out)


def test_u3_exact_contract_rejects_historical_differences_and_blocks_compat_fallback(tmp_path, monkeypatch, capsys):
    """Contract 1 (exact): strict on missing fields; --contract exact fails on historical receipt and modern receipts
    with deleted fields never fall back to compatibility."""
    root = _setup_replay_repo(tmp_path, monkeypatch)
    old = _make_historical_replay_receipt(root)
    ev_dir = root / "research" / "evidence" / old["item"]
    ev_dir.mkdir(parents=True)
    (ev_dir / f"{old['receipt_id'][:16]}.json").write_text(json.dumps(old))
    commit_all(root, "admit historical replay-fixture receipt")
    _install_modern_replay_checker(monkeypatch)

    code, out = _replay_json(capsys, old["item"], "--contract", "exact")
    assert (code, out["contract"], out["result"]) == (1, "exact", "mismatch"), out.get("reasons") or out
    diffs = "\n".join(out["differences"])
    for expected in ("question_digest", "inputs.digest", "research/vl.toml", "environment.packages.identity"):
        assert expected in diffs, diffs

    # Clean control + planted fallback guard: a modern receipt (carrying #verdict-rules) that drops question_digest
    # CANNOT use compat (--contract auto stays exact and mismatches; --contract compat exits 2).
    assert cli.main(["check", old["item"], "--json"]) == 0
    modern_path = root / json.loads(capsys.readouterr().out)["receipt"]
    modern = json.loads(modern_path.read_text())
    modern.pop("question_digest")
    corrupt_modern = seal_receipt(modern)
    modern_path.unlink()
    (ev_dir / f"{corrupt_modern['receipt_id'][:16]}.json").write_text(json.dumps(corrupt_modern))
    commit_all(root, "corrupt modern receipt without question_digest")

    assert cli.main(["replay", old["item"], "--receipt", corrupt_modern["receipt_id"][:12], "--contract", "compat"]) == 2
    capsys.readouterr()
    code = cli.main(["replay", old["item"], "--receipt", corrupt_modern["receipt_id"][:12], "--json"])
    out2 = json.loads(capsys.readouterr().out)
    assert (code, out2["contract"], out2["result"]) == (1, "exact", "mismatch"), out2


def test_u3_compatibility_contract_replays_historical_receipt_and_promotes_foreign(tmp_path, monkeypatch, capsys):
    """Contract 2 (compatibility): replays historical receipts via justified input projection (item_revision question,
    config binding, VLTrusted/ paths, omitted packages.identity) without rewriting history or inventing coverage."""
    root = _setup_replay_repo(tmp_path, monkeypatch, include_vltrusted_file=True)
    old = _make_historical_replay_receipt(root, vltrusted_input=True)
    ev_dir = root / "research" / "evidence" / old["item"]
    ev_dir.mkdir(parents=True)
    old_path = ev_dir / f"{old['receipt_id'][:16]}.json"
    old_bytes = json.dumps(old).encode()
    old_path.write_bytes(old_bytes)
    commit_all(root, "admit historical receipt")
    _install_modern_replay_checker(monkeypatch)

    for flags in ((), ("--contract", "compat")):
        code, out = _replay_json(capsys, old["item"], *flags)
        assert (code, out["contract"], out["result"]) == (0, "compatibility", "match"), out.get("reasons") or out
        assert out["compatibility"]["profile"] == replay.COMPAT_PROFILE
        assert out["compatibility"]["omitted_historical_fields"] == ["environment.packages.identity"]
        assert "not established" in out["compatibility"]["note"]
        assert set(out["compatibility"]["features"]) >= {
            "missing_question_digest", "legacy_config_binding",
            "legacy_vltrusted_trusted_files", "legacy_vltrusted_modules", "missing_packages_identity",
        }
    # Original historical receipt was never mutated or rewritten
    assert old_path.read_bytes() == old_bytes

    # Historical #verdict config binding also replays cleanly even when local cache config would change #verdict
    verdict_hist = _make_historical_replay_receipt(root, cfg_key="research/vl.toml#verdict")
    verdict_path = ev_dir / f"{verdict_hist['receipt_id'][:16]}.json"
    verdict_path.write_text(json.dumps(verdict_hist))
    commit_all(root, "historical #verdict receipt")
    code = cli.main(["replay", old["item"], "--receipt", verdict_hist["receipt_id"][:12], "--json"])
    out_v = json.loads(capsys.readouterr().out)
    assert (code, out_v["contract"], out_v["result"]) == (0, "compatibility", "match"), out_v

    # Foreign historical receipt under compatibility replay leaves quarantine & creates this machine's replay receipt
    old_path.unlink()
    verdict_path.unlink()
    from verifylab.repo import FOREIGN_DIR
    foreign_dir = root / FOREIGN_DIR / "peer" / old["item"]
    foreign_dir.mkdir(parents=True)
    foreign_path = foreign_dir / f"{old['receipt_id'][:16]}.json"
    foreign_path.write_bytes(old_bytes)
    commit_all(root, "quarantine foreign historical receipt")
    code = cli.main(["replay", old["item"], "--receipt", old["receipt_id"][:12], "--json"])
    out_f = json.loads(capsys.readouterr().out)
    assert (code, out_f["contract"], out_f["result"]) == (0, "compatibility", "match"), out_f
    assert out_f["promoted"] and (root / out_f["promoted"]).is_file() and not foreign_path.exists()
    # The promoted receipt carries this machine's full modern basis, never forging old coverage fields onto the old one
    promoted = json.loads((root / out_f["promoted"]).read_text())
    assert promoted["receipt_id"] != old["receipt_id"] and "identity" in promoted["environment"]["packages"]


def test_u3_compatibility_planted_guards_never_degrade_to_common_fields(tmp_path, monkeypatch, capsys):
    """Compatibility replay is NEVER common-fields-only: question change, axiom change, tampered inputs.digest,
    missing VLTrusted closure, missing item_revision blob, and newly triggered vacuous probe all fail."""
    root = _setup_replay_repo(tmp_path, monkeypatch)
    old = _make_historical_replay_receipt(root)
    ev_dir = root / "research" / "evidence" / old["item"]
    ev_dir.mkdir(parents=True)
    old_path = ev_dir / f"{old['receipt_id'][:16]}.json"

    def run_for(rec: dict, **checker_kw) -> tuple[int, dict]:
        for f in ev_dir.glob("*.json"):
            f.unlink()
        (ev_dir / f"{rec['receipt_id'][:16]}.json").write_text(json.dumps(rec))
        if git(root, "status", "--porcelain").strip():
            commit_all(root, "test receipt")
        _install_modern_replay_checker(monkeypatch, **checker_kw)
        code = cli.main(["replay", old["item"], "--receipt", rec["receipt_id"][:12], "--contract", "compat", "--json"])
        return code, json.loads(capsys.readouterr().out)

    # Control matches cleanly before any tampering
    code, out = run_for(old)
    assert (code, out["result"]) == (0, "match"), out

    # Planted guard 1: tampered inputs.digest on historical receipt is blocked both at admission/selection and
    # inside compatibility projection itself
    bad_dig = json.loads(json.dumps(old))
    bad_dig["inputs"]["digest"] = "sha256:" + "0" * 64
    bad_dig = seal_receipt(bad_dig)
    for f in ev_dir.glob("*.json"):
        f.unlink()
    (ev_dir / f"{bad_dig['receipt_id'][:16]}.json").write_text(json.dumps(bad_dig))
    commit_all(root, "bad digest")
    assert cli.main(["replay", old["item"], "--receipt", bad_dig["receipt_id"][:12], "--contract", "compat"]) == 2
    assert "inputs.digest does not match" in capsys.readouterr().err
    rep_rec = json.loads((root / out["record"]).read_text())["replay"]
    direct_found, _ = replay._compare_compatibility(
        root, bad_dig, rep_rec, rep_rec["question_digest"], "item_revision",
        replay.compatibility_features(bad_dig),
    )
    assert any("inputs.digest (recorded self-check)" in d for d in direct_found), direct_found

    # Planted guard 2: replayed shared closure drops historically trusted module
    code, out = run_for(old, closure=[])
    assert (code, out["result"]) == (1, "mismatch") and "checked.trusted_modules" in "\n".join(out["differences"])

    # Planted guard 3: new vacuous probe hit found on replay
    code, out = run_for(old, probes={"VL.Replay.first": {"vacuous_by": "intros; trivial", "trivial_by": None}})
    assert (code, out["result"]) == (1, "mismatch") and "vacuous_by" in "\n".join(out["differences"])

    # Planted guard 4: unrecoverable item_revision blob errors instead of skipping question_digest
    bad_rev = _make_historical_replay_receipt(root, include_item_revision=False)
    code, out = run_for(bad_rev)
    assert (code, out["result"]) == (3, "error"), out

    # Planted guard 5: item edited on trusted ref after old commit (prose statement doesn't alter question,
    # whereas changing wrong vl.toml axioms mismatches against trusted_commit's vl.toml)
    md_path = root / "research" / "items" / f"{old['item']}.md"
    md_path.write_text(md_path.read_text().replace(
        'imports = ["ReplayFixture.Support"]',
        'imports = ["ReplayFixture.Support", "ReplayFixture.Other"]',
        1,
    ))
    commit_all(root, "edit item question imports")
    bad_cfg = json.loads(json.dumps(old))
    bad_cfg["inputs"]["trusted_files"]["research/vl.toml"] = "f" * 64
    bad_cfg["inputs"]["digest"] = "sha256:" + sha256_hex(canonical_json(
        {"files": bad_cfg["inputs"]["files"], "trusted_files": bad_cfg["inputs"]["trusted_files"],
         "target": bad_cfg["target"]}
    ).encode())
    code, out = run_for(seal_receipt(bad_cfg))
    assert (code, out["result"]) == (1, "mismatch") and "research/vl.toml" in "\n".join(out["differences"])

    # Planted guard 6: an item_revision whose question differs from every commit holding the checked files mismatches
    from verifylab.records import git_blob_sha
    tampered_rev = git_blob_sha(md_path.read_bytes())
    md_path.write_text((PROFILE_FIXTURES / "replay-example.md").read_text())
    commit_all(root, "restore original question while #2 stays in ancestor history")
    wrong_q = json.loads(json.dumps(old))
    wrong_q["item_revision"] = tampered_rev
    code, out = run_for(seal_receipt(wrong_q))
    assert (code, out["result"]) == (1, "mismatch") and "question_digest" in "\n".join(out["differences"])
    assert old_path.name  # reference kept for readability


def test_u3_stronger_recheck_contract_records_separate_evidence_and_never_auto_promotes(tmp_path, monkeypatch, capsys):
    """Contract 3 (stronger): `--contract recheck` checks preserved question & inputs under stronger kernels (adding
    nanoda to a 1-kernel historical receipt), records result='rechecked' + relation, never spoofs match or promotes."""
    cfg_1k = (PROFILE_FIXTURES / "vl.toml").read_text().replace('roots = ["ReplayFixture"]',
                                                               'roots = ["ReplayFixture"]\nexternal_kernels = false')
    root = _setup_replay_repo(tmp_path, monkeypatch, vl_toml=cfg_1k)
    old_1k = _make_historical_replay_receipt(root, kernels=["lean"])
    from verifylab.repo import FOREIGN_DIR
    foreign_dir = root / FOREIGN_DIR / "peer" / old_1k["item"]
    foreign_dir.mkdir(parents=True)
    foreign_path = foreign_dir / f"{old_1k['receipt_id'][:16]}.json"
    foreign_path.write_text(json.dumps(old_1k))
    _install_modern_replay_checker(monkeypatch, kernels=["lean", "nanoda"])

    # Stronger recheck succeeds with result='rechecked', explicit relation metadata, and NEVER promotes foreign receipt
    code, out = _replay_json(capsys, old_1k["item"], "--receipt", old_1k["receipt_id"][:12], "--contract", "recheck")
    assert (code, out["contract"], out["result"]) == (0, "stronger", "rechecked"), out.get("reasons") or out
    assert out["promoted"] is None and foreign_path.exists()
    assert out["relation"]["original_receipt_id"] == old_1k["receipt_id"]
    assert out["relation"]["added_kernels"] == ["nanoda"]
    assert out["relation"]["removed_kernels"] == []
    assert out["relation"]["separate_evidence"] is True
    record = json.loads((root / out["record"]).read_text())
    assert (record["result"], record["contract"], record["relation"]["added_kernels"]) == ("rechecked", "stronger", ["nanoda"])

    # Contrast: --contract exact and --contract compat both refuse to treat added kernels as an exact/compat match
    for c in ("exact", "compat"):
        c_code = cli.main(["replay", old_1k["item"], "--receipt", old_1k["receipt_id"][:12], "--contract", c, "--json"])
        c_out = json.loads(capsys.readouterr().out)
        assert c_code == 1 and c_out["result"] == "mismatch" and "checked.kernels" in "\n".join(c_out["differences"])

    # Planted negative: recheck refuses dropped kernels, changed verdict, and new vacuous probes
    _install_modern_replay_checker(monkeypatch, kernels=["lean"], probes=None)
    # Use a 2-kernel receipt checked by a 1-kernel replay -> kernel dropped -> mismatch
    old_2k = _make_historical_replay_receipt(root, kernels=["lean", "nanoda"])
    (root / "research" / "evidence" / old_2k["item"]).mkdir(parents=True)
    (root / "research" / "evidence" / old_2k["item"] / f"{old_2k['receipt_id'][:16]}.json").write_text(json.dumps(old_2k))
    commit_all(root, "admit 2-kernel receipt")
    c_drop = cli.main(["replay", old_2k["item"], "--receipt", old_2k["receipt_id"][:12], "--contract", "recheck", "--json"])
    out_drop = json.loads(capsys.readouterr().out)
    assert (c_drop, out_drop["result"]) == (1, "mismatch") and "dropped kernel" in "\n".join(out_drop["differences"])

    _install_modern_replay_checker(monkeypatch, kernels=["lean", "nanoda"],
                                   probes={"VL.Replay.first": {"vacuous_by": "simp", "trivial_by": None}})
    c_vac = cli.main(["replay", old_2k["item"], "--receipt", old_2k["receipt_id"][:12], "--contract", "recheck", "--json"])
    out_vac = json.loads(capsys.readouterr().out)
    assert (c_vac, out_vac["result"]) == (1, "mismatch") and "vacuous_by" in "\n".join(out_vac["differences"])
    # Check verdict mismatch: stub returns fail when recorded pass
    _install_modern_replay_checker(monkeypatch, kernels=["lean", "nanoda"], verdict="fail")
    c_fail = cli.main(["replay", old_2k["item"], "--receipt", old_2k["receipt_id"][:12], "--contract", "recheck", "--json"])
    out_fail = json.loads(capsys.readouterr().out)
    assert (c_fail, out_fail["result"]) == (1, "mismatch") and "verdict:" in "\n".join(out_fail["differences"])
