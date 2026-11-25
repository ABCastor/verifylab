"""Multi-root removal view: counterfactual deletion on small graphs, fail-closed inputs and elaboration hazards."""
import gzip
import itertools
import json
from pathlib import Path

import pytest

from conftest import commit_all
from test_foreign import foreign, output
from verifylab.cli import main
from verifylab.removal import command_units, owners, profile


def reach(graph, roots, missing=None):
    found, stack = set(), list(roots)
    while stack:
        node = stack.pop()
        if node == missing or node in found:
            continue
        found.add(node)
        stack.extend(graph[node])
    return found


def test_every_three_vertex_digraph_against_deletion():
    names = ["a", "b", "c"]
    edges = list(itertools.permutations(names, 2))
    spans = {"a": ("M", 1, 1), "b": ("M", 2, 4), "c": ("M", 2, 4)}
    weights = {s: s[2] - s[1] + 1 for s in spans.values()}
    for mask in range(1 << len(edges)):
        graph = {n: set() for n in names}
        for i, (u, v) in enumerate(edges):
            if mask & (1 << i):
                graph[u].add(v)
        for roots in [("a",), ("a", "b"), ("a", "a")]:
            before = reach(graph, roots)
            base = {spans[n] for n in before}
            result, total, _ = profile({n: graph[n] for n in before}, roots, spans, weights)
            assert total == sum(weights[k] for k in base)
            for n in before:
                after = reach(graph, roots, n)
                lost = base - {spans[d] for d in after}
                assert result[n][:2] == (sum(weights[k] for k in lost), len(before - after))


def test_shared_dependency_and_unlocated_name():
    graph = {"a": {"hub"}, "b": {"hub"}, "hub": {"leaf"}, "leaf": set()}
    result, total, _ = profile(graph, ["a", "b"], {"leaf": ("M", 10, 14)}, {("M", 10, 14): 5})
    assert (result["hub"][:4], result["leaf"][:4], total) == ((5, 2, 1, 2), (5, 1, 2, 1), 5)
    assert result["a"][:2] == (0, 1) and result["hub"][4] is None and result["leaf"][4] == "hub"


def test_overlapping_ranges_form_one_unit_and_generated_names_inherit():
    units, direct = command_units({"M": [(3, 7, "A", 4), (6, 9, "B", 6), (12, 12, "C", 12)]})
    assert set(units) == {("M", 3, 9), ("M", 12, 12)}
    owner = owners(["A", "B", "A.eq_1", "_private.M.0.C.proof_1", "D"], {n: "M" for n in
                   ["A", "B", "A.eq_1", "_private.M.0.C.proof_1", "D"]}, direct)
    assert owner == {"A": ("M", 3, 9), "B": ("M", 3, 9), "A.eq_1": ("M", 3, 9),
                     "_private.M.0.C.proof_1": ("M", 12, 12)}


def write_gz(path, text):
    path.write_bytes(gzip.compress(text.encode()))
    return path


DEPS = ("DEP\tthm\tTiny\tTiny.conclusion\t-\tTiny.step Tiny.conclusion.proof_1\t\n"
        "DEP\tthm\tTiny\tTiny.conclusion.proof_1\t-\tTiny.step\t\n"
        "DEP\tthm\tTiny.Step\tTiny.step\t-\t\t\n"
        "DEP\tthm\tTiny.Step\tTiny.other\t-\tTiny.step\t\n"
        "DEP\tthm\tTiny.Step\tTiny.unlocated\t-\t\t\n")
RANGES = ("R\tTiny\tTiny.conclusion\t5\t0\t6\t26\t5\n"
          "R\tTiny.Step\tTiny.step\t3\t0\t3\t40\t3\n"
          "R\tTiny.Step\tTiny.other\t5\t0\t5\t40\t5\n"
          "T\tTiny\tTiny.conclusion\t1\t\n")


