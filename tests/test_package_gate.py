"""The third-party package gate (ADR 0012, proposed): a Lake package outside `[lean] trusted_packages` stops a check, a
lane and an import on a workstation, before anything builds, and is allowed on a disposable machine. Each refusal has
a planted case next to a clean control (a Mathlib-only manifest), and the gate is shown able to fail: with it off, or on
a disposable machine, the same project goes on."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from verifylab import cli, machine, packages
from verifylab.adapters.lean_comparator import LeanComparatorAdapter
from verifylab.config import ConfigError, parse_config

from conftest import commit_all, git, write_machine
from lean_helpers import make_repo, request

OFFICIAL = [("mathlib", "leanprover-community/mathlib4.git"), ("plausible", "leanprover-community/plausible"),
            ("LeanSearchClient", "leanprover-community/LeanSearchClient"),
            ("importGraph", "leanprover-community/import-graph"), ("proofwidgets", "leanprover-community/ProofWidgets4"),
            ("aesop", "leanprover-community/aesop"), ("Qq", "leanprover-community/quote4"),
            ("batteries", "leanprover-community/batteries"), ("Cli", "leanprover/lean4-cli")]
GATE = "outside [lean] trusted_packages"


def entry(name: str, repo: str) -> dict:
    return {"url": f"https://github.com/{repo}", "type": "git", "subDir": None, "scope": "", "rev": "0" * 40,
            "name": name, "manifestFile": "lake-manifest.json", "inputRev": "main", "inherited": name != "mathlib",
            "configFile": "lakefile.toml"}


def manifest(*extra: tuple[str, str], official=OFFICIAL) -> bytes:
    """A manifest of the Mathlib-only project (the nine packages), plus `extra` (name, owner/repo) ones."""
    packages_ = [entry(name, repo) for name, repo in [*official, *extra]]
    return json.dumps({"version": "1.2.0", "packagesDir": ".lake/packages", "packages": packages_, "name": "p",
                       "lakeDir": ".lake"}).encode()


THIRD = ("PrimeCert", "b-mehta/PrimeCert.git")
SPOOF = ("Cli", "somebody/lean4-cli")


def workstation():
    write_machine()
    return machine.load()


def disposable():
    write_machine(sandbox="disposable")
    return machine.load()


# The rule -----------------------------------------------------------------------------------------------------------

def test_a_mathlib_only_manifest_has_no_third_party_package():
    assert packages.third_party(manifest(), None) == []                                   # control
    assert packages.third_party(manifest(THIRD), None) == ["PrimeCert"]                   # planted


def test_the_default_needs_the_official_owner_not_only_the_name():
    """A package called `Cli` from another owner is not the Lean organisation's `Cli`."""
    official = [p for p in OFFICIAL if p[0] != "Cli"]
    assert packages.third_party(manifest(SPOOF, official=official), None) == ["Cli"]
    assert packages.third_party(manifest(("Cli", "leanprover/lean4-cli"), official=official), None) == []   # control
    nameless = json.loads(manifest())
    del nameless["packages"][0]["url"]
    assert packages.third_party(json.dumps(nameless).encode(), None) == ["mathlib"]     # no URL, no proof of the owner


@pytest.mark.parametrize("repo", ["leanprover-community/../attacker/batteries",
                                  "leanprover-community/batteries/../../attacker/batteries",
                                  "leanprover-community/%2e%2e/attacker/batteries",
                                  "leanprover-community/batteries?x=/../../attacker/batteries",
                                  "leanprover-community/.."])
def test_the_owner_is_read_from_a_url_that_is_exactly_owner_and_repository(repo):
    """A URL that starts with an official owner but whose path leaves it (git normalizes `..`) is not the owner's."""
    official = [p for p in OFFICIAL if p[0] != "batteries"]
    assert packages.third_party(manifest(("batteries", repo), official=official), None) == ["batteries"]
    control = [("batteries", "leanprover-community/batteries")]
    assert packages.third_party(manifest(*control, official=official), None) == []


def test_a_written_list_trusts_by_name_and_replaces_the_default():
    listed = tuple(name for name, _ in OFFICIAL)
    assert packages.third_party(manifest(THIRD), (*listed, "PrimeCert")) == []
    assert packages.third_party(manifest(THIRD), listed) == ["PrimeCert"]                 # control: not listed
    assert packages.third_party(manifest(), ("batteries",)) == sorted(set(listed) - {"batteries"})


def test_a_manifest_that_does_not_parse_is_refused_on_a_workstation_only():
    names, why = packages.gate([b"{not json"], None, workstation())
    assert names == [] and "lake-manifest.json" in why and "cannot tell" in why
    assert packages.gate([b"{not json"], None, disposable()) == ([], None)
    assert packages.gate([None], None, workstation()) == ([], None)                       # no manifest: no package


