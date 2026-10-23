// Run with: npm test
import assert from 'node:assert/strict'
import { test } from 'node:test'

import { nextChange, nextUtcHour } from './dates.ts'

test('next change is the earliest time still ahead', () => {
  assert.equal(nextChange([300, 100, 200], 150), 200)
  assert.equal(nextChange([100, 200], 200), null) // an end exactly now has already happened
  assert.equal(nextChange([], 0), null)
})

test('the next reset is later today, or tomorrow once it has passed', () => {
  const at = (iso: string) => Date.parse(iso)
  assert.equal(nextUtcHour(8, at('2026-10-23T03:00:00Z')), at('2026-10-23T08:00:00Z'))
  assert.equal(nextUtcHour(8, at('2026-10-23T08:00:00Z')), at('2026-10-23T08:00:00Z'))
  assert.equal(nextUtcHour(8, at('2026-10-23T09:00:00Z')), at('2026-10-24T08:00:00Z'))
})
