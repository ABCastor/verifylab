"""Attempt files of the purpose-and-attempts convention (docs/PURPOSE-AND-ATTEMPTS.md): a folder of Markdown files,
one per attempt, whose YAML front matter (`schema: vl.attempt/2`) holds the forecasts written before the work, the
measured outcome written after it, and signed scores. The folder lives in any repository; nothing here opens
a VerifyLab project.

Three things a reader should not have to take on an agent's care: each file follows the convention (`validate`),
a forecast was committed before its outcome and was not edited afterwards (`check_order`, read from Git), and one
table of forecast against outcome per forecaster (`table`). The JSON record of `vl trial` (`vl.attempt/1`,
commands/attempts.py) is a different thing: one measured trial, written by the tool.
"""

from __future__ import annotations

import json
import re
import statistics
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import gitref
from .records import sha256_hex
from .formalization_yaml import CatalogueError, parse_yaml

SCHEMA = "vl.attempt/2"
CHECK_SCHEMA = "vl.attempts.check/1"
TABLE_SCHEMA = "vl.attempts.table/1"
REQUIRED_TEXT = ("id", "purpose", "base", "claim")
TOP_KEYS = {"schema", *REQUIRED_TEXT, "forecasts", "outcome", "scores"}
FORECAST_KEYS = {"by", "lane", "at", "p_accepted", "value", "cost", "basis", "unconditional"}
OUTCOME_KEYS = {"status", "by", "at", "commit", "value", "kept", "cost", "contract", "evidence"}
SCORE_KEYS = {"what", "value", "text", "forecast", "reader", "task", "by", "lane", "at", "basis"}
STATUSES = ("accepted", "rejected", "abandoned", "unverified", "not-run")
UNVERIFIED, NOT_RUN = "unverified", "not-run"   # the change exists and its contract was not checked; never tried
KEEPS = ("abandoned", "rejected")            # the claim failed, and a part of the change may stay: `outcome.kept`
PERSON = "person:"
TEXT_SCORES = ("elegance", "explanatory")    # a sentence signed by a person, never a number
ANCHORS = (1, 3, 5, 7, 9)                    # the levels with a sentence a second judge can check
FORECAST_FROM = 7                            # from this level an agent's score predicts what a person would say
BANDS = ("0.0-0.2", "0.2-0.4", "0.4-0.6", "0.6-0.8", "0.8-1.0")   # of p_accepted; each holds its lower edge, the last also 1
MAX_BYTES = 1024 * 1024
NOT_ATTEMPT = "not an attempt file: it does not start with a '---' line"
LATE, EDITED, DUPLICATE = "late", "edited", "duplicate"


@dataclass
class Attempt:
    path: Path
    data: dict[str, Any] | None = None                      # the front matter when it could be read
    problems: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)          # reported, never a problem
    entries: list[str] = field(default_factory=list)        # each forecast entry as written, byte for byte
    excluded: dict[int, str] = field(default_factory=dict)  # forecast index -> reason it cannot resolve

    @property
    def valid(self) -> bool:
        return self.data is not None and not self.problems

    @property
    def outcome(self) -> dict[str, Any] | None:
        return self.data.get("outcome") if self.data else None


def trial_output(path: Path) -> Path:
    return path.with_suffix(".trial.json")


# Reading ------------------------------------------------------------------------------------------------------

def opens_front_matter(text: str) -> bool:
    """Whether the first line is `---`: what makes a Markdown file of the folder an attempt file."""
    return [line.rstrip() for line in text.splitlines()[:1]] == ["---"]


def front_matter(text: str) -> str | None:
    """The lines between the opening `---` line and the next one, behind one empty line so that the line numbers
    of the YAML reader are those of the file. None when the file does not start with front matter, or does not
    close it."""
    lines = text.splitlines()
    if not opens_front_matter(text):
        return None
    for end in range(1, len(lines)):
        if lines[end].rstrip() == "---":
            return "\n" + "\n".join(lines[1:end]) + "\n"
    return None


_TOP_KEY = re.compile(r"[A-Za-z_][A-Za-z0-9_.-]*:")


