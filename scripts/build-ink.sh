#!/usr/bin/env bash
# Build an ink! contract for PortalDot, with metadata.
#
#   bash scripts/build-ink.sh [contract-dir]     default: contracts/flipper-rc4
#
# Produces target/ink/{*.wasm, metadata.json, *.contract} and validates the
# module against the chain's rules before you spend anything.
#
# Every version here is matched to the same era, and none of it is arbitrary:
#
#   pallet-contracts 3.0.0   what the chain runs — still has the rent model
#   ink! 3.0.0-rc4           2021-07-22, the last line built for a rent-era pallet
#   cargo-contract 0.13.0    2021-07-22, same day. 0.17 generates a metadata crate
#                            referencing ink_metadata::MetadataVersioned, which
#                            rc4 does not have
#   scale-info 0.6           what rc4 declares (^0.6)
#   nightly-2025-03-01       the only window whose Cargo reads edition-2024
#                            manifests while rustc still has the nightly features
#                            ink! 3.x needs
#
# The image is built in two stages so the 2022 tool and the 2025 toolchain never
# have to be the same thing: cargo-contract is compiled on a 2023 Rust, then
# handed a 2025 nightly to drive. See the Dockerfile.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

DIR="${1:-contracts/flipper-rc4}"
NAME="$(basename "$DIR" | tr '-' '_')"
IMAGE="portaldot-ink:ink3"
OUT="$ROOT/out"
mkdir -p "$OUT"

if command -v cygpath >/dev/null 2>&1; then
  MOUNT="$(cygpath -m "$ROOT")"
else
  MOUNT="$ROOT"
fi

echo "==> building image (first run takes a while — cargo-contract is compiled from source)"
MSYS_NO_PATHCONV=1 docker build --target ink3 -t "$IMAGE" "$MOUNT"

echo
echo "==> building contract"
MSYS_NO_PATHCONV=1 docker run --rm -v "$MOUNT:/work" -w "/work/$DIR" "$IMAGE" \
  cargo +nightly-2025-03-01 contract build --release

ART="$DIR/target/ink"
[ -f "$ART/$NAME.wasm" ] || { echo "no wasm at $ART/$NAME.wasm" >&2; exit 1; }

cp "$ART/$NAME.wasm" "$OUT/ink.wasm"
cp "$ART/metadata.json" "$OUT/ink.metadata.json" 2>/dev/null || true
cp "$ART/$NAME.contract" "$OUT/ink.contract" 2>/dev/null || true

echo
echo "==> validating against the chain's rules"
python tools/portawasm.py check "$OUT/ink.wasm"

echo "artifacts:"
echo "  $OUT/ink.wasm            the code"
echo "  $OUT/ink.metadata.json   the ABI"
echo "  $OUT/ink.contract        both, for tools that take a bundle"
echo
echo "deploy with the constructor selector — new(false) on the flipper:"
echo "  node scripts/deploy.mjs --wasm out/ink.wasm --dev Alice \\"
echo "       --data 0x9bae9d5e00 --endowment 30 --gas 500000000000"
