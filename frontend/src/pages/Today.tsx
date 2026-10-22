import { Plus, TriangleAlert } from 'lucide-react'
import { useState, type ReactNode } from 'react'
import { Link } from 'react-router'

import type { Assignment, Occurrence } from '../api'
import { AssignmentRow } from '../components/AssignmentRow'
import { useAssignmentDialog } from '../components/assignmentDialogContext'
import { useEventDialog } from '../components/eventDialogContext'
import { BlockError, Empty, Loading } from '../components/States'
import { addDays, formatLongDate, formatMonthDay, formatTime, formatWeekday, plural, relativeDay } from '../dates'
import { useCourses, useToday } from '../queries'

const DAYS_AHEAD = 7

export function TodayPage() {
  const today = useToday()
  const courses = useCourses()
  const dialog = useAssignmentDialog()
  const data = today.data

  const firstRun = courses.data?.length === 0 && data && !data.overdue.length && !data.due_today.length

  return (
    <div className="page">
      <div className="title-row">
        <div>
          <h1>Today</h1>
          <p aria-live="polite">{data ? summary(data.today, data.overdue.length, data.due_today.length) : ' '}</p>
        </div>
        <div className="actions">
          <button
            type="button"
            className="btn btn-primary new-assignment"
            onClick={() => dialog.open()}
            aria-keyshortcuts="n"
          >
            <Plus size={16} aria-hidden="true" />
            New assignment
          </button>
        </div>
      </div>

      {firstRun ? (
        <section className="panel">
          <Empty
            action={
              <Link className="btn btn-primary" to="/courses?new=1">
                Add your first course
              </Link>
            }
          >
            Start by adding the courses you&apos;re taking this term. Then add assignments to them, and this page will
            show what&apos;s overdue, what&apos;s due today, and what&apos;s coming up this week.
          </Empty>
        </section>
      ) : (
        <>
          <WeekStrip data={data} />

          {(!data || data.overdue.length > 0) && (
            <Block title="Overdue" count={data?.overdue.length} tone="late">
              {today.isPending ? (
                <Loading label="Loading overdue assignments" rows={1} />
              ) : today.isError ? (
                <BlockError what="overdue assignments" error={today.error} retry={() => today.refetch()} />
              ) : (
                <ul className="list">
                  {data!.overdue.map((a) => (
                    <AssignmentRow key={a.id} a={a} today={data!.today} />
                  ))}
                </ul>
              )}
            </Block>
          )}

          <Block title="Schedule" count={data?.events.length} link="/week" linkLabel="Open week">
            {today.isPending ? (
              <Loading label="Loading today's schedule" rows={2} />
            ) : today.isError ? (
              <BlockError what="today's schedule" error={today.error} retry={() => today.refetch()} />
            ) : data!.events.length === 0 ? (
              <Empty compact>No classes or events today.</Empty>
            ) : (
              <Schedule items={data!.events} today={data!.today} />
            )}
          </Block>

          <Block title="Due today" count={data?.due_today.length}>
            {today.isPending ? (
              <Loading label="Loading today's assignments" rows={2} />
            ) : today.isError ? (
              <BlockError what="today's assignments" error={today.error} retry={() => today.refetch()} />
            ) : data!.due_today.length === 0 ? (
              <Empty compact>Nothing due today.</Empty>
            ) : (
              <ul className="list" id={`day-${data!.today}`}>
                {data!.due_today.map((a) => (
                  <AssignmentRow key={a.id} a={a} today={data!.today} showDay={false} />
                ))}
              </ul>
            )}
          </Block>

          <Block title="Next 7 days" count={data?.upcoming.length} link="/assignments" linkLabel="All open assignments">
            {today.isPending ? (
              <Loading label="Loading upcoming assignments" rows={3} />
            ) : today.isError ? (
              <BlockError what="upcoming assignments" error={today.error} retry={() => today.refetch()} />
            ) : data!.upcoming.length === 0 ? (
              <Empty compact>Nothing due in the next 7 days.</Empty>
            ) : (
              <Upcoming items={data!.upcoming} today={data!.today} />
            )}
          </Block>
        </>
      )}
    </div>
  )
}

function summary(today: string, overdue: number, dueToday: number): string {
  const parts = [formatLongDate(today)]
  if (overdue) parts.push(`${overdue} overdue`)
  parts.push(dueToday ? `${dueToday} due today` : 'nothing due today')
  return parts.join(' · ')
}

