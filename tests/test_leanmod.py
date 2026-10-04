from __future__ import annotations

import pytest

from verifylab import leanmod as lm


def test_simple_header_and_offsets():
    text = "import Fixture.Defs\nimport Mathlib.Data.Nat.Basic\n\ntheorem x : True := trivial\n"
    header = lm.parse_header(text)
    assert header.modules == ["Fixture.Defs", "Mathlib.Data.Nat.Basic"]
    first = header.imports[0]
    assert text[first.start:first.end] == "Fixture.Defs"
    assert not header.module_keyword and not header.prelude


def test_several_imports_per_line_and_comments():
    text = ("-- leading comment\n/- block /- nested -/ still comment -/\n"
            "import A  import B.C -- trailing\nimport /- inline -/ D\n\ndef x := 1\n")
    assert lm.imports_of(text) == ["A", "B.C", "D"]


def test_module_system_keywords():
    text = ("module\n\nprelude\npublic import Fixture.Defs\nmeta import Lean.Elab\npublic meta import X.Y\n"
            "import all Fixture.Proofs\n\npublic section\n")
    header = lm.parse_header(text)
    assert header.module_keyword and header.prelude
    assert header.modules == ["Fixture.Defs", "Lean.Elab", "X.Y", "Fixture.Proofs"]
    flags = [(i.public, i.meta, i.all) for i in header.imports]
    assert flags == [(True, False, False), (False, True, False), (True, True, False), (False, False, True)]


def test_public_section_and_meta_def_end_the_header():
    assert lm.imports_of("module\npublic import A\npublic section\ndef x := 1\n") == ["A"]
    assert lm.imports_of("import A\nmeta def f := 1\n") == ["A"]


def test_doc_comment_ends_the_header():
    text = "import A\n/-! module doc\nimport B\n-/\n/-- doc -/\ndef x := 1\n"
    assert lm.imports_of(text) == ["A"]          # `import B` sits inside a doc comment: not an import


def test_no_header():
    assert lm.parse_header("def x := 1\n").imports == ()
    assert lm.parse_header("").end == 0


@pytest.mark.parametrize("text", [
    "import «Weird Name»\n",
    "import Foo.\n",
    "import\n",
    "import A\ndef x := 1\nimport B\n",        # import after the header: Lean rejects it, we refuse to guess
    "/- unterminated\nimport A\n",
    "import Foo.«bar»\n",
])
def test_fail_closed(text):
    with pytest.raises(lm.LeanModError):
        lm.parse_header(text)


def test_module_path_mapping():
    assert lm.module_to_path("Fixture.Defs") == "Fixture/Defs.lean"
    assert lm.path_to_module("Fixture/Defs.lean") == "Fixture.Defs"
    for bad in ("", "a..b", "a.«b»", "../x", "a/b"):
        with pytest.raises(lm.LeanModError):
            lm.module_to_path(bad)
    with pytest.raises(lm.LeanModError):
        lm.path_to_module("/abs/X.lean")


def test_roots_and_reserved():
    assert lm.in_roots("Fixture.Defs", ["Fixture"]) and lm.in_roots("Fixture", ["Fixture"])
    assert not lm.in_roots("FixtureX.Defs", ["Fixture"])
    assert lm.is_reserved("VLTrusted.Fixture.Defs") and lm.is_reserved("VLChallenge")
    assert not lm.is_reserved("VLTrustedish")


def test_prefix_rewrite_touches_only_project_imports():
    text = "module\n-- c\npublic import Fixture.Defs  import Mathlib.Tactic\nimport all Fixture.Proofs\n\ndef x := 1\n"
    out = lm.prefix_imports(text, "VLTrusted", ["Fixture"])
    assert lm.imports_of(out) == ["VLTrusted.Fixture.Defs", "Mathlib.Tactic", "VLTrusted.Fixture.Proofs"]
    assert out.replace("VLTrusted.", "") == text            # nothing else changed, byte for byte


def test_insert_imports():
    text = "import A\n\nnamespace X\nend X\n"
    out = lm.insert_imports(text, ["B", "A", "C"])
    assert lm.imports_of(out) == ["A", "B", "C"] and out.endswith("namespace X\nend X\n")
    assert lm.imports_of(lm.insert_imports("def x := 1\n", ["A"])) == ["A"]


def test_closure_restricted_to_roots():
    files = {
        "Fixture/A.lean": b"import Fixture.B\nimport Mathlib.Tactic\n",
        "Fixture/B.lean": b"import Fixture.C\nimport Std\n",
        "Fixture/C.lean": b"",
        "Other/D.lean": b"",
    }
    result = lm.closure(["Fixture.A", "Init"], ["Fixture"], files.get)
    assert list(result.modules) == ["Fixture.A", "Fixture.B", "Fixture.C"]
    assert result.external == {"Init", "Mathlib.Tactic", "Std"}
    assert result.parents["Fixture.C"] == "Fixture.B"


