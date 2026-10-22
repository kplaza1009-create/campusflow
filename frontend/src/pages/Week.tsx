import { ChevronLeft, ChevronRight, Plus, TriangleAlert } from 'lucide-react'
import { useEffect, useMemo, useState, type CSSProperties } from 'react'
import { useSearchParams } from 'react-router'

import type { Assignment, CalendarData, Occurrence } from '../api'
import { useAssignmentDialog } from '../components/assignmentDialogContext'
import { useEventDialog } from '../components/eventDialogContext'
import { BlockError, Empty } from '../components/States'
import { hourRange, minutesOf, segmentsForDay, weekStart, type Segment } from '../calendarLayout'
import { addDays, formatMonthDay, formatTime, formatWeekday, plural, todayIn } from '../dates'
import { useCalendar } from '../queries'
import { useUser } from '../session'

const HOUR = 48 // px per hour in the grid
const DAYS = 7

const isPhone = () => typeof window !== 'undefined' && window.matchMedia('(max-width: 760px)').matches

export function WeekPage() {
  const user = useUser()
  const today = todayIn(user.time_zone)
  const [params, setParams] = useSearchParams()
  const start = weekStart(params.get('start') ?? today)
  // The grid needs width; on a phone the agenda is the default view.
  const [defaultView] = useState(() => (isPhone() ? 'agenda' : 'week'))
  const view = params.get('view') ?? defaultView
  const cal = useCalendar(start, DAYS)
  const events = useEventDialog()
  const days = useMemo(() => Array.from({ length: DAYS }, (_, i) => addDays(start, i)), [start])

  const go = (next: Record<string, string | null>) =>
    setParams(
      (p) => {
        const q = new URLSearchParams(p)
        for (const [k, v] of Object.entries(next)) {
          if (v === null) q.delete(k)
          else q.set(k, v)
        }
        return q
      },
      { replace: false },
    )

  const data = cal.data?.start === start ? cal.data : undefined
  const conflictCount = data ? data.occurrences.filter((o) => o.conflicts_with.length > 0).length : 0
  const end = addDays(start, DAYS - 1)
  const range =
    start.slice(0, 7) === end.slice(0, 7)
      ? `${formatMonthDay(start)} – ${end.slice(8).replace(/^0/, '')}`
      : `${formatMonthDay(start)} – ${formatMonthDay(end)}`

  return (
    <div className="page wide">
      <div className="title-row">
        <div>
          <h1>Week</h1>
          <p aria-live="polite">
            {range}
            {data &&
              ` · ${plural(data.occurrences.length, 'class or event', 'classes and events')}` +
                (conflictCount ? ` · ${conflictCount} overlapping` : '') +
                ` · ${plural(data.deadlines.length, 'deadline')}`}
          </p>
        </div>
        <div className="actions">
          <button
            type="button"
            className="btn btn-primary"
            onClick={() => events.open({ date: days.includes(today) ? today : start })}
          >
            <Plus size={16} aria-hidden="true" />
            New event
          </button>
        </div>
      </div>

      <div className="week-toolbar">
        <div className="week-nav" role="group" aria-label="Choose week">
          <button
            type="button"
            className="btn btn-secondary btn-icon"
            onClick={() => go({ start: addDays(start, -7) })}
            aria-label="Previous week"
          >
            <ChevronLeft size={18} aria-hidden="true" />
          </button>
          <button
            type="button"
            className="btn btn-secondary"
            onClick={() => go({ start: null })}
            disabled={days.includes(today)}
          >
            This week
          </button>
          <button
            type="button"
            className="btn btn-secondary btn-icon"
            onClick={() => go({ start: addDays(start, 7) })}
            aria-label="Next week"
          >
            <ChevronRight size={18} aria-hidden="true" />
          </button>
        </div>
        <div className="segmented" role="group" aria-label="View">
          {[
            ['week', 'Week'],
            ['agenda', 'Agenda'],
          ].map(([value, label]) => (
            <button key={value} type="button" aria-pressed={view === value} onClick={() => go({ view: value })}>
              {label}
            </button>
          ))}
        </div>
      </div>

      {cal.isError && !data ? (
        <section className="panel">
          <BlockError what="this week" error={cal.error} retry={() => cal.refetch()} />
        </section>
      ) : view === 'agenda' ? (
        <Agenda days={days} today={today} data={data} />
      ) : (
        <Grid days={days} today={today} data={data} timeZone={user.time_zone} />
      )}
      <p className="hint">
        Weekly classes repeat on their days until their last class. Editing one changes the whole series; skipping a
        single meeting isn&apos;t supported yet.
      </p>
    </div>
  )
}