def test_the_workstation_refuses_and_the_disposable_machine_allows():
    names, why = packages.gate([manifest(THIRD, ("belyi", "lana-agents/belyi.git"))], None, workstation())
    assert names == ["PrimeCert", "belyi"] and GATE in why and "PrimeCert, belyi" in why and "disposable" in why
    assert packages.gate([manifest(THIRD)], None, disposable()) == (["PrimeCert"], None)
    assert packages.gate([manifest()], None, workstation()) == ([], None)                 # control: Mathlib only


def test_the_list_is_read_from_vl_toml_and_must_be_a_list_of_names():
    assert parse_config(Path("/x"), '[lean]\ntrusted_packages = ["a", "b"]\n').lean.trusted_packages == ("a", "b")
    assert parse_config(Path("/x"), "[lean]\n").lean.trusted_packages is None
    with pytest.raises(ConfigError, match="trusted_packages must be a list of strings"):
        parse_config(Path("/x"), '[lean]\ntrusted_packages = "mathlib"\n')


def test_the_list_decides_where_a_check_runs_not_its_verdict_so_no_digest_changes():
    from verifylab.config import check_digest, rules_digest, verdict_digest
    plain = parse_config(Path("/x"), "[lean]\nroots = [\"A\"]\n")
    listed = parse_config(Path("/x"), '[lean]\nroots = ["A"]\ntrusted_packages = ["PrimeCert"]\n')
    assert listed.lean.trusted_packages != plain.lean.trusted_packages
    for digest in (rules_digest, verdict_digest, check_digest):
        assert digest(listed) == digest(plain)                       # no receipt goes stale
    roots = parse_config(Path("/x"), '[lean]\nroots = ["B"]\n')
    assert rules_digest(roots) != rules_digest(plain)                # control: the digest does read the rules


def test_the_machine_declares_its_sandbox_class():
    assert machine.load().sandbox_class == "workstation"                                   # the default
    assert disposable().sandbox_class == "disposable"
    path = machine.config_path()
    for text, message in (('[sandbox]\nclass = "cloud"\n', "must be one of"), ("[sandbox]\nkind = 1\n", "unknown")):
        path.write_text(text)
        machine.forget()
        with pytest.raises(machine.MachineError, match=message):
            machine.load()


# The adapter: before anything is read or built -------------------------------------------------------------------

def project(tmp_path: Path, *extra: tuple[str, str], toml: str = "") -> Path:
    """The Lean fixture on the trusted branch, with a manifest of the Mathlib-only project plus `extra`."""
    root = make_repo(tmp_path)
    git(root, "checkout", "-q", "trusted")
    (root / "lake-manifest.json").write_bytes(manifest(*extra))
    if toml:
        (root / "research" / "vl.toml").write_text((root / "research" / "vl.toml").read_text() + toml)
    commit_all(root, "manifest")
    return root


def check(root: Path, assurance: str = "protected", guards=()):
    return LeanComparatorAdapter().check(request(root, "double-even", assurance=assurance, guards=guards))


def refused(outcome) -> bool:
    return outcome.verdict == "unsupported" and GATE in " ".join(outcome.reasons)


def test_a_check_with_a_third_party_package_is_unsupported_on_a_workstation(tmp_path):
    workstation()
    outcome = check(project(tmp_path, THIRD))
    assert refused(outcome) and "PrimeCert" in outcome.reasons[0] and "disposable" in outcome.reasons[0]
    assert outcome.command == [] and outcome.files == {}       # stopped before a file was read: no receipt


def test_the_same_project_with_only_mathlib_is_not_stopped_by_the_gate(tmp_path):
    workstation()
    outcome = check(project(tmp_path))                          # control; it stops later, on a missing cache or tool
    assert not refused(outcome)


def test_a_disposable_machine_goes_on_and_the_gate_off_lets_the_workstation_through(tmp_path):
    root = project(tmp_path, THIRD)
    disposable()
    assert not refused(check(root))
    workstation()
    assert refused(check(root))
    assert not refused(check(root, guards={"package_gate"}))     # the guard is what stopped it


def test_a_package_the_trusted_ref_lists_by_name_is_let_through(tmp_path):
    workstation()
    listed = ", ".join(json.dumps(name) for name, _ in OFFICIAL)
    covered = project(tmp_path / "a", THIRD, toml=f'trusted_packages = [{listed}, "PrimeCert"]\n')
    assert not refused(check(covered))
    assert refused(check(project(tmp_path / "b", THIRD, toml=f"trusted_packages = [{listed}]\n")))      # control


