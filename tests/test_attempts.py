import json
from pathlib import Path

import pytest

from conftest import git
from test_foreign import foreign, output
from verifylab.cli import main

def tiny_patch(root):
    import difflib
    before = (root / "Tiny.lean").read_text()
    after = before.replace("by\n  exact step p certificate", "certificate")
    return "".join(difflib.unified_diff(before.splitlines(True), after.splitlines(True),
                                        fromfile="a/Tiny.lean", tofile="b/Tiny.lean"))


def runner(path, body):
    path.write_text("#!/bin/sh\nset -eu\n" + body + "\n")
    path.chmod(0o755)
    return path


def workflow(root, capsys, backend):
    lock = output(capsys, "import", root)["project"]
    claim = output(capsys, "inspect", "vl.lock", "Tiny.conclusion")
    assert "(certificate : p)" in claim["binders"]
    assert output(capsys, "graph", "vl.lock")["edges"]["Tiny.conclusion"] == ["Tiny.step"]
    assert output(capsys, "order", "vl.lock", "--root", "Tiny.conclusion")["sources"][0]["line"] == 5
    Path("trial.patch").write_text(tiny_patch(root))
    args = ["simplify", "vl.lock", "--patch", "trial.patch", "--targets", "Tiny", "--by", "agent:fixture",
            "--model", "none", "--cost-usd", "0", "--runner", str(backend), "--out", "attempt.json"]
    rc = main(args)
    attempt = json.loads(Path("attempt.json").read_bytes())
    assert attempt["commit"] == lock["commit"] and attempt["cost_usd"] == 0
    assert not git(root, "status", "--porcelain")
    return args, rc, attempt


def test_foreign_journey_including_patch_build(foreign, capsys, tmp_path):
    backend = runner(tmp_path / "build", 'test "$2" = Tiny\ncat "$1/Tiny.lean"')
    args, rc, result = workflow(foreign, capsys, backend)
    assert rc == 0 and result["outcome"] == "result"
    assert "exact step" in result["base_build"]["log_tail"]
    assert ":= certificate" in result["patched_build"]["log_tail"]
    assert result["changed_files"] == [{"file": "Tiny.lean", "added": "1", "removed": "2"}]
    assert main(args) == 2 and json.loads(Path("attempt.json").read_text()) == result


@pytest.mark.parametrize("condition,code,outcome", [("exit 7", 3, "open"), ('grep -q "exact step" "$1/Tiny.lean"', 1, "dead-road")])
def test_unsuccessful_trials_persist(foreign, capsys, tmp_path, condition, code, outcome):
    _, rc, attempt = workflow(foreign, capsys, runner(tmp_path / "build", condition))
    assert (rc, attempt["outcome"]) == (code, outcome)
    assert (attempt["patched_build"] is None) == (code == 3)


def downstream_project(root, tmp_path):
    import difflib
    from conftest import commit_all

    config = root / "lakefile.toml"
    config.write_text(config.read_text() + '\n[[lean_lib]]\nname = "Optional"\n')
    files = {
        "Optional/Unbuilt.lean": "import Tiny\n\nexample (p : Prop) (h : p) : p := Tiny.conclusion p h\n",
        "Optional/Transitive.lean": "import Optional.Unbuilt\n",
        "Optional/Unrelated.lean": "example : True := trivial\n",
        "Checks/Selected.lean": "import Tiny\n",
        "Checks/Unselected.lean": "import Tiny\n",
        "archive/Historical.lean": "import Tiny\n",
    }
    for name, source in files.items():
        path = root / name
        path.parent.mkdir(exist_ok=True)
        path.write_text(source)
    commit_all(root, "include optional importers and selected clients outside the default build")
    clean = tmp_path / "clean.diff"
    clean.write_text(tiny_patch(root))
    src = (root / "Tiny.lean").read_text()
    broken = tmp_path / "broken.diff"
    broken.write_text("".join(difflib.unified_diff(
        src.splitlines(True), src.replace(": p := by\n  exact step p certificate", ": True := True.intro").splitlines(True),
        fromfile="a/Tiny.lean", tofile="b/Tiny.lean")))
    return clean, broken


