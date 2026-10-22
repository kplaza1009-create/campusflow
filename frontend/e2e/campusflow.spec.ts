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

test('student workflow: account, course, assignment, duplicate warning, completion', async ({ page }) => {
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
  await expect(page.getByText('Database Systems I')).toBeVisible()
  await expect(page.getByText('0 open')).toBeVisible()

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
