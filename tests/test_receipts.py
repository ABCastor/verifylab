"""`vl receipts export|import` and the foreign quarantine (ADR 0015, proposed): the receipts of a machine the person
controls count like local ones once admitted, those of any other machine are shown and counted for nothing until
`vl replay` reproduces them. Two machines are two repositories (the second a clone of the first) under two
machine.toml files; the checker is the recording stub of test_replay, so no Lean runs. Each guard has a planted case
next to a clean control."""

from __future__ import annotations

import hashlib
import io
import json
import shutil
import tarfile
from pathlib import Path

import pytest

from verifylab import cli, machine
from verifylab.commands import check, receipts
from verifylab.records import seal_receipt, write_new_json
from verifylab.repo import Repo
from verifylab.status import derive

from conftest import commit_all, git, synthetic_receipt, write_machine
from batch_helpers import add_lean_items
from test_replay import Recording

TOOLCHAIN = "leanprover/lean4:v4.24.0"      # the one synthetic_receipt records
TOOLS = ("comparator", "lean4export", "landrun", "nanoda")


def as_machine(name: str | None, trust: str | None = None, pins: str | None = "c" * 64) -> None:
    """This test process is machine `name` now: its machine.toml, and a tools store that pins the fixture tools'
    sha256 (`pins`; None: no store)."""
    path = write_machine()
    if name:
        path.write_text(path.read_text() + f'\n[machine]\nname = "{name}"\n' + (f'trust = "{trust}"\n' if trust else ""))
    machine.forget()
    store = machine.store_root() / machine.toolchain_key(TOOLCHAIN)
    shutil.rmtree(store, ignore_errors=True)
    if pins:
        store.mkdir(parents=True, exist_ok=True)
        machine.write_revisions(store, {tool: pins for tool in TOOLS})


@pytest.fixture
def machines(research_repo, monkeypatch, tmp_path):
    """(thinkpad, hp): the integrator's repository on the trusted branch, and a clone of it where `hp` runs checks."""
    thinkpad = research_repo
    add_lean_items(thinkpad)
    (thinkpad / ".gitignore").write_text(".vl-cache/\n")
    commit_all(thinkpad, "ignore the cache")
    hp = tmp_path / "hp"
    git(tmp_path, "clone", "-q", str(thinkpad), str(hp))
    git(hp, "config", "vl.trustedRef", "trusted")
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setattr(check, "load_adapters", lambda: [Recording()])
    Recording.verdict, Recording.seen = "pass", []
    return thinkpad, hp


def run(root: Path, monkeypatch, capsys, *argv) -> tuple[int, str, str]:
    monkeypatch.chdir(root)
    code = cli.main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def check_on_hp(hp, monkeypatch, capsys, name="hp", trust=None) -> dict:
    """`vl check` at `hp` as machine `name`: the receipt it wrote."""
    as_machine(name, trust)
    code, out, _ = run(hp, monkeypatch, capsys, "check", "add-zero", "--json")
    assert code == 0, out
    return json.loads((hp / json.loads(out)["receipt"]).read_text())


def export_from(hp, monkeypatch, capsys, tmp_path, *extra) -> Path:
    bundle = tmp_path / f"out{len(list(tmp_path.glob('out*.tar')))}.tar"
    code, out, err = run(hp, monkeypatch, capsys, "receipts", "export", str(bundle), *extra)
    assert code == 0, err
    return bundle


def status_of(root: Path, item_id: str = "add-zero"):
    repo = Repo.open(root)
    items, _ = repo.load_items()
    return derive(repo, items[item_id], items)


def make_bundle(path: Path, files: dict[str, bytes], name: str = "hp", digests: dict[str, str] | None = None,
                extra_members: list[tarfile.TarInfo] = ()) -> Path:
    manifest = {"schema": receipts.SCHEMA, "machine": {"name": name, "trust": "own"},
                "receipts": digests or {n: hashlib.sha256(d).hexdigest() for n, d in files.items()}}
    with tarfile.open(path, "w") as tar:
        for member, data in [(receipts.MANIFEST, json.dumps(manifest).encode()), *files.items()]:
            info = tarfile.TarInfo(member)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
        for info in extra_members:
            tar.addfile(info)
    return path


def members(bundle: Path) -> dict[str, bytes]:
    with tarfile.open(bundle) as tar:
        return {i.name: tar.extractfile(i).read() for i in tar}


def evidence(root: Path) -> list[Path]:
    return sorted((root / "research" / "evidence" / "add-zero").glob("*.json"))


# The machine file ----------------------------------------------------------------------------------------------------

def test_the_machine_table_is_read_checked_and_recorded_in_the_receipt_policy(tmp_path):
    path = tmp_path / "machine.toml"
    path.write_text('[machine]\nname = "hp-8c"\ntrust = "foreign"\n')
    loaded = machine.load(path)
    assert (loaded.name, loaded.trust) == ("hp-8c", "foreign")
    assert machine.policy_record(loaded)["machine"] == {"name": "hp-8c", "trust": "foreign"}
    path.write_text("")                                           # control: unnamed, own by default, nothing recorded
    machine.forget()
    assert (machine.load(path).name, machine.load(path).trust) == (None, "own")
    assert "machine" not in machine.policy_record(machine.load(path))
    for bad in ('name = "a b"', 'name = 3', 'name = ""', 'trust = "mine"', 'colour = "red"'):
        path.write_text(f"[machine]\n{bad}\n")
        machine.forget()
        with pytest.raises(machine.MachineError, match=r"\[machine\]"):
            machine.load(path)


