"""`vl check --all | --stale | --ids`: which items are chosen, that the machine's queue (not -j) bounds how many run at
once, and the summary and exit code. The checks are real `vl check` processes with a fake adapter."""

from __future__ import annotations

import json
import sys
import textwrap
from argparse import Namespace
from pathlib import Path

import pytest

from verifylab import cli
from verifylab.commands import batch, check
from verifylab.repo import Repo

from batch_helpers import add_lean_items, write_receipt
from conftest import commit_all, write_item, write_machine

TESTS = Path(__file__).parent

# The child process of one check: the real `vl`, with a fake adapter that holds the check for HOLD seconds and logs
# when it starts and ends. An id starting `bad` fails, `err` ends in an error, `unsup` in unsupported (each with a
# receipt), `boom` crashes the adapter (no receipt).
CHILD = textwrap.dedent('''
    import sys, time
    from pathlib import Path
    sys.path.insert(0, {tests!r})
    from test_check_command import FakeAdapter
    from verifylab import cli, queue
    from verifylab.commands import check

    class Slow(FakeAdapter):
        def check(self, request):
            item = request.item.id
            with open({log!r}, "a") as log:
                log.write(f"{{time.monotonic_ns()}} +\\n")
            if {minimum_overlap}:
                ready = Path({log!r}).parent / "ready"
                (ready / item).touch()
                cutoff = time.monotonic() + 30
                while len(list(ready.iterdir())) < {minimum_overlap}:
                    if time.monotonic() >= cutoff:
                        raise RuntimeError("too few batch workers entered the adapter")
                    time.sleep(0.02)
            else:
                time.sleep({hold})
            if {expect_wait}:
                waiting = Path({log!r}).parent / "observed-waiter"
                machine_queue = queue.Queue.from_machine()
                cutoff = time.monotonic() + 30
                while not waiting.exists():
                    if queue.state(machine_queue).waiting:
                        waiting.touch()
                        break
                    if time.monotonic() >= cutoff:
                        raise RuntimeError("no batch worker registered as waiting")
                    time.sleep(0.02)
            with open({log!r}, "a") as log:
                log.write(f"{{time.monotonic_ns()}} -\\n")
            self.verdict = ("fail" if item.startswith("bad") else "error" if item.startswith("err")
                            else "unsupported" if item.startswith("unsup") else "pass")
            self.crash = item.startswith("boom")
            outcome = super().check(request)
            outcome.target["commit"] = request.trusted_commit
            return outcome

    check.load_adapters = lambda: [Slow()]
    sys.exit(cli.main(sys.argv[1:], _batch_worker=True))
''')


@pytest.fixture
def lean_repo(research_repo, monkeypatch):
    add_lean_items(research_repo, "t1", "t2", "t3", "t4", "t5", "t6")
    monkeypatch.chdir(research_repo)
    return research_repo


def fake_checks(monkeypatch, tmp_path, hold=0.0, *, minimum_overlap=0, expect_wait=False) -> Path:
    """Run each check of a batch as the child above; the log has one line per start (+) and end (-)."""
    log = tmp_path / "events.log"
    if minimum_overlap:
        (tmp_path / "ready").mkdir()
    script = CHILD.format(tests=str(TESTS), log=str(log), hold=hold,
                          minimum_overlap=minimum_overlap, expect_wait=expect_wait)
    real = batch.command
    monkeypatch.setattr(batch, "command", lambda item_id, args: [sys.executable, "-c", script, *real(item_id, args)[4:]])
    return log


def most_at_once(log: Path) -> int:
    running = peak = 0
    events = [line.split() for line in log.read_text().splitlines()]
    for _, kind in sorted(events, key=lambda event: int(event[0])):
        running += 1 if kind == "+" else -1
        peak = max(peak, running)
    return peak


def select(root, ids=None, stale=False):
    repo = Repo.open(root)
    return batch.select(Namespace(ids=ids, stale=stale), repo, repo.load_items()[0], check.load_adapters)


def vl(*argv) -> int:
    """`vl` in this process; an argparse refusal is exit 2 as well."""
    try:
        return cli.main(list(argv))
    except SystemExit as exc:
        return exc.code


# Selection --------------------------------------------------------------------------------------------------------