@pytest.mark.parametrize(("phase", "exit_code"), [("pass", 0), ("base", 3), ("patch", 1)])
def test_downstream_plan_includes_unbuilt_importers_and_only_named_clients(foreign, capsys, tmp_path, phase, exit_code):
    clean, _ = downstream_project(foreign, tmp_path)
    output(capsys, "pin", foreign)
    backend = runner(tmp_path / "build",
                     'test "$VL_TRIAL_EXACT_MODULES" = 1\nprintf "%s\\n" "$@"\n' +
                     {"pass": "exit 0", "base": "exit 7",
                      "patch": 'grep -q "exact step" "$1/Tiny.lean"'}[phase])
    assert main(["trial", "vl.lock", "--patch", str(clean), "--downstream", "--targets", "Tiny", "Checks.Selected",
                 "--by", "agent:test", "--model", "none", "--runner", str(backend), "--out", "trial.json"]) == exit_code
    data = json.loads(Path("trial.json").read_text())
    planned = ["Checks.Selected", "Optional.Transitive", "Optional.Unbuilt", "Tiny"]
    assert data["targets"] == planned
    assert data["downstream"] == {
        "scope": "pinned TOML libraries and explicitly named clients", "requested": ["Tiny", "Checks.Selected"],
        "edited": ["Tiny"], "planned": planned, "base_passed": [] if phase == "base" else planned,
        "patched_passed": planned if phase == "pass" else [], "missing": [] if phase == "pass" else planned,
        "complete": phase == "pass",
    }
    assert data["base_build"]["log_tail"].splitlines()[1:] == planned
    if phase != "base":
        assert data["patched_build"]["log_tail"].splitlines()[1:] == planned


def test_plan_previews_the_exact_ssh_targets_without_starting_a_worker(foreign, capsys, tmp_path, monkeypatch):
    import os

    clean, _ = downstream_project(foreign, tmp_path)
    lock = output(capsys, "pin", foreign)["project"]
    tools = tmp_path / "worker"
    tools.mkdir()
    visits = tools / "visits"
    runner(tools / "ssh", 'printf "connected\\n" >> "$VISITS"\n'
           'test "$4" = -F && test "$5" = "$VL_BUILD_SSH_CONFIG" || exit 125\n'
           'for arg do command="$arg"; done\nexec /bin/sh -c "$command"')
    runner(tools / "lake", 'test "$1" = --version && exit 0\n'
           'test "$1" = build && shift\nprintf "%s\\n" "$@"')
    monkeypatch.setenv("PATH", str(tools) + ":" + os.environ["PATH"])
    monkeypatch.setenv("VISITS", str(visits))
    monkeypatch.setenv("VL_BUILD_HOST", "fixture-worker")
    monkeypatch.setenv("VL_BUILD_SSH_CONFIG", str(tools / "config with spaces"))

    args = ["trial", "vl.lock", "--downstream", "--patch", str(clean), "--targets", "Tiny", "Checks.Selected"]
    status = git(foreign, "status", "--porcelain")
    preview = output(capsys, *args, "--plan")
    assert preview["commit"] == lock["commit"]
    expected = ["Checks.Selected", "Optional.Transitive", "Optional.Unbuilt", "Tiny"]
    assert preview["downstream"]["planned"] == expected
    assert not visits.exists() and git(foreign, "status", "--porcelain") == status
    for flags in (["--targets", "Tiny", "--patch", str(clean)], [*args[2:], "--runner", str(tools / "ssh")]):
        assert main(["trial", "vl.lock", "--plan", *flags]) == 2
        assert "requires --downstream and runs without --runner" in capsys.readouterr().err
    bad = tmp_path / "bad.diff"
    bad.write_text("not a patch")
    assert main(["trial", "vl.lock", "--downstream", "--patch", str(bad), "--targets", "Tiny", "--plan"]) == 2
    assert "patch did not apply" in capsys.readouterr().err and not visits.exists()

    transport = Path(__file__).resolve().parents[1] / "scripts/runners/ssh-lake-build"
    attempt = output(capsys, *args, "--runner", transport, "--by", "agent:test", "--model", "none",
                     "--out", "trial.json")["attempt"]
    assert attempt["patch_sha256"] == preview["patch_sha256"]
    for phase in ("base_build", "patched_build"):
        assert attempt[phase]["log_tail"].splitlines()[:len(expected)] == ["+" + name for name in expected]
    assert len(visits.read_text().splitlines()) == 2