def forecast_entries(front: str) -> list[str]:
    """Each item of the block list under the top-level `forecasts:`, as written; the dashes may sit under the key
    or be indented. Git order compares these texts, so an edit of a comment or of spacing inside an entry is an
    edit."""
    lines = front.split("\n")
    start = next((i for i, line in enumerate(lines) if line.startswith("forecasts:")), None)
    if start is None:
        return []
    block = []
    for line in lines[start + 1:]:
        if _TOP_KEY.match(line):
            break
        block.append(line)
    first = next((line for line in block if line.strip() and not line.lstrip().startswith("#")), "")
    dash = first[:len(first) - len(first.lstrip())] + "-"
    if not first.lstrip().startswith("-"):
        return []
    entries: list[list[str]] = []
    for line in block:
        if line == dash or line.startswith(dash + " "):
            entries.append([line])
        elif entries:
            entries[-1].append(line)
    return ["\n".join(entry).rstrip() for entry in entries]


def forecast_fingerprint(entries: list[str]) -> str:
    return sha256_hex("\n".join(entries).encode())


def read(path: Path, *, root: Path | None = None) -> Attempt | None:
    """The attempt file at `path`, with its problems. None for a readable file that does not start with a `---`
    line: another Markdown file kept next to the attempts (a `BASELINE.md`), which is not an attempt file."""
    attempt = Attempt(path)
    try:
        if root is not None and not path.resolve().is_relative_to(root):
            attempt.problems.append("attempt resolves outside the checkout")
            return attempt
        if path.stat().st_size > MAX_BYTES:
            raise OSError(f"larger than {MAX_BYTES} bytes")
        text = path.read_text(encoding="utf-8")
    except (OSError, RuntimeError, UnicodeDecodeError) as exc:
        attempt.problems.append(f"cannot read: {exc}")
        return attempt
    return parse(path, text)


def parse(path: Path, text: str) -> Attempt | None:
    attempt = Attempt(path)
    if not opens_front_matter(text):
        return None
    front = front_matter(text)
    if front is None:
        attempt.problems.append("the YAML front matter is not closed: no second '---' line after the first one")
        return attempt
    try:
        data = parse_yaml(front, typed=True)
    except CatalogueError as exc:
        attempt.problems.append(str(exc))
        return attempt
    if not isinstance(data, dict):
        attempt.problems.append("the front matter is not a mapping of keys to values")
        return attempt
    attempt.data, attempt.entries = data, forecast_entries(front)
    attempt.problems += validate(data)
    attempt.notes += notes(data)
    if isinstance(data.get("forecasts"), list) and len(data["forecasts"]) != len(attempt.entries):
        attempt.problems.append("write 'forecasts' as a block list, one '- by: ...' item per forecaster")
    return attempt


def read_folder(directory: Path, *, root: Path | None = None) -> tuple[list[Attempt], list[Path]]:
    """Every `*.md` directly in the folder except README.md, in name order: the attempt files, and the Markdown
    files that are not one (`read`), which the caller names in a note and counts nowhere."""
    files = {path: read(path, root=root) for path in sorted(directory.glob("*.md"))
             if path.name.lower() != "readme.md"}
    return ([attempt for attempt in files.values() if attempt is not None],
            [path for path, attempt in files.items() if attempt is None])


# The rules ----------------------------------------------------------------------------------------------------

def _text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _need_text(entry: dict[str, Any], keys: tuple[str, ...]) -> list[str]:
    return [f"'{key}' is required: a non-empty text" + (" (put a value made of digits in quotes)"
                                                         if _number(entry.get(key)) else "")
            for key in keys if not _text(entry.get(key))]


def _unknown(entry: dict[str, Any], known: set[str]) -> list[str]:
    return [f"unknown key '{key}'" for key in sorted(set(entry) - known)]


def _lane(entry: dict[str, Any]) -> list[str]:
    """`lane` is optional and names a line of work inside one forecaster: `by` stays the model and its effort, so
    that the table has one row for it."""
    return [] if entry.get("lane") is None or _text(entry["lane"]) else [
        "'lane' names a line of work of the forecaster in 'by': a non-empty text, or leave it out"]


