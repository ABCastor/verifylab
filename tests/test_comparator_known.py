"""Known-answer gate from Comparator's own test suite (fixtures/comparator-known/: Apache-2.0, rev 19e111e).

Every project must give, through vl, the exit code its test.json expects, and vl must read it as a pass or as
the candidate's failure for the cause the project plants (an `error` would mean vl misreads Comparator).

Ten projects fit vl's item format and run through the whole adapter: trusted target, generated check project,
jail, isolation probe, Comparator, classification. The other ten use what an item cannot express (definition
holes, `prelude` modules, «»-quoted names, a target theorem that is not `sorry`, two external kernels): they run
through the adapter's own jail, isolation probe, Comparator runner and classifier, on the project laid out as
Comparator's test runner lays it out (runtests.lean: copy, add lean-toolchain, default lakefile if absent).
In both paths a kernel command `nanoda_bin`, and the legacy `enable_nanoda`, become an `external_kernels` entry
with the resolved nanoda binary, which is how vl registers nanoda.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from lean_helpers import TOOLCHAIN, explain, git, lean_unavailable, request, tool_env
from verifylab.adapters.lean_comparator import (
    NANODA_KERNEL, TOOLS, LeanComparatorAdapter, classify, comparator_jail, landlock_probe, resolve_tool,
    run_comparator, toolchain_dir,
)

KNOWN = Path(__file__).resolve().parents[1] / "fixtures" / "comparator-known"
LEAN_SKIP = lean_unavailable()

# project -> (vl verdict, fragment its reason must contain). The verdict follows vl's rule: `fail` only when the
# failure is certainly the candidate's. Comparator rejects char_ofnat_issue and primitive_issue at the export (their
# `prelude` solutions lack the builtin String.mk), and quot_mismatch because its own `prelude` challenge lacks Nat:
# vl reads those as `error`, never as a pass.
THROUGH_ADAPTER = {
    "olean_issue": ("fail", "the Lean default kernel rejected the solution: while replaying declaration 'boom'"),
    "opaque_value": ("fail", "the solution uses axiom 'f'"),
    "simple_axiom_issue": ("fail", "the solution uses axiom 'helper'"),
    "simple_kind_mismatch": ("fail", "the solution uses axiom 'helper'"),   # same files as simple_axiom_issue
    "simple_match": ("pass", "Comparator accepted the solution"),
    "simple_mismatch": ("fail", "'comm' is not a theorem in the solution"),
    "simple_nanoda": ("pass", "kernels lean, nanoda"),
    "simple_nanoda_compat": ("pass", "kernels lean, nanoda"),
    "simple_nanoda_mismatch": ("fail", "'comm' is not a theorem in the solution"),
    "theorem_hole_issue": ("pass", "2 theorem(s)"),
}
THROUGH_RUNNER = {
    "char_ofnat_issue": ("error", "the solution's environment lacks 'String.mk'"),
    "def_hole": ("pass", "Comparator accepted the solution"),
    "def_hole_axiom_issue": ("fail", "contains `sorry`"),
    "def_hole_kind_mismatch": ("fail", "'n' is not a definition in the solution"),
    "def_hole_type_mismatch": ("fail", "declaration 'n', used by the target statement, differs"),
    "numeric_namespace": ("pass", "Comparator accepted the solution"),
    "primitive_issue": ("error", "the solution's environment lacks 'String.mk'"),
    "proj_trick": ("fail", "declaration 'S.mk', used by the target statement, differs"),
    "quot_mismatch": ("error", "'Nat' does not exist in the challenge environment"),
    "simple_multi_nanoda": ("pass", "Comparator accepted the solution"),
}
# runtests.lean's lakefile for projects that bring none
DEFAULT_LAKEFILE = 'name = "comparatortest"\nversion = "0.1.0"\n\n[[lean_lib]]\nname = "Solution"\n\n' \
                   '[[lean_lib]]\nname = "Challenge"\n'


@pytest.fixture
def lean_env(monkeypatch):
    if LEAN_SKIP:
        pytest.skip(LEAN_SKIP)
    for key, value in tool_env().items():
        monkeypatch.setenv(key, value)


def expected_exit(name: str) -> int:
    return json.loads((KNOWN / name / "test.json").read_text())["exit_code"]


def config_of(name: str) -> dict:
    return json.loads((KNOWN / name / "config.json").read_text())


def vl_kernels(config: dict, nanoda: Path) -> dict[str, list[str]]:
    """The project's external kernels as vl registers them: `nanoda_bin` resolved, `enable_nanoda` translated."""
    kernels = dict(config.get("external_kernels") or {})
    if config.get("enable_nanoda"):
        kernels[NANODA_KERNEL] = ["nanoda_bin"]
    return {name: [str(nanoda) if arg == "nanoda_bin" else arg for arg in argv] for name, argv in kernels.items()}


def test_every_vendored_project_is_run_exactly_once():
    projects = sorted(p.name for p in KNOWN.iterdir() if p.is_dir())
    assert len(projects) == 20 and sorted([*THROUGH_ADAPTER, *THROUGH_RUNNER]) == projects
    assert not set(THROUGH_ADAPTER) & set(THROUGH_RUNNER)
    for name in projects:
        assert ((THROUGH_ADAPTER | THROUGH_RUNNER)[name][0] == "pass") == (expected_exit(name) == 0), name
    assert (KNOWN / "LICENSE").read_text().lstrip().startswith("Apache License")
    assert "19e111e" in (KNOWN / "NOTICE").read_text()


