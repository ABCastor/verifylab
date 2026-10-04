from __future__ import annotations

import glob
import json
import os
import shutil
import socket
import stat
import subprocess
import threading
import time
from pathlib import Path

import pytest

from verifylab import cli, jail, machine
from conftest import git, system_python



def bwrap(test):
    """The test runs bubblewrap (`vl lane exec`): a `jail` test, skipped where bwrap is missing. The others (new,
    list, close) need only git, and stay in the fast suite."""
    return pytest.mark.jail(pytest.mark.skipif(not jail.available(), reason="bwrap missing")(test))


@pytest.fixture
def lane_repo(research_repo, monkeypatch):
    pkg = research_repo / ".lake" / "packages" / "dep"
    pkg.mkdir(parents=True)
    (pkg / "built.olean").write_text("canonical")
    (research_repo / ".gitignore").write_text(".lake/\n")
    git(research_repo, "add", ".gitignore")
    git(research_repo, "commit", "-q", "-m", "ignore lake")
    monkeypatch.chdir(research_repo)
    yield research_repo
    # overlayfs leaves a mode-000 work dir behind; make it removable for pytest's tmp cleanup
    lanes = research_repo.parent / ".vl-lanes"
    if lanes.exists():
        subprocess.run(["chmod", "-R", "u+rwx", str(lanes)], check=False)


def home(root: Path) -> Path:
    """The folder of the repository's own lanes (a folder of `[lanes] dir`, by default ../.vl-lanes)."""
    from verifylab.commands import lane
    from verifylab.config import load_config
    return lane.lanes_dir(load_config(root))


def _new(name="la"):
    assert cli.main(["lane", "new", name]) == 0


def test_new_creates_worktree_branch_and_guard(lane_repo, capsys):
    _new()
    lane = home(lane_repo) / "la"
    assert (lane / "research" / "vl.toml").is_file()
    assert git(lane, "rev-parse", "--abbrev-ref", "HEAD").strip() == "lane/la"
    assert not (lane / ".lake").stat().st_mode & stat.S_IWUSR


@bwrap
def test_exec_sees_shared_cache_but_writes_only_to_its_overlay(lane_repo, capsys):
    _new()
    script = "cat .lake/packages/dep/built.olean; echo lane > .lake/packages/dep/built.olean; echo x > .lake/packages/dep/new"
    assert cli.main(["lane", "exec", "la", "--", "/bin/sh", "-c", script]) == 0
    canonical = lane_repo / ".lake" / "packages" / "dep"
    assert (canonical / "built.olean").read_text() == "canonical"
    assert not (canonical / "new").exists()
    upper = home(lane_repo) / ".overlay" / "la" / "upper" / "packages" / "dep"
    assert (upper / "new").read_text().strip() == "x"


@bwrap
def test_exec_has_no_network(lane_repo, capfd):
    """A server listening on this machine: reachable from outside the lane (the probe can fail), not from inside."""
    _new()
    with socket.create_server(("127.0.0.1", 0)) as server:
        code = (f"import socket\ns=socket.socket(); s.settimeout(2)\ntry:\n s.connect(('127.0.0.1',{server.getsockname()[1]}));"
                " print('net')\nexcept OSError:\n print('nonet')")
        assert subprocess.run([system_python(), "-c", code], capture_output=True, text=True).stdout.strip() == "net"
        assert cli.main(["lane", "exec", "la", "--", system_python(), "-c", code]) == 0
    assert capfd.readouterr().out.strip().splitlines()[-1] == "nonet"


def test_close_refuses_uncommitted_then_force(lane_repo, capsys):
    _new()
    lane = home(lane_repo) / "la"
    (lane / "Fixture" / "Basic.lean").write_text("-- edit\n")
    assert cli.main(["lane", "close", "la"]) == 2
    assert cli.main(["lane", "close", "la", "--force"]) == 0
    assert not lane.exists() and not (home(lane_repo) / ".overlay" / "la").exists()
    assert "lane/la" in git(lane_repo, "branch", "--list", "lane/la")


