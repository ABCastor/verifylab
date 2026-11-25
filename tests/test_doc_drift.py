"""The documentation names only what the code has. Every `vl <command> [--flag]` written in code in the user and
contributor docs exists in the CLI; the status labels of the guide and SPEC tables are exactly those the code can
derive; every key of the vl.toml template is one the parser reads; the machine.toml template loads; the example
item of the AGENTS.md snippet validates. Each check also runs on a planted drift it must catch."""

from __future__ import annotations

import argparse
import ast
import re
import shutil
import tomllib
from pathlib import Path

import pytest

from verifylab import __version__, machine, status
from verifylab.adapters.lean_comparator import TOOLS
from verifylab.cli import PROJECT_ROUTES, build_parser, main
from verifylab.commands import docs as docs_command
from verifylab.config import ConfigError, parse_config
from verifylab.records import CLAIM_TYPES, ITEM_KINDS, VERDICTS

ROOT = Path(__file__).resolve().parents[1]
SKILLS = sorted((ROOT / "skills").rglob("*.md"))
DOCS = [ROOT / "README.md", ROOT / "AGENTS.md", ROOT / "CONTRIBUTING.md", ROOT / "SECURITY.md",
        ROOT / "docs" / "GUIDE.md", ROOT / "docs" / "FOREIGN.md", ROOT / "docs" / "INSTALL.md", ROOT / "docs" / "REPLAY.md",
        ROOT / "docs" / "SPEC.md", ROOT / "docs" / "ARCHITECTURE.md",
        ROOT / "docs" / "CHEATS.md", ROOT / "docs" / "PURPOSE-AND-ATTEMPTS.md",
        ROOT / "examples" / "toy-attempts" / "PURPOSE.md", *sorted((ROOT / "docs" / "adr").glob("*.md")),
        ROOT / "templates" / "AGENTS-snippet.md", *SKILLS]
LABEL_TABLES = [ROOT / "docs" / "GUIDE.md", ROOT / "docs" / "SPEC.md"]


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


def command_problems(text: str, model: dict, *, strict_actions=False) -> list[str]:
    """Every `vl <command>` with an unknown command, lane action or flag."""
    problems = []
    for code in code_texts(text.replace("\\|", "|")):
        for match in _USE.finditer(code):
            names, rest = match.group(1).split("|"), re.split(r" -- | && |; | #", match.group(2) + " ")[0]
            for name in names:
                if name not in model:
                    problems.append(f"`vl {name}`: no such command ({code.strip()})")
                    continue
                words = rest.split()
                if words and (name, words[0]) in PROJECT_ROUTES:
                    name = PROJECT_ROUTES[name, words[0]]
                    rest = rest.replace(words[0], "", 1)
                    words = rest.split()
                flags, actions = model[name]
                action_word = words[0] if words else ""
                known_action = action_word.split("|")[0] in actions
                literal_action = strict_actions and actions and re.fullmatch(r"[a-z][a-z|-]*", action_word)
                chosen = action_word.split("|") if known_action or literal_action else []
                allowed = flags.union(*(actions.get(a, set()) for a in chosen)) if chosen else set(flags)
                if not chosen and actions:
                    allowed = allowed.union(*actions.values())
                problems += [f"`vl {name} {a}`: no such action ({code.strip()})" for a in chosen if a not in actions]
                problems += [f"`vl {name} … {flag}`: no such flag ({code.strip()})"
                             for flag in re.findall(r"(?<![\w-])--[a-z][a-z0-9-]*", rest) if flag not in allowed]
    return problems


def test_every_command_and_flag_in_the_docs_exists():
    model = cli()
    problems = [f"{path.relative_to(ROOT)}: {p}" for path in DOCS for p in command_problems(path.read_text(), model, strict_actions=path in SKILLS)]
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


