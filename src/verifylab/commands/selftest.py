"""`vl check --selftest`: known-answer check on the project's real configuration."""

from __future__ import annotations

import json
import os
import posixpath
import subprocess
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

from .. import gitref, jail, machine
from ..adapters.lean_comparator import PROJECT_FILES
from ..config import CONFIG_PATH, rules_digest
from ..output import EXIT_FAIL, EXIT_PASS, EXIT_TOOL, emit, fail
from ..repo import Repo

if TYPE_CHECKING:
    from . import check as _check_mod

SELFTEST_CASES = (
    ("control", "pass", (), False),
    ("sorry", "fail", ("sorry",), False),
    ("extra-axiom", "fail", ("axiom", "selftestcheat"), False),
    ("weakened-target", "fail", ("differs from the target's",), False),
    ("altered-definition", "fail", ("selftestok", "differs between the trusted definitions"), True),
)


def _prel(project: str, path: str) -> str:
    norm = posixpath.normpath(project)
    return path if norm == "." else f"{norm}/{path}"


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, text=True)


def _real_packages_dir(repo: Repo, manifest_bytes: bytes | None) -> tuple[Path | None, str]:
    """Resolve the real project's Lake packages directory using the same priority as LeanComparatorAdapter."""
    if manifest_bytes is None:
        return None, ".lake/packages"
    try:
        manifest = json.loads(manifest_bytes)
    except (json.JSONDecodeError, TypeError):
        return None, ".lake/packages"
    rel_manifest_dir = str((manifest or {}).get("packagesDir") or ".lake/packages")
    if not ((manifest or {}).get("packages") or []):
        return None, rel_manifest_dir
    try:
        loaded = machine.load()
        machine_cache = loaded.cache_for(gitref.main_worktree(repo.root))
    except machine.MachineError:
        machine_cache = None
    lean_raw = repo.config.raw.get("lean", {}) or {}
    configured = ((str(Path(machine_cache) / "packages") if machine_cache else None)
                  or lean_raw.get("packages")
                  or (str(Path(str(lean_raw["cache"])) / "packages") if lean_raw.get("cache") else None))
    if configured:
        path = Path(str(configured)).expanduser()
        resolved = path if path.is_absolute() else repo.root / path
    else:
        resolved = repo.root / _prel(repo.config.lean.project, rel_manifest_dir)
    return resolved.resolve(), rel_manifest_dir


def _inside_root(root: Path, candidate: Path) -> bool:
    try:
        Path(os.path.abspath(candidate)).relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _rewrite_lean_packages(vl_toml: str, packages_dir: Path) -> str:
    """Point `[lean] packages` in a temporary copy of research/vl.toml to `packages_dir`."""
    out: list[str] = []
    in_lean = False
    replaced = False
    value = json.dumps(str(packages_dir))
    for line in vl_toml.splitlines():
        stripped = line.strip()
        if stripped.startswith("[") and not stripped.startswith("[["):
            if in_lean and not replaced:
                out.append(f"packages = {value}")
                replaced = True
            in_lean = stripped == "[lean]"
        elif in_lean and (stripped.startswith("packages ") or stripped.startswith("packages=")):
            out.append(f"packages = {value}")
            replaced = True
            continue
        out.append(line)
    if in_lean and not replaced:
        out.append(f"packages = {value}")
    return "\n".join(out) + ("\n" if vl_toml.endswith("\n") else "")


