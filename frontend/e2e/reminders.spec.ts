import { expect, test } from '@playwright/test'

import { runWorkerOnce } from './env'

// The whole reminder path: an assignment's reminder comes due, the separate
// worker process delivers it, and the student finds it in Notifications.

test('a due reminder reaches Notifications after the worker runs', async ({ page }) => {
  const email = `remind-${Date.now()}@example.edu`
  await page.goto('/register')
  await page.getByLabel('Email').fill(email)
  await page.getByLabel('Password').fill('correct horse battery staple')
  await page.getByRole('button', { name: 'Create account' }).click()
  await expect(page.getByRole('heading', { name: 'Today', level: 1 })).toBeVisible()

  // Set up through the API: a course, and an assignment due in 3 days with a
  // "1 week before" reminder, which is therefore due now whatever the time.
  const csrf = (await (await page.request.get('/api/auth/session')).json()).csrf_token
  const headers = { 'X-CSRF-Token': csrf }
  const course = await (await page.request.post('/api/courses', { data: { name: 'Physics I' }, headers })).json()
  const today = new Intl.DateTimeFormat('en-CA', { timeZone: 'America/New_York' }).format(new Date())
  const due = new Date(Date.parse(today) + 3 * 86_400_000).toISOString().slice(0, 10)
  const created = await page.request.post('/api/assignments', {
    data: { course_id: course.id, title: 'Lab report', due_date: due, reminder_minutes: 10080 },
    headers,
  })
  expect(created.status()).toBe(201)

  await page.getByRole('link', { name: 'Notifications' }).first().click()
  await expect(page.getByText('No reminders yet.')).toBeVisible()

  runWorkerOnce()

  await page.reload()
  await expect(page.getByText('1 unread reminder')).toBeVisible()
  await expect(page.getByRole('link', { name: /Notifications/ }).first()).toContainText('1')
  await page.getByRole('button', { name: /Unread: Lab report/ }).click()

  // Opening it marks it read and shows the assignment with its reminder history.
  const dialog = page.getByRole('dialog', { name: 'Edit assignment' })
  await expect(dialog.getByText(/In Notifications since/)).toBeVisible()
  await dialog.getByRole('button', { name: 'Cancel' }).click()
  await expect(page.getByText('All caught up')).toBeVisible()

  // Running the worker again delivers nothing twice.
  runWorkerOnce()
  await page.reload()
  await expect(page.getByRole('button', { name: /Lab report/ })).toHaveCount(1)
})