def test_main_help_groups_agent_verbs_and_usage_errors_name_a_next_command(capsys):
    with pytest.raises(SystemExit) as hello:
        main(["--help"])
    assert hello.value.code == 0
    help_text = capsys.readouterr().out
    for purpose in ("Understand and explain", "Improve and create", "Check and review", "Set up and coordinate"):
        assert purpose in help_text
    assert "trial " in help_text and not re.search(r"^  (simplify)\b", help_text, re.M)
    assert all(re.search(rf"^  {verb}\b", help_text, re.M) for verb in ("show", "find", "replay", "status", "queue"))
    with pytest.raises(SystemExit) as mistake:
        main(["attempts", "new", "--bogus"])
    assert mistake.value.code == 2
    assert "Next, run vl attempts new --help." in capsys.readouterr().err


def test_the_guide_names_every_command():
    guide = ROOT.joinpath("docs", "GUIDE.md").read_text().replace("\\|", "|")
    named = {m.group(1) for code in code_texts(guide) for m in _USE.finditer(code)}
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
        if m := re.match(r"^#? ?\[([a-z][a-z0-9_.]*)\]$", line):
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
        config = parse_config(Path("/nonexistent"), f"[{table}]\n{key} = {{ vl_drift = 1 }}\n")
        if table == "context":
            from verifylab.context import settings
            settings(config)
    except ConfigError as exc:
        # A closed table rejects an unknown key regardless of its value. Only a
        # known key refusing this planted wrong type demonstrates parser support.
        return not (table == "context" and "[context] unknown keys:" in str(exc))
    return False


def read_by_appraisal_manifest(table: str, key: str) -> bool:
    """True when the independent appraisal manifest parser recognizes this setting."""
    from types import SimpleNamespace

    from verifylab.appraisals import manifest
    from verifylab.records import RecordError

    raw = {"appraisal": {"version": 1, "criteria": {"novelty": {
        "question": "How novel?", "labels": ["low", "high"]}}}}
    if table == "appraisal":
        raw["appraisal"][key] = {"vl_drift": 1}
    elif table.startswith("appraisal.criteria."):
        criterion = table.removeprefix("appraisal.criteria.")
        raw["appraisal"]["criteria"][criterion] = {"question": "How novel?", "labels": ["low", "high"]}
        raw["appraisal"]["criteria"][criterion][key] = {"vl_drift": 1}
    else:
        return False
    repo = SimpleNamespace(config=SimpleNamespace(raw=raw))
    try:
        manifest(repo)
    except RecordError as exc:
        message = str(exc)
        if table == "appraisal":
            return "supports only version and criteria" not in message
        return "unsupported fields" not in message
    return False


def appraisal_parser_keys() -> set[tuple[str, str]]:
    """The appraisal parser is separate from parse_config; probe its closed manifest schema directly."""
    import verifylab.appraisals as appraisals

    names = {node.value for node in ast.walk(ast.parse(Path(appraisals.__file__).read_text()))
             if isinstance(node, ast.Constant) and isinstance(node.value, str)
             and re.fullmatch(r"[a-z][a-z_]*", node.value)}
    # `criteria` is a TOML parent table, represented by its child tables rather than a key assignment.
    return {(table, key) for table in ("appraisal", "appraisal.criteria.novelty") for key in names
            if not (table == "appraisal" and key == "criteria")
            if read_by_appraisal_manifest(table, key)}


def test_appraisal_key_probe_distinguishes_unknown_keys_from_bad_values():
    assert read_by_appraisal_manifest("appraisal", "version")
    assert read_by_appraisal_manifest("appraisal", "criteria")
    assert not read_by_appraisal_manifest("appraisal", "versionn")
    assert read_by_appraisal_manifest("appraisal.criteria.novelty", "question")
    assert not read_by_appraisal_manifest("appraisal.criteria.novelty", "questoin")


def test_context_key_probe_distinguishes_unknown_keys_from_bad_values():
    assert read_by_parser("context", "problem")
    assert read_by_parser("context", "notes")
    assert not read_by_parser("context", "problemm")


DEPRECATED = {("project", "trusted_ref"), ("check", "memory_total")}     # accepted, ignored, warned about