def _forecast(entry: Any) -> list[str]:
    if not isinstance(entry, dict):
        return ["not a mapping with 'by', 'at' and 'basis'"]
    problems = _unknown(entry, FORECAST_KEYS) + _need_text(entry, ("by", "at", "basis")) + _lane(entry)
    p = entry.get("p_accepted")
    if p is not None and not (_number(p) and 0 <= p <= 1):
        problems.append("'p_accepted' must be a number between 0 and 1")
    if not isinstance(entry.get("unconditional", False), bool):
        problems.append("'unconditional' must be true or false")
    for kind in ("value", "cost"):
        intervals = entry.get(kind)
        if intervals is None:
            continue
        if not isinstance(intervals, dict):
            problems.append(f"'{kind}' must be a mapping of dimension to [low, high]")
            continue
        problems += [f"{kind}.{name} must be [low, high], two numbers with low <= high"
                     for name, pair in intervals.items()
                     if not (isinstance(pair, list) and len(pair) == 2 and all(map(_number, pair)) and pair[0] <= pair[1])]
    return problems


def _outcome(outcome: Any) -> list[str]:
    if outcome is None:
        return ["omit 'outcome' while open, or use 'status: unverified' when a kept change could not be checked"]
    if not isinstance(outcome, dict):
        return ["not a mapping with 'status'"]
    problems = _unknown(outcome, OUTCOME_KEYS)
    if outcome.get("status") not in STATUSES:
        problems.append(f"'status' must be one of {', '.join(STATUSES)}, not {outcome.get('status')!r}")
    if outcome.get("by") is not None and not _text(outcome["by"]):
        problems.append("'by' names who measured: a non-empty text, or leave it out")
    for kind in ("value", "kept", "cost"):
        measured = outcome.get(kind)
        if measured is None:
            continue
        if not isinstance(measured, dict):
            problems.append(f"'{kind}' must be a mapping of dimension to a measured number or null")
            continue
        problems += [f"{kind}.{name} must be a measured number or null" for name, value in measured.items()
                     if value is not None and not _number(value)]
    if outcome.get("kept") is not None and outcome.get("status") in STATUSES and outcome["status"] not in KEEPS:
        problems.append(f"'kept' says what stays of an attempt that ended {' or '.join(KEEPS)}; what an attempt that "
                        f"ended '{outcome['status']}' measured is its 'value'")
    return problems


def _level(entry: Any) -> int | None:
    """The value of a numeric score when it is one the convention accepts."""
    value = entry.get("value") if isinstance(entry, dict) and entry.get("what") not in TEXT_SCORES else None
    return value if isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= 10 else None


def _score(entry: Any) -> list[str]:
    if not isinstance(entry, dict):
        return ["not a mapping with 'what', 'by' and 'at'"]
    problems = _unknown(entry, SCORE_KEYS) + _need_text(entry, ("what", "by", "at")) + _lane(entry)
    if not isinstance(entry.get("forecast", False), bool):
        problems.append("'forecast' must be true or false")
    what = entry.get("what")
    agent_without_flag = not str(entry.get("by") or "").startswith(PERSON) and entry.get("forecast") is not True
    if what in TEXT_SCORES:
        if entry.get("value") is not None:
            problems.append(f"'{what}' takes no 'value': it is a sentence under 'text'")
        problems += _need_text(entry, ("text",))
        if agent_without_flag:
            problems.append(f"'{what}' is signed by a person ('by: {PERSON}<name>'); an agent proposes one with "
                            "'forecast: true'")
        return problems
    if entry.get("text") is not None:
        problems.append(f"'text' belongs to {' and '.join(TEXT_SCORES)}; a numeric score explains itself in 'basis'")
    problems += _need_text(entry, ("reader", "task", "basis"))
    level = _level(entry)
    if level is None:
        problems.append("'value' must be an integer from 1 to 10")
    elif level >= FORECAST_FROM and agent_without_flag:
        problems.append(f"a {FORECAST_FROM} or more given by an agent is a forecast of what a person of the field "
                        f"would say: add 'forecast: true' (a person signs 'by: {PERSON}<name>')")
    return problems