@pytest.mark.lean
def test_downstream_trial_finds_a_break_in_an_optional_module_with_no_cache(foreign, capsys, tmp_path, monkeypatch):
    from test_graph_inputs import lake_build, real_lean

    clean, broken = downstream_project(foreign, tmp_path)
    _, binaries = real_lean(foreign)
    lake_build(foreign, binaries)
    assert not (foreign / ".lake/build/lib/lean/Optional/Unbuilt.olean").exists()
    monkeypatch.setenv("VL_BUILT_TREE", str(foreign))
    monkeypatch.setenv("VL_LEAN", str(binaries / "lean"))
    output(capsys, "pin", foreign)
    backend = Path(__file__).resolve().parents[1] / "scripts/runners/lean-overlay"
    args = ["trial", "vl.lock", "--runner", str(backend), "--by", "agent:test", "--model", "none"]
    focused = output(capsys, *args, "--targets", "Tiny", "--patch", broken, "--out", "focused.json")["attempt"]
    assert focused["evidence"] == "both named builds succeeded"
    downstream = [*args, "--downstream", "--targets", "Tiny", "Checks.Selected"]
    result = output(capsys, *downstream, "--patch", clean, "--out", "clean.json")["attempt"]
    assert result["downstream"]["complete"]
    assert "build Checks.Selected" in result["patched_build"]["log_tail"]
    assert main([*downstream, "--patch", str(broken), "--out", "broken.json"]) == 1
    result = json.loads(Path("broken.json").read_text())
    assert result["downstream"]["missing"] == ["Checks.Selected", "Optional.Transitive", "Optional.Unbuilt", "Tiny"]
    assert "Optional/Unbuilt.lean" in result["patched_build"]["log_tail"]
    assert not result["downstream"]["complete"]


def test_downstream_refuses_an_edit_outside_its_source_scope(foreign, capsys, tmp_path):
    from conftest import commit_all

    doc = foreign / "README.md"
    doc.write_text("original\n")
    commit_all(foreign, "pin a project with browseable documentation")
    output(capsys, "pin", foreign)
    patch = tmp_path / "mixed.diff"
    doc.write_text("edited\n")
    patch.write_text(tiny_patch(foreign) + git(foreign, "diff", "--", "README.md"))
    backend = runner(tmp_path / "build", "exit 0")
    args = ["trial", "vl.lock", "--patch", str(patch), "--targets", "Tiny", "--by", "agent:test",
            "--model", "none", "--runner", str(backend)]
    assert main([*args, "--out", "focused.json"]) == 0
    assert main([*args, "--downstream", "--out", "wrong-scope.json"]) == 2
    assert "existing library or named client sources: README.md" in capsys.readouterr().err
    assert not Path("wrong-scope.json").exists()


@pytest.mark.lean
@pytest.mark.parametrize("downstream", [False, True])
def test_real_lake_patch_journey(foreign, capsys, tmp_path, monkeypatch, downstream):
    import os
    from conftest import lean_elan_home
    from verifylab.adapters.lean_comparator import toolchain_dir
    spec = (foreign / "lean-toolchain").read_text().strip()
    binaries = toolchain_dir(spec, elan_home=Path(lean_elan_home())) / "bin"
    assert (binaries / "lake").is_file(), f"remote gate needs {spec}"
    monkeypatch.setenv("PATH", str(binaries) + ":" + os.environ["PATH"])
    backend = Path(__file__).resolve().parents[1] / "scripts/runners/lake-build"
    if not downstream:
        _, rc, result = workflow(foreign, capsys, backend)
        assert rc == 0, result
    else:
        output(capsys, "pin", foreign)
        Path("trial.patch").write_text(tiny_patch(foreign))
        result = output(capsys, "trial", "vl.lock", "--patch", "trial.patch", "--targets", "Tiny",
                        "--downstream", "--by", "agent:test", "--model", "none", "--runner", backend,
                        "--out", "exact.json")["attempt"]
        assert result["downstream"]["complete"]


