import { expect, test } from '@playwright/test';
import { mkdirSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

/**
 * Documentation screenshots, taken from the running stack at desktop width.
 * Opt in with SCREENSHOTS=1 (they are not assertions about behaviour):
 *   SCREENSHOTS=1 PLAYWRIGHT_CHANNEL=chrome npx playwright test screenshots
 * Output: docs/screenshots/<page>.png (referenced from the README).
 */
const HERE = path.dirname(fileURLToPath(import.meta.url));
const OUT = path.resolve(HERE, '../../docs/screenshots');

test.describe('documentation screenshots', () => {
  test.skip(!process.env.SCREENSHOTS, 'set SCREENSHOTS=1 to capture documentation screenshots');
  test.use({ viewport: { width: 1440, height: 900 } });

  test.beforeAll(() => {
    mkdirSync(OUT, { recursive: true });
  });

  const shoot = async (page: import('@playwright/test').Page, name: string, fullPage = true) => {
    await page.waitForTimeout(1500);
    await page.screenshot({ path: path.join(OUT, `${name}.png`), fullPage });
  };

  test('overview', async ({ page }) => {
    await page.goto('/');
    await expect(page.getByText(/API ok/i)).toBeVisible();
    await page.waitForTimeout(3000);
    await shoot(page, 'overview');
  });

  test('hazard map', async ({ page }) => {
    await page.goto('/map');
    await page.waitForTimeout(6000);
    await shoot(page, 'hazard-map', false);
  });

  test('property detail', async ({ page, request }) => {
    const r = await request.get('http://localhost:8000/api/v1/properties?q=paradise&limit=1');
    const items = (await r.json()).items as { id: number }[];
    test.skip(!items?.length, 'no Paradise address ingested');
    await page.goto(`/properties/${items[0].id}`);
    await expect(page.getByText('Prediction inputs', { exact: false })).toBeVisible({ timeout: 30_000 });
    await page.waitForTimeout(4000);
    await shoot(page, 'property-detail');
    // the app scrolls inside <main>, so a full-page capture stops at the viewport: scroll the
    // block of interest into view and capture the viewport
    await page.getByText('Prediction inputs', { exact: false }).first().scrollIntoViewIfNeeded();
    await shoot(page, 'property-prediction-inputs', false);
  });

  test('model analytics', async ({ page }) => {
    await page.goto('/models');
    await expect(page.getByRole('heading', { name: 'Model analytics' })).toBeVisible();
    await page.waitForTimeout(4000);
    await shoot(page, 'model-analytics');
  });

  test('portfolio with stress test', async ({ page, request }) => {
    const r = await request.get('http://localhost:8000/api/v1/portfolio');
    const list = (await r.json()) as { portfolio_id: string; status: string; name: string | null }[];
    const done = list.find((p) => p.status === 'completed');
    test.skip(!done, 'no completed portfolio');
    await page.goto('/portfolio');
    await page.getByText(done!.name ?? done!.portfolio_id, { exact: false }).first().click();
    await expect(page.getByRole('heading', { name: 'Historical stress tests' })).toBeVisible({ timeout: 30_000 });
    await expect(page.locator('div.panel', { hasText: 'Exposures inside' }).first()).toBeVisible({ timeout: 30_000 });
    await page.waitForTimeout(5000);
    await shoot(page, 'portfolio-analysis');
    await page.getByRole('heading', { name: 'Location accumulation' }).scrollIntoViewIfNeeded();
    await shoot(page, 'portfolio-accumulation', false);
    await page.locator('div.panel', { hasText: 'Exposures inside' }).first().scrollIntoViewIfNeeded();
    await page.waitForTimeout(2500);
    await shoot(page, 'portfolio-stress-test', false);
    await page.getByRole('heading', { name: 'Financial scenarios' }).scrollIntoViewIfNeeded();
    await shoot(page, 'portfolio-financial', false);
  });

  test('data quality', async ({ page }) => {
    await page.goto('/data-quality');
    await page.waitForTimeout(4000);
    await shoot(page, 'data-quality');
  });
});
