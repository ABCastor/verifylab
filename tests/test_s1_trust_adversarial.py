"""S-1 adversarial regressions against the trust build; red tests are intentional findings.

Recording checkers below exercise the real CLI, Git admission and replay comparison, not Lean
soundness. The historical-kernel case additionally runs the real protected Lean adapter.
"""

from __future__ import annotations

import copy
import json

import pytest

from conftest import commit_all, git, lean_elan_home, write_machine
from review_helpers import meaning_args
from test_probe_status import with_target
from test_replay import (
    Recording,
    _install_modern_replay_checker,
    _make_historical_replay_receipt,
    _setup_replay_repo,
    admitted,
)
from verifylab import cli
from verifylab.commands import check
from verifylab.records import canonical_json, receipt_problems, seal_receipt, sha256_hex
from verifylab.repo import Repo


def _json(capsys, *argv):
    code = cli.main([*argv, "--json"])
    captured = capsys.readouterr()
    assert captured.out, (code, captured.err)
    return code, json.loads(captured.out)


@pytest.mark.parametrize("verdict", ["error", "unsupported"])
@pytest.mark.parametrize("contract", ["exact", "recheck"])
def test_inconclusive_replay_never_succeeds_even_when_original_was_inconclusive(
    admitted, capsys, verdict, contract
):
    root, original = admitted
    # Clean control: definitive evidence really reproduces through the same contract.
    code, out = _json(capsys, "replay", "add-zero", "--receipt", original["receipt_id"],
                      "--contract", contract)
    assert code == 0 and out["result"] in ("match", "rechecked"), out

    # An error/unsupported outcome with preserved inputs is a valid, admitted receipt.
    # The replay produces the same inconclusive verdict; agreement is still no successful check.
    Recording.verdict = verdict
    code, out = _json(capsys, "check", "add-zero")
    assert code == 3, out
    inconclusive = json.loads((root / out["receipt"]).read_text())
    assert receipt_problems(inconclusive) == []
    commit_all(root, "admit inconclusive receipt")
    code, out = _json(capsys, "replay", "add-zero", "--receipt", inconclusive["receipt_id"],
                      "--contract", contract)
    assert (code, out["result"]) == (3, "error"), out


@pytest.mark.parametrize("mode, modules", [
    (None, []),
    ("renamed", ["Unrelated.lean"]),
])
def test_historical_closure_cannot_disappear_by_changing_replay_mode(
    tmp_path, monkeypatch, capsys, mode, modules
):
    root = _setup_replay_repo(tmp_path, monkeypatch)
    original = _make_historical_replay_receipt(root)
    folder = root / "research" / "evidence" / original["item"]
    folder.mkdir(parents=True)
    path = folder / f"{original['receipt_id'][:16]}.json"
    path.write_text(json.dumps(original))
    commit_all(root, "admit historical closure")
    _install_modern_replay_checker(monkeypatch)
    code, out = _json(capsys, "replay", original["item"], "--contract", "compat")
    assert (code, out["result"]) == (0, "match"), out

    # Existing negative control: a missing historical module in shared mode is rejected.
    _install_modern_replay_checker(monkeypatch, closure=[])
    code, out = _json(capsys, "replay", original["item"], "--contract", "compat")
    assert (code, out["result"]) == (1, "mismatch"), out

    # Same missing closure, with only the reported mode/modules changed, must stay rejected.
    checker = check.load_adapters()[0]
    original_check = checker.check

    def changed_mode(request):
        outcome = original_check(request)
        outcome.checked["trusted_modules"] = modules
        if mode is None:
            outcome.checked.pop("definitions_mode")
        else:
            outcome.checked["definitions_mode"] = mode
        return outcome

    monkeypatch.setattr(checker, "check", changed_mode)
    monkeypatch.setattr(check, "load_adapters", lambda: [checker])
    before = path.read_bytes()
    code, out = _json(capsys, "replay", original["item"], "--contract", "compat")
    assert path.read_bytes() == before
    assert code != 0 and out["result"] in ("mismatch", "error"), out


