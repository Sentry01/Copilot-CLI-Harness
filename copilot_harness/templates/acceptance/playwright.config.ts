/**
 * Acceptance suite configuration — managed by copilot-harness. Do not edit by hand:
 * this file is part of the frozen suite (harness/tests.lock.json).
 *
 * Determinism choices:
 *  - retries: 0            flakiness must be visible, never retried away
 *  - workers: 1            tests share one app instance; the reset hook isolates state
 *  - fixed locale/timezone/viewport/colour scheme/reduced motion
 *  - the app is always started fresh by Playwright (webServer), on APP_PORT when set
 *  - retired tests (harness/test_plan.json status "retired") are excluded via grepInvert
 *  - tests reach only the app: every other host goes to a closed local port and fails
 */
import { randomBytes } from 'node:crypto';
import { defineConfig, devices } from '@playwright/test';
import { appBaseURL, appContract, projectRoot, retiredIds } from './support/contract';

process.env.HARNESS_RUN_ID ??= randomBytes(4).toString('hex');

const baseURL = appBaseURL();
const url = new URL(baseURL);
const escape = (s: string) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
// Applies to the browser and to the `request` fixture. `<-loopback>` drops Chromium's implicit
// loopback bypass, so only the app's host is reached directly: no CDNs or third-party services,
// and no exfiltration from a frozen test.
const appOnly = { server: 'http://127.0.0.1:9', bypass: ['<-loopback>', ...new Set(['localhost', '127.0.0.1', '[::1]', url.hostname])].join(',') };

export default defineConfig({
  testDir: './specs',
  outputDir: './test-results',
  fullyParallel: false,
  workers: Number(process.env.HARNESS_WORKERS ?? 1),
  retries: 0,
  forbidOnly: true,
  timeout: 30_000,
  expect: { timeout: 5_000 },
  grepInvert: retiredIds.length ? new RegExp(`(${retiredIds.map(escape).join('|')}):`) : undefined,
  reporter: [
    ['list'],
    ['json', { outputFile: 'reports/report.json' }],
    ['junit', { outputFile: 'reports/junit.xml' }],
    ['html', { open: 'never', outputFolder: 'playwright-report' }],
  ],
  use: {
    baseURL,
    proxy: appOnly,
    locale: 'en-US',
    timezoneId: 'UTC',
    colorScheme: 'light',
    reducedMotion: 'reduce',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    video: 'off',
    launchOptions: process.env.PW_CHROMIUM_EXECUTABLE
      ? { executablePath: process.env.PW_CHROMIUM_EXECUTABLE }
      : {},
  },
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'], viewport: { width: 1280, height: 800 } },
    },
  ],
  webServer: appContract
    ? {
        command: appContract.start,
        cwd: projectRoot,
        url: baseURL + appContract.health_path,
        reuseExistingServer: process.env.HARNESS_REUSE_SERVER === '1',
        timeout: appContract.startup_timeout_seconds * 1000,
        env: {
          ...appContract.env,
          PORT: url.port || '80',
          HOST: url.hostname,
          NODE_ENV: 'test',
          APP_ENV: 'test',
        },
        stdout: 'pipe',
        stderr: 'pipe',
      }
    : undefined,
});
