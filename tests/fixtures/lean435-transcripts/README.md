# Lean 4.35 parser fixtures

These outputs exercise two process results. The successful control exited 0 and reports all six kernels.
The empty-axiom case exited 1 and ends with `error: nanoda exited with 101`. The classifier must
treat that as a tool error independently of other kernels accepting the proof.
