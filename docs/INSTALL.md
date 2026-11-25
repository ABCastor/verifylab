# Installation and verification

VerifyLab runs locally on Linux with Python 3.11 or later and Git. Protected Python checks need a system
Python interpreter and bubblewrap. Protected Lean checks additionally need an installed Lean toolchain,
a working systemd user manager with
`RestrictAddressFamilies` support and four verifier tools:

- Comparator checks the fixed target and verifies the candidate's proof and allowed axioms.
- lean4export exports the Lean declarations the kernels replay.
- landrun applies Linux Landlock file restrictions to Comparator's child processes.
- nanoda is the independent Lean kernel used for the second check.

VerifyLab itself downloads nothing.

## Install the command

From the VerifyLab checkout, with `uv`, Git and Python 3.11 or later:

```sh
uv tool install .
export PATH="$(uv tool dir --bin):$PATH"
vl --help
```

Install `uv` using your system's package manager or its official distribution. The `export` applies to the
current shell. Use `uv tool update-shell` and start a new shell to keep the tool directory on PATH.

Run the installed `vl` from the mathematics repository. From that repository, a VerifyLab checkout also works
without a tool install:

```sh
uv run --project /absolute/path/to/verifylab vl attempts check
```

The versioned `verifylab` entry skill ships with the wheel. Run `vl init --skills codex` for
`~/.codex/skills`, `vl init --skills claude` for `~/.claude/skills`, or `vl init --skills PATH` with Pi's
skill directory. With no target it uses `~/.agents/skills`. Open a new harness session after installation. The installed skill is that folder alone; the
documents it names as `docs/NAME.md` ship with the wheel too, and `vl docs NAME` prints one. No particular model
or harness is required.

## Linux isolation

Install bubblewrap and test that this account can create namespaces:

```sh
bwrap --unshare-all --ro-bind / / --proc /proc --dev /dev -- true
```

Lane execution also requires bubblewrap's `--overlay` and `--overlay-src` options; the CI recipe builds
version 0.13.0. The verifier's Landlock enforcement is tested during every protected Lean check. A missing
or ineffective jail stops a check; it never downgrades to unsandboxed execution.

