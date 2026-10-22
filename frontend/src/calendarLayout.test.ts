// Run with: npm test   (Node's built-in test runner, no extra dependencies)
import assert from 'node:assert/strict'
import { test } from 'node:test'

import type { Occurrence } from './api.ts'
import { hourRange, segmentsForDay, weekStart } from './calendarLayout.ts'

let nextId = 1
function occ(start_date: string, start_time: string, end_date: string, end_time: string): Occurrence {
  return {
    event_id: nextId++,
    title: 'x',
    kind: 'other',
    course: null,
    location: null,
    recurring: false,
    start: '',
    end: '',
    start_date,
    start_time,
    end_date,
    end_time,
    conflicts_with: [],
  }
}

const D = '2026-10-21'

test('overnight event is split across both days', () => {
  const late = occ(D, '22:00:00', '2026-10-22', '02:00:00')
  const [today] = segmentsForDay([late], D)
  const [tomorrow] = segmentsForDay([late], '2026-10-22')
  assert.deepEqual([today.startMin, today.endMin, today.continuesAfter], [22 * 60, 24 * 60, true])
  assert.deepEqual([tomorrow.startMin, tomorrow.endMin, tomorrow.continuesBefore], [0, 2 * 60, true])
})

test('an event ending exactly at midnight does not appear on the next day', () => {
  assert.equal(segmentsForDay([occ(D, '22:00:00', '2026-10-22', '00:00:00')], '2026-10-22').length, 0)
})

test('overlapping events sit side by side; adjacent ones do not', () => {
  const a = occ(D, '10:00:00', D, '11:00:00')
  const b = occ(D, '10:30:00', D, '11:30:00')
  const c = occ(D, '11:30:00', D, '12:00:00') // starts when b ends
  const segs = segmentsForDay([c, b, a], D)
  const by = (o: Occurrence) => segs.find((s) => s.occ === o)!
  assert.deepEqual([by(a).col, by(a).cols], [0, 2])
  assert.deepEqual([by(b).col, by(b).cols], [1, 2])
  assert.deepEqual([by(c).col, by(c).cols], [0, 1])
})

test('a freed column is reused inside a chain of overlaps', () => {
  const long = occ(D, '09:00:00', D, '12:00:00')
  const first = occ(D, '09:00:00', D, '10:00:00')
  const second = occ(D, '10:00:00', D, '11:00:00') // reuses `first`'s column
  const segs = segmentsForDay([long, first, second], D)
  assert.deepEqual(
    segs.map((s) => [s.occ.event_id === long.event_id ? 'long' : 'short', s.col, s.cols]),
    [
      ['long', 0, 2],
      ['short', 1, 2],
      ['short', 1, 2],
    ],
  )
})

test('hour range widens to fit early and late events', () => {
  const segs = segmentsForDay([occ(D, '07:30:00', D, '08:15:00'), occ(D, '20:00:00', D, '21:10:00')], D)
  assert.deepEqual(hourRange([segs]), [7, 22])
  assert.deepEqual(hourRange([[]]), [8, 18])
})

test('weeks start on Monday', () => {
  assert.equal(weekStart('2026-10-22'), '2026-10-19') // Thursday
  assert.equal(weekStart('2026-10-19'), '2026-10-19') // Monday
  assert.equal(weekStart('2026-10-25'), '2026-10-19') // Sunday
  assert.equal(weekStart('2027-01-01'), '2026-12-28') // across a year
})

test('the tail of an overnight event does not stretch the grid to midnight', () => {
  const late = occ('2026-10-20', '22:00:00', D, '00:45:00')
  assert.deepEqual(hourRange([segmentsForDay([late], D)]), [8, 18])
  assert.deepEqual(hourRange([segmentsForDay([late], '2026-10-20')]), [8, 24])
})
