/** Black-box security checks and attack payloads. */
import { expect, type APIResponse, type Page } from '@playwright/test';

/** Every payload sets window.__xss = 1 if it executes. */
export const XSS_PAYLOADS = [
  '<script>window.__xss=1</script>',
  '"><img src=x onerror="window.__xss=1">',
  "'><svg onload=window.__xss=1>",
  '<iframe srcdoc="<script>parent.__xss=1</script>"></iframe>',
  'javascript:window.__xss=1',
] as const;

export const SQLI_PAYLOADS = ["' OR '1'='1", "'; DROP TABLE users; --", '1 OR 1=1', '" OR ""="', "admin'--"] as const;
export const PATH_TRAVERSAL_PAYLOADS = ['../../../../etc/passwd', '..%2f..%2f..%2fetc%2fpasswd', '....//....//etc/passwd'] as const;
export const longString = (n = 10_000): string => 'A'.repeat(n);

/** Call before navigating: records script execution and unexpected dialogs. */
export async function installXssTrap(page: Page): Promise<void> {
  await page.addInitScript(() => {
    (window as unknown as { __xss: number }).__xss = 0;
  });
  page.on('dialog', (d) => {
    (page as unknown as { __dialog: boolean }).__dialog = true;
    d.dismiss().catch(() => {});
  });
}

export async function expectNoXssExecuted(page: Page): Promise<void> {
  const flag = await page.evaluate(() => (window as unknown as { __xss?: number }).__xss ?? 0);
  expect(flag, 'an injected payload executed (XSS)').toBe(0);
  expect((page as unknown as { __dialog?: boolean }).__dialog ?? false, 'a dialog opened (XSS)').toBe(false);
}

export function expectSecurityHeaders(res: APIResponse, opts: { csp?: boolean; hsts?: boolean } = {}): void {
  const h = res.headers();
  const csp = h['content-security-policy'] ?? '';
  const problems: string[] = [];
  if ((opts.csp ?? true) && !csp) problems.push('missing Content-Security-Policy');
  if ((h['x-content-type-options'] ?? '').toLowerCase() !== 'nosniff') problems.push('X-Content-Type-Options must be nosniff');
  if (!/frame-ancestors/i.test(csp) && !/^(deny|sameorigin)$/i.test(h['x-frame-options'] ?? '')) {
    problems.push('clickjacking protection missing (CSP frame-ancestors or X-Frame-Options)');
  }
  if (!h['referrer-policy']) problems.push('missing Referrer-Policy');
  if (h['x-powered-by']) problems.push(`X-Powered-By leaks the implementation: ${h['x-powered-by']}`);
  if (/\d/.test(h['server'] ?? '')) problems.push(`Server header leaks a version: ${h['server']}`);
  if (opts.hsts && !h['strict-transport-security']) problems.push('missing Strict-Transport-Security');
  expect(problems, `security headers of ${res.url()}`).toEqual([]);
}

export function setCookies(res: APIResponse): string[] {
  return res.headersArray().filter((h) => h.name.toLowerCase() === 'set-cookie').map((h) => h.value);
}

/** Session cookies must be HttpOnly + SameSite=Lax/Strict (+ Secure over https). */
export function expectSecureCookies(res: APIResponse, opts: { names?: string[]; requireSecure?: boolean } = {}): void {
  const cookies = setCookies(res);
  const problems: string[] = [];
  const seen = new Set<string>();
  for (const c of cookies) {
    const [pair, ...attrs] = c.split(';');
    const name = pair.split('=')[0].trim();
    if (opts.names && !opts.names.includes(name)) continue;
    seen.add(name);
    const a = attrs.map((s) => s.trim().toLowerCase());
    if (!a.includes('httponly')) problems.push(`${name}: missing HttpOnly`);
    const sameSite = a.find((x) => x.startsWith('samesite='));
    if (!sameSite || sameSite === 'samesite=none') problems.push(`${name}: SameSite must be Lax or Strict`);
    if ((opts.requireSecure ?? res.url().startsWith('https:')) && !a.includes('secure')) problems.push(`${name}: missing Secure`);
  }
  for (const n of opts.names ?? []) if (!seen.has(n)) problems.push(`${n}: cookie was not set`);
  if (!opts.names && cookies.length === 0) problems.push('no Set-Cookie header in response');
  expect(problems, 'cookie security flags').toEqual([]);
}

const LEAK_PATTERNS: RegExp[] = [
  /\bat\s+\S+\s+\(\S+:\d+:\d+\)/,
  /Traceback \(most recent call last\)/,
  /SQLITE_[A-Z]+/,
  /syntax error at or near/i,
  /node_modules\//,
  /\b(ReferenceError|TypeError|SyntaxError):/,
  /ORA-\d{5}/,
  /SQLSTATE\[/,
  /Exception in thread/,
];

/** Error responses must not leak stack traces, SQL errors or file paths. */
export async function expectNoErrorLeak(res: APIResponse): Promise<void> {
  const body = await res.text();
  const hit = LEAK_PATTERNS.find((p) => p.test(body));
  expect(hit ? `response leaks internals (${hit}): ${body.slice(0, 300)}` : '', `${res.url()}`).toBe('');
  expect(res.status(), `${res.url()} must not fail with a server error`).toBeLessThan(500);
}

export function expectRejected(res: APIResponse, allowed: number[] = [400, 401, 403, 404, 409, 413, 415, 422]): void {
  expect(allowed, `expected a client error for ${res.url()}, got ${res.status()}`).toContain(res.status());
}

export function expectAuthRequired(res: APIResponse): void {
  expect([401, 403], `expected 401/403 for ${res.url()}, got ${res.status()}`).toContain(res.status());
}

/** Repeating `send` must eventually be throttled with 429. */
export async function expectRateLimited(send: () => Promise<APIResponse>, attempts = 30): Promise<void> {
  const statuses: number[] = [];
  for (let i = 0; i < attempts; i++) {
    const s = (await send()).status();
    statuses.push(s);
    if (s === 429) break;
  }
  expect(statuses, `no 429 after ${attempts} attempts`).toContain(429);
}
