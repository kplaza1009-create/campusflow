import AxeBuilder from '@axe-core/playwright'
import { expect, test, type Page } from '@playwright/test'

import { runWorkerOnce } from './env'

// Automated WCAG 2.2 AA check (axe-core) of every page and dialog, in light and
// dark mode, at desktop and phone widths. It catches what code can catch:
// missing labels, contrast, ARIA misuse, duplicate IDs, heading order. Screen
// reader and keyboard flows still need a human; see the README.

const WCAG = ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa']

async function expectNoViolations(page: Page, where: string) {
  const { violations } = await new AxeBuilder({ page }).withTags(WCAG).analyze()
  const summary = violations.map(
    (v) => `${v.id} (${v.impact}): ${v.help}\n    ${v.nodes.map((n) => n.target.join(' ')).join('\n    ')}`,
  )
  expect.soft(summary, `${where} has accessibility violations`).toEqual([]) // soft: report every page, not just the first
}

async function seed(page: Page) {
  const email = `a11y-${Date.now()}-${Math.random().toString(36).slice(2, 7)}@example.edu`
  const r = await page.request.post('/api/auth/register', {
    data: { email, password: 'correct horse battery staple', time_zone: 'America/New_York' },
  })
  expect(r.ok()).toBe(true)
  const headers = { 'X-CSRF-Token': (await r.json()).csrf_token }
  const today = new Intl.DateTimeFormat('en-CA', { timeZone: 'America/New_York' }).format(new Date())
  const day = (n: number) => new Date(Date.parse(today) + n * 86_400_000).toISOString().slice(0, 10)
  const post = async (path: string, data: object) => {
    const res = await page.request.post(path, { data, headers })
    expect(res.ok(), `${path} ${res.status()}`).toBe(true)
    return res.json()
  }
  const db = await post('/api/courses', { name: 'Database Systems I', code: 'CS248', color: '#2563eb' })
  const calc = await post('/api/courses', { name: 'Calculus II', code: 'MTH 202', color: '#0d9488' })
  await post('/api/assignments', { course_id: db.id, title: 'ER diagram', due_date: day(-1) })
  await post('/api/assignments', { course_id: calc.id, title: 'Problem set 6', due_date: day(0), due_time: '23:59' })
  await post('/api/assignments', { course_id: db.id, title: 'SQL joins practice', due_date: day(2) })
  const term = { first_date: day(-30), last_date: day(30) }
  const everyDay = [0, 1, 2, 3, 4, 5, 6]
  // Two classes that overlap every day, so conflict styling is always on screen.
  for (const [title, course_id, start_time, end_time] of [
    ['Calculus II', calc.id, '10:00', '11:00'],
    ['Database Systems I', db.id, '10:30', '11:45'],
  ] as const) {
    await post('/api/events', {
      title,
      kind: 'class',
      course_id,
      allow_conflicts: true,
      schedule: { type: 'weekly', weekdays: everyDay, start_time, end_time, ...term },
    })
  }
  // An overnight event, for the dashed "continues" edges.
  await post('/api/events', {
    title: 'Late lab write-up',
    kind: 'study',
    schedule: { type: 'once', start_date: day(0), start_time: '22:00', end_date: day(1), end_time: '00:45' },
  })
}

// The app runs under the server's Content-Security-Policy here, as in production.
// Anything the policy blocks (an inline script, a third-party font) fails the test.
let blocked: string[] = []
test.beforeEach(({ page }) => {
  blocked = []
  page.on('console', (m) => {
    if (m.type() === 'error' && m.text().includes('Content Security Policy')) blocked.push(m.text())
  })
})
test.afterEach(() => expect(blocked, 'blocked by the Content-Security-Policy').toEqual([]))

const PAGES = [
  ['Today', '/'],
  ['Week grid', '/week?view=week'],
  ['Week agenda', '/week?view=agenda'],
  ['Assignments', '/assignments'],
  ['Done assignments', '/assignments?status=completed'],
  ['Courses', '/courses'],
  ['Notifications', '/notifications'],
  ['Settings', '/settings'],
] as const

for (const colorScheme of ['light', 'dark'] as const) {
  for (const [size, viewport] of [
    ['desktop', { width: 1440, height: 900 }],
    ['phone', { width: 390, height: 844 }],
  ] as const) {
    test.describe(`accessibility, ${colorScheme}, ${size}`, () => {
      test.use({ colorScheme, viewport, reducedMotion: 'reduce' })

      test('signed-out pages', async ({ page }) => {
        for (const path of ['/login', '/register']) {
          await page.goto(path)
          await expect(page.getByRole('heading', { level: 1 })).toBeVisible()
          await expectNoViolations(page, path)
        }
      })

      test('every signed-in page and dialog', async ({ page }) => {
        await seed(page)
        runWorkerOnce() // delivers the due reminder, so Notifications has an unread item to scan
        for (const [name, path] of PAGES) {
          await page.goto(path)
          await page.waitForLoadState('networkidle')
          await expect(page.getByRole('heading', { level: 1 })).toBeVisible()
          await expectNoViolations(page, name)
        }

        await page.goto('/')
        await page.getByRole('button', { name: 'New assignment' }).first().click()
        await expect(page.getByRole('dialog')).toBeVisible()
        await expectNoViolations(page, 'New assignment dialog')

        // The schedule-conflict warning inside the event dialog.
        await page.goto('/week')
        await page.getByRole('button', { name: 'New event' }).first().click()
        const dialog = page.getByRole('dialog', { name: 'New event' })
        await dialog.getByLabel('Title').fill('Advisor meeting')
        await dialog.getByLabel('Starts').fill('10:15')
        await dialog.getByLabel('Ends').fill('10:45')
        await dialog.getByRole('button', { name: 'Add event' }).click()
        await expect(dialog.getByRole('button', { name: 'Save anyway' })).toBeVisible()
        await expectNoViolations(page, 'Event dialog with conflict warning')
      })
    })
  }
}