def _stage_packages(tmp_repo: Path, repo: Repo, vl_toml: str, manifest_bytes: bytes | None) -> str:
    """Expose the real project's resolved Lake packages read-only to the temporary selftest checkout."""
    packages_dir, rel_manifest_dir = _real_packages_dir(repo, manifest_bytes)
    if packages_dir is None or not packages_dir.is_dir():
        return vl_toml
    lean_raw = repo.config.raw.get("lean", {}) or {}
    pkg_cfg = lean_raw.get("packages")
    cache_cfg = lean_raw.get("cache")
    if pkg_cfg:
        pkg_path = Path(str(pkg_cfg)).expanduser()
        if pkg_path.is_absolute():
            return vl_toml
        dest = tmp_repo / pkg_path
        if _inside_root(tmp_repo, dest) and not dest.exists():
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.symlink_to(packages_dir)
            return vl_toml
        return _rewrite_lean_packages(vl_toml, packages_dir)
    if cache_cfg:
        cache_path = Path(str(cache_cfg)).expanduser()
        if not cache_path.is_absolute():
            dest = tmp_repo / cache_path / "packages"
            if _inside_root(tmp_repo, dest) and not dest.exists():
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.symlink_to(packages_dir)
                return vl_toml
    dest = tmp_repo / _prel(repo.config.lean.project, rel_manifest_dir)
    if not dest.exists():
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.symlink_to(packages_dir)
    return vl_toml


def _reason_matches(reasons: list[str], tokens: tuple[str, ...]) -> bool:
    joined = " ".join(reasons).lower()
    return all(tok in joined for tok in tokens)


def _solution_source(root_lib: str, case_name: str) -> tuple[str, str]:
    defs_trusted = "def selftestOk (_ : Nat) : Prop := ∀ n : Nat, n = n\n"
    defs_altered = "def selftestOk (_ : Nat) : Prop := True\n"
    if case_name == "control":
        return defs_trusted, f"import {root_lib}.SelftestDefs\n\ntheorem selftest_claim : ∀ n : Nat, selftestOk n := fun _ _ => rfl\n"
    if case_name == "sorry":
        return defs_trusted, f"import {root_lib}.SelftestDefs\n\ntheorem selftest_claim : ∀ n : Nat, selftestOk n := by sorry\n"
    if case_name == "extra-axiom":
        return defs_trusted, (
            f"import {root_lib}.SelftestDefs\n\naxiom selftestCheat : ∀ n : Nat, selftestOk n\n"
            "theorem selftest_claim : ∀ n : Nat, selftestOk n := selftestCheat\n"
        )
    if case_name == "weakened-target":
        return defs_trusted, f"import {root_lib}.SelftestDefs\n\ntheorem selftest_claim : True := trivial\n"
    return defs_altered, f"import {root_lib}.SelftestDefs\n\ntheorem selftest_claim : ∀ n : Nat, selftestOk n := fun _ => trivial\n"