@pytest.fixture
def removal_project(foreign, capsys):
    step = foreign / "Tiny/Step.lean"
    step.write_text("namespace Tiny\n\ntheorem step (p : Prop) (h : p) : p := h\n\n"
                    "theorem other (p : Prop) (h : p) : p := step p h\n\n"
                    "theorem unlocated (p : Prop) (h : p) : p := h\n\nend Tiny\n")
    commit_all(foreign, "two roots")
    output(capsys, "import", foreign)
    return write_gz(Path("deps.tsv.gz"), DEPS), write_gz(Path("ranges.gz"), RANGES)


def test_two_roots_charge_shared_lines_once(removal_project, capsys):
    deps, ranges = removal_project
    args = ["value", "vl.lock", "--deps", deps, "--removal", ranges, "--root", "Tiny.conclusion", "--root", "Tiny.other"]
    data = output(capsys, *args)
    rows = {r["name"]: r for r in data["rows"]}
    meta = data["removal"]
    assert meta["schema"] == "vl.removal/1" and meta["roots"] == ["Tiny.conclusion", "Tiny.other"]
    assert meta["union_located_lines"] == 2 + 1 + 1
    assert meta["per_root"]["Tiny.conclusion"] == {"names": 3, "command_lines": 3}
    assert rows["Tiny.step"]["exclusive_source_lines"] == 1 and rows["Tiny.step"]["reachable_users"] == 3
    assert rows["Tiny.step"]["roots"] == ["Tiny.conclusion", "Tiny.other"]
    assert rows["Tiny.conclusion.proof_1"]["first_line"] == 5 and not rows["Tiny.conclusion.proof_1"]["indexed"]
    assert rows["Tiny.conclusion.proof_1"]["exclusive_source_lines"] == 0
    assert rows["Tiny.conclusion"]["exclusive_source_lines"] == 2
    # The flat TSV view carries the same rows.
    assert main([*map(str, args)]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[0].startswith("# removal view") and lines[1].split("\t")[:3] == [
        "name", "kind", "exclusive_source_lines"]
    assert len(lines) == 2 + len(data["rows"])


def test_selected_value_rows_keep_joint_metrics_and_report_each_root_distance(removal_project, capsys):
    deps, ranges = removal_project
    args = ["value", "vl.lock", "--deps", str(deps), "--removal", str(ranges),
            "--root", "Tiny.conclusion", "--root", "Tiny.other"]
    full = output(capsys, *args)
    shortlist = ["Tiny.conclusion.proof_1", "Tiny.step"]
    chosen = [*args, "--select", shortlist[0], "--select", shortlist[1]]
    data = output(capsys, *chosen)
    assert data["selected"] == shortlist and "elaboration_only" not in data
    assert data["removal"] == full["removal"] and data["graph"] == full["graph"]
    assert [row["name"] for row in data["rows"]] == shortlist
    proof, step = data["rows"]
    assert proof["root_distances"] == {"Tiny.conclusion": 1, "Tiny.other": None}
    assert step["root_distances"] == {"Tiny.conclusion": 1, "Tiny.other": 1}
    assert (step["path"], step["first_line"], step["last_line"]) == ("Tiny/Step.lean", 3, 3)
    assert step["exclusive_source_lines"] == 1 and step["reachable_users"] == 3
    # The other root can reach the step but only over its generated proof in this graph.
    deps.write_bytes(gzip.compress(DEPS.replace("-\tTiny.step Tiny.conclusion.proof_1\t",
                                                "-\tTiny.conclusion.proof_1\t").encode()))
    data = output(capsys, *chosen)
    assert data["rows"][1]["root_distances"] == {"Tiny.conclusion": 2, "Tiny.other": 1}
    assert main(chosen) == 0
    assert "Tiny.conclusion=2, Tiny.other=1" in capsys.readouterr().out
    assert main([*args, "--select", "Tiny.unlocated", "--json"]) == 2
    error = capsys.readouterr()
    assert not error.out and "outside the reached local graph: Tiny.unlocated" in error.err


def test_unknown_span_counts_as_a_name_with_zero_lines(removal_project, capsys):
    deps, ranges = removal_project
    data = output(capsys, "value", "vl.lock", "--deps", deps, "--removal", ranges, "--root", "Tiny.unlocated")
    assert data["rows"] == [{**data["rows"][0], "name": "Tiny.unlocated", "exclusive_source_lines": 0,
                             "exclusive_kernel_names": 1, "first_line": None, "last_line": None}]
    assert data["removal"]["unlocated_names_in_cone"] == 1
    data = output(capsys, "value", "vl.lock", "--deps", deps, "--root", "Tiny.conclusion", "--root", "Tiny.other")
    assert data["removal"]["union_located_lines"] is None
    assert all(r["exclusive_source_lines"] is None for r in data["rows"])


@pytest.mark.parametrize("extra,root,message", [
    ("", "Tiny.missing", "no unique supported source declaration"),
    ("DEP\tthm\tTiny.Step\tTiny.step\t-\tTiny.other\t\n", "Tiny.other", "conflicting rows for reached constant Tiny.step"),
])
def test_missing_or_ambiguous_roots_fail_closed(removal_project, capsys, extra, root, message):
    deps, ranges = removal_project
    write_gz(deps, DEPS + extra)
    code = main(["value", "vl.lock", "--deps", str(deps), "--removal", str(ranges), "--root", "Tiny.conclusion",
                 "--root", root])
    assert code == 2 and message in capsys.readouterr().err


def test_stale_ranges_and_missing_graph_fail_closed(removal_project, capsys):
    deps, ranges = removal_project
    write_gz(ranges, RANGES.replace("Tiny.other\t5\t0\t5", "Tiny.other\t5\t0\t50"))
    assert main(["value", "vl.lock", "--deps", str(deps), "--removal", str(ranges), "--root", "Tiny.other"]) == 2
    assert "ranges do not match the pinned source" in capsys.readouterr().err
    write_gz(ranges, RANGES.replace("Tiny.other\t5\t0\t5\t40\t5", "Tiny.other\t1\t0\t1\t40\t1"))
    assert main(["value", "vl.lock", "--deps", str(deps), "--removal", str(ranges), "--root", "Tiny.other"]) == 2
    assert "starts at Tiny/Step.lean:5" in capsys.readouterr().err
    assert main(["value", "vl.lock", "--root", "Tiny.conclusion", "--root", "Tiny.other"]) == 2
    assert "needs --deps" in capsys.readouterr().err


def test_single_root_value_is_unchanged(removal_project, capsys):
    deps, _ = removal_project
    data = output(capsys, "value", "vl.lock", "--deps", deps, "--root", "Tiny.conclusion")
    assert "removal" not in data and data["root"] == "Tiny.conclusion"
    assert set(data["rows"][0]) == {"name", "level", "users", "demand50", "lines", "signature_chars", "edges_observed"}


def test_order_by_removal_is_opt_in(removal_project, capsys):
    deps, ranges = removal_project
    base = ["order", "vl.lock", "--deps", deps, "--root", "Tiny.conclusion", "--root", "Tiny.other"]
    assert main([*map(str, base)]) == 2 and "--aim simplify --rank-by removal" in capsys.readouterr().err
    data = output(capsys, *base, "--removal", ranges, "--aim", "simplify", "--rank-by", "removal")
    assert data["ranked_by"] == "exclusive_source_lines" and "opt-in" in data["ranking_status"]
    assert [s["name"] for s in data["sources"]] == ["Tiny.conclusion", "Tiny.other", "Tiny.step"]
    data = output(capsys, *base, "--aim", "simplify", "--rank-by", "removal")
    assert data["ranked_by"] == "exclusive_kernel_names" and "no compiler ranges" in data["ranking_reason"]
    plain = output(capsys, "order", "vl.lock", "--root", "Tiny.conclusion", "--aim", "simplify")
    assert plain["ranked_by"] == "lines"


HAZARDS = """import Tiny.Step

namespace Tiny

local notation "bdD" => True

syntax "cite!" ident : term

set_option linter.style.longLine true

@[simp]
theorem dpow_zero (p : Prop) : (p ∧ True) = p := by simp

attribute [simp] take_nil

theorem take_nil : True := trivial

theorem jacobian_apply (p : Prop) (h : p) : p := h

theorem conclusion (p : Prop) (certificate : p) : p := by
  simp [jacobian_apply]
  exact step p certificate

end Tiny
"""


def test_elaboration_only_dependencies_are_reported_before_pruning(foreign, capsys):
    (foreign / "Tiny.lean").write_text(HAZARDS)
    commit_all(foreign, "elaboration-only dependencies")
    output(capsys, "import", foreign)
    deps = write_gz(Path("deps.tsv.gz"),
                    "DEP\tthm\tTiny\tTiny.conclusion\t-\tTiny.step\t\n"
                    "DEP\tthm\tTiny\tTiny.dpow_zero\t-\t\t\n"
                    "DEP\tthm\tTiny\tTiny.take_nil\t-\t\t\n"
                    "DEP\tthm\tTiny\tTiny.jacobian_apply\t-\t\t\n"
                    "DEP\tthm\tTiny.Step\tTiny.step\t-\t\t\n")
    report = output(capsys, "value", "vl.lock", "--deps", deps, "--root", "Tiny.conclusion",
                    "--root", "Tiny.step")["elaboration_only"]
    found = {(r["kind"], r["name"], r["detail"]) for r in report["rows"]}
    assert ("syntax", None, "notation") in found and ("syntax", None, "syntax") in found
    assert ("linter option", "linter.style.longLine", "set_option") in found
    assert ("attribute", "Tiny.dpow_zero", "simp") in found and ("attribute", "Tiny.take_nil", "simp") in found
    assert ("source reference", "Tiny.jacobian_apply", "named in Tiny.conclusion") in found
    assert report["counts"] == {"attribute": 2, "linter option": 1, "source reference": 1, "syntax": 2}
    # A simp lemma inside the cone is no hazard.
    write_gz(deps, gzip.decompress(deps.read_bytes()).decode().replace(
        "Tiny.conclusion\t-\tTiny.step", "Tiny.conclusion\t-\tTiny.step Tiny.dpow_zero Tiny.jacobian_apply"))
    report = output(capsys, "value", "vl.lock", "--deps", deps, "--root", "Tiny.conclusion",
                    "--root", "Tiny.step")["elaboration_only"]
    assert {r["name"] for r in report["rows"] if r["kind"] != "syntax"} == {"Tiny.take_nil", "linter.style.longLine"}


# Blocks and the difference between two versions ----------------------------------------------------------------------

TWO_ROOTS = ["--root", "Tiny.conclusion", "--root", "Tiny.other"]
BLOCKS = "# two blocks\nsteps\tTiny\\.Step$\n\nsteps\tNever\\.There\ntop\t^Tiny$\n"


def view(removal_project, *extra):
    deps, ranges = removal_project
    return ["value", "vl.lock", "--deps", str(deps), "--removal", str(ranges), *TWO_ROOTS, *extra]


def test_blocks_sum_located_lines_once_and_the_first_match_wins(removal_project, capsys):
    Path("blocks.tsv").write_text(BLOCKS)
    data = output(capsys, *view(removal_project, "--blocks", "blocks.tsv"))
    assert data["blocks"]["rows"] == [{"block": "steps", "lines": 2, "names": 2, "modules": 1},
                                      {"block": "top", "lines": 2, "names": 2, "modules": 1}]
    assert data["blocks"]["unmatched"] == []
    assert data["blocks"]["total_lines"] == data["removal"]["union_located_lines"] == 4
    assert len(data["rows"]) == 4                              # --json keeps the flat rows beside the blocks
    # Planted: an earlier line that also matches takes the module; the later block keeps nothing.
    Path("blocks.tsv").write_text("all\tTiny\n" + BLOCKS)
    rows = output(capsys, *view(removal_project, "--blocks", "blocks.tsv"))["blocks"]["rows"]
    assert [(r["block"], r["lines"]) for r in rows] == [("all", 4), ("steps", 0), ("top", 0)]
    # A module that matches no block is listed with its lines, and the total still counts it.
    Path("blocks.tsv").write_text("steps\tTiny\\.Step$\n")
    data = output(capsys, *view(removal_project, "--blocks", "blocks.tsv"))
    assert data["blocks"]["unmatched"] == [{"module": "Tiny", "lines": 2, "names": 2}]
    assert data["blocks"]["total_lines"] == 4
    assert main(view(removal_project, "--blocks", "blocks.tsv")) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[1:] == ["block\tlines\tnames\tmodules", "steps\t2\t2\t1", "(no block)\t2\t2\t1", "TOTAL\t4\t4\t2",
                         "# no block: Tiny\t2"]


@pytest.mark.parametrize("text,message", [
    ("steps Tiny\\.Step$\n", "blocks.tsv:1: expected a block name, a tab and a regular expression"),
    ("steps\tTiny\\.Step$\ntop\t(Tiny\n", "blocks.tsv:2: "),
    ("steps\tTiny\tStep\n", "blocks.tsv:1: expected a block name, a tab and a regular expression"),
    ("\tTiny\n", "blocks.tsv:1: expected a block name, a tab and a regular expression"),
    ("# nothing\n\n", "blocks.tsv: no block"),
    ("(no block)\tTiny\n", "names the modules that match no block"),
])
def test_a_block_file_that_cannot_be_read_as_blocks_is_refused(removal_project, capsys, text, message):
    Path("blocks.tsv").write_text(text)
    assert main(view(removal_project, "--blocks", "blocks.tsv")) == 2
    assert message in capsys.readouterr().err


def later_version(root, capsys):
    """A second pin: one theorem more under `other`, one generated name less under `conclusion`."""
    step = root / "Tiny/Step.lean"
    step.write_text(step.read_text().replace("end Tiny", "theorem extra (p : Prop) (h : p) : p := h\n\nend Tiny"))
    commit_all(root, "a later version")
    output(capsys, "import", root, "--lock", "later.lock")
    deps = write_gz(Path("later-deps.tsv.gz"), DEPS.replace("Tiny.step Tiny.conclusion.proof_1", "Tiny.step")
                    .replace("Tiny.other\t-\tTiny.step", "Tiny.other\t-\tTiny.step Tiny.extra")
                    + "DEP\tthm\tTiny.Step\tTiny.extra\t-\t\t\n")
    ranges = write_gz(Path("later-ranges.gz"), RANGES + "R\tTiny.Step\tTiny.extra\t9\t0\t9\t40\t9\n")
    return ["value", "later.lock", "--deps", str(deps), "--removal", str(ranges), *TWO_ROOTS]


def test_difference_between_two_versions_by_block_and_by_module_prefix(removal_project, foreign, capsys):
    assert main([*view(removal_project), "--json"]) == 0
    Path("before.json").write_text(capsys.readouterr().out)
    later = later_version(foreign, capsys)
    change = output(capsys, *later, "--against", "before.json")["against"]
    assert change["by"] == "module prefix of 3 components"
    assert change["rows"] == [{"group": "Tiny", "lines_before": 4, "lines_after": 5, "names_added": 1,
                               "names_removed": 1, "difference": 1}]
    assert change["total"] == {"lines_before": 4, "lines_after": 5, "difference": 1, "names_added": 1,
                               "names_removed": 1}
    Path("blocks.tsv").write_text(BLOCKS + "gone\tOnly\\.Before$\n")
    data = output(capsys, *later, "--against", "before.json", "--blocks", "blocks.tsv")
    assert data["against"]["by"] == "block" and data["against"]["prior_commit"] != data["commit"]
    assert [(r["group"], r["lines_before"], r["lines_after"], r["difference"], r["names_added"], r["names_removed"])
            for r in data["against"]["rows"]] == [("steps", 2, 3, 1, 1, 0), ("top", 2, 2, 0, 0, 1), ("gone", 0, 0, 0, 0, 0)]
    assert data["blocks"]["total_lines"] == data["against"]["total"]["lines_after"] == 5
    assert main([*later, "--against", "before.json", "--blocks", "blocks.tsv"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[2:] == ["block\tlines_before\tlines_after\tdifference\tnames_added\tnames_removed",
                         "steps\t2\t3\t+1\t1\t0", "top\t2\t2\t+0\t0\t1", "gone\t0\t0\t+0\t0\t0", "TOTAL\t4\t5\t+1\t1\t1"]
    # A module that only the earlier version reached, and that matches no block, is still counted and named.
    Path("blocks.tsv").write_text("steps\tTiny\\.Step$\n")
    change = output(capsys, *later, "--against", "before.json", "--blocks", "blocks.tsv")["against"]
    assert change["rows"][-1] == {"group": "(no block)", "lines_before": 2, "lines_after": 2, "names_added": 0,
                                  "names_removed": 1, "difference": 0}
    assert change["unmatched_before"] == change["unmatched_after"] == ["Tiny"]


def test_difference_refuses_a_prior_it_cannot_compare(removal_project, foreign, capsys):
    deps, ranges = removal_project
    assert main([*view(removal_project), "--json"]) == 0
    whole = capsys.readouterr().out
    Path("before.json").write_text(whole)
    later = later_version(foreign, capsys)
    assert main([*later, "--against", "before.json"]) == 0            # control
    capsys.readouterr()

    def refusal(prior, command=later):
        Path("prior.json").write_text(prior if isinstance(prior, str) else json.dumps(prior))
        assert main([*command, "--against", "prior.json"]) == 2
        return capsys.readouterr().err

    data = json.loads(whole)
    assert "measures other roots (Tiny.conclusion, Tiny.other)" in refusal(
        whole, [a for a in later if a != "Tiny.other"][:-1])
    for roots in (42, ["Tiny.conclusion", 1]):
        assert "expected a list of root names" in refusal({**data, "removal": {**data["removal"], "roots": roots}})
    assert "missing root statement identities" in refusal(
        {**data, "removal": {k: v for k, v in data["removal"].items() if k != "root_statements"}})
    assert "do not add up to its 4 reached names and 4 located lines" in refusal({**data, "rows": data["rows"][1:]})
    moved = [{**row, "last_line": row["last_line"] + 1} if row["name"] == "Tiny.step" else row for row in data["rows"]]
    assert "do not add up to its 4 reached names and 4 located lines" in refusal({**data, "rows": moved})
    unlocated = {**data, "removal": {**data["removal"], "union_located_lines": None}}
    assert "has no located lines; repeat it with --removal" in refusal(unlocated)
    assert "expected the --json output of vl value with --removal" in refusal({"schema": "vl.output/1", "rows": []})
    assert "expected the --json output of vl value with --removal" in refusal(
        {**data, "removal": {**data["removal"], "schema": "vl.removal/0"}})
    assert "expected the --json output of vl value with --removal" in refusal(
        {**data, "rows": [{**data["rows"][0], "last_line": None}, *data["rows"][1:]]})
    assert "prior.json: " in refusal("not json")
    assert main([*[a for a in later if a not in ("--removal", "later-ranges.gz")], "--against", "before.json"]) == 2
    assert "they need --removal with the compiler ranges" in capsys.readouterr().err


@pytest.mark.parametrize("span,total,message", [
    ({}, 3, "expected the --json output"),
    ({"first_line": 0, "last_line": 0}, 4, "invalid source span"),
    ({"first_line": 3, "last_line": 1}, 2, "invalid source span"),
])
def test_difference_refuses_impossible_prior_spans(removal_project, capsys, span, total, message):
    _, ranges = removal_project
    # A real zero-line compiler span and a fully unlocated name both round-trip.
    write_gz(ranges, RANGES.replace("3\t0\t3\t40\t3", "3\t0\t3\t0\t3"))
    prior = output(capsys, *view(removal_project))
    step = next(row for row in prior["rows"] if row["name"] == "Tiny.step")
    assert (step["first_line"], step["last_line"]) == (3, 2)
    path = Path("prior.json")
    args = view(removal_project, "--against", str(path))
    path.write_text(json.dumps(prior))
    assert output(capsys, *args)["against"]["total"]["lines_before"] == 3
    step.update(first_line=None, last_line=None)
    path.write_text(json.dumps(prior))
    assert output(capsys, *args)["against"]["total"]["lines_before"] == 3

    del step["first_line"], step["last_line"]
    step.update(span)
    prior["removal"]["union_located_lines"] = total
    path.write_text(json.dumps(prior))
    assert main(args) == 2
    assert message in capsys.readouterr().err


def test_difference_checks_same_name_roots_against_source_signature_and_type(removal_project, foreign, capsys):
    _, ranges = removal_project
    before = output(capsys, *view(removal_project))
    assert before["removal"]["root_statements"]["Tiny.conclusion"]["type_hash"] == "1"
    before_path = Path("before.json")
    before_path.write_text(json.dumps(before))
    comparison = ["--against", str(before_path)]
    assert output(capsys, *view(removal_project, *comparison))["against"]["total"]["difference"] == 0

    # The declaration text still matches but its elaborated type changed.
    write_gz(ranges, RANGES.replace("Tiny.conclusion\t1\t", "Tiny.conclusion\t2\t"))
    assert main(view(removal_project, *comparison)) == 2
    assert "statement identity differs for Tiny.conclusion" in capsys.readouterr().err
    write_gz(ranges, RANGES)

    # A new pin keeps the name and location, and changes the explicit claim.
    path = foreign / "Tiny.lean"
    source = path.read_text()
    changed = source.replace(": p :=", ": p ∧ p :=")
    assert changed != source
    path.write_text(changed)
    commit_all(foreign, "change the claim under the same root name")
    output(capsys, "import", foreign, "--lock", "changed.lock")
    later = view(removal_project, *comparison)
    later[later.index("vl.lock")] = "changed.lock"
    assert main(later) == 2
    assert "statement identity differs for Tiny.conclusion" in capsys.readouterr().err


def test_study_rank_uses_the_shared_spine_and_removal_rank_uses_exclusive_cost(removal_project, foreign, capsys):
    deps, ranges = removal_project
    path = foreign / "Tiny/Step.lean"
    path.write_text(path.read_text().replace(
        "end Tiny",
        "theorem long (p : Prop) (h : p) : p := by\n"
        "  have h₁ : p := h\n  have h₂ : p := h\n  exact h\n\n"
        "theorem twin (p : Prop) (h : p) : p := by\n"
        "  have h₁ : p := h\n  exact h\n\nend Tiny"))
    commit_all(foreign, "add large private branches")
    output(capsys, "pin", foreign, "--lock", "study.lock")
    write_gz(deps, DEPS.replace("Tiny.step Tiny.conclusion.proof_1", "Tiny.step Tiny.long Tiny.twin Tiny.conclusion.proof_1")
             + "DEP\tthm\tTiny.Step\tTiny.long\t-\t\t\nDEP\tthm\tTiny.Step\tTiny.twin\t-\t\t\n")
    write_gz(ranges, RANGES + "R\tTiny.Step\tTiny.long\t9\t0\t12\t9\t9\n"
             "R\tTiny.Step\tTiny.twin\t14\t0\t16\t9\t14\n")
    blocks = Path("blocks.tsv")
    blocks.write_text("Lemmas\t^Tiny[.]Step$\n")
    args = ["value", "study.lock", "--deps", str(deps), "--removal", str(ranges),
            "--root", "Tiny.conclusion", "--root", "Tiny.other", "--study", "--blocks", str(blocks)]
    data = output(capsys, *args)["study"]
    shared = data["study_first"][0]
    long = data["simplify_first"][0]
    assert (shared["name"], shared["roots_reached"], shared["reachable_users"], shared["root_distance"]) == (
        "Tiny.step", 2, 3, 1)
    assert (long["name"], long["exclusive_source_lines"], long["repeated_lines"], long["block"]) == (
        "Tiny.long", 4, 2, "Lemmas")
    assert "2 roots" in shared["reason"] and "4 exclusive located lines" in long["reason"]
    assert {r["name"] for r in data["study_first"]} == {"Tiny.step", "Tiny.long", "Tiny.twin"}
    assert main([*args, "--limit", "1"]) == 0
    stdout = capsys.readouterr().out
    assert "Study first" in stdout and "Simplify first" in stdout and "Tiny.twin" not in stdout
