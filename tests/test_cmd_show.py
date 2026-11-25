from __future__ import annotations

import json
from pathlib import Path

from verifylab.cli import main
from verifylab.records import seal_receipt, sha256_hex, write_new_json

from conftest import synthetic_receipt, commit_all, git, item_question, write_item


def vl(capsys, *args):
    rc = main(list(args))
    captured = capsys.readouterr()
    return rc, captured.out, captured.err


def make_item(root: Path, item_id: str, kind: str = "result", body: str = "Body.\n", **fields) -> Path:
    meta = {"id": item_id, "kind": kind, "title": f"Item {item_id}", "author": "agent:test", "created": "2026-10-01"}
    if kind == "result":
        meta.update(statement=f"Statement of {item_id}.", claim="formal")
    elif kind in ("question", "conjecture", "intuition"):
        meta["statement"] = f"Statement of {item_id}."
    meta.update(fields)
    lines = []
    for key, value in meta.items():
        if isinstance(value, dict):
            value = "{ " + ", ".join(f"{k} = {json.dumps(v)}" for k, v in value.items()) + " }"
        else:
            value = json.dumps(value)
        lines.append(f"{key} = {value}")
    path = root / "research" / "items" / f"{item_id}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("+++\n" + "\n".join(lines) + "\n+++\n" + body)
    return path


def make_receipt(root: Path, item_id: str = "add-zero", assurance: str = "protected", verdict: str = "pass",
                 finished: str = "t1", extra: dict | None = None) -> Path:
    lean = "Fixture/Basic.lean"
    receipt = synthetic_receipt(root, dict(
        item=item_id, item_revision="a" * 40, question_digest=item_question(root, item_id),
        adapter="lean-comparator", assurance=assurance, verdict=verdict,
        reasons=[] if verdict == "pass" else ["mismatch"],
        inputs={"digest": "d", "files": {lean: sha256_hex((root / lean).read_bytes())}},
        environment={}, checked={"declarations": ["add_zero'"], "axioms": []}, command=["vl"],
        started_at="t0", finished_at=finished, tool_version="vl 0.1.0", **({"extra": extra} if extra else {}),
    ))
    path = root / "research" / "evidence" / item_id / f"{receipt['receipt_id'][:16]}.json"
    write_new_json(path, receipt)
    return path


def test_card_puts_corrections_first_then_limits(research_repo, monkeypatch, capsys):
    monkeypatch.chdir(research_repo)
    assert vl(capsys, "review", "add-zero", "--kind", "correction", "--author", "human:ada", "--human-approved",
              "--text", "Fails for integers: the card must say so first.")[0] == 0
    rc, out, _ = vl(capsys, "show", "add-zero")
    assert rc == 0
    order = ["CORRECTIONS AND RETRACTIONS", "Fails for integers", "LIMITS", "Only natural numbers.", "ASSUMPTIONS",
             "STATEMENT", "For every n, n + 0 = n.", "STATUS", "EVIDENCE", "REVIEWS", "RELATIONS", "BODY", "Body text."]
    positions = [out.index(marker) for marker in order]
    assert positions == sorted(positions), out
    assert "READ FIRST: 1 correction" in out.splitlines()[0]
    assert "NOT admitted" in out  # an uncommitted correction is shown, and labelled as not admitted


