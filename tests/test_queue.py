"""The machine's job queue: a job starts only within the slots, memory_total and the free floor; a place is a file
held with flock, so a holder that dies gives it back. Each limit has a planted case next to a clean control."""

from __future__ import annotations

import json
import re
import multiprocessing
import os
import signal
import sys
import time
from pathlib import Path

import pytest

from verifylab import cli, jail, machine, queue

from conftest import write_machine

G = 1 << 30
FORK = multiprocessing.get_context("fork")


def make_queue(tmp_path: Path, slots=12, keep_free=0, available=64 * G, memory_total=None, in_use=0,
               max_wait=None) -> queue.Queue:
    """A queue whose MemAvailable and vl.slice usage are planted files."""
    meminfo, cgroup = tmp_path / "meminfo", tmp_path / "vl.slice"
    meminfo.write_text(f"MemTotal: {64 * G // 1024} kB\nMemAvailable: {available // 1024} kB\n")
    cgroup.mkdir(exist_ok=True)
    (cgroup / "memory.stat").write_text(f"anon {in_use}\nfile {8 * G}\n")
    return queue.Queue(slots, keep_free, 2 * G, memory_total, max_wait, tmp_path / "state", meminfo, cgroup, 0.01)


def _job(q, running, peak, lock, hold):
    with queue.admit("job", queue=q):
        with lock:
            running.value += 1
            peak.value = max(peak.value, running.value)
        time.sleep(hold)
        with lock:
            running.value -= 1


def most_at_once(q: queue.Queue, jobs: int = 8, hold: float = 0.5) -> int:
    """How many of `jobs` concurrent submitters ran at once at most."""
    running, peak, lock = FORK.Value("i", 0), FORK.Value("i", 0), FORK.Lock()
    procs = [FORK.Process(target=_job, args=(q, running, peak, lock, hold)) for _ in range(jobs)]
    for proc in procs:
        proc.start()
    for proc in procs:
        proc.join(60)
    assert [proc.exitcode for proc in procs] == [0] * jobs
    return peak.value


def test_concurrent_submitters_never_exceed_the_slots(tmp_path: Path):
    assert most_at_once(make_queue(tmp_path, slots=3)) == 3
    assert most_at_once(make_queue(tmp_path, slots=12)) > 3          # control: the measure sees overlap


def test_concurrent_submitters_keep_the_free_floor(tmp_path: Path):
    # 10G available, 2G each: three leave 4G, a fourth would leave 2G, under the 3G floor
    assert most_at_once(make_queue(tmp_path, keep_free=3 * G, available=10 * G)) == 3
    assert most_at_once(make_queue(tmp_path, keep_free=0, available=10 * G)) == 5     # control: no floor


def test_why_a_job_waits(tmp_path: Path):
    q = make_queue(tmp_path, slots=2, keep_free=3 * G, memory_total=8 * G)
    job = queue.Job(1, "running", 3 * G, "now")
    idle = queue.State((), 0, 10 * G, 0)
    assert queue.blocked(q, idle, 2 * G) is None
    assert queue.blocked(q, queue.State((job, job), 0, 30 * G, 6 * G), 1) == "all 2 slots are busy"
    assert "memory_total" in queue.blocked(q, queue.State((job,), 0, 30 * G, 3 * G), 6 * G)
    assert queue.blocked(q, queue.State((job,), 0, 30 * G, 3 * G), 5 * G) is None
    # MemAvailable 7G: a running job that has not taken its 3G yet still counts against the floor; once its slice
    # holds that memory, MemAvailable already shows it
    assert "less than 3.0G free" in queue.blocked(q, queue.State((job,), 0, 7 * G, 0), 2 * G)
    using = queue.Job(1, "running", 3 * G, "now", 3 * G)
    assert queue.blocked(q, queue.State((using,), 0, 7 * G, 3 * G), 2 * G) is None
    assert queue.blocked(q, queue.State((), 0, None, 0), 2 * G) is None       # no meminfo: no floor to keep