def parser_keys() -> set[tuple[str, str]]:
    """Every (table, key) parse_config reads, found by trying each identifier-like string of config.py."""
    import verifylab.config as config
    import verifylab.context as context
    names = {node.value for module in (config, context) for node in ast.walk(ast.parse(Path(module.__file__).read_text()))
             if isinstance(node, ast.Constant) and isinstance(node.value, str) and re.fullmatch(r"[a-z][a-z_]*", node.value)}
    return {(t, k) for t in ("project", "lean", "lanes", "check", "context")
            for k in names if read_by_parser(t, k)}


def test_the_vl_toml_template_shows_exactly_the_keys_the_parser_reads():
    template = ROOT.joinpath("templates", "vl.toml").read_text()
    keys = set(template_keys(template))
    appraisal_keys = appraisal_parser_keys()
    problems = []
    for table, key in sorted(keys):
        recognized = (read_by_appraisal_manifest(table, key) if table == "appraisal" or table.startswith("appraisal.")
                      else read_by_parser(table, key))
        if not recognized:
            problems.append(f"[{table}] {key}")
    assert problems == []
    assert sorted((parser_keys() | appraisal_keys) - DEPRECATED - keys) == []
    assert ("lean", "permitted_axioms") in parser_keys() and DEPRECATED <= parser_keys()
    assert not re.search(r"(?m)^\[appraisal(?:\.|\])", template), "optional examples must remain commented out"


def test_default_appraisal_rubric_is_unchanged():
    from types import SimpleNamespace

    from verifylab.appraisals import manifest

    assert manifest(SimpleNamespace(config=SimpleNamespace(raw={}))) == {
        "version": 1,
        "criteria": {
            "novelty": {"question": "How novel is this subject relative to the supplied sources?",
                        "labels": ["low", "medium", "high"], "ordered": True},
            "usefulness": {"question": "How useful is this subject for the named goal?",
                           "labels": ["low", "medium", "high"], "ordered": True},
        },
    }


def test_the_key_check_catches_a_planted_drift():
    keys = template_keys('[lean]\n# permited_axioms = ["propext"]\nroots = []\n[tools]\n# compiler = "/x"\n')
    assert [(t, k) for t, k in keys if not read_by_parser(t, k)] == [("lean", "permited_axioms"), ("tools", "compiler")]


def test_the_machine_template_loads_with_every_example_uncommented(tmp_path: Path):
    text = ROOT.joinpath("templates", "machine.toml").read_text()
    uncommented = re.sub(r"(?m)^# (?=[a-z][a-z0-9_]* = |\")", "", text)
    (tmp_path / "machine.toml").write_text(uncommented)
    loaded = machine.load(tmp_path / "machine.toml")
    assert set(loaded.tools) == set(machine.TOOL_NAMES) and loaded.elan_home and loaded.caches and loaded.memory_total
    assert set(loaded.queue) == set(machine.QUEUE_KEYS)


@pytest.mark.parametrize("path", DOCS, ids=lambda p: str(p.relative_to(ROOT)))
def test_no_doc_names_a_path_of_one_machine(path: Path):
    assert re.findall(r"/home/[a-z]|/Users/[A-Za-z]", path.read_text()) == []


# Stale statements after a merge -----------------------------------------------------------------------------------

def prose(path: str) -> str:
    return " ".join((ROOT / path).read_text().split())


def test_the_changelog_does_not_promise_commands_that_already_exist():
    assert "for when those commands exist" not in prose("CHANGELOG.md")


SKILL = ROOT / "skills" / "verifylab"
_LINK = re.compile(r"\]\(([^)#\s]+\.md)\)")
_ROW = re.compile(r"\b([PESRXA][0-9]+)\b")


