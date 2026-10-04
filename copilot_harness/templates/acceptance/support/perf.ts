/**
 * Performance measurement with explicit budgets.
 *
 * Measurements use warm-up runs and percentiles over many samples so a single slow
 * request cannot fail a test. PERF_BUDGET_MULTIPLIER (default 1) scales every budget
 * for slower CI hardware without editing frozen tests.
 */
import { expect, type APIRequestContext, type Page } from '@playwright/test';

export const PERF_MULTIPLIER = Number(process.env.PERF_BUDGET_MULTIPLIER ?? '1');

export interface Stats {
  samples: number[];
  p50: number;
  p95: number;
  p99: number;
  max: number;
  mean: number;
}

export function percentile(values: number[], p: number): number {
  if (!values.length) return Number.NaN;
  const sorted = [...values].sort((a, b) => a - b);
  const idx = Math.min(sorted.length - 1, Math.max(0, Math.ceil((p / 100) * sorted.length) - 1));
  return sorted[idx];
}

export function stats(samples: number[]): Stats {
  return {
    samples,
    p50: percentile(samples, 50),
    p95: percentile(samples, 95),
    p99: percentile(samples, 99),
    max: Math.max(...samples),
    mean: samples.reduce((a, b) => a + b, 0) / (samples.length || 1),
  };
}

/** Assert `actual <= budget * PERF_BUDGET_MULTIPLIER`. */
export function expectWithinBudget(actual: number, budget: number, label: string, unit = 'ms'): void {
  const limit = budget * PERF_MULTIPLIER;
  expect(
    actual,
    `${label}: ${actual.toFixed(1)}${unit} exceeds budget ${limit}${unit} (budget ${budget}${unit} x ${PERF_MULTIPLIER})`,
  ).toBeLessThanOrEqual(limit);
}

export interface RequestSpec {
  method?: string;
  path: string;
  data?: unknown;
  headers?: Record<string, string>;
  /** Expected status; default: any 2xx. */
  status?: number;
}

/** Time `samples` sequential requests (after `warmup` unmeasured ones). */
export async function measureLatency(
  api: APIRequestContext,
  req: RequestSpec,
  opts: { samples?: number; warmup?: number } = {},
): Promise<Stats> {
  const once = async (): Promise<number> => {
    const t0 = performance.now();
    const res = await api.fetch(req.path, { method: req.method ?? 'GET', data: req.data, headers: req.headers });
    await res.body();
    const elapsed = performance.now() - t0;
    if (req.status !== undefined) expect(res.status(), `${req.method ?? 'GET'} ${req.path}`).toBe(req.status);
    else expect(res.ok(), `${req.method ?? 'GET'} ${req.path} returned ${res.status()}`).toBe(true);
    return elapsed;
  };
  for (let i = 0; i < (opts.warmup ?? 3); i++) await once();
  const out: number[] = [];
  for (let i = 0; i < (opts.samples ?? 20); i++) out.push(await once());
  return stats(out);
}

/** Time `concurrency` parallel requests per round for `rounds` rounds. */
export async function measureConcurrentLatency(
  api: APIRequestContext,
  req: RequestSpec,
  opts: { concurrency?: number; rounds?: number } = {},
): Promise<Stats> {
  const out: number[] = [];
  for (let r = 0; r < (opts.rounds ?? 5); r++) {
    const batch = Array.from({ length: opts.concurrency ?? 10 }, async () => {
      const t0 = performance.now();
      const res = await api.fetch(req.path, { method: req.method ?? 'GET', data: req.data, headers: req.headers });
      await res.body();
      expect(res.status() < 500, `${req.path} returned ${res.status()} under load`).toBe(true);
      return performance.now() - t0;
    });
    out.push(...(await Promise.all(batch)));
  }
  return stats(out);
}

export function expectLatency(
  s: Stats,
  budget: { p50?: number; p95?: number; p99?: number; max?: number },
  label = 'latency',
): void {
  for (const key of ['p50', 'p95', 'p99', 'max'] as const) {
    const b = budget[key];
    if (b !== undefined) expectWithinBudget(s[key], b, `${label} ${key}`);
  }
}

export interface PageTiming {
  ttfb: number;
  fcp: number;
  lcp: number;
  domContentLoaded: number;
  load: number;
  cls: number;
  transferKb: number;
}

/**
 * Navigate to `path` `samples` times (after `warmup` loads) and return the median of each
 * metric. Measures a warm-cache load in the test's browser context.
 */
export async function measurePageLoad(
  page: Page,
  path: string,
  opts: { samples?: number; warmup?: number } = {},
): Promise<{ median: PageTiming; runs: PageTiming[] }> {
  await page.addInitScript(() => {
    const w = window as unknown as { __lcp: number; __cls: number };
    w.__lcp = 0;
    w.__cls = 0;
    new PerformanceObserver((list) => {
      for (const e of list.getEntries()) w.__lcp = e.startTime;
    }).observe({ type: 'largest-contentful-paint', buffered: true });
    new PerformanceObserver((list) => {
      for (const e of list.getEntries() as unknown as { hadRecentInput: boolean; value: number }[]) {
        if (!e.hadRecentInput) w.__cls += e.value;
      }
    }).observe({ type: 'layout-shift', buffered: true });
  });
  const runs: PageTiming[] = [];
  for (let i = 0; i < (opts.warmup ?? 1) + (opts.samples ?? 5); i++) {
    await page.goto(path, { waitUntil: 'load' });
    // On fast pages `load` fires before the first paint; wait for it (no FCP = blank page).
    await page
      .waitForFunction(() => performance.getEntriesByName('first-contentful-paint').length > 0, undefined, {
        timeout: 10_000,
      })
      .catch(() => {});
    const t = await page.evaluate(async () => {
      // Let pending LCP / layout-shift observer callbacks run.
      await new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(() => r(null))));
      const nav = performance.getEntriesByType('navigation')[0] as PerformanceNavigationTiming;
      const fcp = performance.getEntriesByName('first-contentful-paint')[0]?.startTime ?? 0;
      const resources = performance.getEntriesByType('resource') as PerformanceResourceTiming[];
      const transfer = resources.reduce((acc, r) => acc + (r.transferSize || 0), nav.transferSize || 0);
      const w = window as unknown as { __lcp: number; __cls: number };
      return {
        ttfb: nav.responseStart,
        fcp,
        lcp: w.__lcp || fcp,
        domContentLoaded: nav.domContentLoadedEventEnd,
        load: nav.loadEventEnd,
        cls: w.__cls,
        transferKb: transfer / 1024,
      };
    });
    if (i >= (opts.warmup ?? 1)) runs.push(t);
  }
  const median = {} as PageTiming;
  for (const key of Object.keys(runs[0]) as (keyof PageTiming)[]) {
    median[key] = percentile(runs.map((r) => r[key]), 50);
  }
  return { median, runs };
}

export function expectPageTiming(
  t: PageTiming,
  budget: Partial<Record<keyof PageTiming, number>>,
  label = 'page load',
): void {
  for (const [key, b] of Object.entries(budget) as [keyof PageTiming, number][]) {
    if (key === 'cls') {
      expect(t.cls, `${label} cumulative layout shift ${t.cls.toFixed(3)} exceeds ${b}`).toBeLessThanOrEqual(b);
    } else {
      expectWithinBudget(t[key], b, `${label} ${key}`, key === 'transferKb' ? 'KB' : 'ms');
    }
  }
}
