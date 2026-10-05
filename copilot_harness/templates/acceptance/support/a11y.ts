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

/**
 * Focusing `target` with the keyboard must visibly change it (WCAG 2.4.7). Compares screenshots
 * of the element and its surroundings before and after focus, so any real indicator counts
 * (outline, shadow, border, background) and a transparent outline or a permanent decorative
 * shadow does not. `minChangedPixels` is how many pixels must change clearly.
 */
export async function expectVisibleFocus(page: Page, target: Locator, opts: { minChangedPixels?: number } = {}): Promise<void> {
  await target.scrollIntoViewIfNeeded();
  await page.evaluate(() => (document.activeElement as HTMLElement | null)?.blur());
  const shot = async () =>
    page.screenshot({ clip: await aroundElement(target, 8), animations: 'disabled', caret: 'hide' });
  const unfocused = await shot();
  await expectKeyboardReachable(page, target);
  const focused = await shot();
  const changed = await changedPixels(page, unfocused, focused);
  expect(
    changed,
    'focusing the element must visibly change it (outline, shadow, border or background); ' +
      'a transparent outline or an always-on style is not a focus indicator',
  ).toBeGreaterThanOrEqual(opts.minChangedPixels ?? 16);
}

/** The element's box plus `pad` px on each side (outlines and shadows are drawn outside the box). */
async function aroundElement(target: Locator, pad: number) {
  const box = await target.boundingBox();
  expect(box, 'target must be visible').not.toBeNull();
  const x = Math.max(0, box!.x - pad);
  const y = Math.max(0, box!.y - pad);
  return { x, y, width: box!.x + box!.width + pad - x, height: box!.y + box!.height + pad - y };
}

/** Pixels whose colour differs clearly between two PNGs, decoded in a blank page of the same browser. */
async function changedPixels(page: Page, a: Buffer, b: Buffer): Promise<number> {
  const scratch = await page.context().newPage();
  try {
    return await scratch.evaluate(async ([a64, b64]) => {
      const decode = async (b64: string) => {
        const bytes = Uint8Array.from(atob(b64), (c) => c.charCodeAt(0));
        const bitmap = await createImageBitmap(new Blob([bytes], { type: 'image/png' }));
        const canvas = new OffscreenCanvas(bitmap.width, bitmap.height);
        const ctx = canvas.getContext('2d')!;
        ctx.drawImage(bitmap, 0, 0);
        return ctx.getImageData(0, 0, bitmap.width, bitmap.height);
      };
      const [x, y] = await Promise.all([decode(a64), decode(b64)]);
      if (x.width !== y.width || x.height !== y.height) return x.width * x.height;
      let n = 0;
      for (let i = 0; i < x.data.length; i += 4) {
        const d = Math.max(
          Math.abs(x.data[i] - y.data[i]),
          Math.abs(x.data[i + 1] - y.data[i + 1]),
          Math.abs(x.data[i + 2] - y.data[i + 2]),
        );
        if (d >= 48) n++;
      }
      return n;
    }, [a.toString('base64'), b.toString('base64')] as const);
  } finally {
    await scratch.close();
  }
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