def test_patch_refusal_keeps_evidence_before_running_any_target(foreign, capsys, tmp_path):
    output(capsys, "import", foreign)
    path = tmp_path / "change.diff"
    path.write_text(tiny_patch(foreign).replace("exact step p certificate", "old nonexistent proof"))
    args = ["simplify", "vl.lock", "--patch", str(path), "--targets", "Tiny", "--by", "agent:fixture",
            "--model", "none", "--runner", str(runner(tmp_path / "build", "exit 42")), "--out", "failure.json"]
    assert main(args) == 1
    attempt = json.loads(Path("failure.json").read_text())
    assert attempt["patch"] == path.read_text() and attempt["base_build"] is None and attempt["patched_build"] is None


@pytest.mark.parametrize(("route", "original", "arguments"), [
    (("inspect", "--item"), "show", ["add-zero", "--brief"]),
    (("inspect", "--search"), "find", ["zero"]),
    (("check", "--replay"), "replay", ["missing-item"]),
])
def test_project_verbs_preserve_original_contract(route, original, arguments, research_repo, monkeypatch, capsys):
    monkeypatch.chdir(research_repo)
    def invoke(command):
        rc = main([*command, "--json"])
        result = capsys.readouterr()
        return rc, result.out, result.err
    if original == "find":
        invoke([original, *arguments])  # both measurements must use the same warm index
    alias = invoke([*route, *arguments])
    old = invoke([original, *arguments])
    assert alias[:2] == old[:2]
    assert alias[2].replace(" ".join(route), original) == old[2]
    reordered = invoke([route[0], *arguments, route[1]])
    assert reordered == alias


def test_remote_runner_with_offline_transport(foreign, capsys, tmp_path, monkeypatch):
    import os
    tools = tmp_path / "mock-worker"
    tools.mkdir()
    runner(tools / "ssh", 'for arg do command="$arg"; done\nexec /bin/sh -c "$command"')
    runner(tools / "lake", 'test "$1" = --version && exit 0\ntest "$1" = build && test "$2" = Tiny\n'
                          'test ! -e .git\ncat Tiny.lean')
    monkeypatch.setenv("PATH", str(tools) + ":" + os.environ["PATH"])
    monkeypatch.setenv("VL_BUILD_HOST", "offline-fixture")
    transport = Path(__file__).resolve().parents[1] / "scripts/runners/ssh-lake-build"
    _, rc, result = workflow(foreign, capsys, transport)
    assert rc == 0
    assert "exact step" in result["base_build"]["log_tail"]
    assert ":= certificate" in result["patched_build"]["log_tail"]


def test_transport_failure_keeps_patch_open(foreign, capsys, tmp_path):
    backend = runner(tmp_path / "build", 'grep -q "exact step" "$1/Tiny.lean" || exit 125')
    _, rc, result = workflow(foreign, capsys, backend)
    assert rc == 3 and result["outcome"] == "open"
    assert result["patched_build"]["exit"] == 125