# Export --------------------------------------------------------------------------------------------------------------

def test_export_bundles_only_the_protected_receipts_the_trusted_ref_lacks(machines, monkeypatch, capsys, tmp_path):
    thinkpad, hp = machines
    receipt = check_on_hp(hp, monkeypatch, capsys)
    assert receipt["environment"]["machine_policy"]["machine"] == {"name": "hp", "trust": "own"}
    bundle = export_from(hp, monkeypatch, capsys, tmp_path)
    found = members(bundle)
    name = f"evidence/add-zero/{receipt['receipt_id'][:16]}.json"
    assert set(found) == {receipts.MANIFEST, name} and json.loads(found[name]) == receipt
    manifest = json.loads(found[receipts.MANIFEST])
    assert manifest["machine"] == {"name": "hp", "trust": "own"} and list(manifest["receipts"]) == [name]
    # control: once the trusted ref holds it, and for an exploratory receipt, there is nothing new to export
    commit_all(hp, "admit")
    assert run(hp, monkeypatch, capsys, "check", "add-zero", "--explore")[0] == 0
    assert any((hp / ".vl-cache" / "explore" / "add-zero").glob("*.json"))
    again = export_from(hp, monkeypatch, capsys, tmp_path)
    assert set(members(again)) == {receipts.MANIFEST}


def test_export_needs_a_machine_name_a_new_file_and_honours_ids(machines, monkeypatch, capsys, tmp_path):
    _, hp = machines
    check_on_hp(hp, monkeypatch, capsys)
    as_machine(None)
    code, _, err = run(hp, monkeypatch, capsys, "receipts", "export", str(tmp_path / "x.tar"))
    assert code == 2 and "no name" in err and not (tmp_path / "x.tar").exists()
    as_machine("hp")
    (tmp_path / "ids").write_text("# none of ours\nother-item\n")
    assert set(members(export_from(hp, monkeypatch, capsys, tmp_path, "--ids", str(tmp_path / "ids")))) == {
        receipts.MANIFEST}                                         # --ids narrows
    bundle = export_from(hp, monkeypatch, capsys, tmp_path)
    assert len(members(bundle)) == 2                               # control: all, when not narrowed
    code, _, err = run(hp, monkeypatch, capsys, "receipts", "export", str(bundle))
    assert code == 2 and "exists" in err                           # never over a file


# Own receipts count, foreign ones do not -------------------------------------------------------------------------------

def test_own_receipts_of_another_machine_count_once_admitted_whatever_its_machine_policy(
        machines, monkeypatch, capsys, tmp_path):
    """The key requirement: a receipt checked on the HP, under its own machine file, tools store, launchers, XDG
    variables and paths, is `verified` on the integrator's machine when the tool pins agree."""
    thinkpad, hp = machines
    receipt = check_on_hp(hp, monkeypatch, capsys)
    policy = receipt["environment"]["machine_policy"]
    policy.update(config="/fixture/machine-b/.config/verifylab/machine.toml", tools_store="/fixture/machine-b/.local/share/verifylab/tools",
                  overridden_by={"XDG_DATA_HOME": "/srv/hp-data"}, launchers={"bwrap": "/opt/hp/bwrap"})
    receipt["environment"].update(toolchain_dir="/fixture/machine-b/.elan/toolchains/x", memory_cap="3G",
                                  tools={t: {"path": f"/fixture/machine-b/tools/{t}", "sha256": "c" * 64} for t in TOOLS})
    receipt = seal_receipt({k: v for k, v in receipt.items() if k != "receipt_id"})
    bundle = make_bundle(tmp_path / "hp.tar", {f"evidence/add-zero/{receipt['receipt_id'][:16]}.json":
                                               json.dumps(receipt).encode()})
    as_machine("thinkpad")
    code, out, err = run(thinkpad, monkeypatch, capsys, "receipts", "import", str(bundle), "--from", "hp",
                         "--trust", "own", "--json")
    assert code == 0, err
    result = json.loads(out)
    assert len(result["imported"]) == 1 and result["refused"] == []
    assert [p.name for p in evidence(thinkpad)] == [f"{receipt['receipt_id'][:16]}.json"]
    assert status_of(thinkpad).label == "pending-admission"                       # a proposal until the integrator admits
    code, _, err = run(thinkpad, monkeypatch, capsys, "admit")
    assert code == 0, err
    verified = status_of(thinkpad)
    assert verified.label == "verified" and verified.receipt.endswith(f"{receipt['receipt_id'][:16]}.json")
    assert any("XDG_DATA_HOME=/srv/hp-data" in n for n in verified.notes)         # named, not a reason to stale it
    assert not any("changed" in n for n in verified.notes)