def test_closure_missing_module_names_importer():
    with pytest.raises(lm.LeanModError, match="Fixture.Gone not found at Fixture/Gone.lean .imported by Fixture.A"):
        lm.closure(["Fixture.A"], ["Fixture"], {"Fixture/A.lean": b"import Fixture.Gone\n"}.get)


TARGET = """import Fixture.Defs

/-! doc with theorem fake : True := sorry -/

namespace VL.X

/-- Every doubled number is even. -/
theorem main (n : Nat) : (Fixture.double n % 2 = 0)
  ∨ True := sorry

theorem tactic_sorry : True := by
  sorry

theorem proved : True := trivial

private theorem hidden : True := sorry

theorem univ.{u} (α : Sort u) : True := sorry

section Inner
theorem inner : True := sorry
end Inner

end VL.X

theorem _root_.top : True := sorry
"""


def test_find_theorems_namespaces_and_bodies():
    found = lm.find_theorems(TARGET)
    assert set(found) == {"VL.X.main", "VL.X.tactic_sorry", "VL.X.proved", "VL.X.hidden", "VL.X.univ",
                          "VL.X.inner", "top"}
    assert found["VL.X.proved"][0].body is None
    assert found["VL.X.hidden"][0].private
    a, b = found["VL.X.main"][0].body
    assert TARGET[a:b] == "sorry"
    a, b = found["VL.X.tactic_sorry"][0].body
    assert TARGET[a:b] == "by\n  sorry"


def test_target_problems():
    problems = lm.target_problems(TARGET, ["VL.X.main", "VL.X.proved", "VL.X.hidden", "VL.X.nope", "fake"])
    assert problems == [
        "theorem 'VL.X.proved' (line 14) must have body `sorry` in the target",
        "theorem 'VL.X.hidden' is private (module-mangled name); private targets are unsupported",
        "theorem 'VL.X.nope' is not declared in the target",
        "theorem 'fake' is not declared in the target",
    ]
    duplicated = "namespace A\ntheorem t : True := sorry\nend A\nnamespace A\ntheorem t : True := sorry\nend A\n"
    assert lm.target_problems(duplicated, ["A.t"]) == ["theorem 'A.t' is declared 2 times in the target"]


@pytest.mark.parametrize("text", [
    "namespace A\ntheorem t : True := sorry\n",          # never closed
    "namespace A\ntheorem t : True := sorry\nend B\n",   # wrong end
    "end A\n",
])
def test_scope_problems_fail_closed(text):
    assert lm.target_problems(text, ["A.t"])[0].startswith("cannot parse the target")


def test_continuation_at_column_zero_is_not_guessed():
    text = "theorem t (n : Nat) :\nn = n := sorry\n"
    assert lm.target_problems(text, ["t"]) == ["theorem 't' (line 1) must have body `sorry` in the target"]


def test_fill_sorries_keeps_statement_and_handles_comments():
    out = lm.fill_sorries(TARGET, {"VL.X.main": "Or.inr trivial -- weak", "top": "trivial"})
    assert "∨ True := (Or.inr trivial -- weak\n  )" in out
    assert "theorem _root_.top : True := (trivial\n  )" in out
    assert out.count("sorry") == TARGET.count("sorry") - 2
    with pytest.raises(lm.LeanModError):
        lm.fill_sorries(TARGET, {"VL.X.proved": "trivial"})


@pytest.mark.parametrize("text, visible", [
    ("def c := '\"'\nX\ndef t := \"y\"\n", "X"),
    ("def r := r##\"a \"# -/ \"##\nX\n", "X"),
    ('def s := s!"a {f "b" 1} c"\nX\n', "f"),
    ("theorem h' : f '' s = f '' s := rfl\n", "h'"),
])
def test_masking_keeps_offsets_and_cannot_be_derailed_by_quotes(text, visible):
    masked = lm.mask_comments_and_strings(text)
    assert len(masked) == len(text) and masked.count("\n") == text.count("\n")
    assert visible in masked


@pytest.mark.parametrize("text", ['def r := r#"open\n', 'def s := s!"{x"\n'])
def test_masking_refuses_unterminated_literals(text):
    with pytest.raises(lm.LeanModError):
        lm.mask_comments_and_strings(text)


def test_a_byte_order_mark_keeps_rewritten_imports_intact():
    from verifylab import leanmod
    text = "﻿import A\nimport Mathlib\n\ntheorem t : True := sorry\n"
    assert leanmod.prefix_imports(text, "VLTrusted", ["A"]) == (
        "﻿import VLTrusted.A\nimport Mathlib\n\ntheorem t : True := sorry\n")
    assert leanmod.parse_header(text).modules == ["A", "Mathlib"]
    assert leanmod.insert_imports("﻿theorem t : True := sorry\n", ["B"]).count("import B") == 1
    assert leanmod.insert_imports(text, ["B"]).startswith("﻿import A\nimport Mathlib\nimport B\n")
