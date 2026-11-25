"""`vl attempts new|check|table`: the attempt files of docs/PURPOSE-AND-ATTEMPTS.md, in any repository."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from .. import attempt_files, gitref
from ..output import emit, fail, terminal_text
from ..records import RecordError
from . import JSON_HELP

NOT_CHECKED = "the order of forecast and outcome in Git was not checked"
NO_LANE = "(no lane)"
BANDS = "p_accepted by band of the forecast (a band holds its lower edge, the last one also 1)"
LEGEND = ("p_accepted is resolved by the attempts that ended accepted, rejected or abandoned (the last two as not accepted);\n"
          "a value interval only by an accepted one (a forecast marked 'unconditional': also rejected and abandoned, as 0);\n"
          "a cost interval by any run attempt that measured it. 'unverified' and 'not-run' resolve no acceptance and no value.\n"
          "A measured value is a change against the base of its attempt: values with different bases are not additive\n"
          "(--measured lists every one under its base).")
KEPT = "kept of attempts that ended abandoned or rejected (it resolves no forecast), under the base it is a change against"
MEASURED = ("measured changes of each attempt, under the base they are changes against ('kept': what stays of an attempt that\n"
            "ended abandoned or rejected; it resolves no forecast)")


def register(sub):
    parser = sub.add_parser("attempts", help="scaffold and check attempts, then compare forecasts with outcomes",
                            description="Attempt files (Markdown with `schema: vl.attempt/2` front matter) in any "
                                        "repository. No VerifyLab project is needed. See docs/PURPOSE-AND-ATTEMPTS.md.")
    actions = parser.add_subparsers(dest="action", required=True, metavar="ACTION")
    for name, help_text in (("check", "validate a folder or one attempt file, including order before the recorded outcome in Git"),
                            ("table", "forecast against outcome, per forecaster and dimension")):
        action = actions.add_parser(name, help=help_text)
        action.add_argument("dir", nargs="?", type=Path, default=Path("attempts"),
                            metavar="DIR|FILE" if name == "check" else "DIR",
                            help="folder or selected attempt file (default: attempts)" if name == "check"
                            else "folder of attempt files (default: attempts)")
        action.add_argument("--no-git", action="store_true", help="do not read the order of forecast and outcome from Git")
        if name == "table":
            action.add_argument("--lanes", action="store_true",
                                help="under each forecaster, one row per lane of its forecasts (the JSON always has them)")
            action.add_argument("--measured", action="store_true",
                                help="list the measured changes of every attempt under their base, not only what was kept of "
                                     "an abandoned or rejected one (the JSON always has them)")
        action.add_argument("--json", action="store_true", help=JSON_HELP)
    new = actions.add_parser("new", help="scaffold a pre-work forecast in a Markdown attempt")
    new.add_argument("file", type=Path, metavar="FILE", help="new .md path (use - to print)")
    new.add_argument("--base", default="HEAD", metavar="REV", help="revision before the work (default HEAD)")
    new.add_argument("--purpose", required=True, help="purpose of this attempt")
    new.add_argument("--claim", required=True, help="what the change or experiment should establish")
    new.add_argument("--by", required=True, help="author of the forecast")
    new.add_argument("--lane", help="line of work within the forecaster's attempts")
    new.add_argument("--p-accepted", type=float, help="forecast chance of meeting the stated contract, from 0 to 1")
    new.add_argument("--basis", help="actual basis for the forecast, or a reminder to write it before committing")
    parser.set_defaults(handler=run)
    new.set_defaults(handler=scaffold)


def scaffold(args) -> int:
    stdout = args.file == Path("-")
    if not stdout and (args.file.suffix != ".md" or args.file.name.lower() == "readme.md"):
        raise RecordError("an attempt scaffold needs a .md filename or '-' for stdout")
    repo = attempt_files.toplevel(Path.cwd())
    if repo is None:
        raise RecordError("run this from the Git repository that holds the baseline")
    base = gitref.rev_parse(repo, args.base)
    entry = {"by": args.by, "at": datetime.now(timezone.utc).isoformat(),
             "basis": args.basis if args.basis is not None else "TODO: write the basis before committing"}
    if args.lane is not None:
        entry["lane"] = args.lane
    if args.p_accepted is not None:
        entry["p_accepted"] = args.p_accepted
    data = {"schema": attempt_files.SCHEMA, "id": "new-attempt" if stdout else args.file.stem,
            "purpose": args.purpose, "base": base, "claim": args.claim, "forecasts": [entry]}
    if problems := attempt_files.validate(data):
        raise RecordError("; ".join(problems))
    front = [f"{key}: {json.dumps(data[key], ensure_ascii=False)}"
             for key in ("schema", *attempt_files.REQUIRED_TEXT)]
    forecast = [f"{key}: {json.dumps(value, ensure_ascii=False)}" for key, value in entry.items()
                if key != "p_accepted"]
    if args.p_accepted is not None:
        forecast.append(f"p_accepted: {Decimal(str(args.p_accepted)):f}")
    sidecar = "ATTEMPT.trial.json" if stdout else attempt_files.trial_output(args.file).name
    content = "\n".join(["---", *front, "forecasts:", f"  - {forecast[0]}",
                         *(f"    {line}" for line in forecast[1:]), "---", "",
                         "## Evidence", "", "Record the named targets and independent contract check here.",
                         f"Build trial evidence, when available: `{sidecar}`.", ""])
    if stdout:
        sys.stdout.write(content)
    else:
        args.file.parent.mkdir(parents=True, exist_ok=True)
        with args.file.open("x", encoding="utf-8") as stream:
            stream.write(content)
        emit(args, f"Created {args.file}. Complete the forecast, then commit it before starting the work.",
             {"file": str(args.file), "base": base})
    return 0


def _number(value: float) -> str:
    return f"{value:+.4g}"


def _columns(rows: list[list[str]], names: int = 1) -> str:
    """Aligned columns: the first `names` hold names (left), the others numbers (right)."""
    widths = [max(len(row[i]) for row in rows) for i in range(len(rows[0]))]
    return "\n".join("  ".join(cell.ljust(width) if i < names else cell.rjust(width)
                               for i, (cell, width) in enumerate(zip(row, widths))).rstrip() for row in rows)


def _notes(notes: list[dict]) -> list[str]:
    return [f"{note['file']}: note: {note['message']}" for note in notes]


def _laned(rows: list[dict], lane_rows: list[dict], lanes: bool) -> list[dict]:
    """The rows of a table, each followed by the rows of its lanes when they are asked for."""
    return [shown for row in rows for shown in [row] + [lane for lane in lane_rows if lanes and all(
        lane[key] == value for key, value in row.items() if key in ("by", "kind", "dimension"))]]


def _who(row: dict) -> str:
    return f"  {row['lane'] or NO_LANE}" if "lane" in row else row["by"]


def _chances(row: dict) -> list[str]:
    return [str(row["resolved"]), f"{row['mean_forecast']:.2f}", f"{row['share_accepted']:.2f}", f"{row['brier']:.3f}"]


def _measured(rows: list[dict]) -> list[str]:
    """Measured changes under the base they are changes against, one line per attempt and kind."""
    changes: dict[tuple[str, str, str, str], list[str]] = {}
    for row in rows:
        changes.setdefault((row["base"], row["file"], row["status"], row["kind"]), []).append(
            f"{row['dimension']} {row['measured']:+}")
    widths = [max(len(key[i]) for key in changes) for i in (1, 2, 3)]
    return [line for base in dict.fromkeys(key[0] for key in changes) for line in [f"base {base}"] + [
        "  " + "  ".join(cell.ljust(width) for cell, width in zip(key[1:], widths)) + "  " + ", ".join(values)
        for key, values in changes.items() if key[0] == base]]


def table_text(data: dict, directory: Path, lanes: bool = False, measured: bool = False) -> str:
    lines = [f"{directory}: {data['files']} attempt files, {data['with_outcome']} with an outcome, {data['open']} open"]
    if data["intervals"]:
        lines += ["", "80% intervals", _columns(
            [["forecaster", "dimension", "resolved", "inside", "median (outcome - midpoint)"]] +
            [[_who(row), f"{row['kind']}.{row['dimension']}", str(row["resolved"]), str(row["inside"]),
              _number(row["median_error"])] for row in _laned(data["intervals"], data["intervals_by_lane"], lanes)], names=2)]
    if data["p_accepted"]:
        lines += ["", "p_accepted", _columns(
            [["forecaster", "resolved", "mean forecast", "accepted", "Brier"]] +
            [[_who(row), *_chances(row)] for row in _laned(data["p_accepted"], data["p_accepted_by_lane"], lanes)])]
        for key, title in (("band", BANDS), ("purpose", "p_accepted by purpose of the attempt")):
            lines += ["", title, _columns([["forecaster", key, "resolved", "mean forecast", "accepted", "Brier"]] +
                                          [[row["by"], row[key], *_chances(row)] for row in data[f"p_accepted_by_{key}"]], names=2)]
    if not data["intervals"] and not data["p_accepted"]:
        lines += ["", "No forecast is resolved yet under the rules below."]
    changes = [row for row in data["measured"] if measured or row["kind"] == "kept"]
    if changes:
        lines += ["", MEASURED if measured else KEPT, *_measured(changes)]
    lines += ["", LEGEND]
    if data["score_pairs"]:
        lines += ["", "agent score against a later score by a person (same what, reader and task)", _columns(
            [["agent", "what", "pairs", "mean (agent - person)"]] +
            [[row["by"], row["what"], str(row["pairs"]), _number(row["mean_difference"])] for row in data["score_pairs"]],
            names=2)]
    else:
        lines += ["", "No agent score has a later score by a person on the same what, reader and task yet."]
    if data["skipped_files"]:
        lines += ["", f"Left out, files with problems (vl attempts check): {', '.join(data['skipped_files'])}"]
    if data["excluded_forecasts"]:
        lines += ["", "Left out, forecasts not frozen before the outcome: " + ", ".join(
            f"{row['file']} by {row['by']} ({row['reason']})" for row in data["excluded_forecasts"])]
    if data["notes"]:
        lines += ["", *_notes(data["notes"])]
    return "\n".join(lines)


def run(args) -> int:
    path = args.dir
    selected = args.action == "check" and (path.is_file() or path.is_symlink() and not path.is_dir())
    directory = path.parent if selected else path
    if not directory.is_dir():
        return fail(f"{directory}: no such folder of attempt files")
    repository = attempt_files.toplevel(directory)
    root = (repository or directory).resolve()
    if selected:
        if not path.resolve().is_relative_to(root):
            return fail(f"{path}: selected attempt resolves outside the Git repository")
        attempt = (attempt_files.read(path, root=root)
                   if path.suffix == ".md" and path.name.lower() != "readme.md" else None)
        if attempt is None:
            return fail(f"{path}: expected a Markdown attempt file")
        attempts, others = [attempt], []
    else:
        attempts, others = attempt_files.read_folder(directory, root=root)
    not_attempts = [{"file": str(path), "message": attempt_files.NOT_ATTEMPT} for path in others]
    top = None if args.no_git else repository
    for attempt in attempts:
        attempt_files.check_trial_link(attempt, top)
    unassessed = top is not None and attempt_files.check_order(attempts, top)
    attempt_files.check_duplicates(attempts)
    notice = None
    git_order = "checked"
    if top is None:
        git_order = "not-checked"
        notice = NOT_CHECKED + (" (--no-git)" if args.no_git else f": {directory} is not in a Git repository")
    elif unassessed:
        git_order = "partial"
        notice = "forecast chronology unassessed where shallow Git history is missing; deepen the checkout"
    if args.action == "table":
        data = attempt_files.table(attempts)
        data["git_order"], data["notes"] = git_order, not_attempts
        text = table_text(data, directory, args.lanes, args.measured)
    else:
        problems = [{"file": str(attempt.path), "message": message} for attempt in attempts for message in attempt.problems]
        notes = [{"file": str(attempt.path), "message": message} for attempt in attempts for message in attempt.notes
                 ] + not_attempts
        data = {"schema": attempt_files.CHECK_SCHEMA, "files": len(attempts),
                "open": sum(1 for a in attempts if a.data is not None and "outcome" not in a.data),
                "git_order": git_order, "problems": problems, "notes": notes}
        if selected:
            data["scope"] = {"file": str(path)}
        scope = f"{path} (selected file)" if selected else str(directory)
        text = "\n".join([f"{p['file']}: {p['message']}" for p in problems] + _notes(notes) +
                         [f"{scope}: {len(attempts)} attempt files, {data['open']} open, "
                          f"{len({p['file'] for p in problems})} with problems"])
    if args.json:
        sys.stdout.write(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    else:
        sys.stdout.write(terminal_text(text) + "\n")
    if notice:
        sys.stderr.write(f"vl: note: {notice}\n")
    return 1 if args.action == "check" and data["problems"] else 0