@pytest.mark.parametrize("link,target,expected", [
    ("docs/references.bib", "../refs/main.bib", "kept"),
    ("docbuild/docs/references.bib", "../../../outside.bib", "omitted"),
    ("docbuild/dangling.bib", "missing.bib", "omitted"),
    ("Tiny/Escape.lean", "../../outside.lean", "refused"),
    ("Tiny/Dangling.lean", "Missing.lean", "refused"),
    ("lakefile.link", "/etc/passwd", "omitted"),
])
def test_pinned_links_resolve_inside_or_stay_outside_the_build(foreign, capsys, tmp_path, link, target, expected):
    import os
    (foreign / "refs").mkdir()
    (foreign / "refs/main.bib").write_text("@book{x}\n")
    (foreign / link).parent.mkdir(parents=True, exist_ok=True)
    os.symlink(target, foreign / link)
    git(foreign, "add", "-A")
    git(foreign, "commit", "-q", "-m", "link")
    check = f'test "$(cat "$1/refs/main.bib")" = "@book{{x}}"'
    if expected == "kept":
        check += f'\ntest -L "$1/{link}" && test "$(cat "$1/{link}")" = "@book{{x}}"'
    elif expected == "omitted":
        check += f'\n! test -e "$1/{link}" && ! test -L "$1/{link}"'
    backend = runner(tmp_path / "build", check)
    if expected == "refused":
        output(capsys, "import", foreign)
        Path("trial.patch").write_text(tiny_patch(foreign))
        code = main(["simplify", "vl.lock", "--patch", "trial.patch", "--targets", "Tiny", "--by", "agent:fixture",
                     "--model", "none", "--runner", str(backend), "--out", "attempt.json"])
        assert code == 2 and "does not resolve inside the pinned tree" in capsys.readouterr().err
        assert not Path("attempt.json").exists()
        return
    _, rc, attempt = workflow(foreign, capsys, backend)
    assert rc == 0 and attempt["outcome"] == "result", attempt.get("patched_build")
    links = attempt["materialized"]
    if expected == "kept":
        assert links == {"internal_links": [link], "omitted_links": []}
    else:
        assert links["internal_links"] == [] and links["omitted_links"][0]["path"] == link


def test_scaffold_and_linked_trial_keep_the_forecast_and_measurements_separate(foreign, capsys, tmp_path, monkeypatch):
    from conftest import commit_all
    from verifylab import attempt_files

    monkeypatch.chdir(foreign)
    base = output(capsys, "pin", ".")["project"]["commit"]
    path = Path("attempts/change.md")
    creation = ["attempts", "new", str(path), "--purpose", "improve", "--claim", 'Use "certificate" #1',
                "--by", "agent:fixture", "--lane", "one", "--p-accepted", "0.000000001", "--basis", "Read the term"]
    assert main(creation) == 0
    original = path.read_bytes()
    assert main(creation) == 2 and path.read_bytes() == original
    parsed = attempt_files.read(path)
    assert parsed.valid and parsed.data["base"] == base and parsed.outcome is None
    assert parsed.data["forecasts"][0]["p_accepted"] == 0.000000001
    assert "change.trial.json" in path.read_text()
    commit_all(foreign, "freeze forecast")
    patch = tmp_path / "trial.patch"
    patch.write_text(tiny_patch(foreign))
    backend = runner(tmp_path / "build", 'test "$2" = Tiny\ncat "$1/Tiny.lean"')
    trial = ["trial", "vl.lock", "--attempt", str(path), "--patch", str(patch),
             "--targets", "Tiny", "--runner", str(backend)]
    assert main(trial) == 0
    sidecar = path.with_suffix(".trial.json")
    first = sidecar.read_bytes()
    measured = json.loads(first)
    assert measured["by"] == "agent:fixture" and measured["patched_build"]["exit"] == 0
    assert measured["attempt_file"]["path"] == path.as_posix()
    assert len(measured["attempt_file"]["forecast_sha256"]) == 64
    assert path.read_bytes() == original
    assert main(trial) == 2 and sidecar.read_bytes() == first

    mismatch = Path("attempts/other.md")
    assert main([*creation[:2], str(mismatch), *creation[3:]]) == 0
    commit_all(foreign, "freeze the second attempt at the newer base")
    wrong = ["trial", "vl.lock", "--attempt", str(mismatch), *trial[4:]]
    assert main(wrong) == 2
    assert not mismatch.with_suffix(".trial.json").exists()
    assert "same checkout and base commit" in capsys.readouterr().err


