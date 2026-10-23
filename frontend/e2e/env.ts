import { execFileSync } from 'node:child_process'
import { mkdtempSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

// Shared by playwright.config.ts and the tests: the browser tests' own database.
export const E2E_DATABASE_URL =
  process.env.CAMPUSFLOW_E2E_DATABASE_URL ?? 'postgresql+psycopg://campusflow:campusflow@localhost:5432/campusflow_e2e'

/** Run the real reminder worker once against the test database (email is captured, never sent). */
export function runWorkerOnce(): string {
  return runBackend('app.worker', '--once')
}

/** Rebuild the shared demo account in the test database, as the nightly job does. */
export function resetDemo(): string {
  return runBackend('app.demo')
}

function runBackend(module: string, ...args: string[]): string {
  return execFileSync('uv', ['run', 'python', '-m', module, ...args], {
    cwd: join(import.meta.dirname, '..', '..', 'backend'),
    env: {
      ...process.env,
      CAMPUSFLOW_DATABASE_URL: E2E_DATABASE_URL,
      CAMPUSFLOW_EMAIL_MODE: 'capture',
      CAMPUSFLOW_EMAIL_CAPTURE_DIR: mkdtempSync(join(tmpdir(), 'campusflow-mail-')),
    },
    encoding: 'utf8',
    stdio: ['ignore', 'pipe', 'pipe'],
  })
}
