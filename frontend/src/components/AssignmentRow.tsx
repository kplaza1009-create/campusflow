import { Check } from 'lucide-react'

import type { Assignment } from '../api'
import { daysBetween, formatTime, plural, relativeDay } from '../dates'
import { useSetCompleted } from '../queries'
import { useAssignmentDialog } from './assignmentDialogContext'

type Props = { a: Assignment; today: string; showDay?: boolean }

export function AssignmentRow({ a, today, showDay = true }: Props) {
  const complete = useSetCompleted()
  const dialog = useAssignmentDialog()
  const pending = complete.isPending
  const course = a.course.code ?? a.course.name

  return (
    <li className={a.completed ? 'a-row is-done' : 'a-row'} data-testid="assignment-row">
      <button
        type="button"
        role="checkbox"
        className="complete"
        aria-checked={a.completed}
        aria-busy={pending}
        aria-label={a.completed ? `Reopen ${a.title}` : `Mark ${a.title} done`}
        disabled={pending}
        onClick={() => complete.mutate({ a, completed: !a.completed })}
      >
        <span className="ring">
          <Check size={13} strokeWidth={3} aria-hidden="true" />
        </span>
      </button>

      <button type="button" className="a-main" onClick={() => dialog.open(a)} aria-label={`Edit ${a.title}`}>
        <span className="a-title">{a.title}</span>
        <span className="a-meta">
          <span className="course-dot" style={{ background: a.course.color }} aria-hidden="true" />
          <span className="course-name">{course}</span>
          {a.description && <span aria-hidden="true">·</span>}
          {a.description && <span className="course-name">{a.description}</span>}
        </span>
      </button>

      <DueLabel a={a} today={today} showDay={showDay} />
    </li>
  )
}

function DueLabel({ a, today, showDay }: { a: Assignment; today: string; showDay: boolean }) {
  const day = relativeDay(a.due_date, today)
  const time = a.due_time ? formatTime(a.due_time) : null
  const when = showDay ? (time ? `${day}, ${time}` : day) : (time ?? 'End of day')

  let late: string | null = null
  if (a.overdue) {
    const days = daysBetween(a.due_date, today)
    late = days <= 0 ? 'Overdue' : `${plural(days, 'day')} overdue`
  }

  return (
    <span className="a-due">
      <span className="when">{when}</span>
      {late ? <span className="late">{late}</span> : a.completed ? <span className="sub">Done</span> : null}
    </span>
  )
}