def test_brief_cuts_loudly_and_keeps_corrections_before_the_body(research_repo, monkeypatch, capsys):
    root = research_repo
    monkeypatch.chdir(root)
    long_body = "".join(f"Paragraph {i}: details nobody needs first.\n" for i in range(60))
    make_item(root, "add-zero", body=long_body, statement="For every n, n + 0 = n.",
              limits=["Only natural numbers."])
    vl(capsys, "review", "add-zero", "--kind", "correction", "--author", "human:ada", "--human-approved",
       "--text", "Does not cover integers.")
    rc, out, _ = vl(capsys, "show", "add-zero", "--brief", "--budget", "600")
    assert rc == 0
    assert len(out.rstrip("\n")) <= 600
    assert "Does not cover integers." in out
    assert out.index("Does not cover integers.") < out.index("Only natural numbers.")
    assert "[TRUNCATED:" in out and "run vl show add-zero for the full card]" in out
    assert "body" in out[out.index("[TRUNCATED:"):]  # the marker names the body as cut
    assert "Paragraph 59" not in out

    rc, out, _ = vl(capsys, "show", "add-zero", "--brief", "--budget", "100000")
    assert "[TRUNCATED" not in out and "Paragraph 59" in out

    rc, out, _ = vl(capsys, "show", "add-zero", "--brief", "--budget", "600", "--json")
    data = json.loads(out)
    assert data["items"][0]["truncated"] is True and "TRUNCATED" in data["brief"]


def test_brief_defaults_to_8000_characters_and_points_to_library_files_without_inlining_them(research_repo,
                                                                                            monkeypatch, capsys):
    """A brief stays within 8,000 characters unless --budget says otherwise, and a source names its file: the brief
    never reads it, however much budget is left."""
    root = research_repo
    monkeypatch.chdir(root)
    library = root / "library" / "summaries"
    library.mkdir(parents=True)
    (library / "tao-blog.md").write_text("LIBRARY TEXT, never in a brief.\n" * 400)
    make_item(root, "tao-blog", kind="source", ref="library/summaries/tao-blog.md", access="full-text-read",
              body="Read section 3 for the toolkit; see [[add-zero]].\n")
    make_item(root, "add-zero", body="".join(f"Paragraph {i}: details nobody needs first.\n" for i in range(250)),
              statement="For every n, n + 0 = n.", cites=["tao-blog"])
    rc, out, _ = vl(capsys, "show", "add-zero", "tao-blog", "--brief", "--json")
    data = json.loads(out)
    assert rc == 0 and data["budget"] == 8000 and 7000 < data["chars"] <= 8000
    assert "Paragraph 120" in data["brief"] and "Paragraph 249" not in data["brief"]    # 4,000 cut it near 60
    assert data["items"][0]["truncated"] is True
    assert "source: library/summaries/tao-blog.md" in data["brief"]
    assert "LIBRARY TEXT" not in data["brief"] and data["items"][1]["truncated"] is False
    assert "details nobody needs first" not in data["brief"].split("tao-blog@")[1]       # a link is not expanded


def test_brief_over_many_ids_never_drops_a_header_or_the_correction_flag(research_repo, monkeypatch, capsys):
    root = research_repo
    monkeypatch.chdir(root)
    make_item(root, "other", body="x\n" * 50, limits=["Small n only."])
    vl(capsys, "review", "add-zero", "--kind", "correction", "--author", "human:ada", "--human-approved",
       "--text", "Wrong for n < 0.")
    for budget in range(150, 2500, 70):
        rc, out, _ = vl(capsys, "show", "add-zero", "other", "--brief", "--budget", str(budget), "--json")
        data = json.loads(out)
        text = data["brief"]
        assert "add-zero@" in text and "other@" in text
        assert "READ FIRST: 1 correction" in text
        assert data["chars"] <= budget or "[OVER BUDGET" in text
        for meta in data["items"]:
            assert meta["truncated"] == (f"run vl show {meta['id']} for the full card]" in text)
        # Strict priority: the second item never shows its body while the first item's correction is cut.
        first, second = data["items"]
        if "corrections" in first["omitted"] and "Wrong for n < 0." not in text:
            assert "x\nx" not in text


