// Run with: npm test
import assert from 'node:assert/strict'
import { test } from 'node:test'

import { nextChange } from './dates.ts'

test('next change is the earliest time still ahead', () => {
  assert.equal(nextChange([300, 100, 200], 150), 200)
  assert.equal(nextChange([100, 200], 200), null) // an end exactly now has already happened
  assert.equal(nextChange([], 0), null)
})