def test_foreign_receipts_are_shown_and_count_for_nothing(machines, monkeypatch, capsys, tmp_path):
    thinkpad, hp = machines
    receipt = check_on_hp(hp, monkeypatch, capsys, name="rented")
    bundle = export_from(hp, monkeypatch, capsys, tmp_path)
    as_machine("thinkpad", pins=None)                       # foreign needs no pin: replay is the judge
    code, out, err = run(thinkpad, monkeypatch, capsys, "receipts", "import", str(bundle), "--from", "rented",
                         "--trust", "foreign")
    assert code == 0, err
    name = f"{receipt['receipt_id'][:16]}.json"
    quarantined = thinkpad / ".vl-cache" / "foreign" / "rented" / "add-zero" / name
    assert quarantined.is_file() and evidence(thinkpad) == []
    assert status_of(thinkpad).label == "unverified"                              # counted for nothing
    code, out, _ = run(thinkpad, monkeypatch, capsys, "admit")
    assert code == 0 and "nothing to admit" in out
    code, shown, _ = run(thinkpad, monkeypatch, capsys, "show", "add-zero")
    assert f"{name}: foreign, not counted until vl replay (from machine rented" in shown
    code, shown, _ = run(thinkpad, monkeypatch, capsys, "show", "add-zero", "--brief")
    assert "foreign, not counted until vl replay" in shown
    code, shown, _ = run(thinkpad, monkeypatch, capsys, "show", "add-zero", "--json")
    card = json.loads(shown)["items"][0]
    assert card["foreign_receipts"][0]["machine"] == "rented" and card["status"]["label"] == "unverified"
    # control: the same bundle as an own import, with the tool pins here, is a proposal that counts once admitted
    as_machine("thinkpad")
    assert run(thinkpad, monkeypatch, capsys, "receipts", "import", str(bundle), "--from", "rented",
               "--trust", "own")[0] == 0
    assert status_of(thinkpad).label == "pending-admission"
    assert status_of(thinkpad).label != "unverified"


def test_a_foreign_receipt_is_promoted_by_a_matching_replay_and_by_nothing_else(
        machines, monkeypatch, capsys, tmp_path):
    thinkpad, hp = machines
    receipt = check_on_hp(hp, monkeypatch, capsys, name="rented")
    bundle = export_from(hp, monkeypatch, capsys, tmp_path)
    as_machine("thinkpad")
    run(thinkpad, monkeypatch, capsys, "receipts", "import", str(bundle), "--from", "rented", "--trust", "foreign")
    quarantined = thinkpad / ".vl-cache" / "foreign" / "rented" / "add-zero" / f"{receipt['receipt_id'][:16]}.json"
    Recording.verdict = "fail"                                                    # planted: this machine cannot reproduce it
    code, out, _ = run(thinkpad, monkeypatch, capsys, "replay", "add-zero", "--json")
    assert code == 1 and json.loads(out)["result"] == "mismatch" and json.loads(out)["promoted"] is None
    assert quarantined.is_file() and evidence(thinkpad) == [] and status_of(thinkpad).label == "unverified"
    Recording.verdict = "pass"                                                    # control: it reproduces
    code, out, err = run(thinkpad, monkeypatch, capsys, "replay", "add-zero", "--receipt", receipt["receipt_id"][:10],
                         "--json")
    assert code == 0, err
    assert not quarantined.exists() and len(evidence(thinkpad)) == 1
    promoted = json.loads(evidence(thinkpad)[0].read_text())                      # the replay's receipt, made here
    assert json.loads(out)["promoted"] == f"research/evidence/add-zero/{promoted['receipt_id'][:16]}.json"
    assert promoted["environment"]["machine_policy"]["machine"]["name"] == "thinkpad"
    assert (promoted["verdict"], promoted["inputs"]["digest"]) == (receipt["verdict"], receipt["inputs"]["digest"])
    assert status_of(thinkpad).label == "pending-admission"                       # still the integrator's to admit
    assert run(thinkpad, monkeypatch, capsys, "admit")[0] == 0
    assert status_of(thinkpad).label == "verified"


# Guards --------------------------------------------------------------------------------------------------------------

def test_a_tampered_receipt_is_refused_and_a_tampered_bundle_is_read_by_nothing(machines, monkeypatch, capsys, tmp_path):
    thinkpad, hp = machines
    check_on_hp(hp, monkeypatch, capsys)
    found = members(export_from(hp, monkeypatch, capsys, tmp_path))
    name = next(n for n in found if n != receipts.MANIFEST)
    as_machine("thinkpad")
    forged = json.loads(found[name])
    forged["verdict"], forged["reasons"] = "fail", ["edited"]                    # sealed no more: receipt_id no longer fits
    tampered = make_bundle(tmp_path / "tampered.tar", {name: json.dumps(forged).encode()})
    code, out, _ = run(thinkpad, monkeypatch, capsys, "receipts", "import", str(tampered), "--from", "hp",
                       "--trust", "own", "--json")
    refusal = json.loads(out)["refused"]
    assert code == 1 and "receipt_id does not match its content" in refusal[0]["reason"] and evidence(thinkpad) == []
    # a member altered after export, whose manifest digest still says the old bytes: nothing at all is read
    after = make_bundle(tmp_path / "after.tar", {name: json.dumps(forged).encode()},
                        digests={name: hashlib.sha256(found[name]).hexdigest()})
    code, _, err = run(thinkpad, monkeypatch, capsys, "receipts", "import", str(after), "--from", "hp", "--trust", "own")
    assert code == 2 and "differ from the digests in the manifest" in err and evidence(thinkpad) == []
    # control: the bundle as exported is imported
    clean = make_bundle(tmp_path / "clean.tar", {name: found[name]})
    assert run(thinkpad, monkeypatch, capsys, "receipts", "import", str(clean), "--from", "hp", "--trust", "own")[0] == 0
    assert len(evidence(thinkpad)) == 1