def _adapter_repo(tmp_path: Path, name: str, nanoda: bool) -> tuple[Path, str]:
    """A research repo whose trusted target is the project's Challenge.lean and whose solution module
    `KA.Solution` is its Solution.lean, everything committed on `trusted`."""
    config = config_of(name)
    item_id = name.replace("_", "-")
    root = tmp_path / "repo"
    for folder in ("KA", "research/targets", "research/items"):
        (root / folder).mkdir(parents=True)
    (root / "lean-toolchain").write_text(TOOLCHAIN + "\n")
    (root / "lakefile.toml").write_text('name = "known"\nversion = "0.1.0"\n\n[[lean_lib]]\nname = "KA"\n')
    shutil.copy(KNOWN / name / "Solution.lean", root / "KA" / "Solution.lean")
    shutil.copy(KNOWN / name / "Challenge.lean", root / "research" / "targets" / f"{item_id}.lean")
    (root / "research" / "vl.toml").write_text(
        '[project]\nname = "known"\n\n[lean]\nroots = ["KA"]\n'
        f'permitted_axioms = {json.dumps(config["permitted_axioms"])}\nexternal_kernels = {str(nanoda).lower()}\n')
    (root / "research" / "items" / f"{item_id}.md").write_text(
        f'+++\nid = "{item_id}"\nkind = "result"\ntitle = "Comparator known answer {name}"\n'
        f'author = "human:comparator"\ncreated = "2026-10-01"\nstatement = "Comparator tests/projects/{name}"\n'
        f'claim = "formal"\n\n[lean]\ntarget = "research/targets/{item_id}.lean"\n'
        f'theorems = {json.dumps(config["theorem_names"])}\nsolution = "KA.Solution"\n+++\n')
    git(root, "init", "-q", "-b", "trusted")
    git(root, "config", "vl.trustedRef", "trusted")
    git(root, "-c", "user.name=T", "-c", "user.email=t@e", "add", "-A")
    git(root, "-c", "user.name=T", "-c", "user.email=t@e", "commit", "-q", "-m", name)
    return root, item_id


@pytest.mark.lean
@pytest.mark.parametrize("name", sorted(THROUGH_ADAPTER))
def test_known_answer_through_the_adapter(tmp_path, lean_env, name):
    config = config_of(name)
    nanoda = bool(vl_kernels(config, Path("nanoda_bin")))
    if nanoda and "COMPARATOR_NANODA" not in tool_env():
        pytest.skip("nanoda not available")
    root, item_id = _adapter_repo(tmp_path, name, nanoda)
    outcome = LeanComparatorAdapter().check(request(root, item_id))
    verdict, fragment = THROUGH_ADAPTER[name]
    assert f"--- exit {expected_exit(name)} ---" in outcome.log, explain(outcome)
    assert outcome.verdict == verdict and fragment in " ".join(outcome.reasons), explain(outcome)
    assert outcome.checked["kernels"] == ["lean", *([NANODA_KERNEL] if nanoda else [])]
    assert outcome.environment["isolation"]["kind"] == "bwrap+landrun"


@pytest.mark.lean
@pytest.mark.parametrize("name", sorted(THROUGH_RUNNER))
def test_known_answer_through_the_adapter_runner(tmp_path, lean_env, name):
    work = tmp_path / "work"
    project = work / "project"
    shutil.copytree(KNOWN / name, project)
    (project / "lean-toolchain").write_text(TOOLCHAIN + "\n")
    if not (project / "lakefile.toml").exists():
        (project / "lakefile.toml").write_text(DEFAULT_LAKEFILE)
    tools = {tool: resolve_tool(tool, {}, tmp_path) for tool in TOOLS}
    config = config_of(name)
    kernels = vl_kernels(config, tools["nanoda"] or Path("nanoda_bin"))
    if kernels and tools["nanoda"] is None:
        pytest.skip("nanoda not available")
    config.pop("enable_nanoda", None)
    config.pop("external_kernels", None)
    if kernels:
        config["external_kernels"] = kernels
    used = {tool: path for tool, path in tools.items() if path is not None and (tool != "nanoda" or kernels)}
    box = comparator_jail(work, toolchain_dir(TOOLCHAIN), used, None)
    assert landlock_probe(box, used["landrun"], work / "probe", "12G", None).enforced
    run = run_comparator(box, used["comparator"], project, config, work / "comparator.json",
                         timeout=900, memory_max="12G", memory_total=None)
    result = classify(run.stdout, run.stderr, run.returncode, tuple(config["theorem_names"]),
                      ("lean", *sorted(kernels)), config["challenge_module"], config["solution_module"])
    verdict, fragment = THROUGH_RUNNER[name]
    detail = f"{result}\n--- stdout\n{run.stdout[-2000:]}\n--- stderr\n{run.stderr[-2000:]}"
    assert run.returncode == expected_exit(name), detail
    assert result.verdict == verdict and fragment in result.reason, detail