def test_later_faithful_reviews_cannot_hide_current_dissent(research_repo, monkeypatch, capsys):
    root = research_repo
    monkeypatch.chdir(root)
    with_target(root)

    def review(author, verdict):
        code, out = _json(capsys, "review", "add-zero", "--kind", "fidelity", *meaning_args(),
                          "--verdict", verdict, "--author", author, "--text", f"My verdict: {verdict}.")
        assert code == 0, out
        commit_all(root, "admit attributed fidelity review")

    review("agent:critic", "too-weak")
    code, out = _json(capsys, "show", "add-zero")
    assert code == 0
    assert out["items"][0]["ladder"][3]["state"] == "disputed"

    review("agent:reader-a", "faithful")
    review("agent:reader-b", "faithful")
    code, out = _json(capsys, "show", "add-zero")
    row = out["items"][0]["ladder"][3]
    assert code == 0 and row["state"] == "disputed", row
    # No supersession was requested. All 3 current judgements must remain in the row.
    shown = json.dumps(row)
    assert all(author in shown for author in ("agent:critic", "agent:reader-a", "agent:reader-b")), row


@pytest.mark.lean
def test_historical_single_kernel_compatibility_replay_preserves_its_generation(
    tmp_path, monkeypatch, capsys, pinned_tools
):
    from lean_helpers import lean_unavailable, load_case, make_repo

    unavailable = lean_unavailable()
    if unavailable:
        pytest.skip(unavailable)
    write_machine(pinned_tools, elan_home=lean_elan_home())
    root = make_repo(tmp_path, load_case("control-original"))
    git(root, "checkout", "-q", "trusted")
    git(root, "merge", "-q", "--ff-only", "candidate")
    config = root / "research" / "vl.toml"
    config.write_text(config.read_text().replace("external_kernels = true", "external_kernels = false"))
    commit_all(root, "historical policy permitted one kernel")
    monkeypatch.chdir(root)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))

    # New checks still must obey T0. No guard is disabled in this test.
    assert cli.main(["check", "double-even", "--json"]) == 3, capsys.readouterr()
    capsys.readouterr()
    repo = Repo.open(root)
    outcome, stronger = check.execute(repo, repo.load_item("double-even"), "protected", 120,
                                     require_external_kernels=True)
    assert outcome.verdict == "pass" and stronger is not None, outcome.reasons
    assert stronger["checked"]["kernels"] == ["lean", "nanoda"]

    # Synthetic historical evidence over these kernel-checked bytes, with actual tool identities.
    # This constructs a fixture; it neither edits an existing receipt nor claims an old run happened.
    historical = copy.deepcopy(stronger)
    historical["checked"]["kernels"] = ["lean"]
    for key in ("definitions_mode", "probe_run"):
        historical["checked"].pop(key, None)
    historical["environment"]["tools"].pop("nanoda")
    historical["environment"].pop("environment_class", None)
    trusted = historical["inputs"]["trusted_files"]
    trusted.pop("research/vl.toml#verdict-rules")
    trusted["research/vl.toml"] = sha256_hex(config.read_bytes())
    historical["inputs"]["digest"] = "sha256:" + sha256_hex(canonical_json({
        "files": historical["inputs"]["files"], "trusted_files": trusted,
        "target": historical["target"],
    }).encode())
    historical = seal_receipt(historical)
    assert receipt_problems(historical) == []
    path = root / "research" / "evidence" / "double-even" / f"{historical['receipt_id'][:16]}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(historical))
    commit_all(root, "admit synthetic pre-minimum receipt")
    code, out = _json(capsys, "show", "double-even")
    assert code == 0 and out["items"][0]["status"]["label"] == "verified", out

    code, out = _json(capsys, "replay", "double-even", "--receipt", historical["receipt_id"],
                      "--contract", "compat")
    assert (code, out["result"]) == (0, "match"), out