def test_a_tool_this_machine_does_not_pin_is_refused_as_own_and_kept_as_foreign(machines, monkeypatch, capsys, tmp_path):
    thinkpad, hp = machines
    check_on_hp(hp, monkeypatch, capsys)
    bundle = export_from(hp, monkeypatch, capsys, tmp_path)
    args = ("receipts", "import", str(bundle), "--from", "hp")
    as_machine("thinkpad", pins="d" * 64)                                          # pins another build of the tools
    code, out, _ = run(thinkpad, monkeypatch, capsys, *args, "--trust", "own", "--json")
    assert code == 1 and "comparator sha256 cccccccccccc is not the dddddddddddd this machine pins" in (
        json.loads(out)["refused"][0]["reason"])
    as_machine("thinkpad", pins=None)                                              # pins nothing
    code, out, _ = run(thinkpad, monkeypatch, capsys, *args, "--trust", "own", "--json")
    assert code == 1 and "is not pinned on this machine" in json.loads(out)["refused"][0]["reason"]
    assert evidence(thinkpad) == []
    code, out, _ = run(thinkpad, monkeypatch, capsys, *args, "--trust", "foreign", "--json")
    assert code == 0 and len(json.loads(out)["imported"]) == 1                    # replay decides
    as_machine("thinkpad")                                                         # control: the pins agree
    assert run(thinkpad, monkeypatch, capsys, *args, "--trust", "own")[0] == 0


def test_an_own_receipt_must_have_its_question_commit_here_else_it_would_go_stale(machines, monkeypatch, capsys, tmp_path):
    thinkpad, hp = machines
    check_on_hp(hp, monkeypatch, capsys)
    git(hp, "commit", "-q", "--allow-empty", "-m", "a commit only the HP has")
    git(hp, "commit", "-q", "--allow-empty", "-m", "and another")
    receipt = check_on_hp(hp, monkeypatch, capsys)                                 # asked its question of that commit
    assert receipt["inputs"]["trusted_commit"] == git(hp, "rev-parse", "HEAD").strip()
    bundle = export_from(hp, monkeypatch, capsys, tmp_path)
    as_machine("thinkpad")
    args = ("receipts", "import", str(bundle), "--from", "hp")
    code, out, _ = run(thinkpad, monkeypatch, capsys, *args, "--trust", "own", "--json")
    assert code == 1 and "is not in this repository (fetch it first)" in json.loads(out)["refused"][0]["reason"]
    assert run(thinkpad, monkeypatch, capsys, *args, "--trust", "foreign")[0] == 0   # quarantine takes it
    git(thinkpad, "fetch", "-q", str(hp), "trusted:hp-trusted")                      # control: fetched, then own is fine
    code, out, _ = run(thinkpad, monkeypatch, capsys, *args, "--trust", "own", "--json")
    assert code == 0 and len(json.loads(out)["imported"]) == 1


def test_a_receipt_asked_of_a_commit_the_repository_lacks_is_stale_which_is_why_import_checks_it(machines, monkeypatch):
    """The reason for the check above, planted: the same pass is `verified` when its trusted commit is here and
    `verified-stale` when it is not (a Lean receipt binds the [lean] roots of the commit it was asked of)."""
    thinkpad, _ = machines
    repo = Repo.open(thinkpad)
    check_input = "research/vl.toml#check"
    lean = "Fixture/Basic.lean"
    from conftest import item_question
    from verifylab.records import sha256_hex
    outcomes = {}
    for label, commit in (("here", git(thinkpad, "rev-parse", "HEAD").strip()), ("elsewhere", "f" * 40)):
        target = {"path": "ReceiptFixture.lean", "sha256": sha256_hex((thinkpad / "ReceiptFixture.lean").read_bytes()),
                  "source": "trusted-commit", "commit": commit, "theorems": ["Fixture.receipt"]}
        data = synthetic_receipt(thinkpad, dict(
            item="add-zero", item_revision="a" * 40, question_digest=item_question(thinkpad), adapter="lean-comparator",
            assurance="protected", verdict="pass", reasons=[], target=target,
            inputs={"digest": "d", "files": {lean: sha256_hex((thinkpad / lean).read_bytes())}, "trusted_commit": commit,
                    "trusted_files": {check_input: repo.trusted_digest(check_input)}},
            environment={}, checked={}, command=["vl"], started_at="t0", finished_at="t1", tool_version="vl 0.1.0"))
        path = thinkpad / "research" / "evidence" / "add-zero" / f"{data['receipt_id'][:16]}.json"
        write_new_json(path, data)
        commit_all(thinkpad, f"admit {label}")
        outcomes[label] = status_of(thinkpad).label
        git(thinkpad, "rm", "-q", str(path))
        commit_all(thinkpad, f"remove {label}")
    assert outcomes == {"here": "verified", "elsewhere": "verified-stale"}


