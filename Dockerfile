# Reproducible build environment for PortalDot contracts.
#
# PortalDot mainnet runs pallet-contracts 3.0.0 (Substrate 3.0.0, April 2021).
# Two independent walls have kept every project in this ecosystem on localhost:
#
#   Wall 1  the 2021-era cargo-contract no longer resolves its dependencies
#           (toml_datetime and friends moved past the pinned toolchain's MSRV).
#   Wall 2  even once it builds, modern LLVM emits post-MVP WASM that
#           wasmi-validation 0.4 rejects on-chain.
#
# STAGE "minimal" routes around both: a zero-dependency contract written
# straight against the seal0 host functions, so there is no dependency graph to
# break, and explicit target-features so nothing post-MVP is emitted.
#
# STAGE "ink3" is the second front — the real ink! 3.0-rc toolchain — and is
# only worth fighting once STAGE "minimal" has proven the chain accepts a
# contract at all.
#
#   docker build --target minimal -t portaldot-ink:minimal .
#   docker build --target ink3    -t portaldot-ink:ink3    .

ARG RUST_VERSION=1.69.0

# --------------------------------------------------------------- minimal ----
# 1.69 is the last release before sign-ext became a default target feature,
# so this stage is correct even if the -C target-feature flags were dropped.
FROM rust:${RUST_VERSION}-slim AS minimal

RUN rustup target add wasm32-unknown-unknown

RUN apt-get update \
 && apt-get install -y --no-install-recommends binaryen xxd \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /work
CMD ["cargo", "build", "--release"]

# ------------------------------------------------------------------ ink3 ----
# ink! 3.0-rc era. Pinned nightly + a frozen dependency set. Do not "cargo
# update" inside this image: the whole point is that resolution is frozen.
FROM rust:1.69.0-slim AS ink3

ARG INK_NIGHTLY=nightly-2022-08-11

RUN apt-get update \
 && apt-get install -y --no-install-recommends binaryen git pkg-config libssl-dev build-essential \
 && rm -rf /var/lib/apt/lists/*

RUN rustup toolchain install ${INK_NIGHTLY} --profile minimal \
 && rustup component add rust-src --toolchain ${INK_NIGHTLY} \
 && rustup target add wasm32-unknown-unknown --toolchain ${INK_NIGHTLY}

# cargo-contract 0.17 is the last release that targets pallet-contracts 3.0.
# --locked is essential: it forces the crate's own Cargo.lock, which is exactly
# the pin that stops the toml_datetime resolution failure.
RUN cargo install cargo-contract --version 0.17.0 --locked || \
    echo "NOTE: cargo-contract install failed — see README, use the minimal path"

WORKDIR /work
CMD ["cargo", "+nightly-2022-08-11", "contract", "build", "--release"]