def test_all_takes_items_a_protected_check_can_ask(lean_repo):
    write_item(lean_repo, "plain")                                  # no checker table
    commit_all(lean_repo, "plain")
    source = (lean_repo / "research" / "items" / "t1.md").read_text()
    (lean_repo / "research" / "items" / "late.md").write_text(source.replace('id = "t1"', 'id = "late"'))
    assert select(lean_repo) == ["add-zero", "t1", "t2", "t3", "t4", "t5", "t6"]      # no plain; late is not committed


def test_stale_takes_only_the_items_a_check_could_improve(lean_repo):
    write_receipt(lean_repo, "t1")                                      # verified
    write_receipt(lean_repo, "t2", stale=True)                          # verified-stale
    write_receipt(lean_repo, "t3", "fail")                              # failed on the answer it still has
    write_receipt(lean_repo, "t4", "fail", stale=True)                  # failed on an answer since changed
    write_receipt(lean_repo, "t5", assurance="exploratory")             # explored
    commit_all(lean_repo, "receipts")                                   # add-zero and t6 have no receipt
    assert select(lean_repo, stale=True) == ["add-zero", "t2", "t4", "t5", "t6"]
    assert select(lean_repo) == ["add-zero", "t1", "t2", "t3", "t4", "t5", "t6"]      # control: --all takes them all


def test_a_pass_not_yet_committed_is_not_stale(lean_repo):
    write_receipt(lean_repo, "t1")
    assert "t1" not in select(lean_repo, stale=True)


def test_ids_file_keeps_its_order_and_ignores_comments_blanks_and_repeats(lean_repo, tmp_path):
    ids = tmp_path / "ids.txt"
    ids.write_text("t3\n\n# skip t1\nt2  # why\nt3\n")
    assert select(lean_repo, ids=str(ids)) == ["t3", "t2"]


def test_ids_file_with_an_unknown_item_or_no_file_runs_nothing(lean_repo, tmp_path, monkeypatch, capsys):
    ids = tmp_path / "ids.txt"
    ids.write_text("t1\nnope\n")
    ran = fake_checks(monkeypatch, tmp_path)
    assert vl("check", "--ids", str(ids)) == 2
    assert "no such item: nope" in capsys.readouterr().err and not ran.exists()
    assert vl("check", "--ids", str(tmp_path / "missing.txt")) == 2
    assert "cannot read" in capsys.readouterr().err


@pytest.mark.parametrize("argv", [[], ["t1", "--all"], ["--all", "--stale"], ["t1", "-j", "2"], ["--all", "--explore"],
                                  ["--all", "--reserve", "1G"], ["--all", "--keep-scratch"], ["--all", "-j", "0"]])
def test_a_batch_needs_exactly_one_selector_and_only_flags_that_apply(lean_repo, argv, capsys):
    assert vl("check", *argv) == 2
    assert capsys.readouterr().err


# The queue bounds what runs at once ------------------------------------------------------------------------------

def test_the_queue_not_dash_j_bounds_the_checks_running_at_once(lean_repo, monkeypatch, tmp_path, capsys):
    write_machine(queue={"slots": 2, "default_reservation": "16M"})
    log = fake_checks(monkeypatch, tmp_path, minimum_overlap=2, expect_wait=True)
    code = vl("check", "--all", "-j", "7", "--json")
    out = json.loads(capsys.readouterr().out)
    assert code == 0 and out["counts"]["pass"] == 7 and most_at_once(log) == 2
    assert (tmp_path / "observed-waiter").is_file()
    assert max(c["queue_wait_seconds"] for c in out["checks"]) > 0


def test_a_batch_under_vl_queue_run_is_refused_instead_of_running_every_check_in_one_admission(
        lean_repo, monkeypatch, tmp_path, capsys):
    """Inside `vl queue run` (VL_QUEUE_JOB names a live job), each child check would take that admission as its own
    and start at once, with no slot, reservation or cap of its own: the batch refuses."""
    from verifylab import queue
    write_machine(queue={"slots": 2})
    log = fake_checks(monkeypatch, tmp_path)
    with queue.admit("vl queue run -- vl check --all") as ticket:
        monkeypatch.setenv(queue.ENV, ticket.token)
        assert vl("check", "--all") == 2 and not log.exists()
        assert "outside `vl queue run`" in capsys.readouterr().err
        monkeypatch.delenv(queue.ENV)
    assert vl("check", "--all") == 0 and log.exists()                     # control: on its own, the batch runs