def test_impact_is_the_transitive_closure_plus_citing_explanations(research_repo, monkeypatch, capsys):
    root = research_repo
    monkeypatch.chdir(root)
    make_item(root, "lemma-b", uses=["add-zero"])
    make_item(root, "theorem-c", uses=["lemma-b"])
    make_item(root, "unrelated", uses=[])
    make_item(root, "why-c", kind="explanation", body="Explains C.\n", cites=["theorem-c"])
    make_item(root, "why-other", kind="explanation", body="Explains nothing related.\n", cites=["unrelated"])
    rc, out, _ = vl(capsys, "show", "add-zero", "--impact", "--json")
    data = json.loads(out)["impact"][0]
    users = {u["id"]: u for u in data["users"]}
    assert set(users) == {"lemma-b", "theorem-c"}
    assert users["lemma-b"]["direct"] and users["theorem-c"]["via"] == "lemma-b"
    assert [e["id"] for e in data["explanations"]] == ["why-c"]
    rc, out, _ = vl(capsys, "show", "add-zero", "--impact")
    assert "theorem-c (unverified) via lemma-b" in out and "why-c" in out and "unrelated" not in out


def test_show_old_revision_says_so(research_repo, monkeypatch, capsys):
    root = research_repo
    monkeypatch.chdir(root)
    old = git(root, "hash-object", "research/items/add-zero.md").strip()
    write_item(root, "add-zero")
    path = root / "research" / "items" / "add-zero.md"
    path.write_text(path.read_text().replace("For every n, n + 0 = n.", "For every n, 0 + n = n."))
    commit_all(root)
    rc, out, _ = vl(capsys, "show", f"add-zero@{old[:8]}")
    assert rc == 0 and "showing OLD revision" in out
    # The statement status and fidelity hold for is the trusted one; the older text is shown apart, not reviewed.
    assert out.index("0 + n = n") < out.index("TEXT OF THE REVISION ASKED FOR") < out.index("n + 0 = n")
    assert "the revision asked for, not reviewed: statement" in out.splitlines()[0]
    rc, out, _ = vl(capsys, "show", "add-zero@deadbeef")
    assert "not in the git object store" in out and "CURRENT revision" in out and "0 + n = n" in out


def test_receipts_summary_shows_admission_and_staleness(research_repo, monkeypatch, capsys):
    root = research_repo
    monkeypatch.chdir(root)
    make_receipt(root)
    rc, out, _ = vl(capsys, "show", "add-zero")
    assert "pending-admission" in out and "NOT admitted (not on the trusted ref)" in out
    commit_all(root)
    rc, out, _ = vl(capsys, "show", "add-zero")
    assert "status: check passed;" in out and "· admitted ·" in out and "inputs fresh" in out
    assert ('checked: {"axioms": [], "declarations": ["add_zero\'"], "kernels": ["lean", "nanoda"], '
            '"permitted_axioms": [], "theorems": ["Fixture.receipt"]}') in out
    (root / "Fixture" / "Basic.lean").write_text("-- changed\n")
    rc, out, _ = vl(capsys, "show", "add-zero")
    assert "verified-stale" in out and "STALE inputs: Fixture/Basic.lean" in out


def test_show_usage_errors_exit_2(research_repo, monkeypatch, capsys):
    monkeypatch.chdir(research_repo)
    assert vl(capsys, "show", "nope")[0] == 2
    assert vl(capsys, "show", "add-zero", "--budget", "100")[0] == 2
    assert vl(capsys, "show", "add-zero", "--brief", "--impact")[0] == 2
    assert vl(capsys, "show", "Bad Id")[0] == 2