def _unchecked(data: dict[str, Any]) -> str | None:
    """Missing or malformed evidence in an outcome whose author records it as accepted."""
    outcome = data.get("outcome")
    if not isinstance(outcome, dict) or outcome.get("status") != "accepted":
        return None
    contract = outcome.get("contract") if isinstance(outcome.get("contract"), dict) else {}
    statements, axioms = contract.get("statements"), contract.get("axioms")
    identified = _text(statements) or (
        isinstance(statements, list) and bool(statements) and all(_text(s) for s in statements))
    if identified and isinstance(axioms, list) and all(_text(axiom) for axiom in axioms):
        return None
    return ("outcome: 'accepted' requires a non-empty statement reference (or list of references) and an explicit "
            f"list of axiom names, which may be empty; a kept change awaiting a check is 'status: {UNVERIFIED}'")


def _forecasted(data: dict[str, Any]) -> bool:
    return isinstance(data.get("forecasts"), list) and bool(data["forecasts"])


def notes(data: dict[str, Any]) -> list[str]:
    """What is accepted and still worth a line: a score between two anchored levels, and an `accepted` without a
    checked contract on an attempt that carries no forecast."""
    scores = data.get("scores") if isinstance(data.get("scores"), list) else []
    unchecked = _unchecked(data)
    return [f"score {number}: the value {_level(entry)} is between two anchors (the anchored levels are "
            f"{', '.join(map(str, ANCHORS))})"
            for number, entry in enumerate(scores, 1) if _level(entry) is not None and _level(entry) not in ANCHORS
            ] + ([unchecked] if unchecked and not _forecasted(data) else [])


def validate(data: dict[str, Any]) -> list[str]:
    """Every way the front matter departs from the convention, one sentence each."""
    problems = []
    if data.get("schema") != SCHEMA:
        problems.append(f"'schema' must be {SCHEMA}, not {data.get('schema')!r}")
    problems += _unknown(data, TOP_KEYS) + _need_text(data, REQUIRED_TEXT)
    forecasts = data.get("forecasts")
    if not isinstance(forecasts, list):
        problems.append("'forecasts' is required: a list, [] when no forecast was written before the work")
        forecasts = []
    for number, entry in enumerate(forecasts, 1):
        problems += [f"forecast {number}: {p}" for p in _forecast(entry)]
    if "outcome" in data:
        problems += [f"outcome: {p}" for p in _outcome(data["outcome"])]
    if _forecasted(data) and _unchecked(data):
        problems.append(_unchecked(data))
    scores = data.get("scores")
    if scores is not None and not isinstance(scores, list):
        problems.append("'scores' must be a list")
        scores = []
    for number, entry in enumerate(scores or [], 1):
        problems += [f"score {number}: {p}" for p in _score(entry)]
    return problems


# Order in Git -------------------------------------------------------------------------------------------------

def toplevel(directory: Path) -> Path | None:
    """The work tree that holds the folder, or None outside Git (or on a machine without git)."""
    try:
        proc = gitref.git(directory, "rev-parse", "--show-toplevel", check=False)
    except gitref.GitError:
        return None
    return Path(proc.stdout.decode().strip()) if proc.returncode == 0 and proc.stdout.strip() else None


def _not_run(outcome: Any) -> bool:
    return isinstance(outcome, dict) and outcome.get("status") == NOT_RUN


def _has_outcome(front: str | None) -> bool:
    """Whether a version of the file holds an outcome that a forecast must precede. `not-run` is none: an option
    that was priced and not chosen may be recorded together with its forecast."""
    if front is None:
        return False
    try:
        data = parse_yaml(front, typed=True)
    except CatalogueError:
        return re.search(r"(?m)^outcome:", front) is not None       # an unreadable old version: its key decides
    return isinstance(data, dict) and data.get("outcome") is not None and not _not_run(data["outcome"])