def test_the_source_name_the_machine_it_declares_and_its_trust_class_must_agree(machines, monkeypatch, capsys, tmp_path):
    thinkpad, hp = machines
    check_on_hp(hp, monkeypatch, capsys, name="hp", trust="foreign")               # a machine that classes itself foreign
    bundle = export_from(hp, monkeypatch, capsys, tmp_path)
    as_machine("thinkpad")
    code, _, err = run(thinkpad, monkeypatch, capsys, "receipts", "import", str(bundle), "--from", "samsung",
                       "--trust", "own")
    assert code == 2 and "comes from machine hp, not samsung" in err and evidence(thinkpad) == []
    code, out, _ = run(thinkpad, monkeypatch, capsys, "receipts", "import", str(bundle), "--from", "hp", "--trust", "own",
                       "--json")
    assert code == 1 and "classes its own receipts as foreign" in json.loads(out)["refused"][0]["reason"]
    assert evidence(thinkpad) == []
    code, out, _ = run(thinkpad, monkeypatch, capsys, "receipts", "import", str(bundle), "--from", "hp",
                       "--trust", "foreign", "--json")                              # control: as foreign it is welcome
    assert code == 0 and len(json.loads(out)["imported"]) == 1
    receipt = json.loads(next((thinkpad / ".vl-cache" / "foreign" / "hp" / "add-zero").glob("*.json")).read_text())
    name = f"evidence/add-zero/{receipt['receipt_id'][:16]}.json"                  # declared machine "hp" under another name
    liar = make_bundle(tmp_path / "liar.tar", {name: json.dumps(receipt).encode()}, name="samsung")
    code, out, _ = run(thinkpad, monkeypatch, capsys, "receipts", "import", str(liar), "--from", "samsung",
                       "--trust", "foreign", "--json")
    assert code == 1 and "the receipt says machine hp, not samsung" in json.loads(out)["refused"][0]["reason"]


def test_a_bundle_is_read_never_extracted_and_names_only_receipts(machines, monkeypatch, capsys, tmp_path):
    thinkpad, hp = machines
    check_on_hp(hp, monkeypatch, capsys)
    found = members(export_from(hp, monkeypatch, capsys, tmp_path))
    name = next(n for n in found if n != receipts.MANIFEST)
    as_machine("thinkpad")
    link = tarfile.TarInfo("evidence/add-zero/0123456789abcdef.json")
    link.type, link.linkname = tarfile.SYMTYPE, "/etc/passwd"
    for label, bundle in (("a link", make_bundle(tmp_path / "a.tar", {name: found[name]}, extra_members=[link])),
                          ("a path out of the layout", make_bundle(tmp_path / "b.tar", {"../escape.json": b"{}"})),
                          ("an item that is no id", make_bundle(tmp_path / "c.tar", {"evidence/../0123456789abcdef.json": b"{}"})),
                          ("not a tar", tmp_path / "d.tar")):
        if label == "not a tar":
            bundle.write_bytes(b"not a tar at all" * 100)
        code, _, err = run(thinkpad, monkeypatch, capsys, "receipts", "import", str(bundle), "--from", "hp", "--trust", "own")
        assert code == 2 and evidence(thinkpad) == [] and not (tmp_path.parent / "escape.json").exists(), label
    assert run(thinkpad, monkeypatch, capsys, "receipts", "import", str(make_bundle(tmp_path / "ok.tar", {name: found[name]})),
               "--from", "hp", "--trust", "own")[0] == 0                            # control


def test_an_item_the_trusted_ref_lacks_and_a_second_import_are_handled(machines, monkeypatch, capsys, tmp_path):
    thinkpad, hp = machines
    receipt = check_on_hp(hp, monkeypatch, capsys)
    bundle = export_from(hp, monkeypatch, capsys, tmp_path)
    as_machine("thinkpad")
    args = ("receipts", "import", str(bundle), "--from", "hp", "--trust", "own", "--json")
    assert json.loads(run(thinkpad, monkeypatch, capsys, *args)[1])["imported"]
    again = json.loads(run(thinkpad, monkeypatch, capsys, *args)[1])
    assert again["imported"] == [] and len(again["present"]) == 1 and again["refused"] == []   # idempotent
    run(thinkpad, monkeypatch, capsys, "admit")
    assert json.loads(run(thinkpad, monkeypatch, capsys, *args)[1])["present"]                  # also once admitted
    other = json.loads(json.dumps(receipt))
    other["item"] = "ghost"                                                                    # no such item on the trusted ref
    other = seal_receipt({k: v for k, v in other.items() if k != "receipt_id"})
    ghost = make_bundle(tmp_path / "ghost.tar", {f"evidence/ghost/{other['receipt_id'][:16]}.json": json.dumps(other).encode()})
    code, out, _ = run(thinkpad, monkeypatch, capsys, "receipts", "import", str(ghost), "--from", "hp", "--trust", "own",
                       "--json")
    assert code == 1 and "item 'ghost' is not on the trusted ref" in json.loads(out)["refused"][0]["reason"]