function Block({
  title,
  count,
  tone,
  link,
  linkLabel,
  children,
}: {
  title: string
  count?: number
  tone?: 'late'
  link?: string
  linkLabel?: string
  children: ReactNode
}) {
  const id = `h-${title.replaceAll(' ', '-').toLowerCase()}`
  return (
    <section className="section" aria-labelledby={id}>
      <div className="section-head">
        <h2 id={id} style={tone === 'late' && count ? { color: 'var(--negative)' } : undefined}>
          {title}
          {count !== undefined && count > 0 && <span className="muted num"> {count}</span>}
        </h2>
        {link && (
          <Link to={link} className="meta">
            {linkLabel}
          </Link>
        )}
      </div>
      <div className="panel">{children}</div>
    </section>
  )
}

function groupByDate(items: Assignment[]): [string, Assignment[]][] {
  const map = new Map<string, Assignment[]>()
  for (const a of items) map.set(a.due_date, [...(map.get(a.due_date) ?? []), a])
  return [...map.entries()]
}

function Upcoming({ items, today }: { items: Assignment[]; today: string }) {
  return (
    <div>
      {groupByDate(items).map(([date, list]) => (
        <div key={date} id={`day-${date}`} className="day-group">
          <div className="day-label">
            {relativeDay(date, today) === 'Tomorrow'
              ? `Tomorrow, ${formatMonthDay(date)}`
              : `${formatWeekday(date)}, ${formatMonthDay(date)}`}
          </div>
          <ul className="list">
            {list.map((a) => (
              <AssignmentRow key={a.id} a={a} today={today} showDay={false} />
            ))}
          </ul>
        </div>
      ))}
    </div>
  )
}

/** The week at a glance: one column per day, a mark per assignment in its course color. */
function WeekStrip({ data }: { data: ReturnType<typeof useToday>['data'] }) {
  if (!data) return <div className="panel week-strip" style={{ height: 94 }} aria-hidden="true" />
  const all = [...data.due_today, ...data.upcoming]
  const days = Array.from({ length: DAYS_AHEAD + 1 }, (_, i) => addDays(data.today, i))

  return (
    <section aria-label="This week's deadlines">
      <div className="panel week-strip">
        {days.map((date, i) => {
          const due = all.filter((a) => a.due_date === date)
          const label = i === 0 ? 'Today' : formatWeekday(date)
          return (
            <button
              type="button"
              key={date}
              className={i === 0 ? 'week-day is-today' : 'week-day'}
              aria-label={`${label}, ${formatMonthDay(date)}: ${plural(due.length, 'assignment')} due`}
              onClick={() => {
                const target = document.getElementById(`day-${date}`)
                target?.scrollIntoView({ behavior: 'smooth', block: 'center' })
              }}
              disabled={due.length === 0}
            >
              <span className="wd">
                <span>{label}</span>
                <b className="num">{date.slice(8).replace(/^0/, '')}</b>
              </span>
              <span className={due.length ? 'count' : 'count zero'}>{due.length}</span>
              <span className="ticks" aria-hidden="true">
                {due.slice(0, 8).map((a) => (
                  <span key={a.id} className="tick" style={{ background: a.course.color }} title={a.title} />
                ))}
              </span>
            </button>
          )
        })}
      </div>
    </section>
  )
}

function Schedule({ items, today }: { items: Occurrence[]; today: string }) {
  const events = useEventDialog()
  const [now] = useState(() => new Date().toISOString()) // refreshed whenever Today refetches
  const titles = new Map(items.map((o) => [o.event_id, o.title]))
  return (
    <ul className="list">
      {items.map((o) => (
        <li key={`${o.event_id}-${o.start}`}>
          <button
            type="button"
            className={o.end < now ? 'schedule-row is-past' : 'schedule-row'}
            onClick={() => events.open({ eventId: o.event_id })}
          >
            <span className="agenda-time num">
              {o.start_date < today ? 'Until ' : `${formatTime(o.start_time)}–`}
              {formatTime(o.end_time)}
              {o.end_date > today && <span className="time-note">ends next day</span>}
            </span>
            <span className="agenda-main">
              <span className="a-title">{o.title}</span>
              <span className="a-meta">
                <span
                  className="course-dot"
                  style={{ background: o.course?.color ?? 'var(--faint)' }}
                  aria-hidden="true"
                />
                <span className="course-name">
                  {[o.course?.code ?? o.course?.name, o.location].filter(Boolean).join(' · ') || 'No course'}
                </span>
              </span>
            </span>
            {o.conflicts_with.length > 0 && (
              <span className="agenda-conflict">
                <TriangleAlert size={13} aria-hidden="true" />
                Overlaps {o.conflicts_with.map((id) => titles.get(id) ?? 'another event').join(', ')}
              </span>
            )}
          </button>
        </li>
      ))}
    </ul>
  )
}
