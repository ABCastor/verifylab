"""Job lists (`vl.jobs/1`): the protected checks a set of machines should run, and how to split them.

A job is an item of the trusted commit, named by the item, the trusted commit and the digest of its question, so the
same records give the same job on every machine. The list is a plain file; nothing here talks to a network. A machine
runs its share with `vl check --ids FILE` at the same trusted commit, and the receipts it writes come back as files
for the integrator to admit. A job is done when an admitted receipt answers that question at that commit.
"""

from __future__ import annotations

import json
import math
import re
from statistics import median
from typing import Any, Iterable

from .records import canonical_json, question_digest, sha256_hex, wall_seconds
from .render import trust_data
from .repo import EVIDENCE, Repo

SCHEMA = "vl.jobs/1"
RECENT = 5                      # receipts of an item whose largest figure is its expected cost (as `vl check` reserves)
MACHINE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
BY = ("reservation", "seconds")
JOB_KEYS = ("job", "item", "adapter", "trusted_commit", "question_digest", "reservation_bytes", "expected_seconds")


def job_id(item: str, trusted_commit: str, digest: str) -> str:
    return sha256_hex(canonical_json({"item": item, "trusted_commit": trusted_commit,
                                      "question_digest": digest}).encode())[:16]


def manifest(project: str, trust: dict[str, Any], jobs: list[dict[str, Any]]) -> dict[str, Any]:
    """The job list; its `digest` covers the jobs' ids in order, so a changed, added or reordered job is a new list."""
    return {"schema": SCHEMA, "project": project, "trust": trust,
            "digest": "sha256:" + sha256_hex(canonical_json([j["job"] for j in jobs]).encode()), "jobs": jobs}


def dumps(data: dict[str, Any]) -> str:
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


def load(text: str) -> dict[str, Any]:
    """A job list read back and checked: its schema, the shape of each job, every job id and the list's digest must
    be what the content says. ValueError otherwise."""
    try:
        data = json.loads(text)
    except ValueError:
        raise ValueError("not valid JSON") from None
    if not isinstance(data, dict) or data.get("schema") != SCHEMA:
        raise ValueError(f"not a job list (schema {SCHEMA})")
    jobs = data.get("jobs")
    if not isinstance(jobs, list) or not isinstance(data.get("trust"), dict) or not isinstance(data.get("project"), str):
        raise ValueError("a job list needs project, trust and jobs")
    for job in jobs:
        if not isinstance(job, dict) or set(job) != set(JOB_KEYS):
            raise ValueError(f"a job needs exactly the keys {', '.join(JOB_KEYS)}")
        if not all(isinstance(job[k], str) for k in ("job", "item", "adapter", "trusted_commit", "question_digest")):
            raise ValueError(f"job {job.get('job')}: text fields are not text")
        if any(job[k] is not None and (type(job[k]) not in (int, float) or not math.isfinite(job[k]) or job[k] < 0)
               for k in ("reservation_bytes", "expected_seconds")):
            raise ValueError(f"job {job['job']}: a cost is not a finite, non-negative number")
        if job["job"] != job_id(job["item"], job["trusted_commit"], job["question_digest"]):
            raise ValueError(f"job {job['job']}: its id is not that of its item, commit and question")
    if len({j["job"] for j in jobs}) != len(jobs):
        raise ValueError("a job is listed twice")
    if data.get("digest") != manifest(data["project"], data["trust"], jobs)["digest"]:
        raise ValueError("its digest is not that of its jobs: the file was edited")
    return data


# Building ---------------------------------------------------------------------------------------------------------

def evidence(repo: Repo) -> dict[str, list[tuple[str, str, int | None, float | None]]]:
    """Per item with receipts: (finished_at, checker, memory peak or None, wall seconds or None) of each valid receipt
    `repo` counts. Read once for the whole list, not once per job."""
    found = {}
    for item_id in repo.record_folders(EVIDENCE):
        rows = []
        for r in repo.receipts(item_id):
            if r.problems:
                continue
            peak = r.data.get("extra", {}).get("memory_peak_bytes")
            rows.append((r.data["finished_at"], r.data["adapter"], peak if type(peak) is int and peak > 0 else None,
                         wall_seconds(r.data)))
        found[item_id] = rows
    return found


def reservation(own: Iterable[tuple[str, int]], others: Iterable[int]) -> int | None:
    """The memory a check reserves (ADR 0003), one rule for `vl check` (check.last_peak) and the job list: the largest
    peak among the RECENT newest (finished_at, peak) of the item's own receipts; for an item with none, the largest of
    `others`, its checker's peaks in other receipts, read only then; else None."""
    recent = sorted(own, reverse=True)[:RECENT]
    return max(peak for _, peak in recent) if recent else max(others, default=None)