def test_with_slots_free_the_same_batch_runs_wider(lean_repo, monkeypatch, tmp_path):      # control
    write_machine(queue={"slots": 8, "default_reservation": "16M"})
    log = fake_checks(monkeypatch, tmp_path, minimum_overlap=3)
    assert vl("check", "--all", "-j", "7") == 0
    assert most_at_once(log) >= 3


def test_dash_j_bounds_it_too(lean_repo, monkeypatch, tmp_path):
    write_machine(queue={"slots": 8, "default_reservation": "16M"})
    log = fake_checks(monkeypatch, tmp_path, hold=0.3)
    assert vl("check", "--all", "-j", "1") == 0 and most_at_once(log) == 1


# Summary and exit code ---------------------------------------------------------------------------------------------

def test_the_summary_table_and_the_exit_code_follow_the_verdicts(lean_repo, monkeypatch, tmp_path, capsys):
    add_lean_items(lean_repo, "bad1", "err1", "unsup1", "boom1")
    fake_checks(monkeypatch, tmp_path)
    assert vl("check", "--all", "-j", "4") == 1                         # a fail beats an error
    captured = capsys.readouterr()
    rows = {line.split()[0]: line.split() for line in captured.out.splitlines()
            if line.startswith(("t", "bad", "err", "unsup", "boom"))}
    assert [rows[i][1] for i in ("t1", "bad1", "err1", "unsup1", "boom1")] == ["pass", "fail", "error", "unsupported",
                                                                             "error"]
    assert captured.out.splitlines()[0].split() == ["item", "verdict", "seconds", "wait"]
    assert "adapter crashed" in captured.out and "11 checked: 7 pass, 1 fail, 2 error, 1 unsupported" in captured.out
    assert "[11/11]" in captured.err
    ids = tmp_path / "ids.txt"
    ids.write_text("t1\nunsup1\n")
    assert vl("check", "--ids", str(ids)) == 3                          # unsupported alone: not a pass
    capsys.readouterr()
    ids.write_text("t1\nboom1\n")
    assert vl("check", "--ids", str(ids), "--json") == 3                # an error alone: a tool failure
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is False and [c["verdict"] for c in out["checks"]] == ["pass", "error"]
    ids.write_text("t1\nt2\n")
    assert vl("check", "--ids", str(ids)) == 0                          # control: all pass


def test_a_batch_leaves_receipts_that_status_counts_and_nothing_to_check_exits_zero(lean_repo, monkeypatch, tmp_path,
                                                                                    capsys):
    fake_checks(monkeypatch, tmp_path)
    assert vl("check", "--all", "-j", "7") == 0
    assert len(list((lean_repo / "research" / "evidence").glob("*/*.json"))) == 7
    commit_all(lean_repo, "admit")
    capsys.readouterr()
    assert vl("check", "--stale") == 0 and "nothing to check" in capsys.readouterr().out
    (lean_repo / "Fixture" / "Basic.lean").write_text("-- changed\n")          # an input of every receipt
    assert vl("check", "--stale", "-j", "7") == 0 and "7 checked: 7 pass" in capsys.readouterr().out


def test_the_batch_summary_and_the_catalog_agree_on_every_verdict(lean_repo, monkeypatch, tmp_path, capsys):
    """A fixture set with every verdict: passes, a fail, an error and an unsupported check (each with a receipt) and
    a crash (none): `vl check --all --json`, `vl admit`, then the catalog of the trusted commit. Per item the receipt,
    the verdict and the status the batch implies are the catalog's (a check that crashed wrote no receipt: nothing to
    admit, `unverified`), and the catalog's replay command names that receipt; the counts of the summary are those of
    the catalog."""
    add_lean_items(lean_repo, "bad1", "err1", "unsup1", "boom1")
    fake_checks(monkeypatch, tmp_path)
    assert vl("check", "--all", "-j", "4", "--json") == 1
    summary = json.loads(capsys.readouterr().out)
    assert vl("admit") == 0
    out = tmp_path / "catalog"
    assert vl("export", "catalog", str(out)) == 0
    data = json.loads((out / "catalog.json").read_text())
    catalog = {i["id"]: i for i in data["items"]}
    status = {"pass": "verified", "fail": "failed", "error": "check-error", "unsupported": "check-unsupported"}
    assert {c["verdict"] for c in summary["checks"]} == set(status)            # all four kinds are in the fixture
    expected = {}
    for result in summary["checks"]:
        item = catalog[result["item"]]
        label = "unverified" if result["receipt"] is None else status[result["verdict"]]     # the crash wrote none
        expected[label] = expected.get(label, 0) + 1
        assert item["status"]["label"] == label, result
        if result["receipt"] is None:
            assert item["proof"] is None and item["replay"] is None
            continue
        receipt = result["receipt"].rsplit("/", 1)[-1][:-5]
        assert (item["proof"]["receipt"], item["proof"]["verdict"]) == (receipt, result["verdict"]), result
        assert item["replay"]["command"] == f"vl replay {result['item']} --receipt {receipt}"
    assert summary["counts"] == {v: sum(c["verdict"] == v for c in summary["checks"]) for v in status}
    assert data["counts"]["status"] == expected