// ---- Grid view -----------------------------------------------------------------------

function Grid({
  days,
  today,
  data,
  timeZone,
}: {
  days: string[]
  today: string
  data: CalendarData | undefined
  timeZone: string
}) {
  const events = useEventDialog()
  const perDay = useMemo(() => days.map((d) => segmentsForDay(data?.occurrences ?? [], d)), [days, data])
  const [first, last] = hourRange(perDay)
  const titles = useMemo(() => new Map((data?.occurrences ?? []).map((o) => [o.event_id, o.title])), [data])
  const deadlines = data?.deadlines ?? []
  const allDay = days.map((d) => deadlines.filter((a) => a.all_day && a.due_date === d))
  const timed = days.map((d) => deadlines.filter((a) => !a.all_day && a.due_date === d))
  const nowMin = useNowMinutes(timeZone)

  return (
    <section className="panel week-grid" aria-label="Week calendar" aria-busy={!data}>
      <div className="wg-head" aria-hidden="true">
        <span />
        {days.map((d) => (
          <span key={d} className={d === today ? 'wg-day is-today' : 'wg-day'}>
            {formatWeekday(d)} <b className="num">{d.slice(8).replace(/^0/, '')}</b>
          </span>
        ))}
      </div>

      {allDay.some((l) => l.length > 0) && (
        <div className="wg-allday">
          <span className="wg-gutter-label">Due</span>
          {allDay.map((list, i) => (
            <div key={days[i]} className="wg-allday-cell">
              {list.map((a) => (
                <DeadlineChip key={a.id} a={a} />
              ))}
            </div>
          ))}
        </div>
      )}

      <div className="wg-body" style={{ height: (last - first) * HOUR } as CSSProperties}>
        <div className="wg-hours" aria-hidden="true">
          {Array.from({ length: last - first }, (_, i) => (
            <span key={i} style={{ top: i * HOUR }}>
              {i === 0 ? '' : formatTime(`${first + i}:00`)}
            </span>
          ))}
        </div>
        {days.map((d, i) => (
          <DayColumn
            key={d}
            date={d}
            isToday={d === today}
            segments={perDay[i]}
            deadlines={timed[i]}
            first={first}
            last={last}
            titles={titles}
            nowMin={d === today ? nowMin : null}
            onEmptyClick={(startTime) => events.open({ date: d, startTime })}
          />
        ))}
      </div>
      {data && data.occurrences.length === 0 && (
        <div className="wg-empty">
          <Empty
            compact
            action={
              <button
                type="button"
                className="btn btn-secondary btn-small"
                onClick={() => events.open({ date: days[0] })}
              >
                Add a class
              </button>
            }
          >
            Nothing scheduled this week. Add each class once and it repeats every week, or click any time on the grid.
          </Empty>
        </div>
      )}
    </section>
  )
}