def test_show_prints_the_phase_timing_of_the_newest_lean_receipt(research_repo, monkeypatch, capsys):
    root = research_repo
    monkeypatch.chdir(root)
    make_receipt(root, finished="2026-10-01T10:00:00+00:00", extra={"seconds": 9.0, "phases": {"prepare": 9.0}})
    make_receipt(root, finished="2026-10-01T11:00:00+00:00", extra={
        "seconds": 5.1, "phases": {"kernel_lean": 0.2, "build_challenge": 0.8, "prepare": 0.4, "kernel_nanoda": 0.14},
        "modules": [{"module": "Fixture.Defs", "ms": 351, "side": "challenge"}], "memory_peak_bytes": 370 * 2**20})
    rc, out, _ = vl(capsys, "show", "add-zero")
    [line] = [x for x in out.splitlines() if "timing of the newest Lean receipt" in x]
    assert "5.1 s total; prepare 0.4 s, build_challenge 0.8 s, kernel_nanoda 0.14 s, kernel_lean 0.2 s" in line, line
    assert line.endswith("; slowest module Fixture.Defs 351 ms; peak memory 370 MiB")
    card = json.loads(vl(capsys, "show", "add-zero", "--json")[1])["items"][0]
    assert card["timing"]["phases"]["build_challenge"] == 0.8
    make_receipt(root, finished="2026-10-01T12:00:00+00:00")         # newest receipt measured nothing: no line
    assert "timing of the newest" not in vl(capsys, "show", "add-zero")[1]


def test_ctrl_c_ends_a_command_with_one_line_and_no_traceback(research_repo, monkeypatch, capsys):
    from verifylab.commands import show

    def interrupted(args):
        raise KeyboardInterrupt
    monkeypatch.chdir(research_repo)
    monkeypatch.setattr(show, "run", interrupted)
    rc, out, err = vl(capsys, "show", "add-zero")
    assert rc == 130 and err == "vl: interrupted\n" and out == ""


def test_show_snapshot_gives_committed_source_without_item_narrative_or_reviews(research_repo, monkeypatch, capsys):
    root = research_repo
    monkeypatch.chdir(root)
    (root / "Fixture" / "Deep.lean").write_text("namespace Fixture\ndef deepDef : Nat := 7\nend Fixture\n")
    (root / "Fixture" / "Mid.lean").write_text("import Fixture.Deep\nnamespace Fixture\ndef midDef : Nat := deepDef\nend Fixture\n")
    (root / "research" / "targets").mkdir(parents=True, exist_ok=True)
    target = root / "research" / "targets" / "snap.lean"
    target.write_text("import Fixture.Mid\nnamespace VL.Snap\ntheorem main : Fixture.midDef = 7 := rfl\nend VL.Snap\n")
    make_item(
        root,
        "snap",
        title="SENTINEL_TITLE",
        statement="SENTINEL_STATEMENT",
        limits=["SENTINEL_LIMIT"],
        assumptions=["SENTINEL_ASSUMPTION"],
        lean={"target": "research/targets/snap.lean", "theorems": ["VL.Snap.main"]},
        body="SENTINEL_BODY narrative prose.\n",
    )
    commit_all(root, "admit snap target")

    card = json.loads(vl(capsys, "show", "snap", "--json")[1])["items"][0]
    assert vl(capsys, "review", "snap", "--kind", "fidelity", "--verdict", "faithful",
              "--expected-meaning-digest", card["meaning_digest"],
              "--text", "SENTINEL_FIDELITY_NOTE", "--author", "agent:prev")[0] == 0
    assert vl(capsys, "review", "snap", "--kind", "correction",
              "--text", "SENTINEL_CORRECTION", "--author", "agent:prev")[0] == 0
    commit_all(root, "admit reviews")

    # Uncommitted worktree edit must NOT leak into snapshot
    target.write_text("import Fixture.Mid\n-- UNCOMMITTED_WORKTREE_EDIT\n")
    (root / "research" / "items" / "snap.md").write_text(
        (root / "research" / "items" / "snap.md").read_text().replace("SENTINEL_TITLE", "WORKTREE_TITLE")
    )

    rc, text_out, err = vl(capsys, "show", "snap", "--snapshot")
    assert rc == 0 and err == ""
    trusted_head = git(root, "rev-parse", "trusted").strip()
    assert card["meaning_digest"] in text_out
    assert trusted_head in text_out
    assert "theorem main : Fixture.midDef = 7 := rfl" in text_out
    assert "def midDef : Nat := deepDef" in text_out
    assert "def deepDef : Nat := 7" in text_out
    for sentinel in ("SENTINEL_TITLE", "SENTINEL_STATEMENT", "SENTINEL_LIMIT", "SENTINEL_ASSUMPTION",
                     "SENTINEL_BODY", "SENTINEL_FIDELITY_NOTE", "SENTINEL_CORRECTION", "UNCOMMITTED_WORKTREE_EDIT",
                     "WORKTREE_TITLE", "unverified", "faithful"):
        assert sentinel not in text_out

    rc, json_out, _ = vl(capsys, "show", "snap", "--snapshot", "--json")
    assert rc == 0
    pkt = json.loads(json_out)["items"][0]
    assert pkt["meaning_digest"] == card["meaning_digest"]
    assert pkt["trusted_commit"] == trusted_head
    assert [d["module"] for d in pkt["definitions"]] == ["Fixture.Mid", "Fixture.Deep"]
    assert pkt["representation"] == "committed source including comments; no elaboration"
    assert set(pkt.keys()) == {"representation", "id", "revision", "trusted_ref", "trusted_commit", "meaning_digest", "target", "definitions"}

    # Control: normal `vl show` DOES include the prose and reviews that --snapshot stripped
    normal_out = vl(capsys, "show", "snap")[1]
    assert "SENTINEL_STATEMENT" in normal_out and "SENTINEL_FIDELITY_NOTE" in normal_out

    assert vl(capsys, "show", "snap@abcd1234", "--snapshot")[0] == 2
    assert vl(capsys, "show", "snap", "--snapshot", "--brief")[0] == 2
    assert vl(capsys, "show", "snap", "--snapshot", "--impact")[0] == 2


