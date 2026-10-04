/** Read the harness contracts (managed by copilot-harness). */
import { existsSync, readFileSync } from 'node:fs';
import { resolve } from 'node:path';

export const projectRoot = resolve(process.env.HARNESS_PROJECT_ROOT ?? resolve(__dirname, '..', '..'));

function readJson<T>(rel: string, fallback: T): T {
  const path = resolve(projectRoot, rel);
  return existsSync(path) ? (JSON.parse(readFileSync(path, 'utf8')) as T) : fallback;
}

export interface AppContract {
  stack: string;
  install: string[];
  build: string[];
  start: string;
  base_url: string;
  health_path: string;
  env: Record<string, string>;
  startup_timeout_seconds: number;
}

export interface Contract {
  api?: { method: string; path: string; auth?: string }[];
  ui?: { routes?: { path: string; name: string }[]; testids?: Record<string, string> };
  test_hooks?: { reset?: string | null; seed_users?: Record<string, unknown>[] };
  [key: string]: unknown;
}

export const appContract = readJson<AppContract | null>('harness/app-contract.json', null);
export const contract = readJson<Contract>('harness/contract.json', {});
const plan = readJson<{ tests: { id: string; status?: string }[] }>('harness/test_plan.json', { tests: [] });
export const retiredIds = plan.tests.filter((t) => t.status === 'retired').map((t) => t.id);

/** Base URL of the app under test; APP_PORT (set by the harness/CI) overrides the contract port. */
export function appBaseURL(): string {
  const base = new URL(appContract?.base_url ?? 'http://127.0.0.1:3000');
  if (process.env.APP_PORT) base.port = process.env.APP_PORT;
  return base.origin;
}
