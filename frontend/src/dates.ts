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
