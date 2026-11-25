from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from verifylab import catalog, imports, index, render, status
from verifylab.adapters.base import CheckOutcome
from verifylab.cli import main
from verifylab.commands import check, replay, validate
from verifylab.records import (
    RecordError,
    parse_item,
    question_digest,
    receipt_problems,
    seal_receipt,
    seal_review,
    sha256_hex,
    write_new_json,
)
from verifylab.repo import Repo, TrustedRepo

from import_source import make_source

GCA_ITEM_ID = "adaptive-feedback-separation"
GCA_ITEM_BLOB = "5a9c50aaff380485481a63f4b81bc7d200828db3"
GCA_RECEIPT_ID = "ffa9db14c68e60d29305028408a0ff37aa266a4590ee2ba67eb3ed02b151fc53"
GCA_REVIEW_ID = "0a66c7a8dfb0758af7a9f6a621cb29043e6cb60cab990877aaf9878310263954"
GCA_MODULE = "GCA.Proofs.Feedback"
GCA_THEOREMS = ["GCA.adaptive_transcript_same", "GCA.separation_witness"]


def git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def commit_all(root: Path, msg: str) -> str:
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", msg)
    return git(root, "rev-parse", "HEAD")


def vl(capsys, *args: str) -> tuple[int, str, str]:
    rc = main(list(args))
    out = capsys.readouterr()
    return rc, out.out, out.err


def make_consumer_repo(
    tmp_path: Path,
    *,
    pkg_rev: str | None = None,
    with_imports: bool = True,
    theorems: list[str] | None = None,
    target_imports: list[str] | None = None,
    item_blob: str | None = None,
    receipt_id: str | None = None,
    review_id: str | None = None,
    roots: list[str] | None = None,
    limits: list[str] | None = None,
    packages_dir_mode: str = "lake",
) -> tuple[Path, Path]:
    """Create a realistic consumer repo using a copy of `fixtures-goal-conditioned-agency` as an offline dep."""
    consumer = tmp_path / "consumer"
    consumer.mkdir(parents=True)
    git(consumer, "init", "-q", "-b", "trusted")
    git(consumer, "config", "vl.trustedRef", "trusted")
    git(consumer, "config", "user.name", "Test")
    git(consumer, "config", "user.email", "test@example.com")

    if packages_dir_mode == "config":
        pkg_store = tmp_path / "pkg-store"
        pkg_dest = pkg_store / "gca"
        pkg_cfg_line = f'packages = "{pkg_store}"\n'
    else:
        pkg_dest = consumer / ".lake" / "packages" / "gca"
        pkg_cfg_line = ""
    blob, receipt, review = make_source(pkg_dest, GCA_ITEM_ID, GCA_MODULE, GCA_THEOREMS)
    item_blob = item_blob or blob
    receipt_id = receipt_id or receipt
    review_id = review_id or review
    head_rev = git(pkg_dest, "rev-parse", "HEAD")
    rev = pkg_rev or head_rev

    roots_list = roots or ["Consumer"]
    (consumer / "research" / "items").mkdir(parents=True)
    (consumer / "research" / "targets").mkdir(parents=True)
    (consumer / "research" / "evidence").mkdir(parents=True)
    (consumer / "research" / "reviews").mkdir(parents=True)
    (consumer / "research" / "vl.toml").write_text(
        f'[project]\nname = "consumer"\n\n[lean]\nroots = {json.dumps(roots_list)}\n{pkg_cfg_line}'
    )
    (consumer / "lake-manifest.json").write_text(
        json.dumps(
            {
                "version": "1.1.0",
                "packagesDir": ".lake/packages",
                "packages": [
                    {
                        "url": "https://example.org/org/goal-conditioned-agency",
                        "type": "git",
                        "subDir": None,
                        "scope": "",
                        "rev": rev,
                        "name": "gca",
                        "manifestFile": "lake-manifest.json",
                        "inputRev": "main",
                        "inherited": False,
                        "configFile": "lakefile.toml",
                    }
                ],
            },
            indent=2,
        )
    )
    (consumer / "lakefile.toml").write_text(
        'name = "consumer"\ndefaultTargets = ["Consumer"]\n\n[[lean_lib]]\nname = "Consumer"\n'
    )
    (consumer / "lean-toolchain").write_text("leanprover/lean:v4.29.0\n")

    t_imps = [GCA_MODULE] if target_imports is None else target_imports
    imp_lines = "\n".join(f"import {m}" for m in t_imps)
    (consumer / "research" / "targets" / "consequence.lean").write_text(
        (f"{imp_lines}\n\n" if imp_lines else "")
        + "theorem Consumer.consequence : True := by\n  sorry\n"
    )
    (consumer / "Consumer").mkdir()
    (consumer / "Consumer.lean").write_text("import Consumer.Proof\n")
    (consumer / "Consumer" / "Proof.lean").write_text(
        (f"{imp_lines}\n\n" if imp_lines else "")
        + "theorem Consumer.consequence : True := by\n  trivial\n"
    )

    # Carry GCA's stated limits and assumptions so clean control passes validate without constraint warnings.
    default_limits = [
        "Known indistinguishability mechanism, no novelty claim",
        "Restricted deterministic history-only policy interface in Lean; distinct train/test functions and random seeds are covered only by the prose argument",
        "Reward sums are unnormalized; uniform-prior expected utilities divide by 2",
        "No learning algorithm, sample-complexity or neural-training theorem",
        "Two deterministic hidden worlds and two actions as defined",
        "Policy has only scalar reward history and same policy code for probing and terminal choice",
        "Any finite training sample count; no world side effects or other observations",
        "Test utility is first coordinate and no feedback before commitment",
    ]
    item_limits = default_limits if limits is None else limits
    sel_thms = theorems if theorems is not None else GCA_THEOREMS
    imports_block = ""
    if with_imports:
        imports_block = (
            "\n[[imports]]\n"
            'package = "gca"\n'
            f'item = "{GCA_ITEM_ID}"\n'
            f'item_revision = "{item_blob}"\n'
            f'module = "{GCA_MODULE}"\n'
            f"theorems = {json.dumps(sel_thms)}\n"
            f'receipt = "{receipt_id}"\n'
            f'fidelity_review = "{review_id}"\n'
        )

    (consumer / "research" / "items" / "consequence.md").write_text(
        "+++\n"
        'id = "consequence"\n'
        'kind = "result"\n'
        'title = "Downstream consequence of GCA adaptive separation"\n'
        'author = "agent:tester"\n'
        'created = "2026-11-14"\n'
        'statement = "Consumer consequence building on GCA."\n'
        'claim = "formal"\n'
        f"limits = {json.dumps(item_limits)}\n"
        "\n[lean]\n"
        'target = "research/targets/consequence.lean"\n'
        'theorems = ["Consumer.consequence"]\n'
        'solution = "Consumer.Proof"\n'
        f"{imports_block}"
        "+++\n"
        "Imported downstream result.\n"
    )
    commit_all(consumer, "init consumer")
    return consumer, pkg_dest


