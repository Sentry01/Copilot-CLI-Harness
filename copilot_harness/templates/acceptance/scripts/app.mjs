#!/usr/bin/env node
/**
 * Run the app's install/build commands from harness/app-contract.json (used by CI).
 *   node acceptance/scripts/app.mjs install
 *   node acceptance/scripts/app.mjs build
 */
import { execSync } from 'node:child_process';
import { readFileSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..', '..');
const step = process.argv[2];
if (!['install', 'build'].includes(step)) {
  console.error('usage: app.mjs install|build');
  process.exit(2);
}
const contract = JSON.parse(readFileSync(join(ROOT, 'harness', 'app-contract.json'), 'utf8'));
for (const cmd of contract[step] ?? []) {
  console.log(`$ ${cmd}`);
  execSync(cmd, { cwd: ROOT, stdio: 'inherit', env: { ...process.env, ...(contract.env ?? {}), CI: '1' } });
}