# One check's answer ------------------------------------------------------------------------------------------------

def one(monkeypatch, code: int, stdout: str = "", stderr: str = "") -> batch.Result:
    script = f"import sys; sys.stdout.write({stdout!r}); sys.stderr.write({stderr!r}); sys.exit({code})"
    monkeypatch.setattr(batch, "command", lambda item_id, args: [sys.executable, "-c", script])
    return batch.run_one("x", Namespace())


@pytest.mark.parametrize("code, stdout, verdict", [
    (0, '{"verdict": "pass", "queue_wait_seconds": 1.5, "receipt": "r"}', "pass"),
    (1, '{"verdict": "fail", "reasons": ["no"]}', "fail"),
    (3, '{"verdict": "unsupported"}', "unsupported"),
    (3, '{"verdict": "error"}', "error"),
    (3, '{"verdict": "fail", "receipt": null}', "fail")])      # stopped before reading the candidate: no receipt
def test_a_verdict_counts_when_it_agrees_with_the_exit_code(monkeypatch, code, stdout, verdict):
    assert one(monkeypatch, code, stdout).verdict == verdict


@pytest.mark.parametrize("code, stdout", [(1, '{"verdict": "pass"}'), (0, '{"verdict": "fail"}'), (0, ""),
                                          (3, '{"verdict": "fail", "receipt": "r.json"}'),
                                          (0, "not json"), (3, '{"verdict": "pass"}'), (0, "[]")])
def test_a_verdict_that_disagrees_with_the_exit_code_or_is_missing_is_an_error(monkeypatch, code, stdout):
    assert one(monkeypatch, code, stdout).verdict == "error"


def test_a_check_that_could_not_run_is_an_error_with_its_last_stderr_line(monkeypatch):
    result = one(monkeypatch, 2, stderr="warning\nvl: queue: waited 5s, its maximum\n")
    assert (result.verdict, result.reasons) == ("error", ("vl: queue: waited 5s, its maximum",))


# Interruption ------------------------------------------------------------------------------------------------------