def largest_peaks(found: dict) -> dict[str, int]:
    """The largest recorded peak of each checker over every receipt of `found`: the reservation of an item with no
    peak of its own (whose own receipts then add none), found once for the whole list."""
    largest: dict[str, int] = {}
    for rows in found.values():
        for _, adapter, peak, _ in rows:
            if peak is not None:
                largest[adapter] = max(largest.get(adapter, 0), peak)
    return largest


def expected(found: dict, item_id: str, adapter: str,
             largest: dict[str, int] | None = None) -> tuple[int | None, float | None]:
    """(reservation in bytes, seconds) a check of the item should take, from the receipts alone. Reservation is what
    `vl check` reserves (`reservation`); `largest` is `largest_peaks(found)`, when the caller has it already. Seconds:
    the largest among the item's RECENT newest receipts that record a time, else None."""
    largest = largest_peaks(found) if largest is None else largest
    rows = found.get(item_id, ())
    peak = reservation([(r[0], r[2]) for r in rows if r[2] is not None], [largest[adapter]] if adapter in largest else [])
    timed = sorted((r for r in rows if r[3] is not None), reverse=True)[:RECENT]
    return peak, max((r[3] for r in timed), default=None)


def build(repo: Repo, ids: list[str], adapters: dict[str, str]) -> dict[str, Any]:
    """The job list of `ids` (items of the trusted commit; `adapters` maps each to its checker's name), in that order."""
    items = repo.trusted_items()[0]
    found = evidence(repo)
    largest = largest_peaks(found)
    jobs = []
    for item_id in ids:
        digest = question_digest(items[item_id])
        peak, seconds = expected(found, item_id, adapters[item_id], largest)
        jobs.append({"job": job_id(item_id, repo.trusted_commit, digest), "item": item_id,
                     "adapter": adapters[item_id], "trusted_commit": repo.trusted_commit, "question_digest": digest,
                     "reservation_bytes": peak, "expected_seconds": None if seconds is None else round(seconds, 1)})
    return manifest(repo.config.name, trust_data(repo), jobs)


def missing(repo: Repo, data: dict[str, Any]) -> list[dict[str, Any]]:
    """The jobs of the list that no admitted receipt answers: one of the item, protected, valid, for the job's
    question, checked at the job's trusted commit. `repo` must count admitted receipts only (TrustedRepo)."""
    found = {}
    for job in data["jobs"]:
        if job["item"] not in found:
            found[job["item"]] = {(r.data.get("question_digest"), r.data.get("inputs", {}).get("trusted_commit"))
                                  for r in repo.receipts(job["item"])
                                  if not r.problems and r.admitted and r.data.get("assurance") == "protected"}
    return [j for j in data["jobs"] if (j["question_digest"], j["trusted_commit"]) not in found[j["item"]]]


# Sharding ---------------------------------------------------------------------------------------------------------

def cost_of(jobs: list[dict[str, Any]], by: str) -> dict[str, float]:
    """The expected cost of each job (id -> cost) by `by`; a job with no figure costs the median of those that have
    one (1 when none has), so unknown jobs spread evenly instead of landing together."""
    key = {"reservation": "reservation_bytes", "seconds": "expected_seconds"}[by]
    known = [j[key] for j in jobs if j[key] is not None]
    fallback = median(known) if known else 1
    return {j["job"]: float(fallback if j[key] is None else j[key]) for j in jobs}


def shard(jobs: list[dict[str, Any]], weights: dict[str, float], by: str) -> dict[str, list[dict[str, Any]]]:
    """Greedy balancing (longest job first, onto the machine that would finish it earliest relative to its weight,
    on a tie the one with the fewest jobs for its weight, so jobs that cost nothing still spread): every job goes to
    exactly one machine, a machine of weight 4 takes about twice the cost of one of weight 2, and the same list and
    weights always give the same shards. Each shard lists its biggest jobs first."""
    cost = cost_of(jobs, by)
    load = dict.fromkeys(weights, 0.0)
    shards: dict[str, list[dict[str, Any]]] = {name: [] for name in weights}
    for job in sorted(jobs, key=lambda j: (-cost[j["job"]], j["job"])):
        name = min(weights, key=lambda m: ((load[m] + cost[job["job"]]) / weights[m], len(shards[m]) / weights[m]))
        load[name] += cost[job["job"]]
        shards[name].append(job)
    return shards