function useNowMinutes(timeZone: string): number {
  const read = () => {
    const parts = new Intl.DateTimeFormat('en-US', { timeZone, hour: 'numeric', minute: 'numeric', hourCycle: 'h23' })
      .formatToParts(new Date())
      .reduce<Record<string, string>>((acc, p) => ({ ...acc, [p.type]: p.value }), {})
    return Number(parts.hour) * 60 + Number(parts.minute)
  }
  const [now, setNow] = useState(read)
  useEffect(() => {
    const t = setInterval(() => setNow(read()), 60_000)
    return () => clearInterval(t)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [timeZone])
  return now
}

function DayColumn({
  date,
  isToday,
  segments,
  deadlines,
  first,
  last,
  titles,
  nowMin,
  onEmptyClick,
}: {
  date: string
  isToday: boolean
  segments: Segment[]
  deadlines: Assignment[]
  first: number
  last: number
  titles: Map<number, string>
  nowMin: number | null
  onEmptyClick: (startTime: string) => void
}) {
  const events = useEventDialog()
  const y = (min: number) => ((min - first * 60) / 60) * HOUR
  return (
    <div
      className={isToday ? 'wg-col is-today' : 'wg-col'}
      style={{ backgroundSize: `100% ${HOUR}px` }}
      onClick={(e) => {
        if (e.target !== e.currentTarget) return
        const rect = e.currentTarget.getBoundingClientRect()
        const min = first * 60 + Math.floor(((e.clientY - rect.top) / HOUR) * 2) * 30 // nearest half hour above
        const h = Math.min(Math.floor(min / 60), 23)
        onEmptyClick(`${String(h).padStart(2, '0')}:${min % 60 === 30 ? '30' : '00'}`)
      }}
    >
      <span className="visually-hidden">{`${formatWeekday(date)} ${formatMonthDay(date)}`}</span>
      {segments.map((s) => {
        const o = s.occ
        const clash = o.conflicts_with.length > 0
        const color = o.course?.color ?? 'var(--faint)'
        const others = o.conflicts_with.map((id) => titles.get(id)).filter(Boolean)
        const top = Math.max(s.startMin, first * 60)
        const bottom = Math.max(Math.min(s.endMin, last * 60), top + 30) // a pinned tail stays readable
        const label = `${o.title}, ${formatTime(o.start_time)} to ${formatTime(o.end_time)}${o.location ? `, ${o.location}` : ''}${
          clash ? `. Overlaps ${others.join(', ')}` : ''
        }`
        return (
          <button
            type="button"
            key={`${o.event_id}-${o.start}`}
            className={`wg-event${clash ? ' is-conflict' : ''}${bottom - top < 45 ? ' is-short' : ''}`}
            style={{
              top: y(top),
              height: Math.max(y(bottom) - y(top), 18),
              left: `calc(${(s.col / s.cols) * 100}% + 2px)`,
              width: `calc(${100 / s.cols}% - 4px)`,
              ['--ev' as string]: color,
            }}
            onClick={() => events.open({ eventId: o.event_id })}
            aria-label={label}
            title={label}
          >
            <span className="ev-title">
              {clash && <TriangleAlert size={12} aria-hidden="true" />}
              {o.title}
            </span>
            <span className="ev-time num">
              {s.continuesBefore ? '…' : formatTime(o.start_time)}–{s.continuesAfter ? '…' : formatTime(o.end_time)}
              {o.location ? ` · ${o.location}` : ''}
            </span>
          </button>
        )
      })}
      {deadlines.map((a) => {
        const min = a.due_time ? minutesOf(a.due_time) : 0
        if (min < first * 60 || min > last * 60) return null
        return (
          <div key={a.id} className="wg-deadline" style={{ top: Math.min(Math.max(y(min), 12), y(last * 60) - 12) }}>
            <DeadlineChip a={a} />
          </div>
        )
      })}
      {nowMin !== null && nowMin >= first * 60 && nowMin <= last * 60 && (
        <div className="wg-now" style={{ top: y(nowMin) }} aria-hidden="true" />
      )}
    </div>
  )
}

function DeadlineChip({ a }: { a: Assignment }) {
  const dialog = useAssignmentDialog()
  return (
    <button
      type="button"
      className={a.completed ? 'deadline-chip is-done' : a.overdue ? 'deadline-chip is-late' : 'deadline-chip'}
      onClick={() => dialog.open(a)}
      aria-label={`Due ${a.due_time ? formatTime(a.due_time) : 'end of day'}: ${a.title}${a.completed ? ', done' : ''}`}
    >
      <span className="course-dot" style={{ background: a.course.color }} aria-hidden="true" />
      <span className="chip-text">
        {a.due_time ? `${formatTime(a.due_time)} ` : ''}
        {a.title}
      </span>
    </button>
  )
}

// ---- Agenda view (accessible list) ----------------------------------------------------

type Item = { kind: 'event'; occ: Occurrence; sort: string } | { kind: 'deadline'; a: Assignment; sort: string }

function Agenda({ days, today, data }: { days: string[]; today: string; data: CalendarData | undefined }) {
  const events = useEventDialog()
  const assignments = useAssignmentDialog()
  if (!data) {
    return (
      <section className="panel" aria-busy="true">
        <p className="empty compact muted" role="status">
          Loading this week…
        </p>
      </section>
    )
  }
  const titles = new Map(data.occurrences.map((o) => [o.event_id, o.title]))
  return (
    <div className="agenda">
      {days.map((d) => {
        const items: Item[] = [
          // An overnight event is listed on the day it starts.
          ...data.occurrences
            .filter((o) => o.start_date === d)
            .map((occ) => ({ kind: 'event' as const, occ, sort: occ.start_time })),
          ...data.deadlines
            .filter((a) => a.due_date === d)
            .map((a) => ({ kind: 'deadline' as const, a, sort: a.due_time ?? '24:00' })),
        ].sort((x, y) => x.sort.localeCompare(y.sort))
        const heading = `${d === today ? 'Today, ' : ''}${formatWeekday(d)}, ${formatMonthDay(d)}`
        return (
          <section key={d} className="agenda-day" aria-label={heading}>
            <h2 className={d === today ? 'agenda-heading is-today' : 'agenda-heading'}>{heading}</h2>
            <div className="panel">
              {items.length === 0 ? (
                <p className="empty compact muted">Nothing scheduled.</p>
              ) : (
                <ul className="list">
                  {items.map((it) =>
                    it.kind === 'event' ? (
                      <li key={`e-${it.occ.event_id}-${it.occ.start}`}>
                        <button
                          type="button"
                          className="agenda-row"
                          onClick={() => events.open({ eventId: it.occ.event_id })}
                        >
                          <span className="agenda-time num">
                            {formatTime(it.occ.start_time)}–{formatTime(it.occ.end_time)}
                            {it.occ.end_date > it.occ.start_date && <span className="time-note">ends next day</span>}
                          </span>
                          <span className="agenda-main">
                            <span className="a-title">{it.occ.title}</span>
                            <span className="a-meta">
                              <span
                                className="course-dot"
                                style={{ background: it.occ.course?.color ?? 'var(--faint)' }}
                                aria-hidden="true"
                              />
                              <span className="course-name">
                                {[
                                  it.occ.course?.code ?? it.occ.course?.name,
                                  it.occ.location,
                                  it.occ.recurring ? 'Weekly' : null,
                                ]
                                  .filter(Boolean)
                                  .join(' · ') || 'No course'}
                              </span>
                            </span>
                            {it.occ.conflicts_with.length > 0 && (
                              <span className="agenda-conflict">
                                <TriangleAlert size={13} aria-hidden="true" />
                                Overlaps {it.occ.conflicts_with.map((id) => titles.get(id)).join(', ')}
                              </span>
                            )}
                          </span>
                        </button>
                      </li>
                    ) : (
                      <li key={`d-${it.a.id}`}>
                        <button type="button" className="agenda-row" onClick={() => assignments.open(it.a)}>
                          <span className="agenda-time num">
                            {it.a.due_time ? formatTime(it.a.due_time) : 'End of day'}
                          </span>
                          <span className="agenda-main">
                            <span className="a-title">
                              Due: {it.a.title}
                              {it.a.completed ? <span className="muted"> (done)</span> : null}
                            </span>
                            <span className="a-meta">
                              <span
                                className="course-dot"
                                style={{ background: it.a.course.color }}
                                aria-hidden="true"
                              />
                              <span className="course-name">{it.a.course.code ?? it.a.course.name}</span>
                              {it.a.overdue && <span className="late-inline">Overdue</span>}
                            </span>
                          </span>
                        </button>
                      </li>
                    ),
                  )}
                </ul>
              )}
            </div>
          </section>
        )
      })}
    </div>
  )
}
