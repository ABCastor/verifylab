#!/usr/bin/env bash
# Build the verifier revisions exercised by the fixtures, then pin their binaries with vl init --tools.
# --offline requires the four source checkouts and compiler dependency caches to be prepared already.
set -euo pipefail

offline=0
if [[ ${1:-} == --offline ]]; then
    offline=1
    shift
fi
if (($# != 1)); then
    echo 'usage: scripts/install-tools.sh [--offline] ABSOLUTE_BUILD_DIRECTORY' >&2
    exit 2
fi
repo=$(cd "$(dirname "$0")/.." && pwd -P)
build=$1
if [[ $build != /* ]]; then
    echo 'install-tools: the build directory must be absolute' >&2
    exit 2
fi
mkdir -p "$build"
build=$(cd "$build" && pwd -P)
if [[ $build == "$repo" || $build == "$repo/"* ]]; then
    echo 'install-tools: keep upstream sources and binaries outside the VerifyLab checkout' >&2
    exit 2
fi

comparator_rev=19e111e2141cf333c7daff0f64c5f24acc91dd2e
export_rev=cacf989bd75f608700820f6afc595f32e7a99a4d
landrun_rev=811cfff51ceaf3d9843708aa6d22e9b84ccac8b4
nanoda_rev=4c544ed4099c8227f07d5de77ad1e69fb0740a27
toolchain=$(tr -d '\r\n' < "$repo/fixtures/lean-planted/lean-toolchain")
if [[ $toolchain != leanprover/lean4:v4.34.0-rc2 ]]; then
    echo 'install-tools: update and verify the source pins before changing the fixture toolchain' >&2
    exit 2
fi
for command in git elan go cargo uv; do
    command -v "$command" >/dev/null || { echo "install-tools: missing $command" >&2; exit 2; }
done

source_checkout() {
    local name=$1 url=$2 revision=$3 directory="$build/$1"
    if [[ ! -d $directory/.git ]]; then
        if ((offline)); then
            echo "install-tools: offline mode needs a checkout of $name at $revision in $directory" >&2
            exit 2
        fi
        if [[ -e $directory ]]; then
            echo "install-tools: $directory exists and is not a git checkout; choose another build directory" >&2
            exit 2
        fi
        git clone --no-checkout "$url" "$directory"
        git -C "$directory" checkout --detach "$revision"
    fi
    if [[ $(git -C "$directory" rev-parse HEAD) != "$revision" ]]; then
        echo "install-tools: $name has the wrong revision; expected $revision (existing checkouts are preserved)" >&2
        exit 2
    fi
    if ! git -C "$directory" diff --exit-code --quiet || ! git -C "$directory" diff --cached --exit-code --quiet; then
        echo "install-tools: $name has modified tracked files; use a clean checkout" >&2
        exit 2
    fi
    if [[ -n $(git -C "$directory" ls-files --others --exclude-standard) ]]; then
        echo "install-tools: $name has untracked files that could affect its build; use a clean checkout" >&2
        exit 2
    fi
}

source_checkout comparator https://github.com/leanprover/comparator.git "$comparator_rev"
source_checkout lean4export https://github.com/leanprover/lean4export.git "$export_rev"
source_checkout landrun https://github.com/Zouuup/landrun.git "$landrun_rev"
source_checkout nanoda https://github.com/ammkrn/nanoda_lib.git "$nanoda_rev"

# Resolve an already installed toolchain directly. Even online mode leaves installing Lean to the operator;
# build commands below cannot make elan download a missing toolchain implicitly.
lake=$(ELAN_TOOLCHAIN="$toolchain" elan which lake)
if [[ ! -x $lake ]]; then
    echo "install-tools: install $toolchain with elan first" >&2
    exit 2
fi
export PATH="$(dirname "$lake"):$PATH"
export GOTOOLCHAIN=local
if ((offline)); then
    export GOPROXY=off GOSUMDB=off CARGO_NET_OFFLINE=true
fi

# Comparator's committed manifest pins this same exporter. Supply that checkout locally rather than follow
# the lakefile's moving 'master' inputRev. Its origin matches the manifest so Lake does not replace it.
dependency="$build/comparator/.lake/packages/lean4export"
if [[ ! -e $dependency ]]; then
    mkdir -p "$(dirname "$dependency")"
    git clone --no-hardlinks "$build/lean4export" "$dependency"
    git -C "$dependency" remote set-url origin https://github.com/leanprover/lean4export
fi
if [[ $(git -C "$dependency" rev-parse HEAD) != "$export_rev" ]]; then
    echo 'install-tools: Comparator dependency has the wrong exporter revision' >&2
    exit 2
fi
if ! git -C "$dependency" diff --exit-code --quiet || ! git -C "$dependency" diff --cached --exit-code --quiet; then
    echo 'install-tools: Comparator exporter dependency has modified tracked files' >&2
    exit 2
fi
if [[ -n $(git -C "$dependency" ls-files --others --exclude-standard) ]]; then
    echo 'install-tools: Comparator exporter dependency has untracked files' >&2
    exit 2
fi
mkdir -p "$build/bin"
(cd "$build/lean4export" && "$lake" build lean4export)
(cd "$build/comparator" && "$lake" build comparator)
(cd "$build/landrun" && go build -mod=readonly -trimpath -o "$build/bin/landrun" ./cmd/landrun)
(cd "$build/nanoda" && cargo build --release --locked)
cp "$build/lean4export/.lake/build/bin/lean4export" "$build/bin/lean4export"
cp "$build/comparator/.lake/build/bin/comparator" "$build/bin/comparator"
cp "$build/nanoda/target/release/nanoda_bin" "$build/bin/nanoda"
{
    printf 'Lean toolchain: %s\n' "$toolchain"
    "$lake" --version
    go version
    cargo --version
    printf 'comparator %s\nlean4export %s\nlandrun %s\nnanoda %s\n' \
        "$comparator_rev" "$export_rev" "$landrun_rev" "$nanoda_rev"
} > "$build/SOURCE-REVISIONS"

cd "$repo"
uv_args=(run --frozen)
if ((offline)); then uv_args+=(--offline); fi
uv "${uv_args[@]}" python -m verifylab.cli init --tools --toolchain "$toolchain" \
    --comparator "$build/bin/comparator" --lean4export "$build/bin/lean4export" \
    --landrun "$build/bin/landrun" --nanoda "$build/bin/nanoda"
echo "install-tools: source revisions and compiler versions recorded in $build/SOURCE-REVISIONS"
