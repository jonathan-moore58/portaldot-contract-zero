// Generate a PortalDot account locally.
//
// The mnemonic is written to .account.json (gitignored) and never printed, so
// it does not end up in terminal scrollback, a screenshot, or a chat log. Only
// the address goes to the screen — that is the part you share.
//
//   node scripts/newaccount.mjs
//   node scripts/newaccount.mjs --show     (print the mnemonic, on purpose)
//
// ss58 42 is PortalDot's prefix, and the same address is valid on mainnet, the
// dev node, and any testnet the team points you at — the prefix is a display
// format, not a per-chain identity.

import { writeFileSync, existsSync, readFileSync } from 'node:fs';
import { Keyring } from '@polkadot/keyring';
import { cryptoWaitReady, mnemonicGenerate, mnemonicValidate } from '@polkadot/util-crypto';

const SS58 = 42;
const FILE = '.account.json';
const SHOW = process.argv.includes('--show');

await cryptoWaitReady();
const keyring = new Keyring({ type: 'sr25519', ss58Format: SS58 });

if (existsSync(FILE)) {
  const saved = JSON.parse(readFileSync(FILE, 'utf8'));
  if (!mnemonicValidate(saved.mnemonic)) {
    console.error(`${FILE} exists but its mnemonic is not valid — refusing to touch it.`);
    process.exit(1);
  }
  const pair = keyring.addFromUri(saved.mnemonic);
  console.log(`${FILE} already exists — reusing it, not generating a new key.\n`);
  console.log(`  address   ${pair.address}`);
  if (SHOW) console.log(`  mnemonic  ${saved.mnemonic}`);
  else console.log(`  mnemonic  (in ${FILE}; pass --show to print it)`);
  process.exit(0);
}

const mnemonic = mnemonicGenerate(12);
const pair = keyring.addFromUri(mnemonic);

writeFileSync(
  FILE,
  JSON.stringify(
    {
      address: pair.address,
      ss58Format: SS58,
      type: 'sr25519',
      mnemonic,
      created: new Date().toISOString(),
      note: 'Back this up offline. Anyone with this phrase controls the account.',
    },
    null,
    2,
  ),
  { mode: 0o600 },
);

console.log('new PortalDot account\n');
console.log(`  address   ${pair.address}`);
console.log(`  saved to  ${FILE}  (gitignored, not printed here)`);
console.log('\nShare the address. Never the file.');
console.log('Back the file up somewhere offline before any funds arrive.');