def test_import_is_retryable_after_a_short_write_and_refuses_an_incomplete_destination(
        machines, monkeypatch, capsys, tmp_path):
    thinkpad, hp = machines
    receipt = check_on_hp(hp, monkeypatch, capsys)
    bundle = export_from(hp, monkeypatch, capsys, tmp_path)
    as_machine("thinkpad")
    rid = receipt["receipt_id"][:16]
    place = thinkpad / "research/evidence/add-zero" / f"{rid}.json"
    args = ("receipts", "import", str(bundle), "--from", "hp", "--trust", "own", "--json")
    write = Path.write_bytes

    def short_write(path, data):
        if path.name == "record":
            write(path, data[:17])
            raise OSError("disk full during staged write")
        return write(path, data)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "write_bytes", short_write)
        code, out, _ = run(thinkpad, patch, capsys, *args)
    assert code == 1 and "disk full" in json.loads(out)["refused"][0]["reason"]
    assert not place.exists() and not list(place.parent.iterdir())

    code, out, _ = run(thinkpad, monkeypatch, capsys, *args)
    assert code == 0 and json.loads(out)["imported"]
    raw = place.read_bytes()
    assert raw == members(bundle)[f"evidence/add-zero/{rid}.json"]
    assert json.loads(run(thinkpad, monkeypatch, capsys, *args)[1])["present"]
    write(place, raw[:17])
    code, out, _ = run(thinkpad, monkeypatch, capsys, *args)
    assert code == 1 and "is incomplete or differs" in json.loads(out)["refused"][0]["reason"]
    assert place.read_bytes() == raw[:17]

def test_a_replay_that_finds_a_vacuity_the_foreign_receipt_left_out_keeps_it_in_quarantine(
        machines, monkeypatch, capsys, tmp_path):
    """A foreign machine whose sealed receipt leaves out a vacuity its own run found must not have that silence
    counted. The replay compares statement-probe hits in one direction (a `vacuous_by` the replay finds and the receipt
    lacks is a difference), so the replay is a mismatch and the foreign receipt stays quarantined; a matching replay
    would bring this machine's own receipt into research/evidence/, never the foreign one."""
    thinkpad, hp = machines
    receipt = check_on_hp(hp, monkeypatch, capsys, name="rented")                 # says nothing of vacuity
    bundle = export_from(hp, monkeypatch, capsys, tmp_path)
    as_machine("thinkpad")
    run(thinkpad, monkeypatch, capsys, "receipts", "import", str(bundle), "--from", "rented", "--trust", "foreign")
    quarantined = thinkpad / ".vl-cache" / "foreign" / "rented" / "add-zero" / f"{receipt['receipt_id'][:16]}.json"

    class Vacuous(Recording):
        def check(self, request):
            outcome = super().check(request)
            outcome.checked["probes"] = {t: {"vacuous_by": "simp", "prop_hypotheses": ["h"]}
                                         for t in request.item.lean["theorems"]}
            return outcome

    monkeypatch.setattr(check, "load_adapters", lambda: [Vacuous()])
    code, out, err = run(thinkpad, monkeypatch, capsys, "replay", "add-zero", "--json")
    result = json.loads(out)
    assert code == 1 and result["result"] == "mismatch" and result["promoted"] is None, err
    assert any("vacuous_by" in d for d in result["differences"])
    assert quarantined.is_file() and evidence(thinkpad) == [] and status_of(thinkpad).label == "unverified"


def test_a_sealed_receipt_with_a_malformed_machine_policy_is_refused_not_a_crash(machines, monkeypatch, capsys, tmp_path):
    thinkpad, hp = machines
    receipt = check_on_hp(hp, monkeypatch, capsys)
    files = {}
    for policy in ("a string", {"machine": "hp"}, {"machine": {"name": ["hp"]}}):
        bad = json.loads(json.dumps(receipt))
        bad["environment"]["machine_policy"] = policy
        bad = seal_receipt({k: v for k, v in bad.items() if k != "receipt_id"})
        files[f"evidence/add-zero/{bad['receipt_id'][:16]}.json"] = json.dumps(bad).encode()
    as_machine("thinkpad")
    for trust in ("foreign", "own"):
        code, out, _ = run(thinkpad, monkeypatch, capsys, "receipts", "import",
                           str(make_bundle(tmp_path / f"bad-{trust}.tar", files)), "--from", "hp", "--trust", trust,
                           "--json")
        result = json.loads(out)
        assert code == 1 and len(result["refused"]) == 3 and result["imported"] == [], trust
        assert all("machine_policy" in r["reason"] for r in result["refused"])
    clean = make_bundle(tmp_path / "clean.tar", {f"evidence/add-zero/{receipt['receipt_id'][:16]}.json":
                                                 json.dumps(receipt).encode()})
    assert run(thinkpad, monkeypatch, capsys, "receipts", "import", str(clean), "--from", "hp", "--trust", "own")[0] == 0