def link_problems(folder: Path) -> list[str]:
    """Relative reference links must stay in the installed skill and reach every reference from its entry."""
    entry = folder / "SKILL.md"
    files = [entry, *sorted((folder / "references").rglob("*.md"))]
    edges, broken = {}, []
    for path in files:
        edges[path] = []
        for target in _LINK.findall(path.read_text()):
            resolved = (path.parent / target).resolve()
            if not resolved.is_relative_to(folder.resolve()) or not resolved.is_file():
                broken.append(f"{path.name}: {target}")
            else:
                edges[path].append(resolved)
    pending, reached = [entry], set()
    while pending:
        path = pending.pop()
        if path not in reached:
            reached.add(path)
            pending.extend(edges.get(path, []))
    return broken + [f"SKILL.md cannot reach {path.name}" for path in files[1:] if path not in reached]


def test_there_is_one_versioned_skill_with_a_route_for_each_purpose(tmp_path):
    assert [path.parent.name for path in (ROOT / "skills").glob("*/SKILL.md")] == ["verifylab"]
    text = (SKILL / "SKILL.md").read_text()
    assert text.startswith("---\nname: verifylab\ndescription: ") and len(text.splitlines()) <= 60
    assert f'version: "{__version__}"' in text
    for name in ("understand", "explain", "improve", "create", "check", "study"):
        row = next(line for line in text.splitlines() if f"(references/{name}.md)" in line)
        assert "`vl " in row
    assert link_problems(SKILL) == []
    copy = tmp_path / "verifylab"
    shutil.copytree(SKILL, copy)
    (copy / "references" / "study.md").rename(copy / "references" / "reading.md")
    assert link_problems(copy) == [
        "SKILL.md: references/study.md", "explain.md: study.md", "SKILL.md cannot reach reading.md"]
    assert command_problems("`vl attempts nope FILE`", cli(), strict_actions=True)
    assert not command_problems("`vl attempts new FILE`", cli(), strict_actions=True)


_REPOSITORY_PATH = re.compile(r"(?<![\w./-])((?:docs|examples|fixtures|scripts|skills|src|templates|tests)/[\w./-]+"
                              r"|(?:README|CHANGELOG|CONTRIBUTING|SECURITY)\.md)")
_PRINTED = re.compile(r"vl docs ([\w./-]+)")


def pointer_problems(folder: Path) -> list[str]:
    """What a copy of the skill that was installed alone cannot reach: a path of the VerifyLab repository other than
    a document; a `docs/NAME.md` that `vl docs NAME` does not print, or that its file names without that command;
    a `vl docs NAME` that names no document. `NAME` itself is the placeholder of the rule."""
    known, problems = docs_command.documents(), []
    for path in [folder / "SKILL.md", *sorted((folder / "references").glob("*.md"))]:
        text = path.read_text()
        printed = {name for name in _PRINTED.findall(text) if name != "NAME"}
        for pointer in dict.fromkeys(match.rstrip(".") for match in _REPOSITORY_PATH.findall(text)):
            name = pointer.removeprefix("docs/").removesuffix(".md")
            if not (pointer.startswith("docs/") and pointer.endswith(".md")):
                problems.append(f"{path.name}: {pointer} is not in an installed copy of the skill")
            elif name != "NAME" and docs_command.find(name, known) is None:
                problems.append(f"{path.name}: {pointer} is not a document that `vl docs` prints")
            elif name != "NAME" and name not in printed:
                problems.append(f"{path.name}: {pointer} is named without `vl docs {name}`")
        problems += [f"{path.name}: `vl docs {name}` names no document" for name in sorted(printed)
                     if docs_command.find(name, known) is None]
    return problems