Protected Lean checks follow the [pinned Comparator isolation requirement](https://github.com/leanprover/comparator/blob/19e111e2141cf333c7daff0f64c5f24acc91dd2e/README.md):
an outer systemd user service denies AF_UNIX sockets before bubblewrap starts. A trusted helper tests actual
`EAFNOSUPPORT` before each jailed command; missing launchers, manager or enforcement stop the protected check
with `error` or `unsupported`, never a passing verdict. A resource scope alone cannot enforce this property. A kernel version number does not
replace the enforcement test.

The manager also supplies per-run memory/task limits and the aggregate `vl.slice` memory limit. Python,
exploratory checks and lane execution retain their optional resource-cap fallback: without a manager they
run uncapped and record the reason. `scripts/gate` optionally caps its own test process, but protected Lean
tests still require the enforced service. Machine-specific settings belong in the machine configuration,
whose commented example is `templates/machine.toml`; no machine paths belong in committed project configuration.

Ubuntu may restrict unprivileged user namespaces through AppArmor. The workflow disables that restriction
only on its disposable runner. On a personal or shared machine, have the administrator provide an appropriate
policy before running the jail. Do not change a shared machine's policy merely to match CI.

## Build the tested Lean verifier stack

Install elan, Go 1.24 or later, a recent Rust toolchain with Cargo, and a C compiler. The release's fixture
uses `leanprover/lean4:v4.34.0-rc2`; `leanprover/lean4:v4.34.1` is also reviewed. Install the toolchain explicitly:

```sh
elan toolchain install leanprover/lean4:v4.34.0-rc2
uv sync --frozen
scripts/install-tools.sh "$HOME/.cache/verifylab/tool-build"
# another reviewed toolchain, built in its own directory (each store is per toolchain):
elan toolchain install leanprover/lean4:v4.34.1
scripts/install-tools.sh --toolchain leanprover/lean4:v4.34.1 "$HOME/.cache/verifylab/tool-build-4.34.1"
```

Without `--toolchain` the script uses the fixture's. The script clones and builds these immutable source
revisions, then invokes `vl init --tools` to copy and hash-pin the resulting binaries outside the repository:

| Tool | Source revision (v4.34.0-rc2 and v4.34.1) |
|---|---|
| Comparator | `19e111e2141cf333c7daff0f64c5f24acc91dd2e` |
| lean4export | `cacf989bd75f608700820f6afc595f32e7a99a4d` |
| landrun | `811cfff51ceaf3d9843708aa6d22e9b84ccac8b4` |
| nanoda | `4c544ed4099c8227f07d5de77ad1e69fb0740a27` |

The Comparator and lean4export revisions above declare `v4.34.0-rc2` but build with the v4.34.1 `lake` and pass
the planted Lean suite on a v4.34.1 copy of the fixture (`VL_TEST_FIXTURE`, below). landrun and nanoda
do not depend on Lean. The script reads each toolchain's pins from its `case` table; a toolchain without a case is
refused until its pins are reviewed.

Installing tools downloads sources and Go/Cargo build dependencies. It does not publish research or send
project artifacts. `SOURCE-REVISIONS` in the build directory records source revisions and compiler versions.
The binary store's `REVISIONS` records actual binary SHA-256 values. Different compiler versions can produce
different binary hashes; these source pins are a repeatable build recipe, not a claim of bit-identical builds.
The CI build uses Go 1.24.0 and Rust 1.98.1.

The build directory must be absolute and outside the repository. Existing checkouts with another revision
or modified/untracked source files are refused; ignored build outputs are permitted. Existing pinned binaries
are never overwritten by `vl init --tools`.
To reproduce a build without network access, prepare these four exact source checkouts and their build
dependency caches first, then run:

```sh
scripts/install-tools.sh --offline "$HOME/.cache/verifylab/tool-build"
```

Protected checks ignore caller-supplied `COMPARATOR_*`, `PATH` and `ELAN_HOME` when choosing Lean verifier
tools. Custom tool locations in the machine's `[tools]` table take precedence over the store. Receipt tool
hashes describe the tools used for that check; replacing installed tools does not invalidate historical
evidence automatically. Adding a Lean toolchain requires testing compatible Comparator/exporter revisions and the
known-answer cases together (the planted suite on a copy of the fixture); the script refuses an unreviewed toolchain.

## Verify a checkout and its installed package

```sh
uv sync --frozen
scripts/fast
scripts/gate
uv build --wheel
python scripts/smoke-wheel.py dist/*.whl
```

The full gate refuses failed or deselected tests, and by default skipped Lean/jail tests. The documented
`VL_GATE_ALLOW_SKIP=1` escape does not establish protected test coverage and must not be used for release verification. It requires a built Mathlib dependency
cache for its read-only-cache integration test, in addition to the core Lean fixture and the vendored
Comparator cases. The Tiny foreign-project patch trial requires the Lean 4.34.1 toolchain preinstalled. Point `VL_TEST_MATHLIB` at a Lake project whose `.lake/packages/mathlib` is built and whose
manifest pins Mathlib `85e3a25e006c35636f0e53b0e9296caca2685bc0`. The workflow contains the exact project/cache
setup; `Mathlib.Data.Finset.Card` and its imports suffice for this integration test. Preparing this cache
downloads dependencies and trusted upstream build artifacts; checks themselves never download them. The cache
must be built on the fixture's toolchain; the test skips a Mathlib whose `lean-toolchain` differs. A cache filled
by `lake exe cache get` alone lacks `proofwidgets/widget/package-lock.json.hash`; Lake then replays `npm install` in
the read-only jail and the check ends in `error`. A `lake build` of the project with writable packages writes the file
(it runs `npm install`); complete a fresh cache that way before its first check.

To run the Lean tests on another reviewed toolchain, copy `fixtures/lean-planted` (with `cases/`), set the copy's
`lean-toolchain`, install that toolchain and its tools (above), and point `VL_TEST_FIXTURE` at the copy:
`VL_TEST_FIXTURE=/tmp/lean-planted-4.34.1 uv run pytest tests/test_lean_adapter.py`. The tools come from that
toolchain's store. This is a check for the toolchain, not part of `scripts/gate`.

The installed-wheel smoke test installs the wheel in an isolated environment and exercises the protected
Python check, receipt admission and derived status through the installed command. Public CI runs fast tests
on Python 3.11 and 3.14, then the complete protected gate and installed-wheel smoke on Ubuntu 24.04.

Source and workflow files can be inspected locally. A release should be published only after the actual public
workflow passes with no gate exceptions; a local green run does not establish that a fresh hosted runner is green.
