"""Known-answer limits: conclusion-domain emptiness and parameter coverage of a witness.

These toy classes isolate the quantifier structure; they make no claim about a PDE
formalization. The impossible and pinned classes intentionally misrepresent the
modeled growing solution; their nonexistence theorems are formally correct.
Successful kernel replay proves the stated toy claims. Whether the class models
the intended solutions remains a fidelity judgement.
"""

import json

import pytest

from lean_helpers import explain, git, lean_unavailable, load_case, make_repo, tool_env

CASES = (
    "limit-solution-class-impossible",
    "control-solution-class-inhabited",
    "limit-datum-pinning",
    "control-datum-pinning",
)


@pytest.mark.lean
@pytest.mark.parametrize("name", CASES)
def test_solution_class_and_datum_pinning(name, shared_cases, monkeypatch):
    missing = lean_unavailable()
    if missing:
        pytest.skip(missing)
    for key, value in tool_env().items():
        monkeypatch.setenv(key, value)
    outcome = shared_cases(name)
    assert outcome.verdict == "pass", explain(outcome)
    assert outcome.checked["probe_run"]["problems"] == [], explain(outcome)
    probes = outcome.checked["probes"]
    for row in probes.values():
        assert row == {
            "trivial_by": None, "vacuous_by": None, "prop_hypotheses": 0,
            "vacuity_skipped": "no Prop hypothesis",
        }, probes
    # Persist the actual adapter evidence for diagnosis, without admitting it.
    root = shared_cases.run(name)[1]
    (root.parent / "semantic-limit-outcome.json").write_text(
        json.dumps({"verdict": outcome.verdict, "checked": outcome.checked}, indent=2)
    )
    (root.parent / "semantic-limit-outcome.log").write_text(outcome.log)
    # The listed witness and the excluded datum both pass in the pinned case:
    # nonempty somewhere does not establish nonempty for every requested datum.
    if name == "limit-datum-pinning":
        assert set(probes) == {
            "VL.PinnedClass.main", "VL.PinnedClass.witness", "VL.PinnedClass.excludedDatum"
        }


@pytest.mark.lean
def test_nonexistence_check_admit_show_and_validate_keep_evidence_scoped(tmp_path, monkeypatch, capsys):
    from verifylab.cli import main
    missing = lean_unavailable()
    if missing:
        pytest.skip(missing)
    for key, value in tool_env().items():
        monkeypatch.setenv(key, value)
    root = make_repo(tmp_path, load_case("limit-datum-pinning"))
    git(root, "checkout", "-q", "trusted")
    monkeypatch.chdir(root)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))

    def vl(*args):
        rc = main(list(args))
        return rc, capsys.readouterr().out

    rc, out = vl("check", "double-even")
    assert rc == 0, out
    git(root, "add", "-A")
    git(root, "commit", "-qm", "admit checked evidence")
    rc, out = vl("show", "double-even", "--json")
    assert rc == 0, out
    card = json.loads(out)["items"][0]
    assert card["status"]["label"] == "verified"
    assert card["proof"]["kernels"] == ["lean", "nanoda"]
    assert all(row["vacuity_skipped"] == "no Prop hypothesis" for row in card["proof"]["probes"].values())
    for args in [("show", "double-even"), ("show", "double-even", "--brief", "--budget", "100000")]:
        rc, out = vl(*args)
        assert rc == 0 and "premise-vacuity skipped: no Prop hypothesis" in out
        assert "definition adequacy needs review" in out
    rc, out = vl("validate")
    assert rc == 0, out
    # A protected pass and a global witness do not supply a meaning judgment.
    assert card["fidelity"]["label"] == "not reviewed"
    assert "fidelity is 'not reviewed'" in out
