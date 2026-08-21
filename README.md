<div align="center">

# Contract Zero

**Smart contracts work on PortalDot. Here is the toolchain that gets you there.**

[Quick start](#quick-start) · [Why contracts fail](#why-contracts-fail) · [Reference](#reference) · [Blocked projects](#if-your-project-is-one-of-the-blocked-ones)

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
| `Dockerfile` | Pinned build environments: `minimal`, and `ink3` |
| `scripts/build-minimal.sh` | Compile, repair, validate |
| `scripts/deploy.mjs` | `instantiate_with_code` with legacy `Compact<u64>` gas |
| `scripts/call.mjs` | Call a contract and read its storage before and after |
| `scripts/newaccount.mjs` | Generate an account without printing the mnemonic |

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
| `DepositPerContract` | 6.87 POT |
| `TombstoneDeposit` | 6.87 POT |
| `DepositPerStorageByte` | 0.06 POT |
| `DepositPerStorageItem` | 0.15 POT |
| Rent | `RentFraction × (deposit − free_balance)` — a contract funded above its deposit pays none |

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

**Using ink! 4.x or 5.x** — the runtime needs ink! 3.0-rc-era output. Nothing
built with ink! 4 or 5 will instantiate here, on mainnet or on the dev node.
Until the `ink3` stage lands, `contracts/minimal` shows the shape of a module
the chain does accept.

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

First contract deployed and executing on the public dev node:

```
contract    5DzRGitiPMjRQR9BGL5r6qAeoCm5TA5WnG3CD63b6LVkVpYP
code_hash   0x33fe817ca2d745df454196acc5b17a1920e1fe617379db57691ee9537eeba0eb
block       0x73554c992e98d95264213bccf022ae0140024c8e8b0b15aef0e6b41fbb9bfed8
size        405 bytes
endowment   20 POT       fee 0.0554 POT
```

Chain state, read directly from storage:

| | Before | After |
|---|---|---|
| `Contracts.PristineCode` | 0 | **1** |
| `Contracts.ContractInfoOf` | 0 | **1** |
| Contract storage at key `0x00…00` | `0` | **`1`** after one `contracts.call` |

The last row is the one that matters: it deployed *and* ran.

## Status

- [x] Chain surface mapped from live metadata and published source
- [x] Schedule limits verified against the on-chain constant
- [x] Validator written from the chain's own rules, self-tested
- [x] Zero-dependency contract on `seal0`
- [x] Deploy path for the legacy `Compact<u64>` signature
- [x] Deployed to the public dev node
- [x] Called, and it changed state
- [ ] Mainnet deployment
- [ ] `ink3` — restore the ink! 3.0-rc toolchain so existing projects build unchanged

## License

MIT. See [LICENSE](LICENSE).
