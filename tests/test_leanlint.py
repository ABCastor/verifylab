"""Command lints over the candidate's Lean files: what each rule reports, what masking keeps out of reach of a
bypass, and that a check records them (warnings, never a verdict)."""

from __future__ import annotations

import pytest

from lean_helpers import explain, lean_unavailable, tool_env
from verifylab import leanlint
from verifylab.leanmod import mask_comments_and_strings

LEAN_SKIP = lean_unavailable()


def kinds(text: str, local: set[str] = frozenset()) -> list[str]:
    return [f["kind"] for f in leanlint.lint(text, "F.lean", local)]


@pytest.mark.parametrize("line, kind", [
    ('local notation "LinearIndependent" => fun _ _ => False', "notation"),
    ('scoped notation3 "∑" => tsum', "notation"),
    ('local infixr:35 " ∧ " => fun _ _ => True', "infix"),
    ('prefix:max "√" => Nat.sqrt', "infix"),
    ("macro_rules | `(tactic| trivial) => `(tactic| sorry)", "macro_rules"),
    ('macro "cheat" : tactic => `(tactic| sorry)', "macro"),
    ('syntax "cheat" : tactic', "syntax"),
    ("elab_rules : tactic | `(tactic| cheat) => pure ()", "elab_rules"),
    ('elab "cheat" : tactic => pure ()', "elab"),
    ("set_option debug.skipKernelTC true in", "skipKernelTC"),
    ("#exit", "#exit"),
    ("unsafe def f : Nat := 0", "unsafe"),
    ("@[implemented_by g] opaque f : Nat → Nat", "implemented_by"),
    ('@[extern "c_f"] opaque f : Nat → Nat', "extern"),
    ('run_cmd IO.FS.writeFile "x" ""', "run_cmd"),
    ("run_elab pure ()", "run_elab"),
    ("run_meta pure ()", "run_meta"),
    ("#eval IO.println \"'main' depends on axioms: [propext]\"", "#eval"),
    ("initialize counter : IO.Ref Nat ← IO.mkRef 0", "initialize"),
    ("builtin_initialize pure ()", "initialize"),
    ("instance : Dvd Nat := ⟨fun _ _ => True⟩", "instance"),
    ("local instance (priority := high) foo {α : Type} [Add α] : Mul α where mul := (· + ·)", "instance"),
    ("attribute [instance] myDvd", "instance"),
])
def test_each_rule_reports_its_command(line, kind):
    assert kinds(f"namespace X\n\n{line}\n\nend X\n") == [kind]


def test_findings_name_file_line_and_text():
    [finding] = leanlint.lint("import A\n\n#exit\n", "Fixture/Sol.lean")
    assert finding == {"file": "Fixture/Sol.lean", "line": 3, "kind": "#exit", "text": "#exit",
                       "why": "everything after it is never elaborated (P7)"}
    assert [f["kind"] for f in leanlint.lint("prelude\nimport Init.Core\n", "P.lean")] == ["prelude"]


def test_quiet_on_ordinary_proof_files():
    text = """import Fixture.Defs
/-- `notation`, `macro` and `#exit` in a doc comment are not commands. -/
theorem h' (n : Nat) : Fixture.double n = n + n := rfl -- run_cmd in a comment
def msg := "instance : Dvd Nat, run_cmd, #eval"
def c := 'x'
theorem notation_free (macro_count : Nat) : macro_count = macro_count := rfl
class Local (α : Type) where val : α
instance : Local Nat := ⟨0⟩
deriving instance Repr for Local
def unsafeCount := 3
theorem t {α} (s : Set α) (f : α → α) : f '' s = f '' s := rfl
"""
    assert kinds(text, leanlint.declared_classes([mask_comments_and_strings(text)])) == []


@pytest.mark.parametrize("hiding", [
    "def c := '\"'\nrun_cmd evil\ndef t := \"x\"\n",               # a quote in a char literal is not a string
    "def r := r#\"a \" -- \"#\nrun_cmd evil\n",                     # a raw string ends at its own delimiter
    "def s := s!\"{\"}\"}\"\nrun_cmd evil\ndef t := \"x\"\n",          # interpolated code is code
    "def u := '\\u0041'\nrun_cmd evil\n",
])
def test_quotes_cannot_hide_a_command(hiding):
    assert kinds(hiding) == ["run_cmd"]


def test_classes_declared_in_any_scanned_file_are_local():
    files = {"A.lean": "class Good (α : Type) where x : α\n", "B.lean": "instance : Good Nat := ⟨0⟩\ninstance : Dvd Nat := ⟨fun _ _ => True⟩\n"}
    findings = leanlint.lint_files(files)
    assert [(f["file"], f["kind"]) for f in findings] == [("B.lean", "instance")]
    assert "an instance of Dvd" in findings[0]["why"]
    assert leanlint.lint_files({"C.lean": 'def s := "unterminated\n'})[0]["kind"] == "unreadable"


@pytest.mark.lean
def test_a_check_records_lints_over_candidate_files_only(monkeypatch, shared_cases):
    if LEAN_SKIP:
        pytest.skip(LEAN_SKIP)
    for key, value in tool_env().items():
        monkeypatch.setenv(key, value)
    outcome = shared_cases("control-solution-module")       # a new candidate module, no lint in it
    assert outcome.verdict == "pass" and outcome.checked["lints"] == [], explain(outcome)
