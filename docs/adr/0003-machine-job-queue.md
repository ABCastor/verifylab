# ADR 0003 — Heavy runs wait in one machine job queue

Status: accepted, 2026-10-10. Extends the machine memory budget of ADR 0002 §3.

Context: many agents share one machine. Before this, `vl.slice` killed a run that pushed the machine past
`memory_total`, and nothing ever waited: two heavy runs at once were a gamble, and a job outside `vl` (an experiment,
a training) did not take part at all.

## Decision
`vl check` (protected and exploratory) and `vl lane exec` wait before their heavy work until the machine's job queue
admits them; `vl queue run -- CMD` puts any other command in the same queue, in a scope capped at its reservation.
Every capped run of a queued job goes in the job's own slice inside `vl.slice` (`vl-<job>.slice`). A job is admitted
while fewer than `[queue] slots` jobs run, the reservations fit `[check] memory_total`, and MemAvailable, minus what
running jobs reserved but do not use yet (each job's reservation minus the anonymous memory of its own slice, 0 when
that cannot be read, so one job beyond its reservation hides no other's growth to come), minus its own reservation,
stays at least `[queue] keep_free`. A check reserves the largest `extra.memory_peak_bytes` of its item's five newest
admitted or exploratory receipts; an item with none reserves the largest of the repository's receipts from the same
checker, else `default_reservation`. Peaks and usage share one basis, anonymous memory (memory.stat `anon`): page
cache, which MemAvailable counts as mostly free, is in neither. A check's peak adds up its runs that overlap
(Comparator and the statement probes). Places are files held with flock under `$XDG_STATE_HOME/verifylab/queue/`; the
kernel releases a dead holder's lock. `vl queue run` and `vl lane exec` pass the lock to their command, so a place
stays held until the job ends even if the `vl` process that started it is killed. The oldest waiting job's
reservation counts against every other job (head-of-line), so a big check is not starved by a stream of small runs.
A folder that cannot be written stops the job with exit 2, naming the folder and `XDG_STATE_HOME`.

## What does not change
Verdicts, status and admission of records. The queue only orders starts: each run keeps its own MemoryMax and the
slice's cap, which still kill a job that outgrows its reservation. A wait past `--max-wait` exits 2 before the check
runs and writes no receipt; `vl lane exec` takes `--reserve` and `--max-wait` (default: its `--timeout`, or `[queue] max_wait` when shorter), and holds
the lane's exec lock while it waits, so `lane done` and `lane close` refuse a lane with a queued command. A receipt records the wait as the optional `extra.queue_wait_seconds`; nothing reads it
for a status. The queue is resource management, not a trust boundary: a process of the same user can ignore it,
remove its files, or claim an admission through `VL_QUEUE_JOB` (which must name a live place).

## Alternatives
GNU parallel's `sem` counts slots but knows nothing of memory and is not on every machine; systemd's own
MemoryHigh/MemoryMax throttle or kill a run that has started rather than delaying its start. Both remain in use
around the queue.
