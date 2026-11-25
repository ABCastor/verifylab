# ADR 0006 — An exit status that systemd set is not a verdict

Status: accepted, 2026-10-10. Extends ADR 0001.

Context: protected Lean commands run as a transient systemd service (`systemd-run --wait --pipe`). systemd-run
exits with the main process's status only when the service's result is `success` or `exit-code`; for any other
result (`oom-kill` when the memory cap's OOM killer fired, `timeout` for RuntimeMaxSec or for TimeoutStopSec while
it killed processes left after the main one exited, `signal`) it exits 1 (255 for `signal`), whatever the command
returned. Protected receipts of 2026-10-09 show the effect: Comparator printed both kernels' acceptance and
`lake build --no-build` printed "All targets up-to-date", and `vl` saw exit 1 (the stop timeout of zero then in
use; reproduced). An OOM kill looked like a normal exit 1, so a rejection line Comparator printed before systemd
stopped it could count.
Comparator also reports any non-zero exit of an external kernel, a kill by signal included, as a rejection.

Decision: guarded services run without `--quiet`, and `vl` reads the report systemd-run prints after the unit is
gone (`Finished with result: R`, `Main processes terminated with: …`). It takes the report off the command's
stderr, and when R is neither `success` nor `exit-code` it records why in a `vl:` line and in the run's `service`.
A Comparator run with such a result is `error`, whatever it printed; a probe run with one is a named probe problem.
An external kernel that exited with 128 or more (killed by a signal) is `error`, not a rejection.

Consequences: no pass is created; some runs that were `fail` become `error` (fail-safe). Only the report at the
very end of stderr counts: nothing in the service can write after it, and a forged one elsewhere is ignored. A
command that a deadline killed has no report; the deadline's own `error` applies. Accepting a run whose main
process exited 0 with both kernels' acceptance despite a `timeout` result would need its own decision.
