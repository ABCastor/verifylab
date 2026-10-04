# Installation and verification

VerifyLab runs locally on Linux with Python 3.11 or later and Git. Protected Python checks need a system
Python interpreter and bubblewrap. Protected Lean checks additionally need an installed Lean toolchain,
Comparator, lean4export, landrun and nanoda. VerifyLab itself downloads nothing.

## Install the command

From a checkout of this repository:

```sh
uv tool install .
vl --help
```

Install `uv` using your system's package manager or its official distribution. Agent skills are optional;
their Markdown files ship with the wheel. Install the seven `skills/vl-*` directories according to your
harness's instructions. No particular model or harness is required.

## Linux isolation

Install bubblewrap and test that this account can create namespaces:

```sh
bwrap --unshare-all --ro-bind / / --proc /proc --dev /dev -- true
```

Lane execution also requires bubblewrap's `--overlay` and `--overlay-src` options; the CI recipe builds
version 0.13.0. The verifier's Landlock enforcement is tested during every protected Lean check. A missing
or ineffective jail stops a check; it never downgrades to unsandboxed execution.

A working systemd user manager supplies per-run memory/task limits and the aggregate `vl.slice` memory
limit. Without it, checks run without those caps and record the reason. `scripts/gate` uses the same
availability rule. Machine-specific settings belong in the machine configuration, whose commented example
is `templates/machine.toml`; no machine paths belong in a project's committed configuration.

Ubuntu may restrict unprivileged user namespaces through AppArmor. The workflow disables that restriction
only on its disposable runner. On a personal or shared machine, have the administrator provide an appropriate
policy before running the jail. Do not change a shared machine's policy merely to match CI.

## Build the tested Lean verifier stack

Install elan, Go 1.24 or later, a recent Rust toolchain with Cargo, and a C compiler. The release's fixture
uses `leanprover/lean4:v4.34.0-rc2`. Install that exact toolchain explicitly:

```sh
elan toolchain install leanprover/lean4:v4.34.0-rc2
uv sync --frozen
scripts/install-tools.sh "$HOME/.cache/verifylab/tool-build"
```

The script clones and builds these immutable source revisions, then invokes `vl init --tools` to copy and
hash-pin the resulting binaries outside the repository:

| Tool | Source revision |
|---|---|
| Comparator | `19e111e2141cf333c7daff0f64c5f24acc91dd2e` |
| lean4export | `cacf989bd75f608700820f6afc595f32e7a99a4d` |
| landrun | `811cfff51ceaf3d9843708aa6d22e9b84ccac8b4` |
| nanoda | `4c544ed4099c8227f07d5de77ad1e69fb0740a27` |

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
evidence automatically. Moving to another Lean toolchain requires testing compatible Comparator/exporter
revisions and the known-answer cases together; this script deliberately refuses an unreviewed toolchain change.

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
cache for its read-only-cache integration test, in addition to the core Lean fixture and the twenty vendored
Comparator cases. Point `VL_TEST_MATHLIB` at a Lake project whose `.lake/packages/mathlib` is built and whose
manifest pins Mathlib `85e3a25e006c35636f0e53b0e9296caca2685bc0`. The workflow contains the exact project/cache
setup; `Mathlib.Data.Finset.Card` and its imports suffice for this integration test. Preparing this cache
downloads dependencies and trusted upstream build artifacts; checks themselves never download them.

The installed-wheel smoke test installs the wheel in an isolated environment and exercises the protected
Python check, receipt admission and derived status through the installed command. Public CI runs fast tests
on Python 3.11 and 3.14, then the complete protected gate and installed-wheel smoke on Ubuntu 24.04.

Source and workflow files can be inspected locally. A release should be published only after the actual public
workflow passes with no gate exceptions; a local green run does not establish that a fresh hosted runner is green.
