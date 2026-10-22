import { expect, test } from '@playwright/test'

// "Ended" must appear on Today's schedule when an event finishes while the page
// stays open, from a local timer: no navigation, no refresh, no API request.

const NY = 'America/New_York'
const parts = (d: Date) => {
  const p = new Intl.DateTimeFormat('en-CA', {
    timeZone: NY,
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hourCycle: 'h23',
  }).formatToParts(d)
  const get = (t: string) => p.find((x) => x.type === t)!.value
  return { date: `${get('year')}-${get('month')}-${get('day')}`, time: `${get('hour')}:${get('minute')}` }
}

test('"Ended" appears when an event finishes while Today stays open', async ({ page }) => {
  // The page's clock starts at the real time (the API uses the real time too) and only
  // moves when the test moves it.
  const start = new Date()
  await page.clock.install({ time: start })

  const email = `ended-${Date.now()}@example.edu`
  await page.goto('/register')
  await page.getByLabel('Email').fill(email)
  await page.getByLabel('Password').fill('correct horse battery staple')
  await page.getByRole('button', { name: 'Create account' }).click()
  await expect(page.getByRole('heading', { name: 'Today', level: 1 })).toBeVisible()

  // An event that began 30 minutes ago and ends 2 to 3 minutes from now (whole minutes).
  const csrf = (await (await page.request.get('/api/auth/session')).json()).csrf_token
  const from = parts(new Date(start.getTime() - 30 * 60_000))
  const to = parts(new Date(Math.ceil(start.getTime() / 60_000) * 60_000 + 2 * 60_000))
  const created = await page.request.post('/api/events', {
    data: {
      title: 'Lab section',
      kind: 'class',
      allow_conflicts: true,
      schedule: { type: 'once', start_date: from.date, start_time: from.time, end_date: to.date, end_time: to.time },
    },
    headers: { 'X-CSRF-Token': csrf },
  })
  expect(created.status()).toBe(201)

  await page.reload()
  const row = page.getByRole('button', { name: /Lab section/ })
  await expect(row).toBeVisible()
  await expect(row).not.toContainText('Ended')

  const todayRequests: string[] = []
  page.on('request', (r) => r.url().includes('/api/today') && todayRequests.push(r.url()))

  await page.clock.fastForward('03:30') // past the end, well short of Today's 5-minute refetch
  await expect(row).toContainText('Ended')
  expect(todayRequests).toEqual([]) // the label came from the timer, not a refetch
})
