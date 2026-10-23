// Date display helpers. The API already converts every deadline into the account's
// time zone (due_date + due_time), so these only format calendar values. They
// format in UTC on purpose: the browser's own zone must never shift a date.

const DAY = 86_400_000

function utc(date: string): number {
  const [y, m, d] = date.split('-').map(Number)
  return Date.UTC(y, m - 1, d)
}

export function addDays(date: string, days: number): string {
  return new Date(utc(date) + days * DAY).toISOString().slice(0, 10)
}

export function daysBetween(from: string, to: string): number {
  return Math.round((utc(to) - utc(from)) / DAY)
}

/** Today's date (YYYY-MM-DD) in an IANA time zone. */
export function todayIn(timeZone: string): string {
  return new Intl.DateTimeFormat('en-CA', { timeZone }).format(new Date())
}

const weekdayShort = new Intl.DateTimeFormat(undefined, { weekday: 'short', timeZone: 'UTC' })
const monthDay = new Intl.DateTimeFormat(undefined, { month: 'short', day: 'numeric', timeZone: 'UTC' })
const longDate = new Intl.DateTimeFormat(undefined, {
  weekday: 'long',
  month: 'long',
  day: 'numeric',
  timeZone: 'UTC',
})
const timeFmt = new Intl.DateTimeFormat(undefined, { hour: 'numeric', minute: '2-digit', timeZone: 'UTC' })

export function formatWeekday(date: string): string {
  return weekdayShort.format(utc(date))
}

export function formatMonthDay(date: string): string {
  return monthDay.format(utc(date))
}

export function formatLongDate(date: string): string {
  return longDate.format(utc(date))
}

export function formatTime(time: string): string {
  const [h, m] = time.split(':').map(Number)
  return timeFmt.format(Date.UTC(2000, 0, 1, h, m))
}

/** "Today", "Tomorrow", "Fri", or "Oct 24", relative to `today`. */
export function relativeDay(date: string, today: string): string {
  const diff = daysBetween(today, date)
  if (diff === 0) return 'Today'
  if (diff === 1) return 'Tomorrow'
  if (diff === -1) return 'Yesterday'
  if (diff > 1 && diff < 7) return weekdayShort.format(utc(date))
  const sameYear = date.slice(0, 4) === today.slice(0, 4)
  return sameYear
    ? `${formatWeekday(date)}, ${formatMonthDay(date)}`
    : new Intl.DateTimeFormat(undefined, { month: 'short', day: 'numeric', year: 'numeric', timeZone: 'UTC' }).format(
        utc(date),
      )
}

export function plural(n: number, word: string, many = `${word}s`): string {
  return `${n} ${n === 1 ? word : many}`
}

/** An exact instant (ISO string) as "Today, 9:00 AM" or "Mon, Oct 19, 9:00 AM" in `timeZone`. */
export function formatInstant(iso: string, timeZone: string): string {
  const parts = new Intl.DateTimeFormat('en-CA', {
    timeZone,
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hourCycle: 'h23',
  }).formatToParts(new Date(iso))
  const get = (type: string) => parts.find((p) => p.type === type)?.value ?? ''
  const date = `${get('year')}-${get('month')}-${get('day')}`
  return `${relativeDay(date, todayIn(timeZone))}, ${formatTime(`${get('hour')}:${get('minute')}`)}`
}

/** The earliest of `times` (ms) still in the future of `now`, or null: when a view must next re-render. */
export function nextChange(times: number[], now: number): number | null {
  const future = times.filter((t) => t > now)
  return future.length ? Math.min(...future) : null
}

/** The next time (ms) the clock reads `hour`:00 in UTC, at or after `now`. */
export function nextUtcHour(hour: number, now: number): number {
  const d = new Date(now)
  const at = Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate(), hour)
  return at >= now ? at : at + 86_400_000
}