@pytest.mark.lean
def test_overlay_trial_reuses_unchanged_dependencies_and_rebuilds_changed_ones(foreign, capsys, monkeypatch):
    import hashlib
    from conftest import commit_all
    from test_graph_inputs import lake_build, real_lean

    config = foreign / "lakefile.toml"
    config.write_text(config.read_text().replace("[[lean_lib]]", "[leanOptions]\npp.unicode.fun = true\n\n[[lean_lib]]"))
    commit_all(foreign, "set a dotted Lean option as Lake does")
    _, binaries = real_lean(foreign)
    lake_build(foreign, binaries)
    cached = foreign / ".lake/build/lib/lean/Tiny/Step.olean"
    digest = hashlib.sha256(cached.read_bytes()).digest()
    monkeypatch.setenv("VL_BUILT_TREE", str(foreign))
    monkeypatch.setenv("VL_LEAN", str(binaries / "lean"))
    monkeypatch.chdir(foreign)
    git(foreign, "init", "--separate-git-dir", str(foreign.parent / "trial.git"))
    output(capsys, "pin", ".")
    attempt = Path("attempts/overlay.md")
    assert main(["attempts", "new", str(attempt), "--purpose", "improve", "--claim", "Use certificate",
                 "--by", "agent:fixture", "--basis", "Read the term"]) == 0
    capsys.readouterr()
    commit_all(foreign, "record forecast ahead of the trial")
    patch = Path("overlay.diff")
    patch.write_text(tiny_patch(foreign))
    backend = Path(__file__).resolve().parents[1] / "scripts/runners/lean-overlay"
    trial = ["trial", "vl.lock", "--patch", str(patch), "--targets", "Tiny", "--runner", str(backend)]
    result = output(capsys, *trial, "--attempt", attempt)["attempt"]
    for side in ("base_build", "patched_build"):
        assert "reuse Tiny.Step" in result[side]["log_tail"]
        assert result[side]["exit"] == 0

    # Changing a cached import must rebuild it and surface the broken theorem.
    # Generate a patch against the pin while leaving the prepared cache alone.
    step = Path("Tiny/Step.lean")
    original = step.read_text()
    step.write_text(original.replace(": p := h", ": p := True.intro"))
    patch.write_text(git(foreign, "diff", "--", str(step)))
    step.write_text(original)
    assert main([*trial, "--out", "broken.json", "--by", "agent:fixture", "--model", "none"]) == 1
    capsys.readouterr()
    broken = json.loads(Path("broken.json").read_text())
    assert "reuse Tiny.Step" in broken["base_build"]["log_tail"]
    assert "build Tiny.Step" in broken["patched_build"]["log_tail"]
    assert broken["outcome"] == "dead-road"
    assert hashlib.sha256(cached.read_bytes()).digest() == digest

    # An incompatible toolchain is an infrastructure failure, even with the artifacts present.
    monkeypatch.setenv("VL_LEAN", "/bin/false")
    assert main([*trial, "--out", "wrong-lean.json", "--by", "agent:fixture", "--model", "none"]) == 3
    capsys.readouterr()
    incompatible = json.loads(Path("wrong-lean.json").read_text())
    assert incompatible["base_build"]["exit"] == 125 and incompatible["patched_build"] is None

    text = attempt.read_text()
    before, rest = text[4:].split("\n---\n", 1)
    attempt.write_text(f"---\n{before}\noutcome:\n  status: unverified\n---\n{rest}")
    check = output(capsys, "attempts", "check")
    assert check["git_order"] == "checked" and check["problems"] == []


@pytest.mark.parametrize("status, expected", [(1, "dead-road"), (143, "open")])
@pytest.mark.parametrize("remote", [False, True])
def test_bundled_lake_runners_distinguish_failure_from_interruption(
        foreign, capsys, tmp_path, monkeypatch, status, expected, remote):
    import os

    tools = tmp_path / "tools"
    tools.mkdir()
    runner(tools / "ssh", 'for arg do command="$arg"; done\nexec /bin/sh -c "$command"')
    runner(tools / "lake", 'test "$1" = --version && exit 0\ngrep -q "exact step" Tiny.lean || exit ' + str(status))
    monkeypatch.setenv("PATH", str(tools) + ":" + os.environ["PATH"])
    monkeypatch.setenv("VL_BUILD_HOST", "offline-fixture")
    name = "ssh-lake-build" if remote else "lake-build"
    backend = Path(__file__).resolve().parents[1] / "scripts/runners" / name
    _, code, result = workflow(foreign, capsys, backend)
    assert (code, result["outcome"]) == ((1 if status == 1 else 3), expected)
    assert result["patched_build"]["exit"] == (1 if status == 1 else 125)


