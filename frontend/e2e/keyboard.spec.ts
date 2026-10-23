import { expect, test, type Locator, type Page } from '@playwright/test'

// The four usability-test tasks (docs/usability-test.md), done with the keyboard
// alone: Tab, Shift+Tab, Enter, Space, arrows, and typing. No clicks. Every control
// reached this way must show a visible focus ring.

function nyDate(offsetDays: number): string {
  const today = new Intl.DateTimeFormat('en-CA', { timeZone: 'America/New_York' }).format(new Date())
  return new Date(Date.parse(today) + offsetDays * 86_400_000).toISOString().slice(0, 10)
}

/** Press Tab (or Shift+Tab, to go back) until `target` has focus, then check the focus is visible. */
async function tabTo(page: Page, target: Locator, key: 'Tab' | 'Shift+Tab' = 'Tab', max = 80) {
  for (let i = 0; i < max; i++) {
    if (await target.evaluate((el) => el === document.activeElement).catch(() => false)) {
      expect(await target.evaluate((el) => el.matches(':focus-visible')), 'focus ring visible').toBe(true)
      return
    }
    await page.keyboard.press(key)
  }
  throw new Error(`Tab never reached ${target}`)
}

test('the usability-test tasks work with the keyboard alone', async ({ page }) => {
  // Setup (not part of the tasks): an account with a daily 10:00 class to clash with.
  const r = await page.request.post('/api/auth/register', {
    data: { email: `kbd-${Date.now()}@example.edu`, password: 'correct horse battery', time_zone: 'America/New_York' },
  })
  const headers = { 'X-CSRF-Token': (await r.json()).csrf_token }
  const schedule = {
    type: 'weekly',
    weekdays: [0, 1, 2, 3, 4, 5, 6],
    start_time: '10:00',
    end_time: '11:00',
    first_date: nyDate(-7),
    last_date: nyDate(30),
  }
  expect(
    (await page.request.post('/api/events', { data: { title: 'Calculus II', kind: 'class', schedule }, headers })).ok(),
  ).toBe(true)
  await page.goto('/')
  await expect(page.getByRole('heading', { name: 'Today', level: 1 })).toBeVisible()

  // Task 1: create a course.
  await tabTo(page, page.getByRole('link', { name: 'Courses' }).first())
  await page.keyboard.press('Enter')
  await tabTo(page, page.getByRole('button', { name: 'New course' }))
  await page.keyboard.press('Enter')
  await tabTo(page, page.getByLabel('Course name'))
  await page.keyboard.type('Database Systems I')
  await tabTo(page, page.getByRole('radio').first())
  await page.keyboard.press('ArrowRight') // arrows move within the color choices
  await expect(page.getByRole('radio').nth(1)).toBeChecked()
  await tabTo(page, page.getByRole('button', { name: 'Add course' }))
  await page.keyboard.press('Enter')
  await expect(page.getByRole('region', { name: 'Active courses' }).getByText('Database Systems I')).toBeVisible()
  await expect(page.getByRole('button', { name: 'New course' })).toBeFocused() // back where the form started

  // Task 2: add an assignment (due yesterday, so task 4 has something to find).
  await tabTo(page, page.getByRole('link', { name: 'Today' }).first(), 'Shift+Tab')
  await page.keyboard.press('Enter')
  await tabTo(page, page.getByRole('button', { name: 'New assignment' }).first())
  await page.keyboard.press('Enter')
  const assignment = page.getByRole('dialog', { name: 'New assignment' })
  await expect(assignment.getByLabel('Title')).toBeFocused() // the dialog puts focus in its first field
  await page.keyboard.type('ER diagram')
  await tabTo(page, assignment.getByLabel('Due date'))
  const [y, m, d] = nyDate(-1).split('-')
  await page.keyboard.type(`${m}${d}${y}`)
  await page.keyboard.press('Enter') // submits the form
  await expect(assignment).toBeHidden()

  // Task 3: schedule an event that conflicts with the class, and keep it anyway.
  await tabTo(page, page.getByRole('link', { name: 'Week' }).first(), 'Shift+Tab')
  await page.keyboard.press('Enter')
  await tabTo(page, page.getByRole('button', { name: 'New event' }).first())
  await page.keyboard.press('Enter')
  const event = page.getByRole('dialog', { name: 'New event' })
  await expect(event.getByLabel('Title')).toBeFocused()
  await page.keyboard.type('Advisor meeting')
  await tabTo(page, event.getByLabel('Starts'))
  await page.keyboard.type('1030AM')
  await tabTo(page, event.getByLabel('Ends'))
  await page.keyboard.type('1100AM')
  await page.keyboard.press('Enter')
  await expect(event.getByText('This overlaps 1 other time on your calendar.')).toBeVisible()
  await tabTo(page, event.getByRole('button', { name: 'Save anyway' }))
  await page.keyboard.press('Enter')
  await expect(event).toBeHidden()

  // Task 4: find the overdue assignment.
  await tabTo(page, page.getByRole('link', { name: 'Assignments' }).first(), 'Shift+Tab')
  await page.keyboard.press('Enter')
  await tabTo(page, page.getByRole('checkbox', { name: 'Overdue only' }))
  await page.keyboard.press('Space')
  await expect(page).toHaveURL(/overdue=1/)
  await expect(page.getByTestId('assignment-row')).toHaveCount(1)
  await expect(page.getByTestId('assignment-row')).toContainText('ER diagram')
})

test('Escape closes a dialog and puts focus back where it was', async ({ page }) => {
  await page.request.post('/api/auth/register', {
    data: { email: `esc-${Date.now()}@example.edu`, password: 'correct horse battery', time_zone: 'America/New_York' },
  })
  await page.goto('/week')
  const opener = page.getByRole('button', { name: 'New event' }).first()
  await tabTo(page, opener)
  await page.keyboard.press('Enter')
  await expect(page.getByRole('dialog', { name: 'New event' })).toBeVisible()
  await page.keyboard.press('Escape')
  await expect(page.getByRole('dialog')).toBeHidden()
  await expect(opener).toBeFocused()
})
