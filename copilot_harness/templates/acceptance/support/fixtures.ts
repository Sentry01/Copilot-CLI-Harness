/**
 * Base fixtures for every acceptance test.
 *
 *  - `data`: unique, readable test data per test and run (no Math.random / Date.now in specs).
 *  - automatic state reset before each test when the contract declares `test_hooks.reset`
 *    (e.g. "POST /__test__/reset"), so tests never depend on each other.
 */
import { createHash } from 'node:crypto';
import { test as base, expect, type TestInfo } from '@playwright/test';
import { appBaseURL, contract } from './contract';

export function uniqueId(testInfo: TestInfo, salt = ''): string {
  const seed = `${process.env.HARNESS_RUN_ID ?? 'local'}|${testInfo.testId}|${testInfo.repeatEachIndex}|${testInfo.retry}|${salt}`;
  return 't' + createHash('sha256').update(seed).digest('hex').slice(0, 10);
}

export class TestData {
  private counter = 0;
  constructor(readonly prefix: string) {}
  /** A unique token, e.g. `t1a2b3c4d5-order-1`. */
  id(label = 'x'): string {
    this.counter += 1;
    return `${this.prefix}-${label}-${this.counter}`;
  }
  email(label = 'user'): string {
    return `${this.id(label)}@example.test`;
  }
  name(label = 'Name'): string {
    return `${label} ${this.id('n')}`;
  }
  /** A password that satisfies common complexity rules. */
  password(): string {
    return `Pw-${this.id('p')}-Aa1!`;
  }
}

/**
 * Resolve the contract's reset hook ("POST /__test__/reset") to an absolute URL on the app.
 * Anything that would leave the app's origin ("//host/x", "/\\host", a full URL) is refused.
 */
export function resetHookRequest(hook: string, baseURL = appBaseURL()): { method: string; url: string } {
  const m = /^(GET|POST|PUT|PATCH|DELETE) (\/(?!\/)[^\s\\]*)$/.exec(hook.trim());
  if (!m) throw new Error(`test_hooks.reset must look like "POST /__test__/reset" (got "${hook}")`);
  const url = new URL(m[2], baseURL);
  if (url.origin !== new URL(baseURL).origin) {
    throw new Error(`test_hooks.reset "${hook}" resolves outside the app (${url.origin})`);
  }
  return { method: m[1], url: url.href };
}

type Fixtures = { data: TestData; resetAppState: void };

export const test = base.extend<Fixtures>({
  data: async ({}, use, testInfo) => {
    await use(new TestData(uniqueId(testInfo)));
  },
  resetAppState: [
    async ({ request }, use) => {
      const hook = contract.test_hooks?.reset;
      if (hook && process.env.HARNESS_NO_RESET !== '1') {
        const { method, url } = resetHookRequest(hook);
        const res = await request.fetch(url, { method, maxRedirects: 0 });
        if (!res.ok()) {
          throw new Error(`state reset hook "${hook}" failed with ${res.status()}; the app must implement it in test mode`);
        }
      }
      await use();
    },
    { auto: true },
  ],
});

export { expect };