@pytest.mark.lean
@pytest.mark.parametrize("section", ["package", "library"])
def test_overlay_refuses_extra_lean_args_that_lake_enforces(foreign, capsys, tmp_path, monkeypatch, section):
    import difflib
    import os
    from conftest import commit_all
    from test_graph_inputs import lake_build, real_lean

    config = foreign / "lakefile.toml"
    text = config.read_text()
    option = 'moreLeanArgs = ["-DwarningAsError=true"]\n'
    config.write_text(text + option if section == "library" else option + text)
    commit_all(foreign, "make warnings fatal")
    _, binaries = real_lean(foreign)
    lake_build(foreign, binaries)
    monkeypatch.setenv("PATH", str(binaries) + ":" + os.environ["PATH"])
    monkeypatch.setenv("VL_BUILT_TREE", str(foreign))
    monkeypatch.setenv("VL_LEAN", str(binaries / "lean"))
    output(capsys, "pin", foreign)
    source = (foreign / "Tiny.lean").read_text()
    change = tmp_path / "warning.patch"
    change.write_text("".join(difflib.unified_diff(
        source.splitlines(True), source.replace("exact step p certificate", "sorry").splitlines(True),
        fromfile="a/Tiny.lean", tofile="b/Tiny.lean")))
    runners = Path(__file__).resolve().parents[1] / "scripts/runners"
    args = ["trial", "vl.lock", "--patch", str(change), "--targets", "Tiny", "--by", "agent:test", "--model", "none"]
    assert main([*args, "--runner", str(runners / "lake-build"), "--out", "lake.json"]) == 1
    lake = json.loads(Path("lake.json").read_text())
    assert lake["base_build"]["exit"] == 0 and "sorry" in lake["patched_build"]["log_tail"]
    assert main([*args, "--runner", str(runners / "lean-overlay"), "--out", "overlay.json"]) == 3
    overlay = json.loads(Path("overlay.json").read_text())
    assert overlay["base_build"]["exit"] == 125 and "uses moreLeanArgs" in overlay["base_build"]["log_tail"]
    assert overlay["patched_build"] is None


def test_linked_trial_needs_a_committed_complete_forecast_and_audits_its_fingerprint(
        foreign, capsys, tmp_path, monkeypatch):
    from conftest import commit_all

    monkeypatch.chdir(foreign)
    output(capsys, "pin", ".")
    attempt = Path("attempts/change.md")
    assert main(["attempts", "new", str(attempt), "--purpose", "improve", "--claim", "Use certificate",
                 "--by", "agent:fixture"]) == 0
    patch = tmp_path / "change.diff"
    patch.write_text(tiny_patch(foreign))
    backend = runner(tmp_path / "build", "exit 0")
    args = ["trial", "vl.lock", "--patch", str(patch), "--targets", "Tiny", "--runner", str(backend),
            "--attempt", str(attempt)]
    assert main(args) == 2 and not attempt.with_suffix(".trial.json").exists()
    assert "commit the complete open attempt" in capsys.readouterr().err
    commit_all(foreign, "unfinished basis")
    assert main(args) == 2 and not attempt.with_suffix(".trial.json").exists()
    assert "replace the scaffold forecast basis" in capsys.readouterr().err
    attempt.write_text(attempt.read_text().replace("TODO: write the basis before committing", "Checked the term"))
    commit_all(foreign, "freeze the forecast")
    committed = git(foreign, "rev-parse", "HEAD").strip()
    record = output(capsys, *args)["attempt"]
    assert record["attempt_file"]["forecast_commit"] == committed
    assert output(capsys, "attempts", "check")["problems"] == []
    attempt.write_text(attempt.read_text().replace("Checked the term", "Saw the trial pass"))
    assert main(["attempts", "check", "--json"]) == 1
    assert "differs from the snapshot recorded at trial start" in capsys.readouterr().out