def _frozen(top: Path, path: Path) -> list[str]:
    """The forecast entries of the last committed version before the first commit that contains an outcome (the
    version of HEAD when the outcome is not committed yet); nothing when no such version exists."""
    relative = path.resolve().relative_to(top.resolve()).as_posix()
    log = gitref.git(top, "-c", "core.quotePath=false", "log", "--follow", "--topo-order", "--format=%x00%H",
                     "--name-only", "--", relative, check=False)     # it fails in a repository without a commit
    history = []                                                     # (commit, the file's path in it), newest first
    for chunk in log.stdout.decode(errors="replace").split("\0")[1:]:
        lines = [line for line in chunk.split("\n") if line]
        if len(lines) >= 2:
            history.append((lines[0], lines[-1]))
    before: list[str] = []
    for commit, name in reversed(history):
        shown = gitref.git(top, "show", f"{commit}:{name}", check=False)
        if shown.returncode != 0:                                    # the commit that deleted the file
            continue
        front = front_matter(shown.stdout.decode(errors="replace"))
        if _has_outcome(front):
            break
        before = forecast_entries(front) if front else []
    return before


_BY = re.compile(r"(?m)^\s*(?:- +)?by:\s*(.*?)\s*(?:\s#.*)?$")


def _by(entry: str) -> str:
    """Who signed a forecast entry, from its text: an entry of an older version is no longer in the file."""
    try:
        parsed = parse_yaml(entry, typed=True)
    except CatalogueError:
        parsed = None
    by = parsed[0].get("by") if isinstance(parsed, list) and parsed and isinstance(parsed[0], dict) else None
    if _text(by):
        return by
    match = _BY.search(entry)                     # an entry that cannot be read: the line that names its author
    return match.group(1).strip("\"'") if match else "?"


def check_trial_link(attempt: Attempt, top: Path | None) -> None:
    if not attempt.valid:
        return
    path = trial_output(attempt.path.resolve())
    if not path.exists() and not path.is_symlink():
        return
    try:
        if path.is_symlink() or not path.is_file():
            raise ValueError("expected a regular trial record")
        sidecar = json.loads(path.read_bytes())
        if not isinstance(sidecar, dict) or sidecar.get("schema") != "vl.attempt/1":
            raise ValueError("expected a vl.attempt/1 trial record")
        link = sidecar.get("attempt_file")
        if (not isinstance(link, dict) or link.get("id") != attempt.data["id"]
                or not isinstance(link.get("forecast_sha256"), str)):
            raise ValueError("expected a trial linked to this attempt with a forecast fingerprint")
        fingerprint = forecast_fingerprint(attempt.entries)
        if fingerprint != link["forecast_sha256"]:
            raise ValueError("forecast differs from the snapshot recorded at trial start")
        recorded = link.get("forecast_commit")
        if recorded is not None and (not isinstance(recorded, str) or not gitref.is_full_sha(recorded)
                                     or not isinstance(link.get("path"), str)):
            raise ValueError("expected a full forecast commit and attempt path")
        if top is not None and recorded and gitref.ref_exists(top, recorded):
            pinned = gitref.show(top, recorded, link["path"])
            front = front_matter(pinned.decode()) if pinned is not None else None
            if front is None or fingerprint != forecast_fingerprint(forecast_entries(front)) or _has_outcome(front):
                raise ValueError("forecast differs from its recorded open Git snapshot")
    except (OSError, ValueError, KeyError, RecursionError) as exc:
        attempt.problems.append(f"{path.name}: {exc}")


def check_order(attempts: list[Attempt], top: Path) -> bool:
    """For every readable file with an outcome other than `not-run`: a forecast entry that was not committed
    before the outcome is late; an entry committed before the outcome that is no longer in the file as it was has
    been edited. Both are problems, and both leave the table. The working copy counts as the newest version, so
    the check can run before the outcome is committed. An open attempt and a `not-run` one resolve nothing, so
    nothing of theirs is checked; a version that says `not-run` counts as one without an outcome."""
    shallow = gitref.git(top, "rev-parse", "--is-shallow-repository", check=False).stdout.strip() == b"true"
    unassessed = False
    for attempt in attempts:
        if attempt.outcome is None or _not_run(attempt.outcome):
            continue
        frozen = Counter(_frozen(top, attempt.path))        # also when all forecasts were removed
        changed = {_by(entry) for entry in frozen - Counter(attempt.entries)}
        for by in sorted(changed):
            attempt.problems.append(f"forecast edited after the outcome: the entry by {by} committed before the "
                                    "outcome is no longer in the file as it was (a correction is a new entry)")
        for index, entry in enumerate(attempt.entries):
            if frozen[entry] > 0:
                frozen[entry] -= 1
                continue
            by = _by(entry)
            if shallow and by not in changed:
                attempt.excluded[index] = "history-unavailable"
                unassessed = True
                continue
            attempt.excluded[index] = EDITED if by in changed else LATE
            if by not in changed:
                attempt.problems.append(f"late forecast by {by}: forecast not committed before the outcome "
                                        "(a past attempt without a forecast keeps 'forecasts: []')")
    return unassessed


