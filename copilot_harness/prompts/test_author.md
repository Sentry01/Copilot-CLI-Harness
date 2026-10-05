# Task: implement acceptance tests (before the feature exists)

Implement exactly these planned tests:

```json
$cases
```

Write them in these files (create or extend):
$files

Read `harness/contract.json` for routes, endpoints, accessible names, messages and test ids.
Read `acceptance/support/*.ts` for the helper kit. The app does not implement these features
yet, so the tests are expected to FAIL now. They must fail because the behaviour is missing,
not because the test is broken.

## Hard rules (checked automatically; violations are rejected)
- One `test()` per planned ID. The title starts with the ID: `test('FUNC-001: <plan title>', ...)`.
- Import only from the kit: `import { test, expect, ... } from '../../support';` (type-only
  imports from `@playwright/test` are allowed). No Node modules, `require()` or dynamic `import()`.
- Every test asserts the plan's `expected` oracle with `expect(...)` or a kit `expect*` helper.
  No tautologies (`expect(true)`).
- Forbidden: `.only`, `.skip`, `.fixme`, `test.fail`, `waitForTimeout`, `setTimeout`,
  `Math.random`, `page.pause`. Use web-first assertions
  (`await expect(locator).toBeVisible()`), `expect.poll`, or `page.waitForResponse`.
- Locators: `getByRole` / `getByLabel` / `getByText` with the contract's accessible names,
  or `getByTestId` with the contract's test ids. No CSS or XPath tied to layout.
- Relative URLs only (`page.goto('/register')`, `request.get('/api/...')`); `baseURL` is
  configured. Absolute or protocol-relative destinations are rejected, and the runner blocks
  every host except the app. External URLs may appear only as payload data
  (e.g. `request.get('/login?next=https://evil.example/')`).
- Unique data from the `data` fixture: `data.email()`, `data.name()`, `data.password()`,
  `data.id('order')`. State is reset before each test automatically. Do not depend on other
  tests.
- Do not write to anything except the spec files listed above.

## Kit reference (`acceptance/support`)
- Fixtures: `test(..., async ({ page, request, data }) => ...)`.
- Performance: `measureLatency(request, {method, path, data?, headers?, status?}, {samples, warmup})`
  → `{p50, p95, p99, max, mean}`. Also `measureConcurrentLatency(request, req, {concurrency, rounds})`,
  `expectLatency(stats, {p95: 300})`, `measurePageLoad(page, '/path', {samples})` →
  `{median: {ttfb, fcp, lcp, domContentLoaded, load, cls, transferKb}}`,
  `expectPageTiming(median, {lcp: 2500, cls: 0.1})` and `expectWithinBudget(value, budget, label)`.
  Use the plan's `threshold` value as the budget, unchanged. CI scales budgets by
  `PERF_BUDGET_MULTIPLIER`.
- Usability: `expectNoA11yViolations(page)`, `expectKeyboardReachable(page, locator)`,
  `expectVisibleFocus(page, locator)`, `expectNoHorizontalOverflow(page)`,
  `expectMinTargetSize(locator)` and `VIEWPORTS.mobile` (`await page.setViewportSize(VIEWPORTS.mobile)`).
- Security: `expectSecurityHeaders(response)`, `expectSecureCookies(response, {names})`,
  `installXssTrap(page)` + `expectNoXssExecuted(page)` with `XSS_PAYLOADS`, `SQLI_PAYLOADS`,
  `PATH_TRAVERSAL_PAYLOADS`, `longString(n)`, `expectNoErrorLeak(response)`,
  `expectRejected(response)`, `expectAuthRequired(response)` and `expectRateLimited(() => request.post(...))`.
- Seeded users (if any) are in `contract.test_hooks.seed_users`; import `contract` from the kit.

## Before you stop
1. `cd acceptance && npx playwright test --list` must list your tests with no errors.
2. `cd acceptance && npx tsc --noEmit -p .` must report no type errors in your files.
3. Optionally run your tests (`npx playwright test --grep "FUNC-001:"`). Check that they fail
   for the right reason, i.e. the missing behaviour, not a typo.
$feedback
