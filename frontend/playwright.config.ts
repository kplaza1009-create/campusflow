import { defineConfig, devices } from '@playwright/test'

import { E2E_DATABASE_URL } from './e2e/env'

// The browser tests build the frontend and run it the way production does: served by
// FastAPI on one origin, with its security headers, against the tests' own database,
// so they never touch your development data.
const PORT = 8011
const DB_URL = E2E_DATABASE_URL

export default defineConfig({
  testDir: './e2e',
  timeout: 60_000,
  fullyParallel: false,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [['list'], ['html', { open: 'never' }]] : 'list',
  use: {
    baseURL: `http://127.0.0.1:${PORT}`,
    timezoneId: 'America/New_York',
    locale: 'en-US',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  projects: [
    {
      name: 'chromium',
      use: {
        ...devices['Desktop Chrome'],
        // Optional: point at an existing Chromium when Playwright's download is unavailable.
        launchOptions: process.env.PLAYWRIGHT_CHROMIUM_PATH
          ? { executablePath: process.env.PLAYWRIGHT_CHROMIUM_PATH, args: ['--no-sandbox'] }
          : {},
      },
    },
  ],
  webServer: {
    command: [
      'npm --prefix ../frontend run build',
      'uv run python -m scripts.ensure_db',
      'uv run alembic upgrade head',
      `uv run uvicorn app.main:app --host 127.0.0.1 --port ${PORT}`,
    ].join(' && '),
    cwd: '../backend',
    url: `http://127.0.0.1:${PORT}/api/health`,
    env: {
      CAMPUSFLOW_DATABASE_URL: DB_URL,
      CAMPUSFLOW_FRONTEND_DIST: '../frontend/dist',
      CAMPUSFLOW_ALLOWED_ORIGINS: '[]',
      CAMPUSFLOW_REGISTER_MAX_PER_IP_PER_HOUR: '1000',
      CAMPUSFLOW_LOG_LEVEL: 'WARNING',
    },
    reuseExistingServer: false,
    timeout: 180_000,
  },
})