def check_duplicates(attempts: list[Attempt]) -> None:
    """One outcome resolves each distinct forecast once, even when Git chronology is disabled."""
    for attempt in attempts:
        if attempt.data is None or not isinstance(attempt.data.get("forecasts"), list):
            continue
        seen = set()
        for index, forecast in enumerate(attempt.data["forecasts"]):
            if index in attempt.excluded or not isinstance(forecast, dict):
                continue
            key = json.dumps(forecast, sort_keys=True)
            if key in seen:
                attempt.excluded[index] = DUPLICATE
                attempt.problems.append(f"duplicate forecast by {forecast.get('by')}: already listed in this attempt")
            seen.add(key)


# The table ----------------------------------------------------------------------------------------------------

def score_pairs(attempts: list[Attempt]) -> list[dict[str, Any]]:
    """For each agent and each `what`: its numeric scores that a person's score further down the same file answers
    (same `what`, `reader` and `task`), and the mean of agent minus person."""
    differences: dict[tuple[str, str], list[int]] = {}
    for attempt in attempts:
        scores = [entry for entry in attempt.data.get("scores") or [] if _level(entry) is not None]
        for index, agent in enumerate(scores):
            if agent["by"].startswith(PERSON):
                continue
            for person in scores[index + 1:]:
                if person["by"].startswith(PERSON) and all(person[k] == agent[k] for k in ("what", "reader", "task")):
                    differences.setdefault((agent["by"], agent["what"]), []).append(agent["value"] - person["value"])
    return [{"by": by, "what": what, "pairs": len(cases), "mean_difference": statistics.fmean(cases)}
            for (by, what), cases in sorted(differences.items())]


def _interval_row(cases: list[tuple[float, float, float]]) -> dict[str, Any]:
    return {"resolved": len(cases), "inside": sum(low <= got <= high for low, high, got in cases),
            "median_error": statistics.median(got - (low + high) / 2 for low, high, got in cases)}


def _chance_row(cases: list[tuple[float, bool]]) -> dict[str, Any]:
    return {"resolved": len(cases), "mean_forecast": statistics.fmean(p for p, _ in cases),
            "share_accepted": sum(a for _, a in cases) / len(cases),
            "brier": statistics.fmean((p - a) ** 2 for p, a in cases)}


def band(p: float) -> str:
    """The fifth of [0, 1] that holds a forecast of acceptance."""
    return BANDS[sum(p >= edge for edge in (0.2, 0.4, 0.6, 0.8))]


def _rows(groups: dict[tuple, list[tuple]], names: tuple[str, ...], stats) -> tuple[list[dict], list[dict]]:
    """One row per group of cases and, when a case of the group names a lane, one row per lane of the group. A case
    starts with the lane of its forecast, None when it has none: those come last."""
    rows, lane_rows = [], []
    for key, cases in groups.items():
        named = dict(zip(names, key))
        rows.append({**named, **stats([case[1:] for case in cases])})
        lanes: dict[str | None, list[tuple]] = {}
        for lane, *case in cases:
            lanes.setdefault(lane, []).append(tuple(case))
        if set(lanes) != {None}:
            lane_rows += [{**named, "lane": lane, **stats(lanes[lane])}
                          for lane in sorted(lanes, key=lambda lane: (lane is None, lane or ""))]
    return rows, lane_rows