def run_selftest(args, repo: Repo, check_mod: _check_mod, guards: frozenset[str] = frozenset()) -> int:
    """Plant known cheats and a clean control against the project's trusted Lean configuration."""
    trusted_commit = repo.trusted_commit
    if trusted_commit is None:
        return fail(f"the trusted ref '{repo.config.trusted_ref}' (from {repo.config.trusted_ref_source}) does not "
                    "resolve to a commit")
    roots = repo.config.lean.roots
    if not roots:
        return fail("vl check --selftest requires [lean] roots in research/vl.toml on the trusted ref")
    raw_vl_toml = gitref.show(repo.root, trusted_commit, CONFIG_PATH)
    if raw_vl_toml is None:
        return fail(f"{CONFIG_PATH} is missing from the trusted commit {trusted_commit[:12]}")

    root_lib = roots[0]
    project = repo.config.lean.project
    build_files = {name: gitref.show(repo.root, trusted_commit, _prel(project, name)) for name in PROJECT_FILES}
    vl_toml_text = raw_vl_toml.decode("utf-8", "replace")

    with tempfile.TemporaryDirectory(prefix="vl-selftest-") as td:
        tmp_repo_dir = Path(td) / "repo"
        tmp_repo_dir.mkdir()
        _git(tmp_repo_dir, "init", "-q")
        _git(tmp_repo_dir, "config", "user.name", "vl-selftest")
        _git(tmp_repo_dir, "config", "user.email", "selftest@local")

        vl_toml_text = _stage_packages(tmp_repo_dir, repo, vl_toml_text, build_files["lake-manifest.json"])
        cfg_dest = tmp_repo_dir / CONFIG_PATH
        cfg_dest.parent.mkdir(parents=True, exist_ok=True)
        cfg_dest.write_text(vl_toml_text, encoding="utf-8")

        tracked: list[str] = [CONFIG_PATH]
        for name, data in build_files.items():
            if data is not None:
                rel = _prel(project, name)
                dest = tmp_repo_dir / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(data)
                tracked.append(rel)

        defs_rel = _prel(project, f"{root_lib}/SelftestDefs.lean")
        sol_rel = _prel(project, f"{root_lib}/SelftestSol.lean")
        target_rel = repo.config.rel("targets", "selftest.lean")
        item_rel = repo.config.rel("items", "selftest.md")

        trusted_defs, trusted_sol = _solution_source(root_lib, "control")
        files_to_commit = {
            defs_rel: trusted_defs,
            sol_rel: trusted_sol,
            target_rel: f"import {root_lib}.SelftestDefs\n\ntheorem selftest_claim : ∀ n : Nat, selftestOk n := sorry\n",
            item_rel: (
                "+++\n"
                'id = "selftest"\nkind = "result"\ntitle = "selftest known-answer check"\n'
                'author = "human:selftest"\ncreated = "2026-11-14"\nstatement = "selftest check"\n'
                'claim = "formal"\n\n[lean]\n'
                f'target = "{target_rel}"\ntheorems = ["selftest_claim"]\nsolution = "{root_lib}.SelftestSol"\n'
                "+++\nSelftest body.\n"
            ),
        }
        for rel, text in files_to_commit.items():
            dest = tmp_repo_dir / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(text, encoding="utf-8")
            tracked.append(rel)

        _git(tmp_repo_dir, "add", *tracked)
        _git(tmp_repo_dir, "commit", "-q", "-m", "selftest trusted baseline")
        _git(tmp_repo_dir, "branch", "-f", "trusted", "HEAD")
        _git(tmp_repo_dir, "config", "vl.trustedRef", "trusted")

        selftest_repo = Repo.open(tmp_repo_dir, "trusted")
        item = selftest_repo.load_item("selftest")

        results: list[dict] = []
        with jail.terminate_on_sigterm(), jail.watch_coordinator():
            for name, expected, tokens, _altered in SELFTEST_CASES:
                defs_text, sol_text = _solution_source(root_lib, name)
                (tmp_repo_dir / defs_rel).write_text(defs_text, encoding="utf-8")
                (tmp_repo_dir / sol_rel).write_text(sol_text, encoding="utf-8")
                outcome, _ = check_mod.execute(selftest_repo, item, "protected", args.timeout, args.reserve,
                                               args.max_wait, args.keep_scratch, guards)
                ok = outcome.verdict == expected and _reason_matches(outcome.reasons, tokens)
                results.append({"case": name, "expected": expected, "verdict": outcome.verdict,
                                "ok": ok, "reasons": list(outcome.reasons)})

    passed = all(r["ok"] for r in results)
    tool_problem = any(r["verdict"] in ("error", "unsupported") for r in results)
    defect_miss = any((not r["ok"]) and r["verdict"] not in ("error", "unsupported") for r in results)
    lines = [f"selftest on {repo.config.name} ({trusted_commit[:12]}): "
             + ("passed" if passed else "FAILED" if defect_miss else "error/unsupported")]
    for r in results:
        mark = "ok" if r["ok"] else "MISMATCH"
        lines.append(f"  {r['case']:20} expected={r['expected']:4} got={r['verdict']:11} [{mark}]"
                     + (f" — {r['reasons'][0]}" if r["reasons"] else ""))
    emit(args, "\n".join(lines), {
        "ok": passed,
        "trusted_commit": trusted_commit,
        "rules_digest": rules_digest(repo.config),
        "cases": results,
    })
    if passed:
        return EXIT_PASS
    if defect_miss:
        return EXIT_FAIL
    return EXIT_TOOL if tool_problem else EXIT_FAIL