def test_close_refuses_ignored_files_then_discards_and_lists_them(lane_repo, capsys):
    (lane_repo / ".gitignore").write_text(".lake/\n.vl-cache/\n")
    git(lane_repo, "commit", "-qam", "ignore the cache")
    _new()
    lane = home(lane_repo) / "la"
    scratch = lane / ".vl-cache" / "explore" / "add-zero" / "r.json"
    scratch.parent.mkdir(parents=True)
    scratch.write_text("{}")
    capsys.readouterr()
    assert cli.main(["lane", "close", "la"]) == 2
    err = capsys.readouterr().err
    assert ".vl-cache/explore/add-zero/r.json" in err and "--discard-ignored" in err
    assert cli.main(["lane", "close", "la", "--force"]) == 2  # --force covers tracked changes, not ignored files
    assert scratch.exists()
    capsys.readouterr()
    assert cli.main(["lane", "close", "la", "--discard-ignored"]) == 0
    out = capsys.readouterr().out
    assert "discarded 1 gitignored file(s)" in out and ".vl-cache/explore/add-zero/r.json" in out
    assert not lane.exists()


def test_close_does_not_count_the_managed_lake_mount(lane_repo, capsys):
    _new()
    lake = home(lane_repo) / "la" / ".lake"
    lake.chmod(0o755)
    (lake / "build").mkdir()
    (lake / "build" / "x.olean").write_text("x")
    capsys.readouterr()
    assert cli.main(["lane", "close", "la", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["discarded_ignored"] == []


@bwrap
def test_list_reports_overlay_and_uncommitted(lane_repo, capsys):
    _new()
    cli.main(["lane", "exec", "la", "--", "/bin/sh", "-c", "echo 1 > .lake/x"])
    capsys.readouterr()
    assert cli.main(["lane", "list", "--json"]) == 0
    rows = json.loads(capsys.readouterr().out)["lanes"]
    assert rows[0]["name"] == "la" and rows[0]["overlay_bytes"] > 0


@bwrap
def test_lane_uses_configured_cache(lane_repo, capfd):
    shared = lane_repo.parent / "shared-cache"
    (shared / "packages" / "dep").mkdir(parents=True)
    (shared / "packages" / "dep" / "built.olean").write_text("shared")
    cfg = lane_repo / "research" / "vl.toml"
    cfg.write_text(cfg.read_text().replace('roots = ["Fixture"]', f'roots = ["Fixture"]\ncache = "{shared}"'))
    git(lane_repo, "commit", "-q", "-am", "shared cache")       # vl reads the configuration on the trusted ref
    _new("lc")
    capfd.readouterr()
    assert cli.main(["lane", "exec", "lc", "--", "/bin/sh", "-c", "cat .lake/packages/dep/built.olean"]) == 0
    assert "shared" in capfd.readouterr().out
    assert (shared / "packages" / "dep" / "built.olean").read_text() == "shared"


@bwrap
def test_lane_takes_the_build_cache_from_the_machine_configuration_first(lane_repo, capfd):
    from conftest import write_machine
    machine_cache = lane_repo.parent / "machine-cache"
    (machine_cache / "packages" / "dep").mkdir(parents=True)
    (machine_cache / "packages" / "dep" / "built.olean").write_text("machine")
    write_machine({}, caches={str(lane_repo): str(machine_cache)})
    _new("lm")
    capfd.readouterr()
    assert cli.main(["lane", "exec", "lm", "--", "/bin/sh", "-c", "cat .lake/packages/dep/built.olean"]) == 0
    assert "machine" in capfd.readouterr().out


@bwrap
def test_exec_works_from_inside_the_lane(lane_repo, monkeypatch, capfd):
    _new("inner")
    lane = home(lane_repo) / "inner"
    monkeypatch.chdir(lane)
    assert cli.main(["lane", "exec", "inner", "--", "/bin/sh", "-c", "cat .lake/packages/dep/built.olean"]) == 0
    assert "canonical" in capfd.readouterr().out
    assert cli.main(["lane", "list"]) == 0


@bwrap
def test_back_to_back_and_concurrent_execs_do_not_collide(lane_repo, capfd):
    import threading
    _new("cc")
    assert cli.main(["lane", "exec", "cc", "--", "/bin/sh", "-c", "echo 1 > .lake/one"]) == 0
    assert cli.main(["lane", "exec", "cc", "--", "/bin/sh", "-c", "cat .lake/one"]) == 0
    codes = []
    threads = [threading.Thread(target=lambda: codes.append(cli.main(
        ["lane", "exec", "cc", "--", "/bin/sh", "-c", "sleep 0.3; ls .lake >/dev/null"]))) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert codes == [0, 0, 0]
    leftovers = list((home(lane_repo) / ".overlay" / "cc").glob("work-*"))
    assert leftovers == []


def test_new_refuses_beyond_max_parallel(lane_repo, capsys):
    cfg = lane_repo / "research" / "vl.toml"
    cfg.write_text(cfg.read_text() + "\n[lanes]\nmax_parallel = 3\n")
    git(lane_repo, "commit", "-q", "-am", "three lanes")
    for name in ("la", "lb", "lc"):
        _new(name)
    capsys.readouterr()
    assert cli.main(["lane", "new", "ld"]) == 2
    err = capsys.readouterr().err
    assert "3 lanes are open (la, lb, lc) and [lanes] max_parallel is 3" in err
    assert not (home(lane_repo) / "ld").exists()
    assert cli.main(["lane", "close", "la"]) == 0
    _new("ld")


def _vl_slice() -> Path:
    uid = os.getuid()
    return Path(f"/sys/fs/cgroup/user.slice/user-{uid}.slice/user@{uid}.service/vl.slice")


@bwrap
@pytest.mark.skipif(shutil.which("systemd-run") is None, reason="systemd-run missing")
def test_exec_runs_inside_the_capped_vl_slice(lane_repo, capfd):
    from verifylab.config import load_config
    _new("sl")
    marker = f"vl-slice-probe-{os.getpid()}"
    seen: list[str] = []
    done = threading.Event()

    def watch():
        while not done.is_set():
            for scope in glob.glob(str(_vl_slice() / "run-*.scope")):
                try:
                    pids = Path(scope, "cgroup.procs").read_text().split()
                    if any(marker in Path(f"/proc/{pid}/cmdline").read_bytes().decode(errors="replace") for pid in pids):
                        seen.append(scope)
                except OSError:
                    continue
            time.sleep(0.05)
    watcher = threading.Thread(target=watch)
    watcher.start()
    try:
        assert cli.main(["lane", "exec", "sl", "--", "/bin/sh", "-c", "sleep 1", marker]) == 0
    finally:
        done.set()
        watcher.join()
    assert seen, "the lane command did not run in a scope under vl.slice"
    total = machine.load().total_memory_cap()
    shown = subprocess.run(["systemctl", "--user", "show", "vl.slice", "-p", "MemoryMax", "--value"],
                           capture_output=True, text=True).stdout.strip()
    assert int(shown) == int(total.removesuffix("K")) * 1024


def test_close_lists_uncommitted_files_by_their_real_names(lane_repo, capsys):
    _new()
    lane = home(lane_repo) / "la"
    receipt = lane / "research" / "evidence" / "add-zero" / "é ñ.json"
    receipt.parent.mkdir(parents=True)
    receipt.write_text("{}")
    capsys.readouterr()
    assert cli.main(["lane", "close", "la"]) == 2
    err = capsys.readouterr().err
    assert "  research/evidence/add-zero/é ñ.json" in err, err
    assert "1 of them are receipts written in the lane" in err, err
    assert cli.main(["lane", "list", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["lanes"][0]["uncommitted"] == ["research/evidence/add-zero/é ñ.json"]


def test_new_suggests_lake_lean_and_no_text_suggests_lake_env_lean(lane_repo, capsys):
    """`lake lean FILE` builds the file's imports before elaborating it; `lake env lean FILE` fails on an
    unbuilt import."""
    _new()
    out = capsys.readouterr().out
    assert "vl lane exec la -- lake lean <file.lean>" in out and "lake env lean" not in out
    repo = Path(__file__).resolve().parents[1]
    texts = [repo / "README.md", *(repo / "skills").rglob("*.md"), *(repo / "templates").rglob("*"),
             *(repo / "src").rglob("*.py")]
    assert [str(p) for p in texts if p.is_file() and "lake env lean" in p.read_text()] == []


def test_concurrent_new_at_the_limit_admits_exactly_one(lane_repo, capsys, monkeypatch):
    """Counting the open lanes and creating the new one happen under one lock: two agents asking for the last
    free lane at once must not both get it. The count is slowed so that, unlocked, both would see one free slot."""
    from verifylab.commands import lane as lane_cmd
    cfg = lane_repo / "research" / "vl.toml"
    cfg.write_text(cfg.read_text() + "\n[lanes]\nmax_parallel = 2\n")
    git(lane_repo, "commit", "-q", "-am", "two lanes")          # vl reads the configuration on the trusted ref
    _new("la")
    count = lane_cmd.open_lanes

    def slow_count(config):
        names = count(config)
        time.sleep(0.5)
        return names
    monkeypatch.setattr(lane_cmd, "open_lanes", slow_count)
    codes: dict[str, int] = {}
    threads = [threading.Thread(target=lambda n=n: codes.__setitem__(n, cli.main(["lane", "new", n])))
               for n in ("lb", "lc")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(codes.values()) == [0, 2], codes
    assert len(count(load_lane_config(lane_repo))) == 2
    assert "lanes are open" in capsys.readouterr().err


def load_lane_config(root):
    from verifylab.config import load_config
    return load_config(root)


def test_exec_reports_a_slice_that_cannot_be_capped_as_a_tool_failure(lane_repo, monkeypatch, capsys):
    _new()

    def refuse(*args, **kwargs):
        raise RuntimeError("cannot cap vl.slice at 20G: Access denied")
    monkeypatch.setattr(jail, "memory_capped", refuse)
    assert cli.main(["lane", "exec", "la", "--", "true"]) == 2
    err = capsys.readouterr().err
    assert "cannot cap vl.slice at 20G" in err and "internal error" not in err


def test_a_lane_takes_its_elan_home_from_each_call(lane_repo, tmp_path, monkeypatch):
    from verifylab.commands import lane
    from verifylab.repo import Repo
    _new()
    elan = tmp_path / "elan"
    (elan / "bin").mkdir(parents=True)
    monkeypatch.setenv("ELAN_HOME", str(elan))
    config = Repo.open(lane_repo).config
    box = lane.lane_jail(lane.load_lane(config, "la"))
    assert box.env["ELAN_HOME"] == str(elan) and box.env["PATH"].startswith(f"{elan / 'bin'}:")
    assert elan in box.read_only



# B7, B8: closing never touches what a link points to; a lane is made whole or not at all; a running lane stays ----

def test_closing_never_changes_the_permissions_behind_a_link(lane_repo, tmp_path, capsys):
    _new()
    outside = tmp_path / "outside"
    outside.mkdir()
    outside.chmod(0o500)
    upper = home(lane_repo) / ".overlay" / "la" / "upper"
    (upper / "link").symlink_to(outside, target_is_directory=True)
    (upper / "own").mkdir()
    (upper / "own").chmod(0o500)
    from verifylab.commands import lane
    lane._make_writable(upper)
    assert stat.S_IMODE(outside.stat().st_mode) == 0o500                  # the linked folder is untouched
    assert stat.S_IMODE((upper / "own").stat().st_mode) & stat.S_IRWXU == stat.S_IRWXU    # control
    outside.chmod(0o700)


def test_a_lane_that_fails_half_made_is_removed_whole(lane_repo, monkeypatch, capsys):
    from verifylab.commands import lane
    with monkeypatch.context() as patch:
        patch.setattr(lane, "overlay_dirs", lambda config, name: (_ for _ in ()).throw(OSError("disk full")))
        assert cli.main(["lane", "new", "half"]) == 2
    assert "disk full" in capsys.readouterr().err
    assert not (home(lane_repo) / "half").exists()
    assert git(lane_repo, "branch", "--list", "lane/half").strip() == ""
    assert len(git(lane_repo, "worktree", "list").splitlines()) == 1          # only the main checkout
    _new("half")                                                           # the name is free again


def test_a_broken_machine_file_stops_a_new_lane_before_its_worktree(lane_repo, capsys):
    lanes = home(lane_repo)                     # vl's git reads the machine file too: find the folder first
    machine.config_path().write_text("[tools\n")
    machine.forget()
    assert cli.main(["lane", "new", "early"]) == 2
    assert not (lanes / "early").exists()
    assert git(lane_repo, "branch", "--list", "lane/early").strip() == ""


def test_a_lane_running_a_command_is_not_closed(lane_repo, capsys):
    import fcntl
    _new()
    overlay = home(lane_repo) / ".overlay" / "la"
    with open(overlay / "exec.lock", "w") as held:                        # what `vl lane exec` holds while it runs
        fcntl.flock(held, fcntl.LOCK_EX)
        assert cli.main(["lane", "close", "la"]) == 2
        assert "a command is running in lane 'la'" in capsys.readouterr().err
        assert (home(lane_repo) / "la").is_dir()
    assert cli.main(["lane", "close", "la"]) == 0                          # control: once it ended


# CHEATS R26: a lane belongs to the repository that made it ------------------------------------------------------------

def _sibling(lane_repo: Path) -> Path:
    """A second repository next to `lane_repo`, set up the same way: both keep the default `../.vl-lanes`."""
    from conftest import CONFIG, write_item
    root = lane_repo.parent / "other"
    (root / "research").mkdir(parents=True)
    git(root, "init", "-q", "-b", "trusted")
    for key, value in (("vl.trustedRef", "trusted"), ("user.name", "Test"), ("user.email", "test@example.com")):
        git(root, "config", key, value)
    (root / "research" / "vl.toml").write_text(CONFIG)
    (root / "Fixture").mkdir()
    (root / "Fixture" / "Basic.lean").write_text("theorem other (n : Nat) : n = n := rfl\n")
    write_item(root, "add-zero")
    (root / ".lake" / "packages" / "dep").mkdir(parents=True)
    (root / ".lake" / "packages" / "dep" / "built.olean").write_text("other")
    (root / ".gitignore").write_text(".lake/\n")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "init")
    return root


def _rows(capsys) -> list[dict]:
    capsys.readouterr()
    assert cli.main(["lane", "list", "--json"]) == 0
    return json.loads(capsys.readouterr().out)["lanes"]


def test_a_sibling_repository_cannot_list_exec_or_close_a_lane(lane_repo, monkeypatch, capsys):
    a, b = lane_repo, _sibling(lane_repo)
    _new("worker-aa")
    lane_a = home(a) / "worker-aa"
    monkeypatch.chdir(b)
    assert _rows(capsys) == []
    assert cli.main(["lane", "exec", "worker-aa", "--", "/usr/bin/pwd"]) == 2
    assert "no lane 'worker-aa' in this repository" in capsys.readouterr().err
    assert cli.main(["lane", "close", "worker-aa", "--force", "--discard-ignored"]) == 2
    assert lane_a.is_dir() and git(lane_a, "rev-parse", "--abbrev-ref", "HEAD").strip() == "lane/worker-aa"
    _new("worker-aa")                                    # the same name, in B: its own lane, its own count
    [row] = _rows(capsys)
    assert row["path"] == str(home(b) / "worker-aa") != str(lane_a)
    assert row["repository"] == str(Path(git(b, "rev-parse", "--path-format=absolute", "--git-common-dir").strip()))
    monkeypatch.chdir(a)
    assert [r["path"] for r in _rows(capsys)] == [str(lane_a)]
    assert cli.main(["lane", "close", "worker-aa"]) == 0 and not lane_a.exists()
    monkeypatch.chdir(b)
    assert cli.main(["lane", "close", "worker-aa"]) == 0 and not (home(b) / "worker-aa").exists()


def _copy_into(lane_repo: Path, other: Path, name: str) -> None:
    """Put the metadata of `lane_repo`'s lane into `other`'s own lanes folder, as a shared or edited folder could."""
    for part in ("upper", "work"):
        (home(other) / ".overlay" / name / part).mkdir(parents=True, exist_ok=True)
    shutil.copy(home(lane_repo) / f"{name}.vl-lane.json", home(other) / f"{name}.vl-lane.json")


def test_lane_metadata_of_another_repository_is_refused_and_slips_through_without_the_guard(lane_repo, monkeypatch,
                                                                                            capsys):
    from verifylab.commands import lane
    a, b = lane_repo, _sibling(lane_repo)
    _new("worker-aa")
    _copy_into(a, b, "worker-aa")
    monkeypatch.chdir(b)
    assert _rows(capsys) == []
    for action in (["exec", "worker-aa", "--", "/usr/bin/pwd"], ["close", "worker-aa", "--force"]):
        assert cli.main(["lane", *action]) == 2
        assert "belongs to the repository" in capsys.readouterr().err
    assert (home(a) / "worker-aa").is_dir() and (home(b) / "worker-aa.vl-lane.json").is_file()
    monkeypatch.setattr(lane, "owner_problem", lambda *args, **kwargs: None)      # the guard disabled
    assert [r["path"] for r in _rows(capsys)] == [str(home(a) / "worker-aa")]


def test_a_lane_made_before_lanes_recorded_their_repository_closes_from_its_own_repository_only(lane_repo, monkeypatch,
                                                                                               capsys):
    """The layout of earlier versions: metadata and overlay directly in the shared `../.vl-lanes`, no `repository`."""
    a, b = lane_repo, _sibling(lane_repo)
    shared = a.parent / ".vl-lanes"
    path = shared / "old"
    git(a, "worktree", "add", "-q", "-b", "lane/old", str(path), "trusted")
    for part in ("upper", "work"):
        (shared / ".overlay" / "old" / part).mkdir(parents=True)
    (shared / "old.vl-lane.json").write_text(json.dumps({
        "name": "old", "path": str(path), "branch": "lane/old", "from_ref": "trusted", "from_commit": "x",
        "canonical_lake": None, "lean_project": ".", "created": "2026-10-01T00:00:00+00:00"}))
    monkeypatch.chdir(b)
    assert _rows(capsys) == []
    assert cli.main(["lane", "close", "old", "--force"]) == 2
    assert "git does not list" in capsys.readouterr().err
    assert path.is_dir() and (shared / "old.vl-lane.json").is_file()
    monkeypatch.chdir(a)
    assert [r["name"] for r in _rows(capsys)] == ["old"]
    assert cli.main(["lane", "close", "old"]) == 0
    assert not path.exists() and not (shared / "old.vl-lane.json").exists() and not (shared / ".overlay" / "old").exists()
    assert "lane/old" in git(a, "branch", "--list", "lane/old")


@bwrap
def test_each_repository_runs_its_own_lane_and_never_another_ones(lane_repo, monkeypatch, capfd):
    from verifylab.commands import lane
    a, b = lane_repo, _sibling(lane_repo)
    _new("worker-aa")
    monkeypatch.chdir(b)
    _new("worker-bb")
    _copy_into(a, b, "worker-aa")
    capfd.readouterr()
    assert cli.main(["lane", "exec", "worker-bb", "--", "/bin/sh", "-c", "pwd"]) == 0         # control
    assert capfd.readouterr().out.strip().splitlines()[-1] == str(home(b) / "worker-bb")
    assert cli.main(["lane", "exec", "worker-aa", "--", "/bin/sh", "-c", "pwd"]) == 2
    assert "belongs to the repository" in capfd.readouterr().err
    monkeypatch.setattr(lane, "owner_problem", lambda *args, **kwargs: None)      # the guard disabled
    assert cli.main(["lane", "exec", "worker-aa", "--", "/bin/sh", "-c", "pwd"]) == 0
    assert capfd.readouterr().out.strip().splitlines()[-1] == str(home(a) / "worker-aa")


# --json means the same wherever it is placed; `vl lane exec` passes its command through ------------------------------

def test_json_means_the_same_before_or_after_the_lane_action(lane_repo, capsys):
    assert cli.main(["lane", "--json", "new", "la"]) == 0
    assert json.loads(capsys.readouterr().out)["lane"]["name"] == "la"
    outputs = []
    for argv in (["lane", "--json", "list"], ["lane", "list", "--json"]):
        assert cli.main(argv) == 0
        outputs.append(json.loads(capsys.readouterr().out))
    assert outputs[0] == outputs[1] and outputs[0]["schema"] == "vl.output/1" and outputs[0]["lanes"][0]["name"] == "la"
    assert cli.main(["lane", "list"]) == 0                                               # control: text
    assert capsys.readouterr().out.startswith("la: lane/la head ")


def test_lane_exec_refuses_json_wherever_it_is_placed(lane_repo, capfd):
    _new()
    for argv in (["lane", "--json", "exec", "la", "--", "/usr/bin/printf", '{"looks_like_json":true}'],
                 ["lane", "exec", "la", "--json", "--", "/usr/bin/printf", '{"looks_like_json":true}']):
        capfd.readouterr()
        assert cli.main(argv) == 2
        out, err = capfd.readouterr()
        assert out == "" and "passes the command's own output and exit code through unchanged" in err


@bwrap
def test_lane_exec_passes_the_commands_output_and_exit_code_through(lane_repo, capfd):
    _new()
    capfd.readouterr()
    assert cli.main(["lane", "exec", "la", "--", "/bin/sh", "-c", 'printf \'{"looks_like_json":true}\'; exit 7']) == 7
    assert capfd.readouterr().out == '{"looks_like_json":true}'
    assert cli.main(["lane", "exec", "la", "--", "/bin/echo", "--json"]) == 0        # after --, the command's own
    assert capfd.readouterr().out == "--json\n"
