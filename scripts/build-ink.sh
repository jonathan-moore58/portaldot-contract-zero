#!/usr/bin/env bash
# Build an ink! contract for PortalDot's pallet-contracts 3.0.0.
#
#   bash scripts/build-ink.sh [contract-dir]      default: contracts/flipper-rc4
#
# The recipe, and why each piece is here:
#
#   nightly-2025-03-01   The only window that works. Its Cargo understands the
#                        edition-2024 manifests that modern transitive deps
#                        publish, and its rustc still has the nightly features
#                        (alloc_error_handler, core_intrinsics) that ink! 3.x
#                        needs. Older toolchains fail on the manifests; stable
#                        fails on the features.
#
#   no -Z build-std      Rebuilding core pulls host proc-macro crates into the
#                        same graph and you get "duplicate lang item in core".
#                        Not needed: binaryen lowers the bulk-memory ops after
#                        the fact.
#
#   binaryen lowering    Modern LLVM enables five post-MVP wasm features by
#                        default, and this chain's validator (wasmi-validation
#                        0.4) knows only the MVP. --llvm-memory-copy-fill-lowering
#                        and --signext-lowering rewrite those instructions into
#                        MVP equivalents.
#
#   portawasm fix        rustc still exports __data_end and __heap_base, and
#                        prepare.rs accepts only deploy and call.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

DIR="${1:-contracts/flipper-rc4}"
NAME="$(basename "$DIR" | tr '-' '_')"
NIGHTLY="nightly-2025-03-01"
BINARYEN="version_123"
OUT="$ROOT/out"
mkdir -p "$OUT"

if command -v cygpath >/dev/null 2>&1; then
  MOUNT="$(cygpath -m "$ROOT")"
else
  MOUNT="$ROOT"
fi

echo "==> compiling $DIR with $NIGHTLY"
MSYS_NO_PATHCONV=1 docker run --rm -v "$MOUNT:/work" -w "/work/$DIR" rust:slim sh -c "
  set -e
  rustup toolchain install $NIGHTLY --profile minimal -c rust-src >/dev/null 2>&1
  rustup target add wasm32-unknown-unknown --toolchain $NIGHTLY >/dev/null 2>&1
  rustup run $NIGHTLY cargo build --release --target wasm32-unknown-unknown --no-default-features
"

RAW="$DIR/target/wasm32-unknown-unknown/release/$NAME.wasm"
[ -f "$RAW" ] || { echo "no wasm at $RAW" >&2; exit 1; }
cp "$RAW" "$OUT/ink.raw.wasm"

echo
echo "==> lowering post-MVP instructions to MVP"
MSYS_NO_PATHCONV=1 docker run --rm -v "$MOUNT:/work" -w /work debian:bookworm-slim sh -c "
  set -e
  apt-get update -qq >/dev/null 2>&1
  apt-get install -y -qq curl ca-certificates >/dev/null 2>&1
  curl -sL https://github.com/WebAssembly/binaryen/releases/download/$BINARYEN/binaryen-$BINARYEN-x86_64-linux.tar.gz -o /tmp/b.tgz
  tar xzf /tmp/b.tgz -C /tmp
  /tmp/binaryen-$BINARYEN/bin/wasm-opt out/ink.raw.wasm -o out/ink.mvp.wasm \
    --llvm-memory-copy-fill-lowering --signext-lowering \
    --disable-bulk-memory --disable-sign-ext --disable-nontrapping-float-to-int \
    --disable-reference-types --disable-multivalue --disable-simd -Oz
"

echo
echo "==> repairing exports"
python tools/portawasm.py fix "$OUT/ink.mvp.wasm" "$OUT/ink.wasm"

echo
echo "==> validating against the chain's rules"
python tools/portawasm.py check "$OUT/ink.wasm"

echo "artifact: $OUT/ink.wasm"
echo
echo "deploy it with the constructor selector, e.g. new(false):"
echo "  node scripts/deploy.mjs --wasm out/ink.wasm --dev Alice --data 0x9bae9d5e00 --endowment 30 --gas 500000000000"
