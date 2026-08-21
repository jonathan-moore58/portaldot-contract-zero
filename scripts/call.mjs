// Call a deployed PortalDot contract and prove it executed.
//
// Reads the contract's storage before and after, so the result is not a claim
// about events but the chain's own state changing.
//
//   node scripts/call.mjs --addr 5Dz... --dev Alice
//
// The legacy call is contracts.call(dest, value, gas_limit: Compact<u64>, data)
// — no storage_deposit_limit, which is why @polkadot/api-contract cannot build it.

import { ApiPromise, WsProvider } from '@polkadot/api';
import { Keyring } from '@polkadot/keyring';
import { cryptoWaitReady } from '@polkadot/util-crypto';

const DEV_NODE = 'wss://drip-node-production.up.railway.app';
const STORAGE_KEY = '0x' + '00'.repeat(32);   // the key our contract writes to
const SS58 = 42;

function arg(name, fallback) {
  const i = process.argv.indexOf(`--${name}`);
  return i > -1 && process.argv[i + 1] ? process.argv[i + 1] : fallback;
}

const WS = arg('ws', DEV_NODE);
const ADDR = arg('addr', null);
const DEV_ACCOUNT = arg('dev', null);
let SURI = DEV_ACCOUNT ? `//${DEV_ACCOUNT}` : arg('suri', '//Alice');
if (/[\\/](Alice|Bob|Charlie|Dave|Eve|Ferdie)$/.test(SURI) && !SURI.startsWith('//')) {
  SURI = `//${SURI.split(/[\\/]/).pop()}`;
}
const GAS_LIMIT = BigInt(arg('gas', '200000000000'));

if (!ADDR) {
  console.error('usage: node scripts/call.mjs --addr <contract> [--dev Alice]');
  process.exit(1);
}

await cryptoWaitReady();
const api = await ApiPromise.create({ provider: new WsProvider(WS), noInitWarn: true });

const keyring = new Keyring({ type: 'sr25519', ss58Format: SS58 });
const signer = keyring.addFromUri(SURI);

async function readFlag() {
  const raw = await api.rpc.contracts.getStorage(ADDR, STORAGE_KEY);
  if (raw.isNone) return null;
  const bytes = raw.unwrap();
  return bytes.length ? bytes[0] : null;
}

console.log(`contract   ${ADDR}`);
console.log(`caller     ${signer.address}`);

const before = await readFlag();
console.log(`\nstorage before   ${before === null ? '(empty)' : before}`);

const tx = api.tx.contracts.call(ADDR, 0, GAS_LIMIT, '0x');
console.log('\ncalling…\n');

await new Promise((resolve, reject) => {
  tx.signAndSend(signer, ({ status, events, dispatchError }) => {
    if (!status.isInBlock && !status.isFinalized) return;
    const where = status.isInBlock ? status.asInBlock : status.asFinalized;
    console.log(`included in ${where.toHex()}`);
    if (dispatchError) {
      const msg = dispatchError.isModule
        ? (() => {
            const d = api.registry.findMetaError(dispatchError.asModule);
            return `${d.section}.${d.name}: ${d.docs.join(' ')}`;
          })()
        : dispatchError.toString();
      return reject(new Error(msg));
    }
    for (const { event } of events) {
      console.log(`  ${event.section}.${event.method}`);
    }
    resolve();
  }).catch(reject);
});

const after = await readFlag();
console.log(`\nstorage after    ${after === null ? '(empty)' : after}`);

if (before !== after) {
  console.log(`\n  the contract ran: stored value went ${before} -> ${after}`);
} else {
  console.log('\n  storage did not change — the call did not do what we expected');
  process.exitCode = 1;
}

await api.disconnect();