def test_every_pointer_of_the_skill_resolves_from_the_installed_folder_and_the_vl_command(tmp_path, capsys):
    """`vl init --skills DIR --copy` leaves the folder of the skill and nothing else of the repository. From there a
    link must lead to a file of the copy, and a document of the repository must be one that the command prints."""
    assert main(["init", "--skills", str(tmp_path), "--copy"]) == 0
    installed = tmp_path / "verifylab"
    assert sorted(path.name for path in installed.iterdir()) == ["SKILL.md", "references"]
    assert link_problems(installed) == [] and pointer_problems(installed) == []
    texts = [path.read_text() for path in installed.rglob("*.md")]
    named = sorted({name for text in texts for name in _PRINTED.findall(text)} - {"NAME"})
    assert {"CHEATS", "PURPOSE-AND-ATTEMPTS"} <= set(named) and len(named) >= 5      # the convention, the catalogue, the guides
    assert "For a bundled document `docs/NAME.md`, run `vl docs NAME`." in (
        installed / "SKILL.md").read_text()
    capsys.readouterr()
    for name in named:
        assert main(["docs", name]) == 0 and capsys.readouterr().out == (ROOT / "docs" / f"{name}.md").read_text()
    prove, referee = installed / "references" / "prove.md", installed / "references" / "referee.md"     # the check can fail
    prove.write_text(prove.read_text() + "See docs/NOPE.md (`vl docs NOPE`), scripts/lean/dumpx.lean and the README.md.\n")
    referee.write_text(referee.read_text().replace(" (`vl docs CHEATS`, `vl docs SPEC`)", ""))
    assert pointer_problems(installed) == [
        "prove.md: docs/NOPE.md is not a document that `vl docs` prints",
        "prove.md: scripts/lean/dumpx.lean is not in an installed copy of the skill",
        "prove.md: README.md is not in an installed copy of the skill",
        "prove.md: `vl docs NOPE` names no document",
        "referee.md: docs/CHEATS.md is named without `vl docs CHEATS`",
        "referee.md: docs/SPEC.md is named without `vl docs SPEC`"]


def test_every_trap_the_skill_names_is_a_row_of_the_cheat_catalogue():
    part = (SKILL / "references" / "referee.md").read_text()
    rows = set(re.findall(r"(?m)^\| ([PESRXA][0-9]+) \|", (ROOT / "docs" / "CHEATS.md").read_text()))
    named = set(_ROW.findall(part))
    assert len(named) >= 15 and named <= rows, sorted(named - rows)
    assert not {"S99"} <= rows                                       # control: a code that is no row is not accepted


def test_the_line_for_a_groups_agents_file_is_the_same_in_the_readme_and_in_the_snippet():
    line = next(line for line in (ROOT / "README.md").read_text().splitlines()
                if line.startswith("For formal mathematics in this repository, load the `verifylab` skill"))
    assert line in (ROOT / "templates" / "AGENTS-snippet.md").read_text()
    assert "follow its route for your purpose" in line


def slug(heading: str) -> str:
    """The anchor GitHub gives a heading: lower case, punctuation dropped, spaces as hyphens."""
    return re.sub(r"[^a-z0-9 _-]", "", heading.lower()).replace(" ", "-")


def test_the_readme_links_to_its_own_sections_by_their_real_anchor_and_names_comparator_as_leans():
    text = (ROOT / "README.md").read_text()
    anchors = {slug(line.lstrip("#").strip()) for line in text.splitlines() if line.startswith("#")}
    links = re.findall(r"\]\(#([^)]+)\)", text)
    assert "the-verifier-around-leans-comparator" in links and set(links) <= anchors
    section = " ".join(text.split("## The verifier around Lean's Comparator", 1)[1].split())
    for must in ("calls Comparator", "Lean FRO", "Lean v4.35.0", "`lake comparator`",
                 "trusted Git ref", "statement probes", "receipt bound to a revision", "reviews what the statement means"):
        assert must in section, must
    assert "before Lean shipped" not in text and "built first" not in text


def test_cli_reports_the_package_version(capsys):
    version = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
    with pytest.raises(SystemExit) as ended:
        main(["--version"])
    assert ended.value.code == 0
    assert capsys.readouterr().out == f"vl {version}\n"


