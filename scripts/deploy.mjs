// Deploy a contract to PortalDot's pallet-contracts 3.0.0.
//
// Why this exists: @polkadot/api-contract builds `instantiateWithCode` with a
// WeightV2 gas_limit and a storage_deposit_limit argument. PortalDot's runtime
// has neither — its call is:
//
//   instantiate_with_code(endowment, gas_limit: Compact<Weight=u64>, code, data, salt)
//
// so the helper silently produces an extrinsic the chain cannot decode. We build
// the call straight from the chain's own metadata instead, which always matches
// whatever the runtime actually exposes.
//
//   node scripts/deploy.mjs --wasm out/minimal.wasm --suri "//Alice"
//   node scripts/deploy.mjs --wasm out/minimal.wasm --suri "$SEED" \
//        --ws wss://mainnet.portaldot.io --endowment 20
//
// Defaults to the public dev node, where Alice is pre-funded. Point --ws at
// mainnet only when you mean it.

import { readFileSync } from 'node:fs';
import { ApiPromise, WsProvider } from '@polkadot/api';
import { Keyring } from '@polkadot/keyring';
import { cryptoWaitReady, blake2AsHex } from '@polkadot/util-crypto';
import { u8aToHex, hexToU8a } from '@polkadot/util';

const DEV_NODE = 'wss://drip-node-production.up.railway.app';
const DECIMALS = 14n;
const SS58 = 42;

function arg(name, fallback) {
  const i = process.argv.indexOf(`--${name}`);
  return i > -1 && process.argv[i + 1] ? process.argv[i + 1] : fallback;
}
function flag(name) {
  return process.argv.includes(`--${name}`);
}
function pot(n) {
  const [whole, frac = ''] = String(n).split('.');
  return BigInt(whole) * 10n ** DECIMALS
       + BigInt((frac + '0'.repeat(Number(DECIMALS))).slice(0, Number(DECIMALS)) || 0);
}
function fmt(planck) {
  const v = BigInt(planck);
  const w = v / 10n ** DECIMALS;
  const f = (v % 10n ** DECIMALS).toString().padStart(Number(DECIMALS), '0').slice(0, 4);
  return `${w}.${f} POT`;
}

const WS = arg('ws', DEV_NODE);

// git-bash on Windows rewrites a leading "//" into a drive path, so "//Alice"
// arrives as something like "D:/Program Files/Alice". --dev sidesteps it.
const DEV_ACCOUNT = arg('dev', null);
let SURI = DEV_ACCOUNT ? `//${DEV_ACCOUNT}` : arg('suri', '//Alice');
if (/[\\/](Alice|Bob|Charlie|Dave|Eve|Ferdie)$/.test(SURI) && !SURI.startsWith('//')) {
  const who = SURI.split(/[\\/]/).pop();
  console.warn(`note: "${SURI}" looks like a mangled //${who} — using //${who}.`);
  console.warn('      (pass --dev Alice, or prefix the command with MSYS_NO_PATHCONV=1)');
  SURI = `//${who}`;
}
const WASM_PATH = arg('wasm', 'out/minimal.wasm');
const ENDOWMENT = pot(arg('endowment', '20'));
const GAS_LIMIT = BigInt(arg('gas', '200000000000'));
const DATA = arg('data', '0x');
const DRY = flag('dry-run');

const code = new Uint8Array(readFileSync(WASM_PATH));
const salt = hexToU8a('0x' + Date.now().toString(16).padStart(16, '0'));

await cryptoWaitReady();

console.log(`endpoint    ${WS}`);
const api = await ApiPromise.create({ provider: new WsProvider(WS), noInitWarn: true });

const [chain, ver] = await Promise.all([
  api.rpc.system.chain(),
  api.rpc.state.getRuntimeVersion(),
]);
console.log(`chain       ${chain} (specVersion ${ver.specVersion})`);

// --- guard: make sure this really is the legacy pallet ----------------------
const meta = api.tx.contracts?.instantiateWithCode;
if (!meta) {
  console.error('this chain has no contracts.instantiateWithCode — wrong endpoint?');
  process.exit(1);
}
const argNames = meta.meta.args.map((a) => a.name.toString());
console.log(`call args   ${argNames.join(', ')}`);
if (argNames.includes('storage_deposit_limit')) {
  console.error('\nThis node runs a NEWER pallet-contracts than PortalDot mainnet.');
  console.error('Use ink! 4.x/5.x tooling instead — this script targets the 3.0 pallet.');
  process.exit(1);
}

console.log(`code        ${WASM_PATH}  ${code.length} bytes  ${blake2AsHex(code)}`);
if (code.length > 128 * 1024) {
  console.error(`code is ${code.length} bytes, over the 128 KiB code_len limit`);
  process.exit(1);
}

const keyring = new Keyring({ type: 'sr25519', ss58Format: SS58 });
const signer = keyring.addFromUri(SURI);
console.log(`signer      ${signer.address}`);

const { data: bal } = await api.query.system.account(signer.address);
console.log(`balance     ${fmt(bal.free.toString())}`);
if (BigInt(bal.free.toString()) < ENDOWMENT + pot('2')) {
  console.error(`\nnot enough POT. Need at least ${fmt(ENDOWMENT + pot('2'))}.`);
  console.error('Dev node: https://drip-portaldot.netlify.app  (1000 POT, 12h cooldown)');
  console.error('Mainnet:  ask the Portaldot team — DRIP does not fund mainnet.');
  process.exit(1);
}

const tx = api.tx.contracts.instantiateWithCode(
  ENDOWMENT,
  GAS_LIMIT,
  u8aToHex(code),
  DATA,
  u8aToHex(salt),
);

const info = await tx.paymentInfo(signer);
console.log(`endowment   ${fmt(ENDOWMENT)}`);
console.log(`gas_limit   ${GAS_LIMIT} (legacy u64 Weight, not WeightV2)`);
console.log(`fee         ${fmt(info.partialFee.toString())}`);

if (DRY) {
  console.log('\n--dry-run: not submitting.');
  await api.disconnect();
  process.exit(0);
}

console.log('\nsubmitting… (60s blocks — this takes a minute)\n');

await new Promise((resolve, reject) => {
  tx.signAndSend(signer, ({ status, events, dispatchError }) => {
    if (status.isInBlock || status.isFinalized) {
      const where = status.isInBlock ? status.asInBlock : status.asFinalized;
      console.log(`included in ${where.toHex()}`);

      if (dispatchError) {
        if (dispatchError.isModule) {
          const d = api.registry.findMetaError(dispatchError.asModule);
          console.error(`\nFAILED  ${d.section}.${d.name}: ${d.docs.join(' ')}`);
        } else {
          console.error(`\nFAILED  ${dispatchError.toString()}`);
        }
        return reject(new Error('dispatch failed'));
      }

      for (const { event } of events) {
        console.log(`  ${event.section}.${event.method}`);
        if (event.section === 'contracts' && event.method === 'CodeStored') {
          console.log(`     code_hash  ${event.data[0].toString()}`);
        }
        if (event.section === 'contracts' && event.method === 'Instantiated') {
          console.log(`\n  CONTRACT   ${event.data[1].toString()}`);
        }
      }
      resolve();
    }
  }).catch(reject);
});

await api.disconnect();
