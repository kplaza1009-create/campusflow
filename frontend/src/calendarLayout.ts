// Laying out a week of occurrences on a time grid. Pure functions, tested in
// calendarLayout.test.ts. The API already gives every occurrence its local
// start/end date and time in the account's zone, so this only does geometry.

import type { Occurrence } from './api'

export type Segment = {
  occ: Occurrence
  startMin: number // minutes after local midnight on this day
  endMin: number
  continuesBefore: boolean // started on an earlier day (overnight)
  continuesAfter: boolean // ends on a later day
  col: number // which side-by-side column it sits in
  cols: number // how many columns its overlap group needs
}

export const minutesOf = (t: string) => {
  const [h, m] = t.split(':').map(Number)
  return h * 60 + m
}

/** The part of each occurrence that falls on `date`, so an overnight event shows on both days. */
export function segmentsForDay(occs: Occurrence[], date: string): Segment[] {
  const out: Segment[] = []
  for (const occ of occs) {
    if (occ.start_date > date || occ.end_date < date) continue
    const startMin = occ.start_date < date ? 0 : minutesOf(occ.start_time)
    const endMin = occ.end_date > date ? 24 * 60 : minutesOf(occ.end_time)
    if (endMin <= startMin) continue // e.g. ends exactly at midnight: nothing on this day
    out.push({
      occ,
      startMin,
      endMin,
      continuesBefore: occ.start_date < date,
      continuesAfter: occ.end_date > date,
      col: 0,
      cols: 1,
    })
  }
  return layoutColumns(out)
}

/**
 * Side-by-side columns for overlapping segments. Segments that overlap, directly
 * or through a chain, form a group; each takes the first column that's free at
 * its start, and the whole group shares the group's column count.
 */
export function layoutColumns(segs: Segment[]): Segment[] {
  const sorted = [...segs].sort((a, b) => a.startMin - b.startMin || b.endMin - a.endMin)
  let group: Segment[] = []
  let columnsEnd: number[] = []
  let groupEnd = -1
  const close = () => {
    for (const s of group) s.cols = columnsEnd.length
    group = []
    columnsEnd = []
  }
  for (const s of sorted) {
    if (s.startMin >= groupEnd) close() // adjacent events don't overlap
    let col = columnsEnd.findIndex((end) => end <= s.startMin)
    if (col === -1) col = columnsEnd.push(0) - 1
    columnsEnd[col] = s.endMin
    s.col = col
    group.push(s)
    groupEnd = Math.max(groupEnd, s.endMin)
  }
  close()
  return sorted
}

/**
 * Hours to show: at least 8 AM to 6 PM, widened to fit everything that starts
 * that day. The early-morning tail of last night's overnight event doesn't
 * widen it (the grid shows that tail pinned to the top instead).
 */
export function hourRange(days: Segment[][]): [number, number] {
  let first = 8
  let last = 18
  for (const s of days.flat()) {
    if (!s.continuesBefore) first = Math.min(first, Math.floor(s.startMin / 60))
    last = Math.max(last, Math.ceil(s.endMin / 60))
  }
  return [first, last]
}

/** Monday of the week containing `date` (weeks start on Monday). */
export function weekStart(date: string): string {
  const [y, m, d] = date.split('-').map(Number)
  const t = Date.UTC(y, m - 1, d)
  const dow = (new Date(t).getUTCDay() + 6) % 7 // 0 = Monday
  return new Date(t - dow * 86_400_000).toISOString().slice(0, 10)
}