def test_the_skill_points_to_where_statuses_live_and_says_how_a_person_gives_a_direction():
    """The status list is in docs/SPEC.md, not the README; a person's direction needs a recipe a weaker model can
    follow (kind, author, tags, target); an edit of a question field makes receipts stale."""
    orient, understand, improve = (prose(f"skills/verifylab/references/{s}.md") for s in ("orient", "understand", "improve"))
    assert "README of VerifyLab lists every status" not in orient and "docs/SPEC.md" in orient
    assert "there is no rating command" not in orient
    for must in ("**direction**", "`intuition`", '`author = "human:<name>"`', "priority in the body",
                 '`uses = ["<id>"]`', "`area:NAME`", "trusted ref"):
        assert must in understand, must
    assert "its receipts stay valid" not in improve and "makes them stale" in improve


def test_the_attempt_reference_exposes_the_convention_and_measurement_workflows():
    part = prose("skills/verifylab/references/attempts.md")
    for must in ("docs/PURPOSE-AND-ATTEMPTS.md", "Omit `outcome` on open candidates", "`not-run`",
                 "`unverified` resolves measured costs only", "`outcome.kept`", "by band and by purpose",
                 "`vl attempts table --lanes --measured`"):
        assert must in part, must
    convention = prose("docs/PURPOSE-AND-ATTEMPTS.md")
    for must in ("An open attempt has no `outcome` key at all", "The line of work goes in `lane`",
                 "`outcome.kept` is for an attempt that ended `abandoned` or `rejected`",
                 "Values with different bases are not additive", "`probe_minutes` and `probe_peak_ram_gb`: one focused invocation",
                 "by band of the forecast (five bands of 0.2", "the lines of what the statements reach"):
        assert must in convention, must
    improve = prose("skills/verifylab/references/improve.md")
    assert "which use keeps it reachable" in improve
    assert "Rebuild the downstream closure" in improve
    understand = prose("skills/verifylab/references/understand.md")
    assert "readers' raw answers and generality in separate fields" in understand


# One rule for heavy runs ---------------------------------------------------------------------------------------

RULE_FILES = ("skills/verifylab/references/orient.md", "skills/verifylab/references/experiment.md")


@pytest.mark.parametrize("path", RULE_FILES)
def test_every_place_states_the_one_rule_for_heavy_runs(path: str):
    """Lane builds are isolated. Generic queue admission follows the project scheduler."""
    text = prose(path)
    assert "every build and run" in text and "`vl lane exec" in text, path
    assert "`vl queue run --mem SIZE" in text and "outside a lane" in text.lower(), path
    assert "If the project uses VerifyLab scheduling" in text, path


def test_the_skill_distinguishes_queue_reservation_from_conditional_memory_cap():
    text = prose("skills/verifylab/references/experiment.md")
    assert "reservation" in text and "MemoryMax" in text
    assert "systemd user manager" in text and "warns and starts uncapped" in text


def test_the_agents_snippet_links_to_the_skill_without_repeating_rules():
    raw = ROOT.joinpath("templates", "AGENTS-snippet.md").read_text()
    assert raw.count("`verifylab` skill") == 1 and len(raw.splitlines()) <= 12
    assert "vl.trustedRef" in raw and "<!-- vl:context -->" in raw
    assert "MemoryMax" not in raw and "`--status`" not in raw


# Probes run beside Comparator; only their recording waits for a pass -----------------------------------------------

PROBE_FILES = ("src/verifylab/adapters/lean_probes.py", "templates/vl.toml", "docs/GUIDE.md", "docs/CHEATS.md",
               "tests/test_lean_probes.py")


@pytest.mark.parametrize("path", PROBE_FILES)
def test_probes_are_described_as_running_beside_comparator(path: str):
    text = prose(path)
    assert "beside Comparator" in text and "recorded" in text, path


def test_adr_0002_points_to_the_adr_that_runs_the_probes_beside_comparator():
    text = prose("docs/adr/0002-trust-from-repository-and-machine.md")
    assert "## 6. Statement probes recorded after every pass" in text
    assert "ADR 0005 §4" in text


# Proposed decisions are marked wherever their behaviour is stated -----------------------------------------------