def test_show_reports_trusted_reading_size_and_omissions_without_changing_verdict(research_repo, monkeypatch, capsys):
    root = research_repo
    monkeypatch.chdir(root)
    item = root / "research" / "items" / "add-zero.md"
    item.write_text(item.read_text().replace("+++\nBody", '[lean]\ntarget = "Fixture/Target.lean"\n'
                                             'theorems = ["main"]\n+++\nBody'))
    (root / "Fixture").mkdir(exist_ok=True)
    defs = root / "Fixture" / "Defs.lean"
    defs.write_text("def helper : Nat := 1\n")
    target = root / "Fixture" / "Target.lean"
    # Clean control: only in-project imports.
    target.write_text("import Fixture.Defs\ntheorem main : True := trivial\n")
    commit_all(root, "complete target")
    rc, out, _ = vl(capsys, "show", "add-zero", "--json")
    card = json.loads(out)["items"][0]
    rs = card["reading_size"]
    assert rc == 0 and rs["complete"] is True and rs["omissions"] == []
    assert rs["files"] == 2 and rs["lines"] == 3 and card["status"]["label"] == "unverified"
    _, brief, _ = vl(capsys, "show", "add-zero", "--brief")
    assert "trusted reading: 2 file(s), 3 line(s)," in brief and "never a verdict" in brief

    # External import omitted: reported clearly in omissions; status stays unverified.
    target.write_text("import Mathlib.Init\nimport Fixture.Defs\ntheorem main : True := trivial\n")
    commit_all(root, "external import")
    card2 = json.loads(vl(capsys, "show", "add-zero", "--json")[1])["items"][0]
    rs2 = card2["reading_size"]
    assert rs2["complete"] is False and any("Mathlib.Init" in o for o in rs2["omissions"])
    assert rs2["files"] == 2 and card2["status"]["label"] == "unverified"
    assert "omissions: external imports omitted (Mathlib.Init)" in vl(capsys, "show", "add-zero")[1]

    # Missing in-project import on trusted ref: reported as incomplete closure.
    defs.unlink()
    commit_all(root, "drop defs")
    rs3 = json.loads(vl(capsys, "show", "add-zero", "--json")[1])["items"][0]["reading_size"]
    assert rs3["complete"] is False and any("import closure incomplete" in o for o in rs3["omissions"])
