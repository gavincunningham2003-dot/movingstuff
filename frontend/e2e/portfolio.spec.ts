import { expect, test, type APIRequestContext } from '@playwright/test';
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const EXAMPLE_CSV = path.resolve(HERE, '../../data/samples/example_portfolio.csv');
const API = process.env.E2E_API_URL ?? 'http://localhost:8000';

async function apiHealthy(request: APIRequestContext): Promise<boolean> {
  try {
    const r = await request.get(`${API}/api/v1/health`);
    return r.ok();
  } catch {
    return false;
  }
}

test.describe('dashboard', () => {
  test.beforeEach(async ({ request }) => {
    test.skip(!(await apiHealthy(request)), 'API is not running on :8000');
  });

  test('shell loads and reports the API as reachable', async ({ page }) => {
    await page.goto('/');
    await expect(page.getByText('CATRIX', { exact: true }).first()).toBeVisible();
    await expect(page.getByText(/API ok/i)).toBeVisible();
  });

  test('portfolio page serves the CSV contract and the column mapping', async ({ page }) => {
    await page.goto('/portfolio');
    await expect(page.getByRole('heading', { name: 'Portfolio analysis' })).toBeVisible();
    // the mapping panel is driven by GET /portfolio/csv-contract
    await expect(page.getByText('Contract served by the API', { exact: false })).toBeVisible();
    for (const col of ['latitude', 'longitude', 'property_id', 'insured_value', 'limit', 'deductible', 'contents_value']) {
      await expect(page.locator('span.font-mono', { hasText: new RegExp(`^${col}$`) }).first()).toBeVisible();
    }
  });

  test('uploading the example portfolio classifies rows and reaches a completed analysis', async ({ page }) => {
    await page.goto('/portfolio');
    const csv = readFileSync(EXAMPLE_CSV);
    await page.setInputFiles('input[type="file"]', {
      name: 'example_portfolio.csv',
      mimeType: 'text/csv',
      buffer: csv,
    });
    await page.getByPlaceholder('e.g. Q3 homeowners book').fill('e2e example');
    await page.getByRole('button', { name: /Upload & analyse/ }).click();

    const processing = page.getByRole('heading', { name: '2. Processing' });
    await expect(processing).toBeVisible();
    // tiles reflect the server-side validation of the 25-row example
    const tile = (label: string) => page.locator('div.panel', { hasText: label }).first();
    await expect(tile('Rows')).toContainText('25');
    await expect(tile('Valid')).toContainText('24');
    await expect(tile('Invalid')).toContainText('1');
    await expect(tile('Exact duplicates')).toContainText('1');
    await expect(tile('Shared locations')).toContainText('1');
    await expect(tile('Distinct locations')).toContainText('22');
    // the validation list distinguishes row errors from field warnings
    await page.getByText(/Validation issues/).click();
    await expect(page.getByText(/malformed insured_value/)).toBeVisible();
    await expect(page.getByText('impossible coordinate values')).toBeVisible();

    // enrichment completes (hazard coverage depends on what is ingested; counts do not)
    await expect(page.getByRole('heading', { name: 'Location accumulation' })).toBeVisible({ timeout: 110_000 });
    await expect(page.getByRole('heading', { name: 'Historical stress tests' })).toBeVisible();
    await expect(page.getByRole('heading', { name: 'Financial scenarios' })).toBeVisible();
    await expect(page.locator('div.panel', { hasText: 'Sum insured (TIV)' }).first()).toContainText('$');
  });
});