ADR_MENTION = re.compile(r"(?:ADR (\d{4})(?: §\d+)?|docs/adr/(\d{4})-[a-z-]+\.md)(, proposed\))?")
ADR_BEHAVIOUR = {"0004": r"environment_from|environment of the commit that added|infers nothing",
                 "0005": r"build store|build_cache|lean-builds",
                 "0008": r"reuse_solution_builds|[Rr]eused solution builds?|lean-solution-builds|solution_build\b",
                 "0009": r"`vl replay",
                 "0011": r"vl export catalog|catalog\.json",
                 "0012": r"trusted_packages|\[sandbox\]|disposable sandbox|sandbox class",
                 "0015": r"vl receipts|foreign receipts?"}
ADR_BEHAVIOUR_IN_CODE = {"0005": ADR_BEHAVIOUR["0005"], "0008": ADR_BEHAVIOUR["0008"],
                         "0012": ADR_BEHAVIOUR["0012"], "0015": r"vl receipts"}     # the source's prose; `environment_from` is only a name


def adr_statuses() -> dict[str, bool]:
    """ADR number -> whether its Status line says proposed."""
    return {path.name[:4]: re.search(r"(?m)^Status: (\w+)", path.read_text()).group(1) == "proposed"
            for path in (ROOT / "docs" / "adr").glob("[0-9]*.md")}


def adr_marking_problems(text: str, own: str, proposed: dict[str, bool]) -> list[str]:
    """A mention of a proposed ADR must end `, proposed)`; a mention of an accepted one must not."""
    problems = []
    for m in ADR_MENTION.finditer(text):
        number = m.group(1) or m.group(2)
        if number != own and number in proposed and proposed[number] != bool(m.group(3)):
            problems.append(f"{m.group(0)!r}: ADR {number} is {'' if proposed[number] else 'not '}proposed")
    return problems


def test_the_marking_check_catches_a_planted_drift():
    status_ = {"0004": True, "0005": False}
    assert adr_marking_problems("a (ADR 0004). b (docs/adr/0004-x-y.md, proposed) c (ADR 0005 §4, proposed)",
                                "0002", status_) == [
        "'ADR 0004': ADR 0004 is proposed", "'ADR 0005 §4, proposed)': ADR 0005 is not proposed"]
    assert adr_marking_problems("(ADR 0004, proposed) (ADR 0005 §4) ADR 0004 itself", "0004", status_) == []


MARKED_FILES = [*DOCS, ROOT / "CHANGELOG.md", ROOT / "templates" / "machine.toml", ROOT / "templates" / "vl.toml",
                *sorted((ROOT / "src").rglob("*.py")), *sorted((ROOT / "tests").glob("test_*.py"))]


def test_proposed_adrs_are_marked_proposed_wherever_they_are_cited():
    proposed = adr_statuses()
    assert not proposed["0008"] and proposed["0012"] and not proposed["0004"] and not proposed["0002"]  # their status is the owner's to change
    problems = [f"{path.relative_to(ROOT)}: {p}" for path in MARKED_FILES if path != Path(__file__)
                for p in adr_marking_problems(path.read_text(), path.name[:4] if path.parent.name == "adr" else "",
                                              proposed)]
    assert problems == []


def test_every_file_that_states_the_behaviour_of_a_proposed_adr_marks_it():
    missing = []
    for number, proposed in adr_statuses().items():
        marked = re.compile(rf"ADR {number}(?: §\d+)?, proposed\)|docs/adr/{number}-[a-z-]+\.md, proposed\)")
        for path in MARKED_FILES:
            relative = path.relative_to(ROOT)
            in_code = relative.parts[0] == "src"
            pattern = (ADR_BEHAVIOUR_IN_CODE if in_code else ADR_BEHAVIOUR).get(number)
            if proposed and pattern and (in_code or path.suffix in (".md", ".toml")) and path.parent.name != "adr":
                text = path.read_text()
                if re.search(pattern, text) and not marked.search(text):
                    missing.append(f"{relative}: ADR {number}")
    assert missing == []
