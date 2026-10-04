"""Receipts bind to what decides a verdict in research/vl.toml (`research/vl.toml#verdict-rules`), so editing a
resource limit or moving the build cache does not stale them; receipts that recorded the older `#verdict` (with the
packages and cache paths), `#check` (also without roots) or the whole file keep being compared as before, the roots of
a `#check` receipt through the commit it checked."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from verifylab import cli, gitref
from verifylab.adapters.base import CheckRequest
from verifylab.adapters.lean_comparator import LeanComparatorAdapter
from verifylab.config import CHECK_INPUT, RULES_INPUT, VERDICT_INPUT, check_digest, rules_digest, verdict_digest
from verifylab.records import seal_receipt, sha256_hex, write_new_json
from verifylab.repo import Repo

from conftest import synthetic_receipt, commit_all, git, item_question

CONFIG = "research/vl.toml"


def admit_receipt(root: Path, trusted_input: str) -> Path:
    """An admitted protected pass whose only trusted input is the configuration, bound one way or the other."""
    repo = Repo.open(root)
    digest = {RULES_INPUT: rules_digest, VERDICT_INPUT: verdict_digest, CHECK_INPUT: check_digest}.get(
        trusted_input, lambda _: sha256_hex((root / CONFIG).read_bytes()))(repo.config)
    lean = "Fixture/Basic.lean"
    receipt = synthetic_receipt(root, dict(
        item="add-zero", item_revision="a" * 40, question_digest=item_question(root, "add-zero"),
        adapter="lean-comparator", assurance="protected", verdict="pass",
        reasons=[], inputs={"digest": "d", "files": {lean: sha256_hex((root / lean).read_bytes())},
                            "trusted_files": {trusted_input: digest},
                            "trusted_commit": gitref.rev_parse(root, "trusted")},
        environment={}, checked={}, command=["vl"], started_at="t0", finished_at="t1", tool_version="vl 0.1.0"))
    path = root / "research" / "evidence" / "add-zero" / f"{receipt['receipt_id'][:16]}.json"
    write_new_json(path, receipt)
    commit_all(root, "admit")
    return path


def edit_config(root: Path, old: str, new: str, commit: bool = True) -> None:
    cfg = root / CONFIG
    text = cfg.read_text()
    cfg.write_text(text.replace(old, new) if old else text + new)
    if commit:
        commit_all(root, "config")


def stale(root: Path, path: Path | None = None) -> list[str]:
    """Stale inputs of the receipt at `path`, or of the only admitted receipt."""
    repo = Repo.open(root)
    [record] = [r for r in repo.receipts("add-zero") if r.admitted and (path is None or r.path.endswith(path.name))]
    return repo.stale_inputs(record.data, repo.load_item("add-zero"), admitted=True)


MEMORY = "\n[check]\nmemory_max = \"4G\"\nmemory_total = \"9G\"\n"
AXIOMS = 'roots = ["Fixture"]\npermitted_axioms = ["propext", "Quot.sound", "Classical.choice", "sorryAx"]'


def test_resource_limits_do_not_stale_but_permitted_axioms_do(research_repo):
    admit_receipt(research_repo, VERDICT_INPUT)
    assert stale(research_repo) == []
    edit_config(research_repo, "", MEMORY)
    assert stale(research_repo) == []
    edit_config(research_repo, 'roots = ["Fixture"]', AXIOMS)
    assert stale(research_repo) == [f"{VERDICT_INPUT} (trusted ref)"]


def test_reordering_axioms_or_spelling_out_defaults_keeps_receipts_fresh(research_repo):
    admit_receipt(research_repo, VERDICT_INPUT)
    edit_config(research_repo, 'roots = ["Fixture"]', 'roots = ["Fixture"]\nexternal_kernels = true\n'
                'permitted_axioms = ["Classical.choice", "propext", "Quot.sound"]')
    assert stale(research_repo) == []
    edit_config(research_repo, "external_kernels = true", "external_kernels = false")
    assert stale(research_repo) == [f"{VERDICT_INPUT} (trusted ref)"]


ROOTS = 'roots = ["Fixture", "Extra"]'


def test_lean_roots_are_part_of_the_verdict_digest(research_repo):
    admit_receipt(research_repo, VERDICT_INPUT)
    edit_config(research_repo, 'roots = ["Fixture"]', 'roots = ["Fixture", "Fixture"]')     # the same set of roots
    assert stale(research_repo) == []
    edit_config(research_repo, 'roots = ["Fixture", "Fixture"]', ROOTS)
    assert stale(research_repo) == [f"{VERDICT_INPUT} (trusted ref)"]


def test_a_receipt_bound_before_roots_compares_them_through_its_trusted_commit(research_repo):
    """`#check` receipts (no roots in the digest) stay fresh while the roots of the commit they checked are still
    the trusted ref's, and go stale when the roots change."""
    admit_receipt(research_repo, CHECK_INPUT)
    edit_config(research_repo, "", MEMORY)
    assert stale(research_repo) == []
    edit_config(research_repo, 'roots = ["Fixture"]', ROOTS)
    assert len(stale(research_repo)) == 1 and "roots" in stale(research_repo)[0]