def table(attempts: list[Attempt]) -> dict[str, Any]:
    """Forecast against outcome, per forecaster: the `by` of a forecast, whatever its `lane`. A value interval is
    conditional on acceptance: only an accepted attempt resolves it, unless its forecast says `unconditional: true`,
    which a rejected or abandoned attempt resolves with 0. `p_accepted` is resolved by an accepted, a rejected or an
    abandoned attempt. A cost dimension is resolved by any attempt that was run and measured it, an `unverified` one
    included: that is all an `unverified` attempt resolves, since its contract was not checked. `not-run` resolves
    nothing. The same numbers are given per lane for a forecaster whose forecasts name one, and the resolved
    `p_accepted` of a forecaster also by band of the forecast and by purpose of the attempt: one mean hides a
    forecaster who is right on the likely follow-ons and wrong on the unlikely change of route. Files with problems
    and forecasts excluded by `check_order` are left out and counted.

    `measured` lists every number under `outcome.value` and `outcome.kept` next to the `base` of its file, the only
    thing it is a change against. `kept` (what stays of an attempt that ended abandoned or rejected) is listed there
    and resolves nothing: a forecast resolves on the status of the claim it priced."""
    intervals: dict[tuple[str, str, str], list[tuple]] = {}        # (by, kind, dimension) -> (lane, low, high, outcome)
    chances: dict[tuple[str], list[tuple]] = {}                    # (by,) -> (lane, p_accepted, accepted)
    bands: dict[tuple[str, str], list[tuple]] = {}                 # (by, band) -> (None, p_accepted, accepted): these
    purposes: dict[tuple[str, str], list[tuple]] = {}              # (by, purpose) -> the same      rows have no lanes
    skipped = [attempt.path.name for attempt in attempts if attempt.data is None or
               any(not p.startswith(("late forecast", "forecast edited", "duplicate forecast")) for p in attempt.problems)]
    excluded = []
    used = [attempt for attempt in attempts if attempt.path.name not in skipped]
    for attempt in used:
        outcome = attempt.outcome
        for index, forecast in enumerate(attempt.data["forecasts"]):
            if index in attempt.excluded:
                excluded.append({"file": attempt.path.name, "by": forecast["by"], "reason": attempt.excluded[index]})
                continue
            if outcome is None or outcome["status"] == NOT_RUN:
                continue
            by, lane = forecast["by"], forecast.get("lane")
            accepted, checked = outcome["status"] == "accepted", outcome["status"] != UNVERIFIED
            if forecast.get("p_accepted") is not None and checked:
                chances.setdefault((by,), []).append((lane, forecast["p_accepted"], accepted))
                for groups, key in ((bands, band(forecast["p_accepted"])), (purposes, attempt.data["purpose"])):
                    groups.setdefault((by, key), []).append((None, forecast["p_accepted"], accepted))
            for kind in ("value", "cost"):
                if kind == "value" and (not checked or not accepted and not forecast.get("unconditional")):
                    continue
                measured = outcome.get(kind) or {}
                for name, (low, high) in (forecast.get(kind) or {}).items():
                    got = 0 if kind == "value" and not accepted else measured.get(name)
                    if got is not None:
                        intervals.setdefault((by, kind, name), []).append((lane, low, high, got))
    rows, lane_rows = _rows(dict(sorted(intervals.items(), key=lambda item: (item[0][0], item[0][1] != "value", item[0][2]))),
                            ("by", "kind", "dimension"), _interval_row)
    p_rows, p_lane_rows = _rows(dict(sorted(chances.items())), ("by",), _chance_row)
    measured = [{"file": attempt.path.name, "status": attempt.outcome["status"], "base": attempt.data["base"], "kind": kind,
                 "dimension": name, "measured": number}
                for attempt in used if attempt.outcome is not None for kind in ("value", "kept")
                for name, number in (attempt.outcome.get(kind) or {}).items() if number is not None]
    return {"schema": TABLE_SCHEMA, "files": len(attempts),
            "with_outcome": sum(1 for a in used if a.outcome is not None),
            "open": sum(1 for a in used if a.outcome is None),
            "intervals": rows, "p_accepted": p_rows, "intervals_by_lane": lane_rows, "p_accepted_by_lane": p_lane_rows,
            "p_accepted_by_band": _rows(dict(sorted(bands.items())), ("by", "band"), _chance_row)[0],
            "p_accepted_by_purpose": _rows(dict(sorted(purposes.items())), ("by", "purpose"), _chance_row)[0],
            "measured": measured, "score_pairs": score_pairs(used), "skipped_files": skipped,
            "excluded_forecasts": excluded}
