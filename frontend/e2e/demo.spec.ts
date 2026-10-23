import AxeBuilder from '@axe-core/playwright'
import { expect, test } from '@playwright/test'

import { resetDemo } from './env'

// The one-click demo: the nightly reset command builds it, a visitor walks in from the
// sign-in page, sees it's shared and when it resets, and can leave to make their own.

test('Try the demo signs in to the shared sample account', async ({ page }) => {
  resetDemo()
  await page.goto('/login')
  await page.getByRole('button', { name: 'Try the demo' }).click()

  await expect(page.getByRole('heading', { name: 'Today', level: 1 })).toBeVisible()
  const banner = page.getByRole('region', { name: 'About the demo' })
  await expect(banner).toContainText("You're in the shared demo.")
  await expect(banner).toContainText(/next reset (today|tomorrow), \d{1,2}:00 [AP]M/)
  await expect(
    page.getByRole('region', { name: /Overdue/ }).getByText('ER diagram for the library schema'),
  ).toBeVisible()

  const { violations } = await new AxeBuilder({ page })
    .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa'])
    .analyze()
  expect(violations.map((v) => v.id)).toEqual([])

  // Account-level changes that would spoil it for others are off.
  await page.getByRole('link', { name: 'Settings' }).first().click()
  await expect(page.getByRole('heading', { name: 'Shared demo' })).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Password' })).toHaveCount(0)
  await expect(page.getByRole('heading', { name: 'Delete account' })).toHaveCount(0)
  await expect(page.getByLabel(/Email me reminders/)).toBeDisabled()

  await banner.getByRole('button', { name: 'Create your own account' }).click()
  await expect(page.getByRole('heading', { name: 'Create your account' })).toBeVisible()
})
