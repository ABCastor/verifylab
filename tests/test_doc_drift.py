"""The documentation names only what the code has. Every `vl <command> [--flag]` written in code in the user and
contributor docs exists in the CLI; the status labels of the README and SPEC tables are exactly those the code can
derive; every key of the vl.toml template is one the parser reads; the machine.toml template loads; the example
item of the AGENTS.md snippet validates. Each check also runs on a planted drift it must catch."""

from __future__ import annotations

import argparse
import ast
import re
from pathlib import Path

import pytest

from verifylab import machine, status
from verifylab.adapters.lean_comparator import TOOLS, parse_spec
from verifylab.cli import build_parser
from verifylab.config import ConfigError, parse_config
from verifylab.records import CLAIM_TYPES, ITEM_KINDS, VERDICTS, parse_item

ROOT = Path(__file__).resolve().parents[1]
DOCS = [ROOT / "README.md", ROOT / "AGENTS.md", ROOT / "CONTRIBUTING.md", ROOT / "SECURITY.md",
        ROOT / "docs" / "INSTALL.md", ROOT / "docs" / "SPEC.md", ROOT / "docs" / "ARCHITECTURE.md",
        ROOT / "docs" / "CHEATS.md", *sorted((ROOT / "docs" / "adr").glob("*.md")),
        ROOT / "templates" / "AGENTS-snippet.md", *sorted((ROOT / "skills").glob("*/SKILL.md"))]
LABEL_TABLES = [ROOT / "README.md", ROOT / "docs" / "SPEC.md"]


# Commands and flags ---------------------------------------------------------------------------------------------

def _subparsers(parser: argparse.ArgumentParser) -> dict[str, argparse.ArgumentParser]:
    return {name: p for action in parser._actions if isinstance(action, argparse._SubParsersAction)
            for name, p in action.choices.items()}


def _flags(parser: argparse.ArgumentParser) -> set[str]:
    return {option for action in parser._actions for option in action.option_strings}


def cli() -> dict[str, tuple[set[str], dict[str, set[str]]]]:
    """command -> (its flags, {action: the action's flags}) of the real parser."""
    return {name: (_flags(p), {a: _flags(ap) for a, ap in _subparsers(p).items()})
            for name, p in _subparsers(build_parser()).items()}


def code_texts(text: str) -> list[str]:
    """Inline code spans and the lines of fenced code blocks: where the docs write commands."""
    blocks, inline, fenced = [], [], False
    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            fenced = not fenced
            continue
        (blocks if fenced else inline).append(line)
    return blocks + re.findall(r"`([^`\n]+)`", "\n".join(inline))


_USE = re.compile(r"(?<![\w/.-])vl ([a-z][a-z-]*(?:\|[a-z][a-z-]*)*)(.*)")


def command_problems(text: str, model: dict) -> list[str]:
    """Every `vl <command>` with an unknown command, lane action or flag."""
    problems = []
    for code in code_texts(text.replace("\\|", "|")):
        for match in _USE.finditer(code):
            names, rest = match.group(1).split("|"), re.split(r" -- | && |; | #", match.group(2) + " ")[0]
            for name in names:
                if name not in model:
                    problems.append(f"`vl {name}`: no such command ({code.strip()})")
                    continue
                flags, actions = model[name]
                words = rest.split()
                chosen = words[0].split("|") if words and words[0].split("|")[0] in actions else []
                allowed = flags.union(*(actions.get(a, set()) for a in chosen)) if chosen else set(flags)
                if not chosen and actions:
                    allowed = allowed.union(*actions.values())
                problems += [f"`vl {name} {a}`: no such action ({code.strip()})" for a in chosen if a not in actions]
                problems += [f"`vl {name} … {flag}`: no such flag ({code.strip()})"
                             for flag in re.findall(r"(?<![\w-])--[a-z][a-z0-9-]*", rest) if flag not in allowed]
    return problems


def test_every_command_and_flag_in_the_docs_exists():
    model = cli()
    problems = [f"{path.relative_to(ROOT)}: {p}" for path in DOCS for p in command_problems(path.read_text(), model)]
    assert problems == []
    uses = sum(len(_USE.findall(code)) for path in DOCS for code in code_texts(path.read_text()))
    assert uses >= 80, f"only {uses} `vl` commands found in the docs: the scan itself is broken"


def test_the_command_check_catches_a_planted_drift():
    model = cli()
    assert command_problems("`vl check ID --deep` and `vl frobnicate`", model) == [
        "`vl check … --deep`: no such flag (vl check ID --deep)", "`vl frobnicate`: no such command (vl frobnicate)"]
    assert command_problems("`vl lane close NAME --from main`", model) != []          # --from belongs to `lane new`
    assert command_problems("```\nvl lane exec la -- lake build --no-build\n```\n`vl lane new|list|exec|close`",
                            model) == []


def test_the_readme_names_every_command():
    readme = ROOT.joinpath("README.md").read_text().replace("\\|", "|")
    named = {m.group(1) for code in code_texts(readme) for m in _USE.finditer(code)}
    assert set(cli()) <= {name for names in named for name in names.split("|")}


# Status labels --------------------------------------------------------------------------------------------------