def test_a_compressed_bundle_is_read_like_a_plain_one_and_export_honours_a_gz_name(machines, monkeypatch, capsys,
                                                                                  tmp_path):
    """A bundle compressed on the way (gzip, bzip2, xz) imports as the plain one does; `export OUT.tar.gz` writes a
    gzip bundle, and a name promising another compression is refused rather than written as a plain tar."""
    import bz2
    import gzip
    import lzma
    thinkpad, hp = machines
    check_on_hp(hp, monkeypatch, capsys)
    plain = export_from(hp, monkeypatch, capsys, tmp_path)
    code, out, err = run(hp, monkeypatch, capsys, "receipts", "export", str(tmp_path / "hp.tar.gz"))
    assert code == 0 and "tar, gzip" in out, err
    packed = (tmp_path / "hp.tar.gz").read_bytes()
    assert gzip.decompress(packed) == plain.read_bytes()
    assert run(hp, monkeypatch, capsys, "receipts", "export", str(tmp_path / "other.tgz"))[0] == 0
    assert (tmp_path / "other.tgz").read_bytes() == packed
    code, _, err = run(hp, monkeypatch, capsys, "receipts", "export", str(tmp_path / "hp.tar.xz"))
    assert code == 2 and "OUT.tar.gz" in err and not (tmp_path / "hp.tar.xz").exists()
    as_machine("thinkpad")
    data = plain.read_bytes()
    for name, packed in (("b.tar.bz2", bz2.compress(data)), ("c.tar.xz", lzma.compress(data)),
                         ("d.tgz", gzip.compress(data))):
        (tmp_path / name).write_bytes(packed)
        assert receipts.read_bundle(str(tmp_path / name)) == receipts.read_bundle(str(plain)), name
    code, out, err = run(thinkpad, monkeypatch, capsys, "receipts", "import", str(tmp_path / "hp.tar.gz"),
                         "--from", "hp", "--trust", "own", "--json")
    assert code == 0 and len(json.loads(out)["imported"]) == 1, err


def test_a_bundle_that_inflates_past_the_cap_is_refused_before_it_is_held(machines, monkeypatch, tmp_path):
    """Compression makes size cheap: members each within MAX_MEMBER_BYTES, or one long header, can still add up to
    far more than any honest bundle. The whole is read up to MAX_BUNDLE_BYTES, uncompressed, and refused beyond."""
    import gzip
    monkeypatch.setattr(receipts, "MAX_BUNDLE_BYTES", 64 * 1024)
    big = {f"evidence/add-zero/{i:016x}.json": b"x" * 30_000 for i in range(4)}           # 120 kB, each member small
    bomb = tmp_path / "bomb.tar"
    make_bundle(bomb, big)
    (tmp_path / "bomb.tar.gz").write_bytes(gzip.compress(bomb.read_bytes()))
    for bundle in (bomb, tmp_path / "bomb.tar.gz"):
        with pytest.raises(receipts.BundleError, match="larger than"):
            receipts.read_bundle(str(bundle))
    small = make_bundle(tmp_path / "small.tar", dict(list(big.items())[:1]))                # control: within the cap
    assert len(receipts.read_bundle(str(small))[1]) == 1


