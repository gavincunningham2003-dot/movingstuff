import { defineConfig, devices } from '@playwright/test';

/**
 * End-to-end tests drive the real dashboard against a running API (the Vite dev server
 * proxies /api to http://localhost:8000). Start the API first:
 *   catrix api            # from the repository root, database migrated
 * then `npx playwright test` (or `PLAYWRIGHT_CHANNEL=chrome npx playwright test`). CI starts both and runs against an empty, migrated database.
 */
export default defineConfig({
  testDir: './e2e',
  timeout: 120_000,
  expect: { timeout: 15_000 },
  fullyParallel: false,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [['list'], ['html', { open: 'never' }]] : 'list',
  use: {
    baseURL: process.env.E2E_BASE_URL ?? 'http://localhost:5173',
    // PLAYWRIGHT_CHANNEL=chrome|msedge uses an installed browser instead of the bundled
    // Chromium (needed on machines where the download cannot launch).
    ...(process.env.PLAYWRIGHT_CHANNEL ? { channel: process.env.PLAYWRIGHT_CHANNEL } : {}),
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  webServer: {
    command: 'npm run dev',
    url: 'http://localhost:5173',
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
});
