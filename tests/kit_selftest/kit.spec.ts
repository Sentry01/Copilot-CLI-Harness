/**
 * Self-test of the acceptance support kit: every helper must pass on a good endpoint and
 * fail on a deliberately broken one (test.fail = "this helper must detect the defect").
 */
import {
  test, expect, uniqueId, VIEWPORTS,
  measureLatency, measureConcurrentLatency, expectLatency, measurePageLoad, expectPageTiming, expectWithinBudget, stats,
  expectNoA11yViolations, expectKeyboardReachable, expectVisibleFocus, expectNoHorizontalOverflow, expectMinTargetSize,
  expectSecurityHeaders, expectSecureCookies, installXssTrap, expectNoXssExecuted, XSS_PAYLOADS,
  expectNoErrorLeak, expectRejected, expectAuthRequired, expectRateLimited, resetHookRequest, appBaseURL,
} from '../../support';

// What a request to any host but the app looks like under the config's network guard.
const BLOCKED = /127\.0\.0\.1:9\b|ERR_PROXY_CONNECTION_FAILED/;

test.describe('good', () => {
  test('data fixture is unique and stable within a test', async ({ data }, testInfo) => {
    const a = data.email();
    const b = data.email();
    expect(a).not.toBe(b);
    expect(a).toMatch(/@example\.test$/);
    expect(uniqueId(testInfo)).toBe(uniqueId(testInfo));
  });

  test('perf helpers', async ({ request, page }) => {
    const s = await measureLatency(request, { path: '/api/items' }, { samples: 8, warmup: 2 });
    expect(s.samples).toHaveLength(8);
    expectLatency(s, { p95: 500 });
    const c = await measureConcurrentLatency(request, { path: '/api/items' }, { concurrency: 5, rounds: 2 });
    expect(c.samples).toHaveLength(10);
    const { median, runs } = await measurePageLoad(page, '/', { samples: 3 });
    expect(runs).toHaveLength(3);
    expect(median.fcp).toBeGreaterThan(0);
    expect(median.lcp).toBeGreaterThan(0);
    expectPageTiming(median, { lcp: 4000, cls: 0.1, transferKb: 500 });
    expect(stats([1, 2, 3, 4, 100]).p50).toBe(3);
  });

  test('a11y helpers', async ({ page }) => {
    await page.goto('/');
    await expectNoA11yViolations(page);
    await expectKeyboardReachable(page, page.getByRole('button', { name: 'Go' }));
    await expectVisibleFocus(page, page.getByLabel('Search'));
    await expectMinTargetSize(page.getByRole('button', { name: 'Go' }));
    await page.setViewportSize(VIEWPORTS.mobile);
    await expectNoHorizontalOverflow(page);
  });

  test('only the app is reachable', async ({ page, request, playwright, browser }) => {
    expect((await request.get('/health')).ok()).toBe(true);
    await expect(request.get('http://example.com/')).rejects.toThrow(BLOCKED);
    await expect(request.get('http://203.0.113.7/')).rejects.toThrow(BLOCKED);
    await expect(page.goto('https://example.com/')).rejects.toThrow(BLOCKED);
    const api = await playwright.request.newContext();
    await expect(api.get('http://example.com/')).rejects.toThrow(BLOCKED);
    await api.dispose();
    const ctx = await browser.newContext();
    await expect((await ctx.newPage()).goto('http://203.0.113.7/')).rejects.toThrow(BLOCKED);
    await expect(ctx.request.get('http://example.com/')).rejects.toThrow(BLOCKED);
    await ctx.close();
  });

  test('reset hook stays on the app origin', async () => {
    expect(resetHookRequest('POST /__test__/reset')).toEqual({ method: 'POST', url: `${appBaseURL()}/__test__/reset` });
  });

  test('security helpers', async ({ page, request }) => {
    expectSecurityHeaders(await request.get('/'));
    expectSecureCookies(await request.post('/login'), { names: ['sid'] });
    await installXssTrap(page);
    for (const p of XSS_PAYLOADS) {
      await page.goto(`/search?q=${encodeURIComponent(p)}`);
      await expectNoXssExecuted(page);
    }
    await expectNoErrorLeak(await request.post('/api/items', { data: {} }));
    expectRejected(await request.post('/api/items', { data: {} }));
    expectAuthRequired(await request.get('/api/private'));
    await expectRateLimited(() => request.post('/login'), 10);
  });
});

test.describe('detects defects', () => {
  test.fail('latency budget', async ({ request }) => {
    expectLatency(await measureLatency(request, { path: '/api/items' }, { samples: 3, warmup: 0 }), { p95: 0.0001 });
  });
  test.fail('budget helper', async () => { expectWithinBudget(120, 100, 'x'); });
  test.fail('missing security headers', async ({ request }) => { expectSecurityHeaders(await request.get('/naked')); });
  test.fail('weak cookie', async ({ request }) => { expectSecureCookies(await request.post('/weak-login')); });
  test.fail('invalid SameSite value', async ({ request }) => { expectSecureCookies(await request.post('/bogus-samesite-login')); });
  test.fail('permissive frame-ancestors', async ({ request }) => {
    const res = await request.get('/frame-star');
    expectSecurityHeaders(res, { csp: false });
  });
  test.fail('fast errors under load are not served requests', async ({ request }) => {
    await measureConcurrentLatency(request, { path: '/api/private' }, { concurrency: 3, rounds: 1 });
  });
  test.fail('a page that never paints', async ({ page }) => {
    await measurePageLoad(page, '/blank', { samples: 1, warmup: 0 });
  });
  test.fail('reflected XSS', async ({ page }) => {
    await installXssTrap(page);
    await page.goto(`/unsafe-search?q=${encodeURIComponent(XSS_PAYLOADS[1])}`);
    await expectNoXssExecuted(page);
  });
  test.fail('stack trace leak', async ({ request }) => { await expectNoErrorLeak(await request.get('/leaky')); });
  test.fail('no auth required', async ({ request }) => { expectAuthRequired(await request.get('/api/items')); });
  test.fail('no rate limit', async ({ request }) => { await expectRateLimited(() => request.get('/api/items'), 5); });
  test.fail('horizontal overflow', async ({ page }) => {
    await page.setViewportSize(VIEWPORTS.mobile);
    await page.goto('/wide');
    await expectNoHorizontalOverflow(page);
  });
  test.fail('protocol-relative reset hook', async () => { resetHookRequest('POST //external.example/reset'); });
  test.fail('backslash reset hook', async () => { resetHookRequest('POST /\\external.example/reset'); });
  test.fail('absolute reset hook', async () => { resetHookRequest('POST https://external.example/reset'); });
  test.fail('a11y violation', async ({ page }) => {
    await page.setContent('<html><body><img src="x.png"><input></body></html>');
    await expectNoA11yViolations(page, { impacts: ['minor', 'moderate', 'serious', 'critical'] });
  });
});