def test_an_export_that_loses_the_race_for_its_file_leaves_the_winners_file(machines, monkeypatch, capsys, tmp_path):
    """Another process creates OUT between the existence check and the exclusive open: the export fails, and the file
    it never created stays."""
    _, hp = machines
    check_on_hp(hp, monkeypatch, capsys)
    out = tmp_path / "raced.tar"
    real = Path.open

    def racing(self, mode="r", *args, **kwargs):
        if self == out and mode == "xb":
            out.write_bytes(b"the other process's bundle")
        return real(self, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", racing)
    code, _, err = run(hp, monkeypatch, capsys, "receipts", "export", str(out))
    assert code == 2 and "cannot write" in err
    assert out.read_bytes() == b"the other process's bundle"


def test_t0_import_agrees_with_records_and_status_on_external_kernel_minimum(machines, monkeypatch, capsys, tmp_path):
    """T0: `vl receipts import` refuses newly-issued single-kernel protected passes under both --trust own and
    --trust foreign (even when [lean] external_kernels = false), and refuses historical single-kernel --trust own when
    external_kernels is active while permitting historical --trust foreign quarantine."""
    thinkpad, hp = machines
    from verifylab.config import RULES_INPUT
    from verifylab.records import canonical_json, sha256_hex

    # Start from a valid hp check receipt so candidate files/target match thinkpad's repo
    as_machine("hp")
    code, out, err = run(hp, monkeypatch, capsys, "check", "add-zero", "--json")
    assert code == 0, err
    base = json.loads((hp / json.loads(out)["receipt"]).read_text())

    # 1. Modern-profile receipt with single kernel (no nanoda): invalid under T0 everywhere
    modern_1k = json.loads(json.dumps(base))
    modern_1k["inputs"]["trusted_files"][RULES_INPUT] = "d" * 64
    m_basis = {"files": modern_1k["inputs"]["files"], "trusted_files": modern_1k["inputs"]["trusted_files"],
               "target": modern_1k["target"]}
    modern_1k["inputs"]["digest"] = "sha256:" + sha256_hex(canonical_json(m_basis).encode())
    modern_1k["checked"]["kernels"] = ["lean"]
    modern_1k = seal_receipt(modern_1k)
    bad_bundle = make_bundle(tmp_path / "modern-1k.tar", {f"evidence/add-zero/{modern_1k['receipt_id'][:16]}.json":
                                                          json.dumps(modern_1k).encode()})

    # Even if thinkpad sets external_kernels = false, modern single-kernel pass is refused by import
    cfg = thinkpad / "research" / "vl.toml"
    orig_cfg = cfg.read_text()
    cfg.write_text(orig_cfg.replace('roots = ["Fixture"]', 'roots = ["Fixture"]\nexternal_kernels = false'))
    commit_all(thinkpad, "disable external_kernels")
    as_machine("thinkpad", trust="own")
    for trust_mode in ("foreign", "own"):
        code, out, err = run(thinkpad, monkeypatch, capsys, "receipts", "import", str(bad_bundle),
                             "--from", "hp", "--trust", trust_mode)
        assert code == 1 and "external kernel" in out and "nanoda" in out, (trust_mode, code, out, err)

    # Restore external_kernels = true on thinkpad
    cfg.write_text(orig_cfg)
    commit_all(thinkpad, "restore external_kernels")

    # 2. Historical-profile single-kernel receipt: valid historical record, so --trust foreign quarantines it,
    # while --trust own refuses it while thinkpad requires external_kernels.
    hist_1k = json.loads(json.dumps(base))
    trusted = {k: v for k, v in hist_1k["inputs"]["trusted_files"].items() if k != RULES_INPUT}
    trusted["research/vl.toml"] = sha256_hex(orig_cfg.encode())
    hist_1k["inputs"]["trusted_files"] = trusted
    basis = {"files": hist_1k["inputs"]["files"], "trusted_files": trusted, "target": hist_1k["target"]}
    hist_1k["inputs"]["digest"] = "sha256:" + sha256_hex(canonical_json(basis).encode())
    hist_1k["environment"].pop("packages", None)
    hist_1k["checked"] = {**{k: v for k, v in hist_1k["checked"].items()
                             if k not in ("definitions_mode", "probe_run")},
                          "kernels": ["lean"]}
    hist_1k = seal_receipt(hist_1k)
    hist_bundle = make_bundle(tmp_path / "hist-1k.tar", {f"evidence/add-zero/{hist_1k['receipt_id'][:16]}.json":
                                                         json.dumps(hist_1k).encode()})
    code, out, err = run(thinkpad, monkeypatch, capsys, "receipts", "import", str(hist_bundle),
                         "--from", "hp", "--trust", "own")
    assert code == 1 and "single-kernel" in out, (code, out, err)
    code, out, err = run(thinkpad, monkeypatch, capsys, "receipts", "import", str(hist_bundle),
                         "--from", "hp", "--trust", "foreign", "--json")
    assert code == 0 and len(json.loads(out)["imported"]) == 1, err


@pytest.mark.parametrize("dangling", [False, True])
def test_import_rejects_link_at_destination(machines, monkeypatch, capsys, tmp_path, dangling):
    recipient, origin = machines
    receipt = check_on_hp(origin, monkeypatch, capsys)
    bundle = export_from(origin, monkeypatch, capsys, tmp_path)
    as_machine("thinkpad")
    rid = receipt["receipt_id"][:16]
    place = recipient / "research/evidence/add-zero" / f"{rid}.json"
    place.parent.mkdir(parents=True, exist_ok=True)
    target = tmp_path / "saved.json"
    if not dangling:
        target.write_bytes(members(bundle)[f"evidence/add-zero/{rid}.json"])
    place.symlink_to(target)
    args = ("receipts", "import", str(bundle), "--from", "hp", "--trust", "own", "--json")
    code, out, _ = run(recipient, monkeypatch, capsys, *args)
    assert code == 1 and "not a regular file" in json.loads(out)["refused"][0]["reason"]
    assert place.is_symlink()
    place.unlink()
    code, out, _ = run(recipient, monkeypatch, capsys, *args)
    assert code == 0 and json.loads(out)["imported"] and place.is_file()


def test_export_does_not_claim_a_relay_receipt_origin(machines, monkeypatch, capsys, tmp_path):
    recipient, origin = machines
    check_on_hp(origin, monkeypatch, capsys)
    direct = export_from(origin, monkeypatch, capsys, tmp_path)
    as_machine("relay")
    relayed = tmp_path / "relay.tar"
    code, out, _ = run(origin, monkeypatch, capsys, "receipts", "export", str(relayed), "--json")
    result = json.loads(out)
    assert code == 0 and result["receipts"] == [] and result["skipped"] == 1
    assert result["other_origins"][0]["origin"] == "hp"
    as_machine("thinkpad")
    code, out, _ = run(recipient, monkeypatch, capsys, "receipts", "import", str(direct),
                       "--from", "hp", "--trust", "own", "--json")
    assert code == 0 and json.loads(out)["imported"]
