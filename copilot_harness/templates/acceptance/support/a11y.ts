/** Usability and accessibility checks (axe-core + keyboard + responsive layout). */
import AxeBuilder from '@axe-core/playwright';
import { expect, type Locator, type Page } from '@playwright/test';

export type Impact = 'minor' | 'moderate' | 'serious' | 'critical';

export const VIEWPORTS = {
  mobile: { width: 375, height: 812 },
  tablet: { width: 768, height: 1024 },
  desktop: { width: 1280, height: 800 },
} as const;

/** Fail on axe violations of the given impact levels (default: serious + critical, WCAG 2.1 AA). */
export async function expectNoA11yViolations(
  page: Page,
  opts: { impacts?: Impact[]; tags?: string[]; include?: string; exclude?: string[]; disableRules?: string[] } = {},
): Promise<void> {
  const impacts = opts.impacts ?? ['serious', 'critical'];
  let builder = new AxeBuilder({ page }).withTags(opts.tags ?? ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa']);
  if (opts.include) builder = builder.include(opts.include);
  for (const sel of opts.exclude ?? []) builder = builder.exclude(sel);
  if (opts.disableRules?.length) builder = builder.disableRules(opts.disableRules);
  const results = await builder.analyze();
  const violations = results.violations
    .filter((v) => impacts.includes(v.impact as Impact))
    .map((v) => `${v.impact} ${v.id}: ${v.help} -> ${v.nodes.slice(0, 3).map((n) => n.target.join(' ')).join(', ')}`);
  expect(violations, `accessibility violations on ${page.url()}`).toEqual([]);
}

/** Press Tab from the top of the page until `target` has focus. */
export async function expectKeyboardReachable(page: Page, target: Locator, maxTabs = 40): Promise<void> {
  await page.evaluate(() => (document.activeElement as HTMLElement | null)?.blur());
  for (let i = 0; i < maxTabs; i++) {
    await page.keyboard.press('Tab');
    if (await target.evaluate((el) => el === document.activeElement)) return;
  }
  expect(false, `element not reachable with ${maxTabs} Tab presses`).toBe(true);
}

/** The keyboard-focused element must show a visible focus indicator. */
export async function expectVisibleFocus(page: Page, target: Locator): Promise<void> {
  await expectKeyboardReachable(page, target);
  const visible = await target.evaluate((el) => {
    const s = getComputedStyle(el);
    const outline = s.outlineStyle !== 'none' && parseFloat(s.outlineWidth) > 0;
    const shadow = s.boxShadow !== 'none' && s.boxShadow !== '';
    return outline || shadow;
  });
  expect(visible, 'focused element must have a visible focus indicator (outline or box-shadow)').toBe(true);
}

export async function expectNoHorizontalOverflow(page: Page): Promise<void> {
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
  );
  expect(overflow, `page scrolls horizontally by ${overflow}px`).toBeLessThanOrEqual(1);
}

/** WCAG 2.2 target size (minimum): 24 x 24 CSS px. */
export async function expectMinTargetSize(target: Locator, min = 24): Promise<void> {
  const box = await target.boundingBox();
  expect(box, 'target must be visible').not.toBeNull();
  expect(Math.min(box!.width, box!.height), `target is ${box!.width}x${box!.height}px`).toBeGreaterThanOrEqual(min);
}