def test_old_receipt_bound_to_the_whole_file_evaluates_as_before(research_repo):
    admit_receipt(research_repo, CONFIG)
    assert stale(research_repo) == []
    edit_config(research_repo, "", MEMORY)
    assert stale(research_repo) == [f"{CONFIG} (trusted ref)"]


def test_unreadable_config_on_the_trusted_ref_is_stale_not_a_crash(research_repo):
    admit_receipt(research_repo, VERDICT_INPUT)
    good = (research_repo / CONFIG).read_text()
    edit_config(research_repo, "", "[broken\n")
    (research_repo / CONFIG).write_text(good)                # the worktree stays readable; the trusted ref is not
    assert stale(research_repo) == [f"{VERDICT_INPUT} (trusted ref)"]


def test_incoming_lane_staling_the_config_part_is_named(research_repo, monkeypatch, capsys):
    root = research_repo
    monkeypatch.chdir(root)
    receipt = admit_receipt(root, VERDICT_INPUT)
    for branch, change in (("lane/mem", ("", MEMORY)), ("lane/ax", ('roots = ["Fixture"]', AXIOMS))):
        git(root, "checkout", "-q", "-b", branch, "trusted")
        edit_config(root, *change)
        git(root, "checkout", "-q", "trusted")
    assert cli.main(["validate", "--incoming", "lane/mem", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert [c["path"] for c in data["trusted_changes"]] == [CONFIG] and data["stale_after_merge"] == []
    assert cli.main(["validate", "--incoming", "lane/ax", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["stale_after_merge"] == [{"receipt": str(receipt.relative_to(root)), "item": "add-zero",
                                          "inputs": [f"{VERDICT_INPUT} (trusted)"]}]


@pytest.mark.parametrize("assurance, side", [("protected", "trusted_files"), ("exploratory", "files")])
def test_the_lean_checker_records_only_the_config_part(research_repo, assurance, side):
    item = research_repo / "research" / "items" / "add-zero.md"
    item.write_text(item.read_text().replace("+++\nBody", '[lean]\ntarget = "nope"\n+++\nBody'))
    commit_all(research_repo, "lean table")
    repo = Repo.open(research_repo)
    request = CheckRequest(repo=repo, item=repo.load_item("add-zero"), assurance=assurance,
                           trusted_commit=gitref.rev_parse(research_repo, "trusted"),
                           scratch=Path(tempfile.mkdtemp(dir=research_repo.parent)), timeout=60, memory_max=None)
    outcome = LeanComparatorAdapter().check(request)
    assert outcome.verdict == "error"                     # the malformed target stops it right after the config
    assert getattr(outcome, side) == {RULES_INPUT: rules_digest(repo.config)}


@pytest.mark.parametrize("bad, fragment", [
    ('[lean]\nexternal_kernels = "false"\n', "[lean] external_kernels must be true or false"),
    ("[lanes]\nmax_parallel = 2.9\n", "[lanes] max_parallel must be an integer"),
    ('[lanes]\nmax_parallel = "many"\n', "[lanes] max_parallel must be an integer"),
    ("[lanes]\nmax_parallel = 0\n", "[lanes] max_parallel must be at least 1"),
    ('tools = "x"\n', "[tools] must be a table"),
    ("lean = 1\n", "[lean] must be a table"),
    ("project = []\n", "[project] must be a table"),
    ("[check]\nmemory_max = 16\n", "[check] memory_max must be a string"),
    ("[lean]\ncache = 3\n", "[lean] cache must be a string"),
    ("[project]\nname = 3\n", "[project] name must be a string"),
])
def test_configuration_types_are_strict(tmp_path, bad, fragment):
    from verifylab.config import ConfigError, parse_config
    with pytest.raises(ConfigError) as caught:
        parse_config(tmp_path, bad)
    assert fragment in str(caught.value)


CACHE_MOVED = ('roots = ["Fixture"]', 'roots = ["Fixture"]\ncache = "/elsewhere/.lake"\npackages = "/elsewhere/.lake/packages"')


def test_a_receipt_bound_to_the_verdict_rules_survives_a_moved_build_cache(research_repo):
    """The build cache and packages are paths of one machine: moving them changes no verdict. A receipt that recorded
    `#verdict` keeps its basis, which held them, and stays fresh only while they do not move."""
    root = research_repo
    new, old = admit_receipt(root, RULES_INPUT), admit_receipt(root, VERDICT_INPUT)
    assert stale(root, new) == [] and stale(root, old) == []
    edit_config(root, *CACHE_MOVED)
    assert stale(root, new) == []
    assert stale(root, old) == [f"{VERDICT_INPUT} (trusted ref)"]


@pytest.mark.parametrize("bound", [RULES_INPUT, VERDICT_INPUT, CHECK_INPUT])
def test_changing_the_permitted_axioms_stales_every_basis(research_repo, bound):
    root = research_repo
    path = admit_receipt(root, bound)
    edit_config(root, "", MEMORY)
    assert stale(root, path) == []
    edit_config(root, 'roots = ["Fixture"]', AXIOMS)
    assert stale(root, path) == [f"{bound} (trusted ref)"]
