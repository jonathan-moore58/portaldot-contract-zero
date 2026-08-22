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
# cargo-contract is only an orchestrator: it shells out to `cargo` and then
# builds a metadata crate. That lets us split the problem in two.
#
#   stage ccbuild   compiles cargo-contract (2021 code) on a 2023 Rust, which is
#                   the newest toolchain it still builds on. `--locked` is what
#                   makes that work — without it Cargo re-resolves its
#                   dependencies to releases that no longer build.
#
#   stage ink3      hands that binary a 2025 nightly to drive, so the *contract's*
#                   dependency graph is resolved by a Cargo new enough to read
#                   edition-2024 manifests, while rustc still has the nightly
#                   features ink! 3.x needs.
#
# The payoff over a hand-driven cargo build is metadata: `cargo contract build`
# emits the .contract bundle with the ABI, which a plain wasm build cannot,
# because the ABI comes from compiling the contract a second time for the host
# and running it.
FROM rust:1.69.0-slim AS ccbuild

# cargo-contract must match the ink! release, not just be "old enough".
# 0.17 generates a metadata crate that references ink_metadata::MetadataVersioned,
# a type ink! 3.0.0-rc4 does not have. 0.13.0 shipped on 2021-07-22 — the same
# day as rc4 — and generates the metadata crate rc4 actually exposes.
ARG CARGO_CONTRACT_VERSION=0.13.0

RUN apt-get update  && apt-get install -y --no-install-recommends pkg-config libssl-dev build-essential ca-certificates  && rm -rf /var/lib/apt/lists/*

RUN cargo install cargo-contract --version ${CARGO_CONTRACT_VERSION} --locked --root /out


FROM rust:slim AS ink3

ARG INK_NIGHTLY=nightly-2025-03-01

RUN apt-get update  && apt-get install -y --no-install-recommends binaryen libssl-dev ca-certificates  && rm -rf /var/lib/apt/lists/*

COPY --from=ccbuild /out/bin/cargo-contract /usr/local/cargo/bin/cargo-contract

RUN rustup toolchain install ${INK_NIGHTLY} --profile minimal -c rust-src  && rustup target add wasm32-unknown-unknown --toolchain ${INK_NIGHTLY}

# cargo-contract overwrites RUSTFLAGS, so target features cannot be passed that
# way — without this the build dies in post-processing with "Unknown opcode 252"
# (0xFC, bulk-memory), because cargo-contract's parity-wasm is MVP-only, exactly
# like the chain's validator. RUSTC_WRAPPER sits underneath RUSTFLAGS: cargo
# runs every rustc through it. The flag is added only for the wasm target, since
# target-cpu=mvp is not a valid CPU for a host build.
COPY docker/rustc-mvp /usr/local/bin/rustc-mvp
RUN chmod +x /usr/local/bin/rustc-mvp
ENV RUSTC_WRAPPER=/usr/local/bin/rustc-mvp

WORKDIR /work
