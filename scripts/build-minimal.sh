#!/usr/bin/env bash
# Build the zero-dependency contract, repair the WASM, and validate it against
# the chain's own rules — all before a single POT is spent.
#
#   bash scripts/build-minimal.sh
#
# On Windows/git-bash, MSYS_NO_PATHCONV stops the shell from mangling the
# container-side paths in -v and -w.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

IMAGE="portaldot-ink:minimal"
OUT="$ROOT/out"
mkdir -p "$OUT"

# Docker on Windows needs a native path; git-bash hands out /d/... which the
# daemon cannot resolve. cygpath -m gives the mixed form (D:/...) that works for
# both the build context and the bind mount.
if command -v cygpath >/dev/null 2>&1; then
  MOUNT="$(cygpath -m "$ROOT")"
else
  MOUNT="$ROOT"
fi

echo "==> building image"
MSYS_NO_PATHCONV=1 docker build --target minimal -t "$IMAGE" "$MOUNT"

echo
echo "==> compiling contract (no dependencies, MVP-only WASM)"
# CARGO_TARGET_DIR keeps build output off the bind mount's Windows filesystem,
# which is both slow and prone to permission trouble; we copy the artifact out.
MSYS_NO_PATHCONV=1 docker run --rm \
  -v "$MOUNT:/work" \
  -w /work/contracts/minimal \
  "$IMAGE" \
  cargo build --release

RAW="contracts/minimal/target/wasm32-unknown-unknown/release/portaldot_minimal.wasm"
if [ ! -f "$RAW" ]; then
  echo "build produced no wasm at $RAW" >&2
  exit 1
fi

cp "$RAW" "$OUT/minimal.raw.wasm"

echo
echo "==> what LLVM actually emitted"
python tools/portawasm.py dump "$OUT/minimal.raw.wasm"

echo
echo "==> repairing for pallet-contracts 3.0.0"
python tools/portawasm.py fix "$OUT/minimal.raw.wasm" "$OUT/minimal.wasm"

echo
echo "==> validating against the chain's rules"
python tools/portawasm.py check "$OUT/minimal.wasm"

echo "artifact: $OUT/minimal.wasm"