def test_an_impostor_with_a_trusted_name_is_stopped(tmp_path):
    workstation()
    official = [p for p in OFFICIAL if p[0] != "Cli"]
    root = make_repo(tmp_path)
    git(root, "checkout", "-q", "trusted")
    (root / "lake-manifest.json").write_bytes(manifest(SPOOF, official=official))
    commit_all(root, "manifest")
    outcome = check(root)
    assert refused(outcome) and "Cli" in outcome.reasons[0]


def test_an_exploratory_check_is_gated_by_the_manifest_it_would_build_and_by_the_trusted_one(tmp_path):
    workstation()
    root = project(tmp_path)
    assert not refused(check(root, "exploratory"))               # control
    (root / "lake-manifest.json").write_bytes(manifest(THIRD))    # the worktree adds a package, uncommitted
    assert refused(check(root, "exploratory"))
    assert not refused(check(root, "protected"))                 # a protected check never reads the worktree's
    (root / "lake-manifest.json").write_bytes(manifest())
    (root / "research" / "vl.toml").write_text(                  # a worktree vl.toml cannot widen the trust
        (root / "research" / "vl.toml").read_text() + 'trusted_packages = ["PrimeCert"]\n')
    assert not refused(check(root, "exploratory"))
    git(root, "checkout", "-q", "--", ".")
    root2 = project(tmp_path / "t", THIRD)
    (root2 / "research" / "vl.toml").write_text(
        (root2 / "research" / "vl.toml").read_text() + 'trusted_packages = ["PrimeCert", "mathlib"]\n')
    assert refused(check(root2, "exploratory"))


def test_vl_check_exits_with_unsupported_and_writes_no_receipt(tmp_path, monkeypatch, capsys):
    workstation()
    root = project(tmp_path, THIRD)
    monkeypatch.chdir(root)
    assert cli.main(["check", "double-even", "--json"]) == 3
    out = json.loads(capsys.readouterr().out)
    assert out["verdict"] == "unsupported" and GATE in out["reasons"][0] and out["receipt"] is None
    assert not (root / "research" / "evidence").exists()


# The commands that build nothing themselves ------------------------------------------------------------------------

def test_vl_lane_new_is_refused_on_a_workstation_before_it_makes_anything(research_repo, monkeypatch, capsys):
    workstation()
    (research_repo / "lake-manifest.json").write_bytes(manifest(THIRD))
    commit_all(research_repo, "third party")
    monkeypatch.chdir(research_repo)
    assert cli.main(["lane", "new", "la"]) == 2
    assert "unsupported" in capsys.readouterr().err
    assert not (research_repo.parent / ".vl-lanes").exists() and "lane/la" not in git(research_repo, "branch")
    disposable()                                                  # the same project on a disposable machine
    assert cli.main(["lane", "new", "la"]) == 0
    assert "lane/la" in git(research_repo, "branch")


def test_vl_lane_new_with_only_mathlib_goes_on(research_repo, monkeypatch):
    workstation()
    (research_repo / "lake-manifest.json").write_bytes(manifest())
    commit_all(research_repo, "mathlib only")
    monkeypatch.chdir(research_repo)
    assert cli.main(["lane", "new", "la"]) == 0


def test_a_manifest_that_is_only_in_the_worktree_does_not_gate_a_lane(research_repo, monkeypatch):
    workstation()
    (research_repo / "lake-manifest.json").write_bytes(manifest(THIRD))      # uncommitted: not on the trusted ref
    monkeypatch.chdir(research_repo)
    assert cli.main(["lane", "new", "la"]) == 0


def test_vl_import_is_refused_on_a_workstation_and_writes_nothing(research_repo, monkeypatch, capsys, tmp_path):
    workstation()
    (research_repo / "lake-manifest.json").write_bytes(manifest(THIRD))
    commit_all(research_repo, "third party")
    challenges = tmp_path / "challenges"
    challenges.mkdir()
    monkeypatch.chdir(research_repo)
    assert cli.main(["import", "comparator", str(challenges)]) == 2
    err = capsys.readouterr().err
    assert "unsupported" in err and "PrimeCert" in err
    assert cli.main(["import", "comparator", str(challenges), "--trusted-ref", "trusted"]) == 2
    disposable()
    assert cli.main(["import", "comparator", str(challenges)]) == 0


def test_vl_import_with_only_mathlib_goes_on(research_repo, monkeypatch, tmp_path):
    workstation()
    (research_repo / "lake-manifest.json").write_bytes(manifest())
    commit_all(research_repo, "mathlib only")
    challenges = tmp_path / "challenges"
    challenges.mkdir()
    monkeypatch.chdir(research_repo)
    assert cli.main(["import", "comparator", str(challenges)]) == 0