def code_labels() -> set[str]:
    """Every label `status.derive` can produce, read from the Status(...) calls of status.py."""
    labels, prefixes, kinds = set(), set(), False
    for node in ast.walk(ast.parse(Path(status.__file__).read_text())):
        if not (isinstance(node, ast.Call) and getattr(node.func, "id", None) == "Status" and node.args):
            continue
        arg = node.args[0]
        if isinstance(arg, ast.Constant):
            labels.add(arg.value)
        elif isinstance(arg, ast.Name):
            labels.add(getattr(status, arg.id))
        elif isinstance(arg, ast.JoinedStr):
            prefixes.add(arg.values[0].value)
        elif isinstance(arg, ast.Attribute) and arg.attr == "kind":
            kinds = True
        elif not (isinstance(arg, ast.Attribute) and arg.attr == "label"):      # re-wrapping a derived status
            raise AssertionError(f"status.py:{node.lineno}: a status label this test cannot enumerate")
    assert prefixes == {"check-", "recorded-"} and kinds, "a new kind of computed label: teach this test"
    labels |= {f"check-{v}" for v in VERDICTS if v not in ("pass", "fail")}       # pass and fail have own labels
    labels |= {f"recorded-{c}" for c in CLAIM_TYPES if c not in status.CHECKABLE_CLAIMS}
    labels |= {k for k in ITEM_KINDS if k not in ("result", "conjecture", "question")}
    return labels


def table_labels(text: str) -> set[str]:
    """The code spans in the first column of the table whose header's first cell is `Status`."""
    labels, inside = set(), False
    for line in text.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")] if line.startswith("|") else None
        if cells and cells[0] == "Status":
            inside = True
        elif inside and cells is None:
            break
        elif inside and not set(cells[0]) <= set("-: "):
            labels |= set(re.findall(r"`([^`]+)`", cells[0]))
    return labels


def test_status_tables_name_exactly_the_labels_the_code_derives():
    expected = code_labels()
    assert {"verified", "vacuous", "check-unsupported", "recorded-prose", "explanation", "undetermined"} <= expected
    for path in LABEL_TABLES:
        assert table_labels(path.read_text()) == expected, path.name


def test_the_label_check_catches_a_planted_drift():
    table = "| Status | Meaning |\n|---|---|\n| `verified` | x |\n| `stale` | y |\n\nafter `missing`\n"
    assert table_labels(table) == {"verified", "stale"} != code_labels()


# Templates ------------------------------------------------------------------------------------------------------

_KEY = re.compile(r"^#? ?([a-z][a-z0-9_]*) = ")


def template_keys(text: str) -> list[tuple[str, str]]:
    """(table, key) of every `key = value` line of a TOML template, commented-out examples included."""
    keys, table = [], ""
    for line in text.splitlines():
        if m := re.match(r"^\[([a-z]+)\]$", line):
            table = m.group(1)
        elif m := _KEY.match(line):
            keys.append((table, m.group(1)))
    return keys


def read_by_parser(table: str, key: str) -> bool:
    """True when parse_config reads `[table] key`: given a value no key accepts, it must refuse it. [tools] accepts
    any string, so its keys are those a checker reads."""
    if table == "tools":
        return key in {"python", *TOOLS}
    try:
        parse_config(Path("/nonexistent"), f"[{table}]\n{key} = {{ vl_drift = 1 }}\n")
    except ConfigError:
        return True
    return False


DEPRECATED = {("project", "trusted_ref"), ("check", "memory_total")}     # accepted, ignored, warned about


def parser_keys() -> set[tuple[str, str]]:
    """Every (table, key) parse_config reads, found by trying each identifier-like string of config.py."""
    import verifylab.config as config
    names = {node.value for node in ast.walk(ast.parse(Path(config.__file__).read_text()))
             if isinstance(node, ast.Constant) and isinstance(node.value, str) and re.fullmatch(r"[a-z][a-z_]*", node.value)}
    return {(t, k) for t in ("project", "lean", "lanes", "check") for k in names if read_by_parser(t, k)}


def test_the_vl_toml_template_shows_exactly_the_keys_the_parser_reads():
    keys = set(template_keys(ROOT.joinpath("templates", "vl.toml").read_text()))
    assert [f"[{t}] {k}" for t, k in sorted(keys) if not read_by_parser(t, k)] == []
    assert sorted(parser_keys() - DEPRECATED - keys) == []
    assert ("lean", "permitted_axioms") in parser_keys() and DEPRECATED <= parser_keys()


def test_the_key_check_catches_a_planted_drift():
    keys = template_keys('[lean]\n# permited_axioms = ["propext"]\nroots = []\n[tools]\n# compiler = "/x"\n')
    assert [(t, k) for t, k in keys if not read_by_parser(t, k)] == [("lean", "permited_axioms"), ("tools", "compiler")]


def test_the_machine_template_loads_with_every_example_uncommented(tmp_path: Path):
    text = ROOT.joinpath("templates", "machine.toml").read_text()
    uncommented = re.sub(r"(?m)^# (?=[a-z][a-z0-9_]* = |\")", "", text)
    (tmp_path / "machine.toml").write_text(uncommented)
    loaded = machine.load(tmp_path / "machine.toml")
    assert set(loaded.tools) == set(machine.TOOL_NAMES) and loaded.elan_home and loaded.caches and loaded.memory_total


def test_the_agents_snippet_example_validates():
    text = ROOT.joinpath("templates", "AGENTS-snippet.md").read_text()
    block = re.search(r"```toml\n(.*?)```", text, re.S).group(1)
    item = parse_item((block + "Body.\n").encode(), "research/items/my-result.md")
    spec, problems = parse_spec(item.lean, "research/targets")
    assert problems == [] and spec.witnesses and set(spec.witnesses) <= set(spec.theorems)


@pytest.mark.parametrize("path", DOCS, ids=lambda p: str(p.relative_to(ROOT)))
def test_no_doc_names_a_path_of_one_machine(path: Path):
    assert re.findall(r"/home/[a-z]|/Users/[A-Za-z]", path.read_text()) == []