@pytest.fixture
def waiting_batch(lean_repo, tmp_path):
    """The first worker signals readiness after logging its start, then blocks until released or cancelled."""
    import os
    import select
    import signal
    import subprocess

    log = tmp_path / "events.log"
    ready = tmp_path / "ready"
    release = tmp_path / "release"
    os.mkfifo(ready)
    os.mkfifo(release)
    ids = tmp_path / "ids.txt"
    ids.write_text("add-zero\nt1\n")
    child = textwrap.dedent(f'''
        import json
        import os
        import sys

        item = sys.argv[1]
        with open({str(log)!r}, "a") as fh:
            print(item, file=fh)
        if item == "add-zero":
            with open({str(ready)!r}, "wb", buffering=0) as fh:
                fh.write(str(os.getpid()).encode() + b"\\n")
            with open({str(release)!r}, "rb", buffering=0) as fh:
                fh.read(1)
        print(json.dumps({{"verdict": "pass"}}))
    ''')
    parent = textwrap.dedent(f'''
        import signal
        import sys
        signal.signal(signal.SIGINT, signal.default_int_handler)   # a background job inherits SIGINT ignored
        from verifylab import cli
        from verifylab.commands import batch
        batch.command = lambda item, args: [sys.executable, "-c", {child!r}, item]
        sys.exit(cli.main(sys.argv[1:], _batch_worker=True))
    ''')
    with (os.fdopen(os.open(ready, os.O_RDWR), "rb", buffering=0) as started,
          os.fdopen(os.open(release, os.O_RDWR), "wb", buffering=0) as gate):
        proc = subprocess.Popen([sys.executable, "-c", parent, "check", "--ids", str(ids), "-j", "1"],
                                cwd=lean_repo, stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
        try:
            assert select.select([started], [], [], 30)[0], "the first check never signalled readiness"
            pid = int(started.readline())
            yield proc, pid, log, gate
        finally:
            if proc.poll() is None:
                os.killpg(proc.pid, signal.SIGKILL)
                proc.communicate(timeout=5)


@pytest.mark.parametrize("interrupt", [True, False])
def test_an_interrupted_batch_starts_no_further_checks(waiting_batch, interrupt):
    import signal

    proc, _, log, gate = waiting_batch
    if interrupt:
        proc.send_signal(signal.SIGINT)
    else:
        gate.write(b"x")
    _, err = proc.communicate(timeout=15)
    if interrupt:
        assert proc.returncode != 0
        assert log.read_text().splitlines() == ["add-zero"]
    else:
        assert proc.returncode == 0, err.decode()
        assert log.read_text().splitlines() == ["add-zero", "t1"]


def test_ids_names_an_item_not_on_the_trusted_ref_and_it_is_unsupported(lean_repo, monkeypatch, tmp_path, capsys):
    source = (lean_repo / "research" / "items" / "t1.md").read_text()
    (lean_repo / "research" / "items" / "late.md").write_text(source.replace('id = "t1"', 'id = "late"'))
    ids = tmp_path / "ids.txt"
    ids.write_text("late\nt1\n")
    fake_checks(monkeypatch, tmp_path)
    assert vl("check", "--ids", str(ids), "--json") == 3
    out = json.loads(capsys.readouterr().out)
    assert [(c["item"], c["verdict"]) for c in out["checks"]] == [("late", "unsupported"), ("t1", "pass")]
    assert "not on the trusted ref" in out["checks"][0]["reasons"][0] and out["checks"][0]["queue_wait_seconds"] is None


def test_a_check_process_does_not_import_vl_from_the_worktree(tmp_path):
    """`python -m` puts the working directory first on sys.path: a candidate's top-level `verifylab/` in the
    integrator's checkout would run, outside the jail, as the check. The control is the same process without the
    planted package, which finds the real `vl`."""
    import subprocess
    planted = tmp_path / "planted"
    (planted / "verifylab").mkdir(parents=True)
    (planted / "verifylab" / "__init__.py").write_text(f"open({str(tmp_path / 'ran')!r}, 'w').write('x')\n")
    clean = tmp_path / "clean"
    clean.mkdir()
    argv = [*batch.command("t1", Namespace(timeout=1.0, max_wait=None, trusted_ref=None))[:4], "--help"]
    assert subprocess.run(argv, cwd=clean, capture_output=True).returncode == 0
    subprocess.run(argv, cwd=planted, capture_output=True)
    assert not (tmp_path / "ran").exists()


@pytest.mark.parametrize("sig", ["SIGINT", "SIGTERM"])
def test_batch_cancel_stops_running_worker_promptly(waiting_batch, sig):
    """The batch reaps a cancelled worker that cannot complete on its own."""
    import os
    import signal

    proc, worker_pid, _, _ = waiting_batch
    proc.send_signal(getattr(signal, sig))
    proc.communicate(timeout=15)
    assert proc.returncode != 0
    with pytest.raises(ProcessLookupError):
        os.kill(worker_pid, 0)


@pytest.mark.jail
def test_batch_sigkill_stops_worker_and_cleans_owned_service_only(lean_repo, tmp_path):
    """Planted coordinator SIGKILL: even when the batch process cannot run cleanup, its worker detects coordinator
    death via VL_COORDINATOR_FD, unwinds jail.stream(), and stops only its own systemd service unit."""
    import os
    import secrets
    import signal
    import subprocess
    import time
    from verifylab import jail
    if not jail.available():
        pytest.skip("bwrap missing")
    from conftest import system_python
    systemctl = jail.program("systemctl")
    systemd_run = jail.program("systemd-run")
    unrelated = f"run-vl-unrelated-{secrets.token_hex(6)}.service"
    other = subprocess.Popen([systemd_run, "--user", "--wait", "--collect", f"--unit={unrelated}", "--",
                              system_python(), "-c", "import time; time.sleep(30)"],
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    unit_file = tmp_path / "owned.unit"
    pid_file = tmp_path / "worker.pid"
    child = textwrap.dedent(f'''
        import os, sys, time
        from pathlib import Path
        sys.path.insert(0, {str(TESTS)!r})
        from test_check_command import FakeAdapter
        from verifylab import cli, jail
        from verifylab.commands import check

        real_service = jail.guarded_service
        def cap(argv, unit, *rest):
            Path({str(unit_file)!r}).write_text(unit)
            return real_service(argv, unit, *rest)
        jail.guarded_service = cap

        class Jailed(FakeAdapter):
            def check(self, request):
                Path({str(pid_file)!r}).write_text(str(os.getpid()))
                jail.stream(jail.Jail(workdir=Path({str(tmp_path)!r}), deny_unix=True),
                            [{system_python()!r}, "-c", "import time; print('up', flush=True); time.sleep(30)"],
                            timeout=60)
                return super().check(request)

        check.load_adapters = lambda: [Jailed()]
        sys.exit(cli.main(sys.argv[1:], _batch_worker=True))
    ''')
    parent = textwrap.dedent(f'''
        import sys
        from verifylab import cli
        from verifylab.commands import batch
        real = batch.command
        batch.command = lambda item_id, args: [sys.executable, "-c", {child!r}, *real(item_id, args)[4:]]
        sys.exit(cli.main(sys.argv[1:], _batch_worker=True))
    ''')
    proc = subprocess.Popen([sys.executable, "-c", parent, "check", "--all", "-j", "1"], cwd=lean_repo,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    owned = None
    try:
        deadline = time.monotonic() + 30
        while not (unit_file.exists() and pid_file.exists()) and time.monotonic() < deadline:
            time.sleep(0.05)
        assert unit_file.exists() and pid_file.exists()
        owned = unit_file.read_text().strip()
        worker_pid = int(pid_file.read_text().strip())
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if subprocess.run([systemctl, "--user", "is-active", owned], capture_output=True).returncode == 0:
                break
            time.sleep(0.05)
        os.kill(proc.pid, signal.SIGKILL)
        proc.wait(timeout=10)
        end = time.monotonic() + 10
        while time.monotonic() < end:
            if subprocess.run([systemctl, "--user", "is-active", owned], capture_output=True).returncode != 0:
                try:
                    os.kill(worker_pid, 0)
                except ProcessLookupError:
                    break
            time.sleep(0.1)
        assert subprocess.run([systemctl, "--user", "is-active", owned], capture_output=True).returncode != 0
        with pytest.raises(ProcessLookupError):
            os.kill(worker_pid, 0)
        assert subprocess.run([systemctl, "--user", "is-active", unrelated], capture_output=True).returncode == 0
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)
        proc.stdout.close()
        proc.stderr.close()
        if owned:
            subprocess.run([systemctl, "--user", "stop", owned], capture_output=True)
        subprocess.run([systemctl, "--user", "stop", unrelated], capture_output=True)
        other.wait(timeout=5)
        other.stdout.close()
        other.stderr.close()


@pytest.mark.parametrize("move", [False, True])
def test_batch_keeps_its_question_when_the_ref_moves(lean_repo, monkeypatch, tmp_path, capsys, move):
    from conftest import git

    start = git(lean_repo, "rev-parse", "HEAD").strip()
    (lean_repo / "new-trusted-content").touch()
    commit_all(lean_repo, "later trusted snapshot")
    later = git(lean_repo, "rev-parse", "HEAD").strip()
    git(lean_repo, "reset", "--hard", start)
    ids = tmp_path / "shard.txt"
    ids.write_text(f"# trusted commit {start}\nt1\nt2\n")
    fake_checks(monkeypatch, tmp_path)
    command = batch.command

    def start_check(item, args):
        if move and item == "t2":
            git(lean_repo, "update-ref", "refs/heads/trusted", later, start)
        return command(item, args)

    monkeypatch.setattr(batch, "command", start_check)
    assert vl("check", "--ids", str(ids), "--trusted-ref", "trusted", "-j", "1", "--json") == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["trusted_commit"] == start
    for result in summary["checks"]:
        inputs = json.loads((lean_repo / result["receipt"]).read_text())["inputs"]
        assert inputs["trusted_commit"] == start
        assert inputs["trusted_ref"] == "trusted" and inputs["trusted_ref_source"] == "--trusted-ref"
