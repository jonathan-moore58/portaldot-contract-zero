<div align="center">

# Contract Zero

**Smart contracts work on PortalDot. Here is the toolchain that gets you there.**

[Quick start](#quick-start) · [Why contracts fail](#why-contracts-fail) · [Building with ink!](#building-with-ink) · [Reference](#reference) · [Blocked projects](#if-your-project-is-one-of-the-blocked-ones)

</div>

---

PortalDot has been producing blocks since June 2021. Until now,
`Contracts.PristineCode` was empty on every one of its chains — no contract had
ever been deployed, across 2.6 million blocks, 47 hackathon projects and 222
hackers. The ecosystem's own troubleshooter documents the situation as blocked
pending a node upgrade from the core team.

It was not blocked on that. It was blocked on three WASM-level rules that stack,
none of which require a node upgrade to satisfy.

This repository contains a validator built from the chain's own source, a
contract that deploys and runs today, and the deploy path that the standard
tooling cannot produce.

## Quick start

Requires Docker and Python 3. Node 18+ to deploy.

```bash
python tools/selftest.py         # prove the validator, no Docker needed
bash scripts/build-minimal.sh    # compile → repair → validate
npm install
node scripts/deploy.mjs --wasm out/minimal.wasm --dev Alice
```

The build script never touches the network. It tells you whether the chain would
accept your module *before* you spend anything.

```
==> validating against the chain's rules
portawasm check: out/minimal.wasm
  · size: 405 bytes (limit 131072)
  · memory: 1..16 pages (limit 16)

  ACCEPTED — this module satisfies pallet-contracts 3.0.0.
```

## Why contracts fail

PortalDot runs `pallet-contracts` **3.0.0** — the pre-rent-removal pallet from
Substrate 3.0.0, April 2021. Modern Rust emits WASM it rejects, for three
reasons that only reveal themselves one at a time.

| # | Rule | Source | Fix |
|---|---|---|---|
| 1 | `wasmi-validation 0.4` accepts only the WASM MVP; Rust ≥ 1.70 enables `sign-ext` by default and emits `i32.extend8_s` | `Cargo.toml` | `-C target-feature=-sign-ext` |
| 2 | A contract may not declare its own memory — it must import `env.memory`, with a maximum declared | `prepare.rs::ensure_no_internal_memory` | `-C link-arg=--import-memory --max-memory=1048576` |
| 3 | Exactly two exports are permitted, `deploy` and `call`. rustc also exports `__data_end` and `__heap_base` | `prepare.rs::scan_exports` | strip after the build — `portawasm fix` |

Rule 3 is why people got stuck. It only surfaces once rules 1 and 2 are already
solved, so nobody reached it, and the failure it produces is an opaque
`ExtrinsicFailed: Other`.

A fourth issue sits above the WASM layer: `@polkadot/api-contract` builds
`instantiate_with_code` with a `WeightV2` gas limit and a `storage_deposit_limit`
argument. This runtime has neither — its signature is
`instantiate_with_code(endowment, gas_limit: Compact<u64>, code, data, salt)`.
`scripts/deploy.mjs` builds the call from chain metadata instead, which always
matches whatever the runtime actually exposes.

### On the dependency wall

The widely reported blocker is that the 2021-era `cargo-contract` no longer
resolves its dependency graph. That wall is real, and this repository walks
around it rather than through it.

The chain does not know what ink! is. `contracts.instantiate_with_code` takes
raw WASM bytes; contract metadata is a client-side convenience for
`@polkadot/api-contract` and never goes on-chain. `contracts/minimal` therefore
has **no dependencies at all** — there is no graph to fail to resolve, and plain
`cargo build` produces a deployable module.

Restoring the real ink! toolchain is still worth doing, and is tracked below.

## Contents

| Path | Purpose |
|---|---|
| `tools/portawasm.py` | Validate and repair WASM against the chain's own rules |
| `tools/selftest.py` | Proves the validator against hand-built WASM fixtures |
| `contracts/minimal/` | A contract with zero dependencies, written on `seal0` |
| `contracts/flipper-rc4/` | **A working ink! contract** — ink! 3.0.0-rc4 |
| `contracts/flipper/` | ink! 3.4.0. Compiles and validates, but traps on chain — see below |
| `scripts/build-minimal.sh` | Compile, repair, validate the raw contract |
| `scripts/build-ink.sh` | Build an ink! contract with its metadata, and validate it |
| `scripts/deploy.mjs` | `instantiate_with_code` with legacy `Compact<u64>` gas |
| `scripts/call.mjs` | Call a contract and read its storage before and after |
| `scripts/newaccount.mjs` | Generate an account without printing the mnemonic |
| `Dockerfile` | Build environments — `minimal` for the raw contract, `ink3` for ink! |
| `docker/rustc-mvp` | `rustc` wrapper that forces MVP WASM out of a modern LLVM |

### portawasm

```bash
python tools/portawasm.py dump  in.wasm            # what the compiler emitted
python tools/portawasm.py fix   in.wasm out.wasm   # repair what is repairable
python tools/portawasm.py check out.wasm           # would the chain accept it
```

`check` exits `0` only if the chain would accept the module, and names the
specific rule that failed otherwise. `fix` repairs structural problems —
internal memory, surplus exports, oversized page limits — and deliberately
leaves instruction-level ones alone. A sign-extension opcode is a compiler
setting, not something a patcher should paper over.

## Building with ink!

```bash
bash scripts/build-ink.sh
node scripts/deploy.mjs --wasm out/ink.wasm --dev Alice \
     --data 0x9bae9d5e00 --endowment 30 --gas 500000000000
```

ink! works on PortalDot. Getting there meant clearing four separate walls, each
of which only becomes visible once the one in front of it is down.

**1 — `cargo-contract` will not install.** The widely reported symptom. The fix
is `--locked`: without it Cargo re-resolves the crate's transitive dependencies
to current releases, which no longer build on the toolchain it pins. With it,
`cargo install cargo-contract --version 0.13.0 --locked --root /out` completes
on a 2023-era Rust.

The version matters as much as the flag — see [Metadata](#metadata-the-abi).

**2 — the contract's own dependency graph will not resolve.** Building a
contract resolves *its* dependencies fresh, and modern crates publish
edition-2024 manifests that a 2023-era Cargo cannot parse:

```
error: failed to parse the `edition` key
this version of Cargo is older than the `2024` edition
```

Pinning crate by crate is whack-a-mole — the graph simply has too many crates
that have since published an edition-2024 release. What actually works is
picking a toolchain whose *Cargo* is new enough, which leads to:

**3 — old rustc and new Cargo are both required, and they ship together.**
`ink_allocator` uses `#![feature(alloc_error_handler, core_intrinsics)]`, so
stable is out. Modern Cargo is needed for the manifests. Mixing them by pointing
`RUSTC` at an old toolchain fails, because new Cargo probes rustc with flags it
does not have (`unknown print request 'split-debuginfo'`).

The resolution is that a window exists where both hold: **`nightly-2025-03-01`**.
Its Cargo understands edition 2024; its rustc still has the nightly features.

**4 — the emitted WASM is post-MVP.** Modern LLVM turns on five post-MVP
features by default for `wasm32-unknown-unknown` — `bulk-memory`, `multivalue`,
`nontrapping-fptoint`, `reference-types`, `sign-ext` — and `wasmi-validation 0.4`
knows none of them. `-C target-feature=-...` covers your own crates but not the
precompiled `core`, so `core::slice::copy_from_slice` still emits `memory.copy`.

Three things fix this, in ascending order of how well they hold:

- Binaryen's `--llvm-memory-copy-fill-lowering` and `--signext-lowering` rewrite
  the post-MVP instructions into MVP equivalents after the build. Works, but it
  is a repair, and it runs after `cargo-contract` has already rejected the file.
- `-Z build-std` recompiles `core` with your target features. It works for the
  raw contract but collides with ink! rc4 (`duplicate lang item in core`).
- **`-C target-cpu=mvp`** turns every post-MVP feature off at the LLVM level,
  precompiled `core` included. This is what the image uses.

`cargo-contract` overwrites `RUSTFLAGS`, so the flag cannot be passed that way.
`RUSTC_WRAPPER` sits underneath it — Cargo runs every `rustc` invocation through
the wrapper — so [`docker/rustc-mvp`](docker/rustc-mvp) appends the flag, and
only for the wasm target, since `mvp` is not a valid CPU for a host build.

### Metadata (the ABI)

`scripts/build-ink.sh` emits three files into `out/`:

| File | What it is |
|---|---|
| `ink.wasm` | the code the chain runs |
| `ink.metadata.json` | the ABI — constructors, messages, selectors, storage layout |
| `ink.contract` | both together, for tools that take a bundle |

Metadata is what makes a contract usable by anything other than its author. Hand-rolled
builds cannot produce it; only `cargo contract build` can, because it compiles the
contract a second time for the host and runs it to emit its own description.

That second compile is why the `cargo-contract` version has to match the ink!
version, not merely be old enough. **0.13.0**, released 2021-07-22 — the same day
as rc4 — generates a metadata crate against the API rc4 exposes. 0.17 generates one
referencing `ink_metadata::MetadataVersioned`, a type rc4 does not have:

```
error[E0432]: unresolved import `ink_metadata::MetadataVersioned`
```

The image builds `cargo-contract` in its own stage on Rust 1.69, then hands the
binary to a 2025 nightly to drive. The 2022 tool and the 2025 toolchain never
have to be the same thing.

One useful side effect: `cargo-contract 0.13`'s `parity-wasm` is MVP-only, exactly
like the chain's `wasmi-validation`. Both are 2021 code. A post-MVP instruction
fails the build with `Unknown opcode 252` rather than reaching the chain — the
toolchain rejects what the chain would have rejected, before you spend anything.

Selectors from the generated metadata match the hand-computed
`blake2_256(name)[..4]`, which is worth checking once if you are calling a
contract by raw selector:

| Message | Selector |
|---|---|
| `new` | `0x9bae9d5e` |
| `default` | `0xed4b9d1b` |
| `flip` | `0x633aa551` |
| `get` | `0x2f865bd9` |

### Which ink! version

**ink! 3.0.0-rc4.** Not the 3.x finals.

PortalDot's `pallet-contracts` 3.0.0 still has the rent model, which Substrate
removed in December 2021. Every ink! 3.x *final* release came after that, so
they target a pallet this chain is not.

The evidence is in `contracts/flipper` (ink! 3.4.0): it compiles, and
`portawasm check` accepts the module, but instantiating it returns
`ContractTrapped` — with **identical gas consumed for every input, including a
deliberately invalid selector**. Same gas for all inputs means it traps before
it ever reads the input, so this is not a dispatch problem; ink! 3.4's startup
path does not run on this pallet. rc4 (2021-07-22), a month after this chain's
genesis, runs correctly.

### One thing that bites at every layer

Crates pin their families loosely, so the resolver mixes eras and the build
breaks in confusing ways:

| Crate | Pulled in | Result |
|---|---|---|
| `parity-scale-codec 3.1.5` | `parity-scale-codec-derive 3.7.5` (2025) | `toml_datetime` with edition 2024 |
| `ink_lang_macro rc4` | a different `ink_lang_ir` | `could not find 'InkTrait' in ink_lang_ir` |

Pinning the parent is never enough. `contracts/flipper-rc4/Cargo.toml` pins
every derive and internal crate explicitly, and that is the reason it builds.

## Reference

Read off the live runtime and the published source. Note that these disagree
with the developer documentation in several places; the values here are what the
chain actually enforces.

### Chains

| | |
|---|---|
| Mainnet | `wss://mainnet.portaldot.io` — POT, 14 decimals, ss58 42, 60s blocks |
| Public dev node | `wss://drip-node-production.up.railway.app` — identical runtime, Alice funded |
| Runtime | specVersion 1002, metadata **V13**, 31 pallets, 25 with calls, 155 extrinsics |
| Contracts pallet | `pallet-contracts` 3.0.0, host functions in module `seal0` (plus `seal1.seal_random`) |

### Deposits

| | |
|---|---|
| `DepositPerContract` | 7.35 POT |
| `TombstoneDeposit` | 7.35 POT |
| `DepositPerStorageByte` | 0.06 POT |
| `DepositPerStorageItem` | 0.15 POT |
| `Balances.ExistentialDeposit` | 1 POT |
| Rent | `RentFraction × (deposit − free_balance)` — a contract funded above its deposit pays none |

**Minimum endowment.** `instantiate_with_code` needs strictly more than the
subsistence threshold, `ExistentialDeposit + TombstoneDeposit` = 8.35 POT:

```
8.35 POT → NewContractNotFunded      (Module index 13, error 9)
8.36 POT → instantiates
```

Worth knowing because `NewContractNotFunded` reads like a problem with your
module, and it is not — the wasm never runs. Use 20–30 POT in practice; a
contract sitting at the floor is immediately rent-evictable.

### Schedule limits

Verified against the on-chain `Schedule` constant. All eleven match the
published source.

| Limit | Value | | Limit | Value |
|---|---|---|---|---|
| `code_len` | 128 KiB | | `table_size` | 4096 |
| `memory_pages` | 16 (1 MiB) | | `br_table_size` | 256 |
| `stack_height` | 512 | | `subject_len` | 32 |
| `globals` | 256 | | `call_depth` | 32 |
| `parameters` | 128 | | `payload_len` | 16 KiB |
| `event_topics` | 4 | | | |

### Module rules

- Exports must be exactly `deploy` and `call`, both `() -> ()`, both pointing at
  declared rather than imported functions.
- Memory must be imported as `env.memory` with a declared maximum.
- No floating point anywhere — globals, locals, or function signatures.
- MVP instruction set only.

Source: [`prepare.rs`](https://github.com/portaldotVolunteer/Portaldot/blob/main/frame/contracts/src/wasm/prepare.rs) ·
[`schedule.rs`](https://github.com/portaldotVolunteer/Portaldot/blob/main/frame/contracts/src/schedule.rs) ·
[`wasm/runtime.rs`](https://github.com/portaldotVolunteer/Portaldot/blob/main/frame/contracts/src/wasm/runtime.rs)

## If your project is one of the blocked ones

Several PortalDot projects ship contracts that cannot deploy to this runtime.
The fixes are small, and none of them need a node upgrade.

**Using ink! 4.x or 5.x** — the runtime needs ink! 3.0-rc-era output, and it is
not the API version number that stops you: ink! 4 and 5 import `seal1`/`seal2`
host functions, and this runtime's `seal1` contains exactly one function
(`seal_random`). Moving to ink! 3.0.0-rc4 is a real source change, since ink! 3
and 4 differ, but it is a version move rather than a rewrite —
`contracts/flipper-rc4` is a working example with the full pin set.

**Building `WeightV2` gas** — this runtime takes `Compact<u64>`. Build the call
from chain metadata rather than through `@polkadot/api-contract`; see
`scripts/deploy.mjs`. `@polkadot/api` decodes V13 metadata correctly, so
`api.tx.contracts.instantiateWithCode` already has the right shape.

**Getting `ExtrinsicFailed: Other` on deploy** — that is usually WASM
validation rather than your code. Run `portawasm check` on the module; it names
the rule.

**Defaulting to `ws://127.0.0.1:9944`** — the public dev node runs the identical
runtime with Alice funded, which makes it a better default for anything others
need to reproduce.

Issues and PRs welcome, including against your repo if that is easier.

## Verification

Three contracts are deployed and executing on the public dev node.

**Raw `seal0` contract** — `contracts/minimal`

```
contract    5DzRGitiPMjRQR9BGL5r6qAeoCm5TA5WnG3CD63b6LVkVpYP
code_hash   0x33fe817ca2d745df454196acc5b17a1920e1fe617379db57691ee9537eeba0eb
size        405 bytes        endowment 20 POT     fee 0.0554 POT
```

**ink! contract, hand-driven build** — `contracts/flipper-rc4`

```
contract    5FpP1cf9Zuc2nwMkNfFirU66UkHrRXHHTaXUGDKyvBSCU22g
code_hash   0x99dc4425d97c0609823689ca7cf934d5ca63b1dd2cf98e4d29eabde6011d2405
size        10,885 bytes     endowment 30 POT
```

**ink! contract, `cargo contract build`, with metadata** — the same source

```
contract    5CZxNAKbijnQnd1GTHcyEkjPAQo3apxMzo3biqWRJFQSv43b
size        1,712 bytes      endowment 30 POT
```

Same contract, one sixth the size, and this one ships an ABI. `portawasm fix` is
not needed on it either — `cargo-contract` strips the surplus exports itself.

Chain state before this work and after:

| | Before | After |
|---|---|---|
| `Contracts.PristineCode` | 0 | **3** |
| `Contracts.ContractInfoOf` | 0 | **3** |

And they run, not just deploy:

```
raw contract   storage at key 0x00…00     0  ->  1   after one contracts.call
ink! flipper   get()  ->  0x00 (false)
               flip()                         ExtrinsicSuccess
               get()  ->  0x01 (true)
```

## A note on the validator

`portawasm check` initially rejected the working ink! module, insisting entry
points be `() -> ()`. The chain accepted it anyway. Reading `scan_exports`
again:

```rust
// Both "call" and "deploy" has a () -> () function type.
// We still support () -> (i32) for backwards compatibility.
```

ink! 3.0-rc entry points return `i32`, so the rule was a false positive, and it
is fixed. Worth stating plainly, because a validator that says no when the chain
says yes is worse than no validator at all — it sends people back to believing
the thing is impossible. Every rule in `portawasm.py` is transcribed from the
chain's source, and the chain remains the authority.

## Status

- [x] Chain surface mapped from live metadata and published source
- [x] Schedule limits verified against the on-chain constant
- [x] Validator written from the chain's own rules, self-tested
- [x] Zero-dependency contract on `seal0`
- [x] Deploy path for the legacy `Compact<u64>` signature
- [x] Deployed to the public dev node
- [x] Called, and it changed state
- [x] **ink! builds, deploys and runs** — ink! 3.0.0-rc4, via `scripts/build-ink.sh`
- [x] **Metadata** — `cargo contract build` produces the ABI and the `.contract` bundle
- [ ] Mainnet deployment
- [ ] Metadata (ABI) generation, so `@polkadot/api-contract` clients can be used

## License

MIT. See [LICENSE](LICENSE).