def test_one_jobs_overshoot_does_not_hide_anothers_growth_to_come(tmp_path: Path):
    q = make_queue(tmp_path, keep_free=3 * G)
    over = queue.Job(1, "over", G, "now", 4 * G)                  # reserved 1G, uses 4G
    fresh = queue.Job(2, "fresh", 3 * G, "now", 0)               # reserved 3G, uses none of it yet
    assert "less than 3.0G free" in queue.blocked(q, queue.State((over, fresh), 0, 7 * G, 4 * G), 2 * G)
    grown = queue.Job(2, "fresh", 3 * G, "now", 3 * G)           # control: it took its 3G, MemAvailable shows it
    assert queue.blocked(q, queue.State((over, grown), 0, 7 * G, 7 * G), 2 * G) is None


def test_each_job_is_charged_the_anonymous_memory_of_its_own_slice(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(jail, "cap_problem", lambda memory_max: None)
    monkeypatch.setattr(jail, "program", lambda name: f"/usr/bin/{name}")
    q = make_queue(tmp_path, in_use=5 * G)
    with queue.admit("a", 3 * G, queue=q) as ticket, queue.admit("b", 2 * G, queue=q):
        own = q.cgroup / jail.job_slice(ticket.token)
        own.mkdir()
        (own / "memory.stat").write_text(f"anon {G}\nfile {4 * G}\n")
        now = queue.state(q)
        assert {j.label: j.in_use for j in now.running} == {"a": G, "b": 0}     # b's slice is unreadable: 0
        assert now.pending == 2 * G + 2 * G
        with queue.inside(ticket):                                 # the runs of this process go in that slice
            assert f"--slice={own.name}" in jail.memory_capped(["true"], "4G")
        assert "--slice=vl.slice" in jail.memory_capped(["true"], "4G")          # control: outside the job


def test_runs_left_by_a_job_whose_holder_died_still_take_a_slot(tmp_path: Path):
    """A killed `vl check` gives up its place at once, but its Comparator service, which is no child of it, runs on
    in the job's slice: while that slice is populated it counts as running, with what it uses as its reservation."""
    q = make_queue(tmp_path, slots=1)
    left = q.cgroup / jail.job_slice("4242-0123456789ab")
    left.mkdir()
    (left / "memory.stat").write_text(f"anon {3 * G}\n")
    (left / "cgroup.events").write_text("populated 1\nfrozen 0\n")
    now = queue.state(q)
    assert [(j.pid, j.reservation, j.in_use) for j in now.running] == [(0, 3 * G, 3 * G)]
    assert queue.blocked(q, now, G) == "all 1 slots are busy"
    (left / "cgroup.events").write_text("populated 0\nfrozen 0\n")      # control: once its runs end, it is gone
    assert queue.state(q).running == ()
    (left / "cgroup.events").write_text("populated 1\nfrozen 0\n")
    with queue.admit("live", G, max_wait=0.1, queue=make_queue(tmp_path, slots=2)) as ticket:
        own = q.cgroup / jail.job_slice(ticket.token)                    # a live job's slice is not counted twice
        own.mkdir()
        (own / "cgroup.events").write_text("populated 1\n")
        assert sorted(j.pid for j in queue.state(q).running) == [0, os.getpid()]


def test_an_admission_removes_the_stopped_slices_systemd_left_behind(tmp_path: Path, monkeypatch):
    """Only the sweep under the admission lock removes, and only empty slices of no live job."""
    q = make_queue(tmp_path)
    removed = []
    monkeypatch.setattr(jail, "remove_stopped_slices", lambda cgroups: removed.extend(c.name for c in cgroups))
    for token, events in (("1111-0123456789ab", "populated 0\n"), ("2222-0123456789ab", "populated 1\n")):
        (left := q.cgroup / jail.job_slice(token)).mkdir()
        (left / "cgroup.events").write_text(events)
    queue.state(q)
    assert removed == []                                                  # control: a read without the sweep
    with queue.admit("live", G, queue=q) as ticket:
        (own := q.cgroup / jail.job_slice(ticket.token)).mkdir()
        (own / "cgroup.events").write_text("populated 0\n")
        queue.state(q, sweep=True)
    assert set(removed) == {jail.job_slice("1111-0123456789ab")}         # the admission's sweep and this one


def test_only_the_folders_of_inactive_units_are_removed(tmp_path: Path, monkeypatch):
    import subprocess
    stopped, active, busy = (tmp_path / jail.job_slice(t) for t in ("1-aa", "2-bb", "3-cc"))
    for folder in (stopped, active, busy):
        folder.mkdir()
    (busy / "cgroup.procs").write_text("42\n")                            # rmdir refuses what is not empty
    monkeypatch.setattr(jail, "program", lambda name: "/usr/bin/systemctl")
    listed = subprocess.CompletedProcess([], 0, f"{active.name} loaded active active Slice\n", "")
    monkeypatch.setattr(jail.subprocess, "run", lambda *a, **k: listed)
    jail.remove_stopped_slices([stopped, active, busy])
    assert (stopped.exists(), active.exists(), busy.exists()) == (False, True, True)
    stopped.mkdir()
    monkeypatch.setattr(jail.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess([], 1, "", "no bus"))
    jail.remove_stopped_slices([stopped])
    assert stopped.exists()                                               # control: units unknown, nothing goes


def test_the_state_reads_meminfo_and_the_slice(tmp_path: Path):
    q = make_queue(tmp_path, available=10 * G, in_use=G)
    with queue.admit("a", 3 * G, queue=q):
        now = queue.state(q)
        assert (now.available, now.in_use, now.reserved, [j.label for j in now.running]) == (10 * G, G, 3 * G, ["a"])
    assert queue.state(q).running == ()


def test_a_reservation_is_at_most_memory_total(tmp_path: Path):
    q = make_queue(tmp_path, memory_total=4 * G)
    with queue.admit("big", 100 * G, queue=q):
        assert [j.reservation for j in queue.state(q).running] == [4 * G]


def _hold_forever(q, ready):
    with queue.admit("holder", queue=q):
        ready.set()
        time.sleep(60)


def test_a_killed_holder_releases_its_place(tmp_path: Path):
    q = make_queue(tmp_path, slots=1)
    ready = FORK.Event()
    holder = FORK.Process(target=_hold_forever, args=(q, ready))
    holder.start()
    assert ready.wait(30)
    assert [j.pid for j in queue.state(q).running] == [holder.pid]
    with pytest.raises(queue.QueueError, match="all 1 slots are busy"):    # control: alive, it holds the slot
        queue.admit("next", max_wait=0.2, queue=q)
    os.kill(holder.pid, signal.SIGKILL)
    holder.join(10)
    started = time.monotonic()
    with queue.admit("next", max_wait=5, queue=q):
        assert time.monotonic() - started < 2
        assert [j.label for j in queue.state(q).running] == ["next"]
    assert list((q.root / "jobs").glob("*.json")) == []          # the dead holder's file was swept


def test_the_wait_ends_at_max_wait_with_one_status_line(tmp_path: Path, capsys):
    q = make_queue(tmp_path, slots=1)
    with queue.admit("first", queue=q) as first:
        assert first.waited is not None and first.waited < 1
        with pytest.raises(queue.QueueError, match=r"second waited 0s, its maximum, and did not start: all 1"):
            queue.admit("second", max_wait=0.3, queue=q)
    err = capsys.readouterr().err.splitlines()
    assert err == [f"vl: queue: second waits: all 1 slots are busy (1 of 1 slots busy, 2.0G reserved of unbounded, "
                   f"MemAvailable 64.0G, floor 0.0G)"]
    assert queue.state(q).waiting == 0 and list((q.root / "waiting").glob("*")) == []
    with queue.admit("third", max_wait=0.3, queue=q) as third:       # released: the slot is free again
        assert third.waited < 0.3


def test_a_command_inside_an_admission_does_not_queue_again(tmp_path: Path, monkeypatch):
    q = make_queue(tmp_path, slots=1)
    with queue.admit("outer", queue=q) as outer:
        monkeypatch.setenv(queue.ENV, outer.token)
        inner = queue.admit("inner", max_wait=0.2, queue=q)
        assert (inner.path, inner.waited) == (None, None)
        for planted in ("4242-000000000000", "../outer", ""):      # a job that is not live does not count
            monkeypatch.setenv(queue.ENV, planted)
            with pytest.raises(queue.QueueError):
                queue.admit("inner", max_wait=0.1, queue=q)


def test_machine_queue_settings_and_their_defaults(monkeypatch):
    write_machine(check={"memory_total": "20G"},
                  queue={"slots": 4, "keep_free": "1G", "default_reservation": "512M", "max_wait": 30})
    q = queue.Queue.from_machine()
    assert (q.slots, q.keep_free, q.default_reservation, q.memory_total, q.max_wait) == (4, G, G // 2, 20 * G, 30)
    assert q.root == Path(os.environ["XDG_STATE_HOME"]) / "verifylab" / "queue"
    machine.config_path().write_text("")
    machine.forget()
    q = queue.Queue.from_machine()
    threads = min(4, os.cpu_count())
    assert (q.threads, q.slots, q.keep_free, q.default_reservation, q.max_wait) == (
        threads, max(1, os.cpu_count() // threads), 3 * G, 2 * G, None)
    write_machine(check={"memory_total": "20GB"})
    with pytest.raises(machine.MachineError, match=r"\[check\] memory_total: not a size"):
        queue.Queue.from_machine()
    for bad in ("slots = 0", "slots = 1.5", "slots = true", "threads = 0", "threads = 2.5", 'keep_free = "lots"', "keep_free = 3",
                'default_reservation = "infinity"', "max_wait = -1", "wait = 3"):
        machine.config_path().write_text(f"[queue]\n{bad}\n")
        machine.forget()
        with pytest.raises(machine.MachineError, match=r"\[queue\]"):
            machine.load()


def test_the_default_slots_never_ask_for_more_cpus_than_there_are(monkeypatch):
    """The starvation of a 16-CPU node running one check per CPU, each with 16 Lean workers: the default slots now
    divide the CPUs by the threads one check may use, and an explicit slots still wins."""
    monkeypatch.setattr(os, "cpu_count", lambda: 16)
    write_machine()
    q = queue.Queue.from_machine()
    assert (q.threads, q.slots) == (4, 4)                    # 4 checks of 4 CPUs on 16, not 16 checks of 16
    write_machine(queue={"threads": 8})
    assert (queue.Queue.from_machine().threads, queue.Queue.from_machine().slots) == (8, 2)
    write_machine(queue={"threads": 32})
    assert queue.Queue.from_machine().slots == 1             # one check at least, however many threads it may use
    write_machine(queue={"slots": 6})
    assert queue.Queue.from_machine().slots == 6             # control: an explicit slots wins
    monkeypatch.setattr(os, "cpu_count", lambda: 2)
    write_machine()
    assert (queue.Queue.from_machine().threads, queue.Queue.from_machine().slots) == (2, 1)


def test_sizes_are_read_as_systemd_writes_them(tmp_path: Path):
    meminfo = tmp_path / "meminfo"
    meminfo.write_text("MemTotal: 1000 kB\n")
    assert [machine.size_bytes(s) for s in ("2G", "512m", "1048576", "1.5K", "0")] == [2 * G, 512 << 20, 1 << 20,
                                                                                       1536, 0]
    assert machine.size_bytes("50%", meminfo) == 512000 and machine.size_bytes("infinity") is None
    for bad in ("2X", "", "-1G", "G"):
        with pytest.raises(ValueError):
            machine.size_bytes(bad)


def test_status_shows_the_running_jobs(capsys):
    write_machine(check={"memory_total": "20G"}, queue={"slots": 2, "keep_free": "1G"})
    with queue.admit("vl check demo", 3 * G):
        assert cli.main(["queue", "status", "--json"]) == 0
        data = json.loads(capsys.readouterr().out)
        assert data["schema"] == "vl.output/1"
        assert (data["slots"], data["keep_free"], data["memory_total"], data["reserved"], data["waiting"]) == (
            2, G, 20 * G, 3 * G, 0)
        assert [(j["label"], j["pid"], j["reservation"]) for j in data["running"]] == [("vl check demo", os.getpid(),
                                                                                        3 * G)]
        assert isinstance(data["mem_available"], int)
        assert cli.main(["queue", "status"]) == 0
        text = capsys.readouterr().out
        assert "1 of 2 slots busy, 3.0G reserved of 20.0G" in text and "vl check demo" in text
    assert cli.main(["queue", "status", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["running"] == []


def test_queue_run_passes_the_command_through_and_names_its_job(monkeypatch, capfd, tmp_path: Path):
    monkeypatch.setattr(jail, "cap_problem", lambda memory_max: "no systemd here (test)")
    code = "import os, sys; print(os.environ['VL_QUEUE_JOB']); sys.exit(5)"
    assert cli.main(["queue", "run", "--mem", "1G", "--", sys.executable, "-c", code]) == 5
    out, err = capfd.readouterr()
    assert out.strip().split("-")[0] == str(os.getpid()) and "without a memory cap (no systemd here" in err
    assert cli.main(["queue", "run", "--json", "--", "true"]) == 2


def test_queue_run_gives_up_at_max_wait_without_running(monkeypatch, capfd, tmp_path: Path):
    monkeypatch.setattr(jail, "cap_problem", lambda memory_max: "no systemd here (test)")
    write_machine(queue={"slots": 1})
    marker = tmp_path / "ran"
    with queue.admit("busy"):
        assert cli.main(["queue", "run", "--max-wait", "0.2", "--", "touch", str(marker)]) == 2
    assert not marker.exists() and "did not start: all 1 slots are busy" in capfd.readouterr().err
    assert cli.main(["queue", "run", "--max-wait", "5", "--", "touch", str(marker)]) == 0 and marker.exists()


def test_a_capped_queue_run_joins_vl_slice_at_its_reservation(monkeypatch):
    seen = []
    monkeypatch.setattr(jail, "cap_problem", lambda memory_max: None)
    monkeypatch.setattr(jail, "program", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(jail, "cap_slice", lambda total: None)
    monkeypatch.setattr(jail, "remove_stopped_slices", lambda cgroups: None)    # the host's slices are not this test's
    monkeypatch.setattr("verifylab.queue.subprocess.run",
                        lambda argv, env=None, **kw: seen.append(argv) or type("Done", (), {"returncode": 0})())
    assert cli.main(["queue", "run", "--mem", "3G", "--", "python", "train.py"]) == 0
    argv = seen[0]
    assert argv[:5] == ["/usr/bin/systemd-run", "--user", "--scope", "--quiet", "--collect"]
    assert re.fullmatch(r"--slice=vl-[0-9]+_[0-9a-f]+\.slice", argv[5])     # the job's own, in vl.slice
    assert f"MemoryMax={3 * G}" in argv and argv[-3:] == ["--", "python", "train.py"]


@pytest.mark.lean
def test_protected_checks_run_at_once_through_the_queue_keep_their_verdicts(tmp_path: Path, pinned_tools):
    """Two protected checks of one repository and a third of another, at once: the same verdicts as the planted
    suite gets running them one at a time (`expect` of each case), distinct valid receipts, overlapping runs."""
    import subprocess
    import lean_helpers
    from conftest import lean_elan_home
    from verifylab.records import receipt_problems
    if (why := lean_helpers.lean_unavailable()):
        pytest.skip(why)
    # A token reservation: admission still counts MemAvailable, so the default 2G each would make the three wait
    # whenever the host is busy with other work, and the last assertion would measure the host, not the queue.
    write_machine(pinned_tools, elan_home=lean_elan_home(), queue={"slots": 3, "default_reservation": "1M"})
    cases = [lean_helpers.load_case(name) for name in ("control-original", "d4-sorry")]
    good, bad = (lean_helpers.make_repo(tmp_path / case.name, case) for case in cases)
    vl = [sys.executable, "-m", "verifylab.cli", "check", "double-even", "--json"]
    procs = [subprocess.Popen(vl, cwd=root, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
             for root in (good, good, bad)]
    done = [(proc.wait(1800), proc.communicate()) for proc in procs]
    assert [code for code, _ in done] == [0, 0, 1], [err for _, (_, err) in done]
    assert [c.expect for c in cases] == ["pass", "fail"]
    receipts = [json.loads((root / json.loads(out)["receipt"]).read_text())
                for root, (_, (out, _)) in zip((good, good, bad), done)]
    assert [r["verdict"] for r in receipts] == ["pass", "pass", "fail"]
    assert all(receipt_problems(r) == [] for r in receipts) and len({r["receipt_id"] for r in receipts}) == 3
    assert max(r["started_at"] for r in receipts) < min(r["finished_at"] for r in receipts)    # they overlapped
    assert all(r["extra"]["queue_wait_seconds"] < 5 for r in receipts)


def test_a_lane_exec_waits_in_the_queue_and_gives_up_at_max_wait(research_repo, monkeypatch, capsys, tmp_path: Path):
    import subprocess
    from conftest import git
    (research_repo / ".lake" / "packages").mkdir(parents=True)
    (research_repo / ".gitignore").write_text(".lake/\n")
    git(research_repo, "add", ".gitignore")
    git(research_repo, "commit", "-q", "-m", "ignore lake")
    monkeypatch.chdir(research_repo)
    assert cli.main(["lane", "new", "la"]) == 0
    marker = tmp_path / "ran"
    monkeypatch.setattr(jail, "memory_capped", lambda argv, *caps: ["touch", str(marker)])   # no bwrap needed
    write_machine(queue={"slots": 1, "max_wait": 0.3})
    try:
        with queue.admit("busy"):
            assert cli.main(["lane", "exec", "la", "--", "true"]) == 2
        assert not marker.exists() and "vl lane exec la waited" in capsys.readouterr().err
        assert cli.main(["lane", "exec", "la", "--", "true"]) == 0 and marker.exists()      # control: a free slot
    finally:
        subprocess.run(["chmod", "-R", "u+rwx", str(research_repo.parent / ".vl-lanes")], check=False)


def test_a_default_reservation_of_nothing_is_refused():
    for bad in ('default_reservation = "0"', "max_wait = nan", "max_wait = inf"):
        machine.config_path().write_text(f"[queue]\n{bad}\n")
        machine.forget()
        with pytest.raises(machine.MachineError, match=r"\[queue\]"):
            machine.load()
    machine.config_path().write_text('[queue]\ndefault_reservation = "1"\nmax_wait = 0.5\n')     # control
    machine.forget()
    assert machine.load().queue == {"default_reservation": "1", "max_wait": 0.5}


def test_an_unusable_state_folder_is_a_queue_error_naming_the_folder_and_xdg_state_home(tmp_path: Path, capfd,
                                                                                      monkeypatch):
    blocker = tmp_path / "a-file"
    blocker.write_text("")
    q = make_queue(tmp_path)
    for root in (blocker / "state", blocker):                      # below a file; a file itself
        planted = queue.Queue(**{**q.__dict__, "root": root})
        with pytest.raises(queue.QueueError, match=rf"cannot use .*{root.name}.*XDG_STATE_HOME"):
            queue.admit("job", queue=planted)
    readonly = tmp_path / "readonly"                               # exists, but admission.lock cannot be made
    readonly.mkdir(mode=0o500)
    try:
        if os.access(readonly, os.W_OK):
            pytest.skip("this account writes into read-only folders")
        with pytest.raises(queue.QueueError, match=r"cannot use .*readonly.*XDG_STATE_HOME"):
            queue.admit("job", queue=queue.Queue(**{**q.__dict__, "root": readonly}))
    finally:
        readonly.chmod(0o700)
    with queue.admit("job", queue=q):                              # control: a writable folder
        assert len(queue.state(q).running) == 1
    monkeypatch.setenv("XDG_STATE_HOME", str(blocker))
    assert cli.main(["queue", "run", "--", "true"]) == 2
    assert "cannot use" in capfd.readouterr().err


def test_a_killed_queue_run_keeps_its_place_while_its_command_runs(monkeypatch, tmp_path: Path):
    from conftest import assert_place_outlives_wrapper, kill_wrapper_midway
    monkeypatch.setattr(jail, "cap_problem", lambda memory_max: "no systemd here (test)")
    write_machine(queue={"slots": 1})
    pidfile = tmp_path / "pid"
    pid = kill_wrapper_midway(["queue", "run", "--", "sh", "-c", f"echo $$ > {pidfile}; exec sleep 60"], pidfile)
    assert_place_outlives_wrapper(pid)


def test_a_reservation_no_idle_machine_could_admit_is_refused_at_once(tmp_path: Path):
    """A job that, with keep_free, asks for more than the machine's RAM would wait forever, and as the head of the
    line hold up every job behind it: it is refused at once, whatever max_wait says."""
    q = make_queue(tmp_path, keep_free=3 * G, available=32 * G)     # MemTotal 64G
    start = time.monotonic()
    with pytest.raises(queue.QueueError, match=r"could never start.*keep_free"):
        queue.admit("huge", 62 * G, max_wait=3, queue=q)            # without max_wait it would wait forever
    assert time.monotonic() - start < 2 and queue.state(q).waiting == 0
    with pytest.raises(queue.QueueError, match=r"waited"):          # control: 61G + 3G fit an idle machine; it waits
        queue.admit("big", 61 * G, max_wait=0.2, queue=q)


def test_a_big_job_is_not_starved_by_younger_small_ones(tmp_path: Path):
    import threading
    q = make_queue(tmp_path, memory_total=8 * G)
    admitted = []

    def big():
        with queue.admit("big", 4 * G, max_wait=30, queue=q):
            admitted.append("big")

    with queue.admit("runner", 6 * G, queue=q) as runner:
        with queue.admit("alone", G, max_wait=0.3, queue=q):          # control: no one waits ahead, 1G fits
            pass
        waiting = threading.Thread(target=big)
        waiting.start()
        deadline = time.monotonic() + 10
        while queue.state(q).waiting == 0 and time.monotonic() < deadline:
            time.sleep(0.01)                                            # big waits: 6G + 4G exceed memory_total
        with pytest.raises(queue.QueueError, match=r"held for big"):    # 1G would fit beside runner, not beside big
            queue.admit("small", G, max_wait=0.3, queue=q)
        assert admitted == []
        runner.release()
        waiting.join(10)
    assert admitted == ["big"]
    with queue.admit("small", G, max_wait=1, queue=q):                  # the line is gone with big
        pass


def test_the_wait_line_names_the_head_only_when_it_is_the_cause(tmp_path: Path):
    import threading
    q = make_queue(tmp_path, slots=1, memory_total=8 * G)
    with queue.admit("runner", G, queue=q):

        def big():
            with pytest.raises(queue.QueueError):
                queue.admit("big", 4 * G, max_wait=0.5, queue=q)

        waiting = threading.Thread(target=big)
        waiting.start()
        deadline = time.monotonic() + 10
        while queue.state(q).waiting == 0 and time.monotonic() < deadline:
            time.sleep(0.01)
        with pytest.raises(queue.QueueError) as busy:               # the only slot is taken: big is not the cause
            queue.admit("small", G, max_wait=0.2, queue=q)
        assert "slots are busy" in str(busy.value) and "held for" not in str(busy.value)
        waiting.join(10)


def test_younger_job_leaves_the_last_free_slot_for_the_waiter(tmp_path, monkeypatch):
    import threading

    q = make_queue(tmp_path, slots=1, memory_total=8 * G)
    waiting, resume, started = threading.Event(), threading.Event(), threading.Event()
    sleep = time.sleep

    def pause(seconds):
        if threading.current_thread() is older:
            waiting.set()
            assert resume.wait(10)
        else:
            sleep(seconds)

    def oldest():
        with queue.admit("oldest", 4 * G, max_wait=10, queue=q):
            started.set()

    older = threading.Thread(target=oldest)
    monkeypatch.setattr(queue.time, "sleep", pause)
    with queue.admit("without a waiter", G, max_wait=0, queue=q):
        pass
    try:
        with queue.admit("running", G, queue=q):
            older.start()
            assert waiting.wait(10)
        with pytest.raises(queue.QueueError, match="last slot is held for oldest"):
            queue.admit("younger", G, max_wait=0.01, queue=q)
    finally:
        resume.set()
        older.join(10)
    assert started.is_set()