# N1: Schema parsing, digest compatibility, and contract refusals --------------------------------------------------

def test_parse_valid_imports_and_preserve_non_import_hashes():
    plain_raw = (
        b'+++\nid = "t1"\nkind = "result"\ntitle = "T1"\nauthor = "agent:test"\n'
        b'created = "2026-11-14"\nstatement = "S"\nclaim = "formal"\n'
        b'[lean]\ntarget = "research/targets/t1.lean"\ntheorems = ["T1.thm"]\n'
        b'solution = "Proj.Sol"\n+++\nBody\n'
    )
    plain = parse_item(plain_raw, "research/items/t1.md")
    assert plain.imports == ()
    # Non-import items keep exact legacy question_digest (no "imports" key emitted).
    import json as _json
    legacy_basis = _json.dumps(
        {"lean": {"solution": "Proj.Sol", "target": "research/targets/t1.lean", "theorems": ["T1.thm"]}, "python": {}},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    assert question_digest(plain) == "sha256:" + sha256_hex(legacy_basis.encode())

    with_imp_raw = plain_raw.replace(
        b"+++\nBody",
        (
            f'[[imports]]\npackage = "gca"\nitem = "{GCA_ITEM_ID}"\n'
            f'item_revision = "{GCA_ITEM_BLOB}"\nmodule = "{GCA_MODULE}"\n'
            f'theorems = {json.dumps(GCA_THEOREMS)}\n'
            f'receipt = "{GCA_RECEIPT_ID}"\nfidelity_review = "{GCA_REVIEW_ID}"\n+++\nBody'
        ).encode(),
    )
    with_imp = parse_item(with_imp_raw, "research/items/t1.md")
    assert len(with_imp.imports) == 1
    assert question_digest(with_imp) != question_digest(plain)


@pytest.mark.parametrize("extra, match", [
    ('url = "https://github.com/org/gca"', "cannot set url"),
    ('rev = "' + "a" * 40 + '"', "cannot set rev"),
    ('commit = "' + "a" * 40 + '"', "cannot set commit"),
    ('subDir = "sub"', "cannot set subDir"),
    ('subdir = "sub"', "cannot set subdir"),
    ('unknown = "x"', "unknown field"),
])
def test_imports_forbid_manifest_fields_and_unknown_keys(extra, match):
    raw = (
        f'+++\nid = "t1"\nkind = "result"\ntitle = "T1"\nauthor = "agent:test"\n'
        f'created = "2026-11-14"\nstatement = "S"\nclaim = "formal"\n'
        f'[[imports]]\npackage = "gca"\nitem = "{GCA_ITEM_ID}"\n'
        f'item_revision = "{GCA_ITEM_BLOB}"\nmodule = "{GCA_MODULE}"\n'
        f'theorems = ["GCA.adaptive_transcript_same"]\n'
        f'receipt = "{GCA_RECEIPT_ID}"\nfidelity_review = "{GCA_REVIEW_ID}"\n'
        f"{extra}\n+++\nBody\n"
    ).encode()
    with pytest.raises(RecordError, match=match):
        parse_item(raw, "research/items/t1.md")


@pytest.mark.parametrize("override, match", [
    ('item_revision = "abc123"', "item_revision"),
    ('receipt = "short"', "receipt"),
    ('fidelity_review = "short"', "fidelity_review"),
    ("theorems = []", "theorems"),
    ('theorems = ["GCA.a", "GCA.a"]', "duplicate"),
    ('module = "VLTrusted.Foo"', "module"),
    ('package = "bad/name"', "package"),
])
def test_imports_reject_short_ids_reserved_modules_and_empty_theorems(override, match):
    fields = {
        "package": '"gca"',
        "item": f'"{GCA_ITEM_ID}"',
        "item_revision": f'"{GCA_ITEM_BLOB}"',
        "module": f'"{GCA_MODULE}"',
        "theorems": '["GCA.adaptive_transcript_same"]',
        "receipt": f'"{GCA_RECEIPT_ID}"',
        "fidelity_review": f'"{GCA_REVIEW_ID}"',
    }
    key = override.split("=", 1)[0].strip()
    fields[key] = override.split("=", 1)[1].strip()
    body_kv = "\n".join(f"{k} = {v}" for k, v in fields.items())
    raw = (
        f'+++\nid = "t1"\nkind = "result"\ntitle = "T1"\nauthor = "agent:test"\n'
        f'created = "2026-11-14"\nstatement = "S"\nclaim = "formal"\n'
        f"[[imports]]\n{body_kv}\n+++\nBody\n"
    ).encode()
    with pytest.raises(RecordError, match=match):
        parse_item(raw, "research/items/t1.md")


# N2: Offline resolution using real GCA fixture copy ---------------------------------------------------------------

def test_clean_control_resolves_source_fixture_offline(tmp_path, monkeypatch, capsys):
    consumer, pkg_dest = make_consumer_repo(tmp_path, packages_dir_mode="config")
    # Also move pkg worktree HEAD/config to a bogus trustedRef & uncommitted edit to prove snapshot reads ONLY rev!
    git(pkg_dest, "config", "vl.trustedRef", "refs/heads/nonexistent")
    (pkg_dest / "research" / "items" / f"{GCA_ITEM_ID}.md").write_text("CORRUPTED WORKTREE")

    monkeypatch.chdir(consumer)
    rc, out, err = vl(capsys, "validate", "--json")
    data = json.loads(out)
    assert rc == 0, (out, err)
    assert data["errors"] == []
    import_warnings = [w for w in data["warnings"] if "limit" in w["message"].lower() or "import" in w["message"].lower()]
    assert import_warnings == [], data["warnings"]

    # `vl show --json` and `vl export catalog` expose source status/fidelity separately from consumer's own.
    rc, show_out, _ = vl(capsys, "show", "consequence", "--json")
    assert rc == 0
    card = json.loads(show_out)["items"][0]
    assert card["status"]["label"] == "unverified"
    assert card["fidelity"]["label"] == "not reviewed"
    assert len(card["imports"]) == 1
    imp = card["imports"][0]
    assert imp["resolved"] is True
    assert imp["source_status"] == "verified"
    assert imp["source_fidelity"]["label"] == "faithful"
    assert imp["manifest"]["rev"] == git(pkg_dest, "rev-parse", "HEAD")
    assert imp["manifest"]["url"] == "https://example.org/org/goal-conditioned-agency"
    assert imp["manifest"]["subDir"] is None
    assert "pending (N3)" in imp["qualification"]

    rc, text_show, _ = vl(capsys, "show", "consequence")
    assert rc == 0 and "IMPORTS" in text_show and "source proof: verified" in text_show

    out_dir = tmp_path / "catalog-out"
    rc, _, err = vl(capsys, "export", "catalog", str(out_dir))
    assert rc == 0, err
    cat = json.loads((out_dir / "catalog.json").read_text())
    cat_entry = next(e for e in cat["items"] if e["id"] == "consequence")
    assert cat_entry["imports"][0]["source_status"] == "verified"
    assert cat_entry["imports"][0]["source_fidelity"]["label"] == "faithful"


def test_offline_resolver_never_falls_back_to_head_when_pinned_rev_missing(tmp_path):
    consumer, _ = make_consumer_repo(tmp_path, pkg_rev="f" * 40, packages_dir_mode="config")
    repo = Repo.open(consumer)
    item = repo.load_item("consequence")
    resolved, errs = imports.resolve_imports(repo, item, commit=repo.trusted_commit)
    assert resolved == []
    assert any("does not contain pinned commit" in e and "never fetches or falls back to HEAD" in e for e in errs)


# Planted Case A: Cherry-picked source pass vs whole-snapshot status (retraction / newer fail) --------------------

def test_planted_cherry_picked_pass_rejected_by_whole_snapshot_and_slips_with_guard_disabled(tmp_path):
    consumer, pkg_dest = make_consumer_repo(tmp_path, packages_dir_mode="config")
    # Upstream admits a retraction review in commit B; consumer pins commit B while still naming old pass receipt.
    retraction = seal_review({
        "item": GCA_ITEM_ID,
        "item_revision": Repo.open(pkg_dest).load_item(GCA_ITEM_ID).revision,
        "kind": "retraction",
        "author": "agent:auditor",
        "text": "Found hidden gap in proof.",
        "created": "2026-11-14T12:00:00Z",
    })
    rev_path = pkg_dest / "research" / "reviews" / GCA_ITEM_ID / f"{retraction['review_id'][:16]}.json"
    write_new_json(rev_path, retraction)
    rev_b = commit_all(pkg_dest, "admit retraction")

    # Update consumer manifest pin to rev_b (keeps old passing receipt + faithful review id).
    manifest_p = consumer / "lake-manifest.json"
    manifest_data = json.loads(manifest_p.read_text())
    manifest_data["packages"][0]["rev"] = rev_b
    manifest_p.write_text(json.dumps(manifest_data, indent=2))
    commit_all(consumer, "pin retracted source commit B")

    repo = Repo.open(consumer)
    item = repo.load_item("consequence")
    resolved, errs = imports.resolve_imports(repo, item, commit=repo.trusted_commit)
    assert resolved == []
    assert any("snapshot status" in e and "'retracted'" in e for e in errs)

    # Disabling `snapshot_status` lets the cherry-picked pass slip through.
    slip_res, slip_errs = imports.resolve_imports(
        repo, item, commit=repo.trusted_commit, guards=frozenset({"snapshot_status"})
    )
    assert slip_errs == [] and len(slip_res) == 1


# Planted Case 1: Source moved after review & Freshness invariance under upstream movement ------------------------

def test_planted_source_moved_after_review_and_upstream_branch_freshness(tmp_path):
    consumer, pkg_dest = make_consumer_repo(tmp_path, packages_dir_mode="config")
    repo = Repo.open(consumer)
    item = repo.load_item("consequence")

    # Upstream adds commit B modifying source item statement AFTER fidelity review + retraction,
    # WITHOUT consumer changing its manifest pin A (`GCA_HEAD`).
    src_md = pkg_dest / "research" / "items" / f"{GCA_ITEM_ID}.md"
    old_text = src_md.read_text()
    src_md.write_text(old_text.replace("For the defined two-world", "Changed statement B for the two-world"))
    original = git(pkg_dest, "rev-parse", "HEAD")
    rev_b = commit_all(pkg_dest, "upstream moves to B")

    # Freshness check 1: consumer pinned to A stays valid and NOT stale!
    res_a, errs_a = imports.resolve_imports(repo, item, commit=repo.trusted_commit)
    assert errs_a == [] and res_a[0].manifest_rev == original

    # Write a protected consumer receipt pinned at A and check `stale_inputs` stays empty when upstream moved to B!
    receipt_a = seal_receipt({
        "item": "consequence",
        "item_revision": item.revision,
        "question_digest": question_digest(item),
        "adapter": "lean-comparator",
        "assurance": "protected",
        "verdict": "pass",
        "reasons": [],
        "inputs": {
            "digest": "sha256:" + sha256_hex(json.dumps({
                "files": {"Consumer/Proof.lean": sha256_hex((consumer / "Consumer" / "Proof.lean").read_bytes())},
                "trusted_files": {
                    "research/targets/consequence.lean": sha256_hex((consumer / "research" / "targets" / "consequence.lean").read_bytes()),
                    "lake-manifest.json": sha256_hex((consumer / "lake-manifest.json").read_bytes()),
                },
                "target": {"path": "research/targets/consequence.lean", "sha256": sha256_hex((consumer / "research" / "targets" / "consequence.lean").read_bytes()), "commit": repo.trusted_commit, "source": "trusted-commit", "theorems": ["Consumer.consequence"]},
                "imports": [res_a[0].receipt_entry()],
            }, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()),
            "files": {"Consumer/Proof.lean": sha256_hex((consumer / "Consumer" / "Proof.lean").read_bytes())},
            "trusted_files": {
                "research/targets/consequence.lean": sha256_hex((consumer / "research" / "targets" / "consequence.lean").read_bytes()),
                "lake-manifest.json": sha256_hex((consumer / "lake-manifest.json").read_bytes()),
            },
            "imports": [res_a[0].receipt_entry()],
            "trusted_ref": "trusted",
            "trusted_ref_source": "git-config",
            "trusted_commit": repo.trusted_commit,
            "candidate_head": repo.trusted_commit,
            "candidate_uncommitted": [],
        },
        "target": {"path": "research/targets/consequence.lean", "sha256": sha256_hex((consumer / "research" / "targets" / "consequence.lean").read_bytes()), "commit": repo.trusted_commit, "source": "trusted-commit", "theorems": ["Consumer.consequence"]},
        "environment": {"toolchain": "v4", "lean_version": "4.29", "isolation": {"net": "off"}, "tools": {
            t: {"path": f"/usr/bin/{t}", "sha256": "a" * 64} for t in ("comparator", "lean4export", "landrun", "nanoda")
        }},
        "checked": {"theorems": ["Consumer.consequence"], "kernels": ["lean", "nanoda"], "permitted_axioms": []},
        "command": ["vl"], "started_at": "2026-11-14T00:00:00Z", "finished_at": "2026-11-14T00:00:01Z", "tool_version": "vl 0.1.0",
    })
    assert receipt_problems(receipt_a) == []
    assert repo.stale_inputs(receipt_a, item, admitted=False) == []

    # Now consumer updates manifest pin A -> B while keeping old item_revision and old fidelity_review.
    manifest_p = consumer / "lake-manifest.json"
    mdata = json.loads(manifest_p.read_text())
    mdata["packages"][0]["rev"] = rev_b
    manifest_p.write_text(json.dumps(mdata, indent=2))
    commit_all(consumer, "consumer updates pin to B")
    repo_b = Repo.open(consumer)

    # Consumer receipt pinned at A now goes stale (`lake-manifest.json` + `stale_imports`)!
    stale = repo_b.stale_inputs(receipt_a, item, admitted=False)
    assert any("pinned commit/url changed" in s for s in stale)

    # Planted defect 1a: old blob mismatch (`blob_match` guard) & old fidelity review (`source_fidelity` guard).
    res_b, errs_b = imports.resolve_imports(repo_b, item, commit=repo_b.trusted_commit)
    assert res_b == []
    assert any("blob mismatch" in e for e in errs_b)
    assert any("fidelity" in e for e in errs_b)

    # Disable `blob_match`, `source_fidelity`, `snapshot_status` -> slips through when guards disabled!
    _, errs_off = imports.resolve_imports(
        repo_b, item, commit=repo_b.trusted_commit,
        guards=frozenset({"blob_match", "source_fidelity", "snapshot_status"}),
    )
    assert errs_off == []


# Planted Case 2: Weaker restatement / dropped limit in consumer -------------------------------------------------

def test_planted_weaker_restatement_drops_limit_and_keeps_fidelity_separate(tmp_path, monkeypatch, capsys):
    consumer, _ = make_consumer_repo(
        tmp_path,
        limits=["Proven over arbitrary spaces."],  # drops GCA's discrete finite types / concrete K=2 limits
        packages_dir_mode="config",
    )
    monkeypatch.chdir(consumer)
    rc, out, _ = vl(capsys, "validate", "--json")
    assert rc == 0
    data = json.loads(out)
    assert any("states limit not carried in consumer limits/assumptions" in w["message"] for w in data["warnings"])

    # Consumer fidelity remains 'not reviewed' despite GCA's fidelity being 'faithful'.
    repo = Repo.open(consumer)
    item = repo.load_item("consequence")
    fid = status.fidelity(repo, item)
    assert fid is not None and fid.label == "not reviewed"

    # If a fidelity review exists for consumer and limits are edited later (weakening limits), fidelity stales!
    meaning_now = status.meaning(repo, item)
    f_rev = seal_review({
        "item": "consequence",
        "item_revision": item.revision,
        "kind": "fidelity",
        "verdict": "faithful",
        "author": "agent:ref",
        "text": "Matches target.",
        "created": "2026-11-14T10:00:00Z",
        "target_path": "research/targets/consequence.lean",
        "target_sha256": sha256_hex((consumer / "research" / "targets" / "consequence.lean").read_bytes()),
        "meaning": meaning_now,
        "meaning_digest": status.meaning_digest(meaning_now),
    })
    write_new_json(consumer / "research" / "reviews" / "consequence" / f"{f_rev['review_id'][:16]}.json", f_rev)
    commit_all(consumer, "admit consumer fidelity review")
    assert status.fidelity(Repo.open(consumer), item).label == "faithful"

    md_path = consumer / "research" / "items" / "consequence.md"
    md_path.write_text(md_path.read_text().replace("Proven over arbitrary spaces.", "No limit."))
    commit_all(consumer, "weaken consumer limits")
    repo2 = Repo.open(consumer)
    staled = status.fidelity(repo2, repo2.load_item("consequence"))
    assert staled is not None and staled.label.startswith("review stale: limits")


# Planted Case 3: Unused import ----------------------------------------------------------------------------------

def test_planted_unused_import_rejected_and_slips_when_guard_disabled(tmp_path):
    consumer, _ = make_consumer_repo(
        tmp_path,
        target_imports=[],  # declares [[imports]] for GCA.Proofs.Feedback, yet Lean never imports it!
        packages_dir_mode="config",
    )
    repo = Repo.open(consumer)
    item = repo.load_item("consequence")
    errs = imports.check_consumer_lean_usage(repo, item, commit=repo.trusted_commit)
    assert any("unused import" in e and GCA_MODULE in e for e in errs)
    assert imports.check_consumer_lean_usage(
        repo, item, commit=repo.trusted_commit, guards=frozenset({"unused_import"})
    ) == []


# Planted Case 4: Declaration not in source checked theorem list -------------------------------------------------

def test_planted_declaration_not_in_checked_list_rejected_vs_control(tmp_path):
    # `GCA.transfer_same` is declared inside GCA/Proofs/Feedback.lean, NOT in adaptive-feedback-separation checked list!
    consumer, _ = make_consumer_repo(
        tmp_path,
        theorems=["GCA.adaptive_transcript_same", "GCA.transfer_same"],
        packages_dir_mode="config",
    )
    repo = Repo.open(consumer)
    item = repo.load_item("consequence")
    resolved, errs = imports.resolve_imports(repo, item, commit=repo.trusted_commit)
    assert resolved == []
    assert any("GCA.transfer_same" in e and "not in source" in e for e in errs)

    # Guard disabled lets it slip through!
    slip, slip_errs = imports.resolve_imports(
        repo, item, commit=repo.trusted_commit, guards=frozenset({"checked_theorems"})
    )
    assert slip_errs == [] and len(slip) == 1


# Planted Case 5: Dependency without receipt ---------------------------------------------------------------------

def test_planted_dependency_without_receipt_and_official_mathlib_control(tmp_path):
    consumer, _ = make_consumer_repo(
        tmp_path,
        with_imports=False,
        target_imports=[GCA_MODULE, "Mathlib.Tactic"],  # imports custom GCA without [[imports]]; Mathlib is allowed
        packages_dir_mode="config",
    )
    repo = Repo.open(consumer)
    item = repo.load_item("consequence")
    errs = imports.check_consumer_lean_usage(repo, item, commit=repo.trusted_commit)
    assert len(errs) == 1 and "dependency without receipt" in errs[0] and GCA_MODULE in errs[0]
    assert "Mathlib.Tactic" not in errs[0]

    assert imports.check_consumer_lean_usage(
        repo, item, commit=repo.trusted_commit, guards=frozenset({"dependency_receipt"})
    ) == []


# Planted Case 6: Ambiguous module provider ----------------------------------------------------------------------

def test_planted_ambiguous_module_provider_and_root_collision(tmp_path):
    # Root collision: consumer [lean] roots claims "GCA"
    consumer_col, _ = make_consumer_repo(
        tmp_path / "col", roots=["Consumer", "GCA"], packages_dir_mode="config"
    )
    repo_col = Repo.open(consumer_col)
    _, errs_col = imports.resolve_imports(repo_col, repo_col.load_item("consequence"), commit=repo_col.trusted_commit)
    assert any("ambiguous module provider" in e and "consumer [lean].roots" in e for e in errs_col)

    # Two manifest packages both providing GCA.Proofs.Feedback
    consumer_dup, pkg_dest = make_consumer_repo(tmp_path / "dup", packages_dir_mode="config")
    other_pkg = pkg_dest.parent / "gca_mirror"
    shutil.copytree(pkg_dest, other_pkg)
    other_rev = git(other_pkg, "rev-parse", "HEAD")
    manifest_p = consumer_dup / "lake-manifest.json"
    mdata = json.loads(manifest_p.read_text())
    mdata["packages"].append({
        "url": "https://example.org/org/gca-mirror",
        "type": "git",
        "subDir": None,
        "scope": "",
        "rev": other_rev,
        "name": "gca_mirror",
        "manifestFile": "lake-manifest.json",
        "inputRev": "main",
        "inherited": False,
        "configFile": "lakefile.toml",
    })
    manifest_p.write_text(json.dumps(mdata, indent=2))
    commit_all(consumer_dup, "add second provider package")
    repo_dup = Repo.open(consumer_dup)
    _, errs_dup = imports.resolve_imports(repo_dup, repo_dup.load_item("consequence"), commit=repo_dup.trusted_commit)
    assert any("ambiguous module provider" in e and "multiple packages" in e for e in errs_dup)

    # Disabling `provider_uniqueness` bypasses ambiguity refusal
    _, errs_off = imports.resolve_imports(
        repo_dup, repo_dup.load_item("consequence"), commit=repo_dup.trusted_commit,
        guards=frozenset({"provider_uniqueness"}),
    )
    assert errs_off == []


# Wrapper mapping refusal on synthetic package fixture ------------------------------------------------------------

def test_sample_wrapper_proofs_item_rejected_until_bridge(tmp_path):
    consumer, _ = make_consumer_repo(tmp_path, packages_dir_mode="config")
    sample_pkg = tmp_path / "pkg-store" / "sample"
    make_source(sample_pkg, "prequential-code-length", "Sample.Information.Prequential", ["Sample.preq"], wrapper=True)
    git(sample_pkg, "config", "user.name", "Test")
    git(sample_pkg, "config", "user.email", "test@example.com")
    sample_rev = git(sample_pkg, "rev-parse", "HEAD")

    # Verify item prequential-code-length uses wrapper proofs (no [lean].solution).
    snap = imports.SnapshotRepo.open_at(sample_pkg, sample_rev)
    src_items, _ = snap.trusted_items()
    preq = src_items["prequential-code-length"]
    assert "proofs" in preq.lean and "solution" not in preq.lean
    # Any import pointing at its module without solution is refused with wrapper bridge message.
    valid_pass = next(
        r for r in snap.receipts("prequential-code-length")
        if not r.problems and r.data["assurance"] == "protected" and r.data["verdict"] == "pass"
    )
    valid_fid = next(
        r for r in snap.reviews("prequential-code-length")
        if not r.problems and r.data["kind"] == "fidelity" and r.data["verdict"] == "faithful"
    )
    mdata = json.loads((consumer / "lake-manifest.json").read_text())
    mdata["packages"].append({
        "url": "https://example.org/org/sample",
        "type": "git", "subDir": None, "scope": "",
        "rev": sample_rev, "name": "sample", "manifestFile": "lake-manifest.json",
        "inputRev": "main", "inherited": False, "configFile": "lakefile.toml",
    })
    (consumer / "lake-manifest.json").write_text(json.dumps(mdata, indent=2))
    md_path = consumer / "research" / "items" / "consequence.md"
    md_path.write_text(
        f'+++\nid = "consequence"\nkind = "result"\ntitle = "Sample wrapper"\nauthor = "agent:tester"\n'
        f'created = "2026-11-14"\nstatement = "S"\nclaim = "formal"\n'
        f'[lean]\ntarget = "research/targets/consequence.lean"\ntheorems = ["Consumer.consequence"]\n'
        f'solution = "Consumer.Proof"\n[[imports]]\npackage = "sample"\nitem = "prequential-code-length"\n'
        f'item_revision = "{preq.revision}"\nmodule = "Sample.Information.Prequential"\n'
        f'theorems = {json.dumps(preq.lean["theorems"][:1])}\n'
        f'receipt = "{valid_pass.data["receipt_id"]}"\nfidelity_review = "{valid_fid.data["review_id"]}"\n'
        f'+++\nBody\n'
    )
    commit_all(consumer, "test sample wrapper refusal")
    repo = Repo.open(consumer)
    _, errs = imports.resolve_imports(repo, repo.load_item("consequence"), commit=repo.trusted_commit)
    assert any("wrapper [lean].proofs" in e and "wrapper bridge" in e for e in errs)


# check.execute + replay integration with fake Lean adapter ------------------------------------------------------

class _FakeLeanAdapter:
    name = "lean-comparator"

    def applies(self, item):
        return bool(item.lean)

    def check(self, req):
        cand = req.repo.root / "Consumer" / "Proof.lean"
        tgt = req.repo.read_trusted("research/targets/consequence.lean")
        tgt_sha = sha256_hex(tgt)
        return CheckOutcome(
            verdict="pass",
            reasons=[],
            files={"Consumer/Proof.lean": sha256_hex(cand.read_bytes())},
            trusted_files={"research/targets/consequence.lean": tgt_sha},
            target={
                "path": "research/targets/consequence.lean",
                "sha256": tgt_sha,
                "commit": req.trusted_commit,
                "source": "trusted-commit",
                "theorems": list(req.item.lean["theorems"]),
            },
            environment={
                "toolchain": "v4.29.0",
                "lean_version": "Lean 4.29.0",
                "isolation": {"net": "off"},
                "tools": {
                    t: {"path": f"/usr/bin/{t}", "sha256": "b" * 64}
                    for t in ("comparator", "lean4export", "landrun", "nanoda")
                },
            },
            checked={
                "theorems": list(req.item.lean["theorems"]),
                "kernels": ["lean", "nanoda"],
                "permitted_axioms": [],
            },
            command=["fake-lean"],
            log="ok\n",
        )


def test_check_and_replay_roundtrip_and_missing_store_inconclusive(tmp_path, monkeypatch, capsys):
    consumer, pkg_dest = make_consumer_repo(tmp_path, packages_dir_mode="config")
    monkeypatch.setattr(check, "load_adapters", lambda: [_FakeLeanAdapter()])
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg-cache"))

    repo = Repo.open(consumer)
    item = repo.load_item("consequence")
    outcome, receipt = check.execute(repo, item, "protected", 60.0)
    assert outcome.verdict == "pass" and receipt is not None
    assert receipt_problems(receipt) == []
    assert receipt["inputs"]["imports"][0]["package"] == "gca"
    assert receipt["inputs"]["imports"][0]["manifest"]["rev"] == git(pkg_dest, "rev-parse", "HEAD")

    ev_path = consumer / "research" / "evidence" / "consequence" / f"{receipt['receipt_id'][:16]}.json"
    write_new_json(ev_path, receipt)
    commit_all(consumer, "admit consumer receipt")

    # Protected check reads [[imports]] from trusted_commit, ignoring unintegrated worktree edits.
    md_path = consumer / "research" / "items" / "consequence.md"
    saved_md = md_path.read_text()
    md_path.write_text(saved_md.replace("GCA.separation_witness", "GCA.vector_solves_both"))
    asked, prob = check.question_item(Repo.open(consumer), Repo.open(consumer).load_item("consequence"), "protected", Repo.open(consumer).trusted_commit)
    assert prob is None and asked.imports[0]["theorems"] == GCA_THEOREMS
    md_path.write_text(saved_md)

    # Replay succeeds (`match`) offline when package store holds pinned commit.
    monkeypatch.chdir(consumer)
    rc, rep_out, err = vl(capsys, "replay", "consequence", "--json")
    assert rc == 0, (rep_out, err)
    assert json.loads(rep_out)["result"] == "match"

    # When offline package store lacks the repo, replay returns inconclusive (`error`), never fetching.
    shutil.move(str(pkg_dest), str(tmp_path / "moved-away"))
    rc2, rep_out2, _ = vl(capsys, "replay", "consequence", "--json")
    assert rc2 == 3
    rep2 = json.loads(rep_out2)
    assert rep2["result"] == "error"
    assert any("stopped before reading the candidate: unsupported" in r for r in rep2["reasons"])


# N5: Local declaration & limits index as locator only, never evidence -------------------------------------------

def test_n5_index_locates_imported_decls_and_limits_as_locator_never_evidence(tmp_path, monkeypatch, capsys):
    consumer, _ = make_consumer_repo(tmp_path, packages_dir_mode="config")
    monkeypatch.chdir(consumer)

    rc, out, err = vl(capsys, "find", "transfer_same", "--json")
    assert rc == 0, (out, err)
    data = json.loads(out)
    assert data["index_disclaimer"] == index.INDEX_DISCLAIMER
    assert data["imported_declarations"]
    hit = data["imported_declarations"][0]
    assert hit["name"] == "GCA.transfer_same"
    assert hit["package"] == "gca"
    assert hit["locator_only"] is True

    rc_lim, lim_out, _ = vl(capsys, "find", "indistinguishability mechanism", "--json")
    assert rc_lim == 0
    lim_data = json.loads(lim_out)
    imp_hits = [r for r in lim_data["imported_items"] if r["id"] == GCA_ITEM_ID]
    assert len(imp_hits) == 1
    assert imp_hits[0]["locator_only"] is True
    pinned = Repo.open(consumer).load_item("consequence").imports[0]
    assert imp_hits[0]["evidence_locator"] == f"research/evidence/{GCA_ITEM_ID}/{pinned['receipt'][:16]}.json"
    assert imp_hits[0]["review_locator"] == f"research/reviews/{GCA_ITEM_ID}/{pinned['fidelity_review'][:16]}.json"

    # Search limits API marks both local and imported rows locator-only with disclaimer.
    idx = index.open_index(Repo.open(consumer))
    try:
        rows = idx.search_limits("indistinguishability")
        assert any(r["scope"] == "imported" and r["locator_only"] and r["disclaimer"] == index.INDEX_DISCLAIMER for r in rows)
    finally:
        idx.close()


def test_subdir_package_manifest_resolution(tmp_path):
    """Manifest `subDir` locates research/, config, and Lean files inside a nested package directory."""
    consumer, pkg_dest = make_consumer_repo(tmp_path / "base", packages_dir_mode="config")
    # Build a repo where the entire GCA snapshot lives under `packages/gca_sub/`.
    sub_repo = tmp_path / "pkg-store-sub" / "gca"
    sub_repo.mkdir(parents=True)
    git(sub_repo, "init", "-q", "-b", "main")
    git(sub_repo, "config", "user.name", "Test")
    git(sub_repo, "config", "user.email", "test@example.com")
    shutil.copytree(pkg_dest, sub_repo / "packages" / "gca_sub", ignore=shutil.ignore_patterns(".git"))
    sub_rev = commit_all(sub_repo, "nest gca under subDir")

    (consumer / "research" / "vl.toml").write_text(
        f'[project]\nname = "consumer"\n\n[lean]\nroots = ["Consumer"]\npackages = "{tmp_path / "pkg-store-sub"}"\n'
    )
    manifest_p = consumer / "lake-manifest.json"
    mdata = json.loads(manifest_p.read_text())
    mdata["packages"][0]["subDir"] = "packages/gca_sub"
    mdata["packages"][0]["rev"] = sub_rev
    manifest_p.write_text(json.dumps(mdata, indent=2))
    commit_all(consumer, "use subDir manifest package")

    repo = Repo.open(consumer)
    res, errs = imports.resolve_imports(repo, repo.load_item("consequence"), commit=repo.trusted_commit)
    assert errs == [] and len(res) == 1
    assert res[0].manifest_sub_dir == "packages/gca_sub"
    assert res[0].manifest_rev == sub_rev
    assert res[0].source_status == "verified"


def test_offline_package_module_lookup_uses_library_globs_and_srcdir(tmp_path):
    package = tmp_path
    git(package, "init", "-qb", "main")
    git(package, "config", "user.name", "Test")
    git(package, "config", "user.email", "test@example.com")
    (package / "lakefile.toml").write_text(
        '[[lean_lib]]\nname = "Pkg"\nroots = ["Pkg.Core"]\nglobs = ["Pkg.Extra.*"]\nsrcDir = "src"\n')
    for module in ("Core", "Extra", "Unrelated"):
        path = package / f"src/Pkg/{module}.lean"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("theorem found : True := trivial\n")
    pin = commit_all(package, "package with a glob")
    assert imports.read_package_module(package, pin, None, "Pkg.Core")[0] == "src/Pkg/Core.lean"
    assert imports.read_package_module(package, pin, None, "Pkg.Extra")[0] == "src/Pkg/Extra.lean"
    assert imports.read_package_module(package, pin, None, "Pkg.Unrelated") == (None, None)
