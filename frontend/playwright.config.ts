import { defineConfig, devices } from '@playwright/test'

// The browser tests start their own API (on its own database) and dev server,
// so they never touch your development data.
const API_PORT = 8011
const WEB_PORT = 5181
const DB_URL =
  process.env.CAMPUSFLOW_E2E_DATABASE_URL ?? 'postgresql+psycopg://campusflow:campusflow@localhost:5432/campusflow_e2e'

export default defineConfig({
  testDir: './e2e',
  timeout: 60_000,
  fullyParallel: false,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [['list'], ['html', { open: 'never' }]] : 'list',
  use: {
    baseURL: `http://127.0.0.1:${WEB_PORT}`,
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
  webServer: [
    {
      command: `uv run python -m scripts.ensure_db && uv run alembic upgrade head && uv run uvicorn app.main:app --host 127.0.0.1 --port ${API_PORT}`,
      cwd: '../backend',
      url: `http://127.0.0.1:${API_PORT}/api/health`,
      env: {
        CAMPUSFLOW_DATABASE_URL: DB_URL,
        CAMPUSFLOW_ALLOWED_ORIGINS: `["http://127.0.0.1:${WEB_PORT}"]`,
        CAMPUSFLOW_REGISTER_MAX_PER_IP_PER_HOUR: '1000',
      },
      reuseExistingServer: false,
      timeout: 120_000,
    },
    {
      command: `npx vite --host 127.0.0.1`,
      url: `http://127.0.0.1:${WEB_PORT}`,
      env: { PORT: String(WEB_PORT), CAMPUSFLOW_API_URL: `http://127.0.0.1:${API_PORT}` },
      reuseExistingServer: false,
      timeout: 60_000,
    },
  ],
})
