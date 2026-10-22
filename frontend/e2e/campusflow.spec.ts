import { expect, test, type Page } from '@playwright/test'

// One student's essential path through Milestone 1: create an account, add a
// course and an assignment, get warned about a duplicate, complete the work,
// and find it again after signing out and back in.

const password = 'correct horse battery staple'

function nyDate(offsetDays: number): string {
  const today = new Intl.DateTimeFormat('en-CA', { timeZone: 'America/New_York' }).format(new Date())
  return new Date(Date.parse(today) + offsetDays * 86_400_000).toISOString().slice(0, 10)
}

async function addAssignment(page: Page, title: string, due: string) {
  await page.getByRole('button', { name: 'New assignment' }).click()
  const dialog = page.getByRole('dialog', { name: 'New assignment' })
  await expect(dialog.getByLabel('Title')).toBeFocused()
  await dialog.getByLabel('Title').fill(title)
  await dialog.getByLabel('Due date').fill(due)
  await dialog.getByRole('button', { name: 'Add assignment' }).click()
  return dialog
}

test('student workflow: account, course, assignment, duplicate warning, calendar conflict, completion', async ({
  page,
}) => {
  const email = `e2e-${Date.now()}@example.edu`

  // Register. A new account lands on Today with a first-run prompt.
  await page.goto('/register')
  await page.getByLabel('Email').fill(email)
  await page.getByLabel('Password').fill(password)
  await page.getByRole('button', { name: 'Create account' }).click()
  await expect(page.getByRole('heading', { name: 'Today', level: 1 })).toBeVisible()

  // Create a course from the first-run prompt.
  await page.getByRole('link', { name: 'Add your first course' }).click()
  await page.getByLabel('Course name').fill('Database Systems I')
  await page.getByLabel('Code (optional)').fill('CS248')
  await page.getByRole('radio', { name: 'Teal' }).click()
  await page.getByRole('button', { name: 'Add course' }).click()
  const courseList = page.getByRole('region', { name: 'Active courses' })
  await expect(courseList.getByText('Database Systems I', { exact: true })).toBeVisible()
  await expect(courseList.getByText('0 open')).toBeVisible()

  // Add an assignment that was due yesterday: it shows as overdue on Today.
  await page.getByRole('link', { name: 'Today' }).first().click()
  const created = await addAssignment(page, 'ER diagram', nyDate(-1))
  await expect(created).toBeHidden()
  const overdue = page.getByRole('region', { name: /Overdue/ })
  await expect(overdue.getByText('ER diagram')).toBeVisible()
  await expect(overdue.getByText('1 day overdue')).toBeVisible()

  // The same title, course, and date again: warned, not silently duplicated.
  const dup = await addAssignment(page, '  er diagram ', nyDate(-1))
  await expect(dup.getByText('This looks like one you already have.')).toBeVisible()
  await dup.getByRole('button', { name: 'Keep editing' }).click()
  // Closing with unsaved input asks first, and keeps the input on "Keep editing".
  await dup.getByRole('button', { name: 'Cancel' }).click()
  await expect(dup.getByText('Discard your changes?')).toBeVisible()
  await dup.getByRole('button', { name: 'Keep editing' }).click()
  await expect(dup.getByLabel('Title')).toHaveValue('  er diagram ')
  await dup.getByRole('button', { name: 'Cancel' }).click()
  await dup.getByRole('button', { name: 'Discard' }).click()
  await expect(dup).toBeHidden()

  // Calendar: add a weekly class, then an event that clashes with it.
  await page.getByRole('link', { name: 'Week' }).first().click()
  await page.getByRole('button', { name: 'New event' }).click()
  const cls = page.getByRole('dialog', { name: 'New event' })
  await cls.getByLabel('Title').fill('Database lecture')
  await cls.getByLabel('Type').selectOption('class')
  await expect(cls.getByLabel('Repeats every week')).toBeChecked() // picking "Class" turns on weekly
  await cls.getByLabel('Course (optional)').selectOption({ label: 'CS248 · Database Systems I' })
  await cls.getByLabel('Starts').fill('10:00')
  await cls.getByLabel('Ends').fill('11:15')
  await cls.getByRole('button', { name: 'Add event' }).click()
  await expect(cls).toBeHidden()
  await expect(page.getByRole('button', { name: /^Database lecture, 10:00 AM to 11:15 AM/ })).toBeVisible()

  await page.getByRole('button', { name: 'New event' }).click()
  const clash = page.getByRole('dialog', { name: 'New event' })
  await clash.getByLabel('Title').fill('Advisor meeting')
  await clash.getByLabel('Starts').fill('10:30')
  await clash.getByLabel('Ends').fill('11:00')
  await clash.getByRole('button', { name: 'Add event' }).click()
  await expect(clash.getByText('This overlaps 1 other time on your calendar.')).toBeVisible()
  await expect(clash.getByText(/Database lecture,/)).toBeVisible()
  await clash.getByRole('button', { name: 'Save anyway' }).click()
  await expect(clash).toBeHidden()

  // The accessible agenda lists both, each flagged with what it overlaps.
  await page.getByRole('button', { name: 'Agenda' }).click()
  await expect(page.getByText('Overlaps Database lecture')).toBeVisible()
  await expect(page.getByText('Overlaps Advisor meeting')).toBeVisible()

  // The assignment list: search finds it, and only one exists.
  await page.getByRole('link', { name: 'Assignments' }).first().click()
  await page.getByLabel('Search titles').fill('diagram')
  await expect(page).toHaveURL(/q=diagram/)
  await expect(page.getByTestId('assignment-row')).toHaveCount(1)

  // Complete it. Once saved, it leaves the Open list and appears under Done.
  await page.getByRole('checkbox', { name: 'Mark ER diagram done' }).click()
  await expect(page.getByText('Marked “ER diagram” done')).toBeVisible()
  await expect(page.getByTestId('assignment-row')).toHaveCount(0)
  await page.getByRole('button', { name: 'Done', exact: true }).click()
  await expect(page.getByRole('checkbox', { name: 'Reopen ER diagram' })).toBeChecked()

  // The session survives a reload.
  await page.reload()
  await expect(page.getByRole('heading', { name: 'Assignments', level: 1 })).toBeVisible()

  // Sign out, sign back in: the data is still there.
  await page.getByRole('link', { name: 'Settings' }).first().click()
  await page.getByRole('button', { name: 'Sign out' }).click()
  await expect(page.getByRole('heading', { name: 'Sign in' })).toBeVisible()
  await page.goto('/assignments?status=completed')
  await expect(page.getByRole('heading', { name: 'Sign in' })).toBeVisible() // still signed out
  await page.getByLabel('Email').fill(email)
  await page.getByLabel('Password').fill(password)
  await page.getByRole('button', { name: 'Sign in' }).click()
  await expect(page.getByRole('checkbox', { name: 'Reopen ER diagram' })).toBeVisible()
})

test('wrong password shows an error and keeps the email', async ({ page }) => {
  await page.goto('/login')
  await page.getByLabel('Email').fill('nobody@example.edu')
  await page.getByLabel('Password').fill('not the password')
  await page.getByRole('button', { name: 'Sign in' }).click()
  await expect(page.getByRole('alert')).toHaveText('Incorrect email or password.')
  await expect(page.getByLabel('Email')).toHaveValue('nobody@example.edu')
})
