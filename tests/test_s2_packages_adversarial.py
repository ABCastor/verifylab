"""Real protected checks of a local tiny package. Run only under the build's Lean flock."""

from __future__ import annotations

import os
import json
import subprocess

import pytest

from lean_helpers import TOOLCHAIN, explain, git, lean_unavailable, request
from test_lean_adapter import _dep_package, _depend_on, lean_env
from verifylab.adapters.lean_comparator import LeanComparatorAdapter, toolchain_dir


@pytest.mark.lean
@pytest.mark.skipif(lean_unavailable() is not None, reason=lean_unavailable() or "")
@pytest.mark.parametrize("mutation", ["wrong-head", "dirty-tracked", "dirty-untracked"])
def test_package_source_identity_is_enforced_before_protected_pass(tmp_path, lean_env, mutation):
    packages, rev = _dep_package(tmp_path)
    package = packages / "dep"
    env = {**os.environ, "PATH": f"{toolchain_dir(TOOLCHAIN) / 'bin'}:/usr/bin:/bin", "LEAN_NUM_THREADS": "1"}
    subprocess.run([str(toolchain_dir(TOOLCHAIN) / "bin/lake"), "build", "Dep"],
                   cwd=package, env=env, check=True, capture_output=True)
    root = _depend_on(tmp_path, packages, rev)
    adapter = LeanComparatorAdapter()
    clean = adapter.check(request(root, "double-value"))
    assert clean.verdict == "pass", explain(clean)
    assert clean.environment["packages"]["identity"]["revisions"]["dep"]["matches"] is True
    if mutation == "wrong-head":
        (package / "EXTRA").write_text("The package now has another commit.\n")
        git(package, "add", "EXTRA")
        git(package, "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-q", "-m", "move head")
    elif mutation == "dirty-tracked":
        # Rebuild coherent artifacts from dirty source, leaving the manifest/HEAD at the old source.
        (package / "Dep.lean").write_text("def Dep.value : Nat := 8\n")
        subprocess.run([str(toolchain_dir(TOOLCHAIN) / "bin/lake"), "build", "Dep"],
                       cwd=package, env=env, check=True, capture_output=True)
    else:
        (package / "Extra.lean").write_text("theorem Extra.unreviewed : True := trivial\n")
    planted = adapter.check(request(root, "double-value"))
    print("S2 package observation:", json.dumps({"mutation": mutation, "verdict": planted.verdict,
                                               "reasons": planted.reasons,
                                               "identity": planted.environment.get("packages", {}).get("identity")}))
    assert planted.verdict in {"error", "unsupported"}, explain(planted)
