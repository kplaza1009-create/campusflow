import { useQuery, useQueryClient } from '@tanstack/react-query'
import { X } from 'lucide-react'
import { useCallback, useEffect, useId, useLayoutEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { toast } from 'sonner'

import {
  ApiError,
  api,
  EVENT_KINDS,
  type CalendarEvent,
  type Course,
  type EventInput,
  type EventKind,
  type ScheduleConflict,
} from '../api'
import { addDays, formatLongDate, formatMonthDay, formatTime, formatWeekday, plural, todayIn } from '../dates'
import { errorMessage } from '../errors'
import { invalidateWork, useCourses } from '../queries'
import { remember, remembered } from '../remember'
import { useUser } from '../session'
import { EventDialogContext, type EventDialogOptions } from './eventDialogContext'

const LAST_COURSE = 'campusflow:last-event-course'
const WEEKDAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']
const TERM_DAYS = 7 * 15 - 1 // default length of a new weekly class: about a semester

export function EventDialogProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<{ open: boolean; options: EventDialogOptions; session: number }>({
    open: false,
    options: {},
    session: 0,
  })
  const open = useCallback(
    (options: EventDialogOptions = {}) => setState((s) => ({ open: true, options, session: s.session + 1 })),
    [],
  )
  const close = useCallback(() => setState((s) => ({ ...s, open: false })), [])
  const value = useMemo(() => ({ open }), [open])
  return (
    <EventDialogContext.Provider value={value}>
      {children}
      {state.open && <EventDialog key={state.session} options={state.options} onClose={close} />}
    </EventDialogContext.Provider>
  )
}

type Form = {
  title: string
  kind: EventKind
  course_id: string // "" = none
  location: string
  weekly: boolean
  date: string // one-time: the start date
  start_time: string // "HH:MM"
  end_time: string
  weekdays: number[]
  first_date: string
  last_date: string
}

const weekdayOf = (date: string) => (new Date(`${date}T00:00:00Z`).getUTCDay() + 6) % 7
const hhmm = (t: string) => t.slice(0, 5)
const plusHour = (t: string) => `${String((Number(t.slice(0, 2)) + 1) % 24).padStart(2, '0')}${t.slice(2, 5)}`
/** With an end time at or before the start time, the event ends the next day. */
const endsNextDay = (f: Form) => f.end_time <= f.start_time

function newForm(options: EventDialogOptions, today: string, courses: Course[]): Form {
  const date = options.date ?? today
  const start = options.startTime ?? `${String(Math.min(new Date().getHours() + 1, 23)).padStart(2, '0')}:00`
  const last = remembered(LAST_COURSE)
  return {
    title: '',
    kind: 'other',
    course_id: courses.some((c) => String(c.id) === last) ? last! : '',
    location: '',
    weekly: false,
    date,
    start_time: start,
    end_time: plusHour(start),
    weekdays: [weekdayOf(date)],
    first_date: date,
    last_date: addDays(date, TERM_DAYS),
  }
}

function editForm(e: CalendarEvent): Form {
  const s = e.schedule
  const base = {
    title: e.title,
    kind: e.kind,
    course_id: e.course ? String(e.course.id) : '',
    location: e.location ?? '',
    start_time: hhmm(s.start_time),
    end_time: hhmm(s.end_time),
  }
  if (s.type === 'weekly') {
    return {
      ...base,
      weekly: true,
      date: s.first_date,
      weekdays: s.weekdays,
      first_date: s.first_date,
      last_date: s.last_date,
    }
  }
  return {
    ...base,
    weekly: false,
    date: s.start_date,
    weekdays: [weekdayOf(s.start_date)],
    first_date: s.start_date,
    last_date: addDays(s.start_date, TERM_DAYS),
  }
}

function toInput(f: Form, allowConflicts: boolean): EventInput {
  return {
    title: f.title,
    kind: f.kind,
    course_id: f.course_id ? Number(f.course_id) : null,
    location: f.location.trim() || null,
    schedule: f.weekly
      ? {
          type: 'weekly',
          weekdays: f.weekdays,
          start_time: f.start_time,
          end_time: f.end_time,
          first_date: f.first_date,
          last_date: f.last_date,
        }
      : {
          type: 'once',
          start_date: f.date,
          start_time: f.start_time,
          end_date: endsNextDay(f) ? addDays(f.date, 1) : f.date,
          end_time: f.end_time,
        },
    allow_conflicts: allowConflicts,
  }
}

function EventDialog({ options, onClose }: { options: EventDialogOptions; onClose: () => void }) {
  const courses = useCourses()
  const existing = useQuery({
    queryKey: ['event', options.eventId],
    queryFn: () => api.event(options.eventId!),
    enabled: options.eventId !== undefined,
    staleTime: 0,
  })
  const ref = useRef<HTMLDialogElement>(null)
  const ids = useId()
  const requestClose = useRef<() => void>(onClose)
  const editing = options.eventId !== undefined

  useLayoutEffect(() => {
    const d = ref.current
    if (d && !d.open) d.showModal()
  }, [])

  const loading = courses.isPending || (editing && existing.isPending)
  const failed = courses.isError || (editing && existing.isError)

  return (
    <dialog
      ref={ref}
      className="sheet"
      aria-labelledby={`${ids}-heading`}
      onCancel={(e) => {
        e.preventDefault()
        requestClose.current()
      }}
      onClose={onClose}
    >
      <div className="sheet-head">
        <h2 id={`${ids}-heading`}>{editing ? 'Edit event' : 'New event'}</h2>
        <button
          type="button"
          className="btn btn-ghost btn-icon"
          onClick={() => requestClose.current()}
          aria-label="Close"
        >
          <X size={18} aria-hidden="true" />
        </button>
      </div>
      <div className="sheet-body">
        {loading ? (
          <p className="muted" role="status">
            Loading…
          </p>
        ) : failed ? (
          <div className="notice error" role="alert">
            {existing.error instanceof ApiError && existing.error.status === 404
              ? 'This event no longer exists.'
              : "Couldn't load this form. Check your connection and try again."}
            <div className="notice-actions">
              <button
                type="button"
                className="btn btn-secondary btn-small"
                onClick={() => {
                  void courses.refetch()
                  if (editing) void existing.refetch()
                }}
              >
                Try again
              </button>
            </div>
          </div>
        ) : (
          <EventForm
            options={options}
            existing={existing.data ?? null}
            courses={courses.data ?? []}
            onClose={onClose}
            closeRef={requestClose}
          />
        )}
      </div>
    </dialog>
  )
}

function EventForm({
  options,
  existing,
  courses,
  onClose,
  closeRef,
}: {
  options: EventDialogOptions
  existing: CalendarEvent | null
  courses: Course[]
  onClose: () => void
  closeRef: { current: () => void }
}) {
  const user = useUser()
  const qc = useQueryClient()
  const ids = useId()
  const today = todayIn(user.time_zone)
  const [initial] = useState<Form>(() => (existing ? editForm(existing) : newForm(options, today, courses)))
  const [form, setForm] = useState<Form>(initial)
  const [kindTouchedRepeat, setKindTouchedRepeat] = useState(!!existing)
  const [idempotencyKey] = useState(() => crypto.randomUUID())
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<ApiError | null>(null)
  const [conflict, setConflict] = useState<ScheduleConflict | null>(null)
  const [confirmDiscard, setConfirmDiscard] = useState(false)
  const [confirmDelete, setConfirmDelete] = useState(false)
  const titleRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    titleRef.current?.focus()
  }, [])

  const dirty = JSON.stringify(form) !== JSON.stringify(initial)
  const requestClose = () => {
    if (saving) return
    if (dirty) setConfirmDiscard(true)
    else onClose()
  }
  useEffect(() => {
    closeRef.current = requestClose
  })

  const set = <K extends keyof Form>(key: K, value: Form[K]) => {
    setForm((f) => ({ ...f, [key]: value }))
    setConflict(null)
  }
  // Picking "Class" for a new event switches on weekly repeat, once.
  const setKind = (kind: EventKind) => {
    setForm((f) => ({ ...f, kind, weekly: !kindTouchedRepeat && kind === 'class' ? true : f.weekly }))
    setConflict(null)
  }
  const toggleDay = (d: number) =>
    set('weekdays', form.weekdays.includes(d) ? form.weekdays.filter((x) => x !== d) : [...form.weekdays, d].sort())

  // A weekly class keeps the zone it was created in; say so when that differs.
  const zone = existing?.recurring && form.weekly ? existing.time_zone : user.time_zone
  const options_ = courses.filter((c) => !c.archived)

  const save = async (allowConflicts = false) => {
    if (saving) return
    if (form.weekly && form.weekdays.length === 0) {
      setError(
        new ApiError(422, 'Choose at least one day of the week.', null, { weekdays: 'Choose at least one day.' }),
      )
      return
    }
    setSaving(true)
    setError(null)
    try {
      const body = toInput(form, allowConflicts)
      const saved = existing ? await api.updateEvent(existing.id, body) : await api.createEvent(body, idempotencyKey)
      if (!existing && form.course_id) remember(LAST_COURSE, form.course_id)
      await Promise.all([invalidateWork(qc), qc.invalidateQueries({ queryKey: ['event'] })])
      toast.success(
        existing ? (saved.recurring ? 'Every meeting updated' : 'Event updated') : `Added “${saved.title}”`,
        {
          description:
            saved.time_note === 'ambiguous_earlier'
              ? 'That time happens twice that night because clocks fall back. We used the first one.'
              : undefined,
        },
      )
      onClose()
    } catch (err) {
      if (err instanceof ApiError && err.scheduleConflict) setConflict(err.scheduleConflict)
      else setError(err instanceof ApiError ? err : new ApiError(0, errorMessage(err), null))
    } finally {
      setSaving(false)
    }
  }

  const remove = async () => {
    if (!existing) return
    setSaving(true)
    setError(null)
    try {
      await api.deleteEvent(existing.id)
      await invalidateWork(qc)
      toast.success(existing.recurring ? `Deleted every meeting of “${existing.title}”` : `Deleted “${existing.title}”`)
      onClose()
    } catch (err) {
      setError(err instanceof ApiError ? err : new ApiError(0, errorMessage(err), null))
      setConfirmDelete(false)
    } finally {
      setSaving(false)
    }
  }

  const field = (name: string) => error?.fields[name]

  return (
    <form
      className="form"
      noValidate
      onSubmit={(e) => {
        e.preventDefault()
        void save()
      }}
    >
      <div className="field">
        <label htmlFor={`${ids}-title`}>Title</label>
        <input
          id={`${ids}-title`}
          ref={titleRef}
          className="input"
          maxLength={200}
          value={form.title}
          onChange={(e) => set('title', e.target.value)}
          aria-invalid={!!field('title')}
          placeholder={form.kind === 'class' ? 'Calculus II lecture' : 'Study group'}
          autoComplete="off"
        />
        {field('title') && <span className="error">{field('title')}</span>}
      </div>

      <div className="row-2">
        <div className="field">
          <label htmlFor={`${ids}-kind`}>Type</label>
          <select
            id={`${ids}-kind`}
            className="select"
            value={form.kind}
            onChange={(e) => setKind(e.target.value as EventKind)}
          >
            {EVENT_KINDS.map((k) => (
              <option key={k.value} value={k.value}>
                {k.label}
              </option>
            ))}
          </select>
        </div>
        <div className="field">
          <label htmlFor={`${ids}-course`}>
            Course <span className="muted">(optional)</span>
          </label>
          <select
            id={`${ids}-course`}
            className="select"
            value={form.course_id}
            onChange={(e) => set('course_id', e.target.value)}
          >
            <option value="">No course</option>
            {existing?.course?.archived && (
              <option value={existing.course.id}>{existing.course.name} (archived)</option>
            )}
            {options_.map((c) => (
              <option key={c.id} value={c.id}>
                {c.code ? `${c.code} · ${c.name}` : c.name}
              </option>
            ))}
          </select>
          {field('course_id') && <span className="error">{field('course_id')}</span>}
        </div>
      </div>

      <label className="check-label">
        <input
          type="checkbox"
          checked={form.weekly}
          onChange={(e) => {
            setKindTouchedRepeat(true)
            set('weekly', e.target.checked)
          }}
        />
        Repeats every week
      </label>

      {form.weekly ? (
        <>
          <fieldset className="field days-field">
            <legend className="label">Meets on</legend>
            <div className="day-toggles">
              {WEEKDAYS.map((label, d) => (
                <button
                  key={label}
                  type="button"
                  className="day-toggle"
                  aria-pressed={form.weekdays.includes(d)}
                  onClick={() => toggleDay(d)}
                >
                  {label}
                </button>
              ))}
            </div>
            {field('weekdays') && <span className="error">{field('weekdays')}</span>}
          </fieldset>
          <div className="row-2">
            <TimeField
              id={`${ids}-start`}
              label="Starts"
              value={form.start_time}
              onChange={(v) => set('start_time', v)}
            />
            <TimeField id={`${ids}-end`} label="Ends" value={form.end_time} onChange={(v) => set('end_time', v)} />
          </div>
          <div className="row-2">
            <DateField
              id={`${ids}-first`}
              label="First class"
              value={form.first_date}
              onChange={(v) => set('first_date', v)}
            />
            <DateField
              id={`${ids}-last`}
              label="Last class"
              value={form.last_date}
              onChange={(v) => set('last_date', v)}
            />
          </div>
          <p className="hint">
            {endsNextDay(form) && form.end_time !== form.start_time ? 'Ends the next day. ' : ''}
            {existing?.recurring
              ? 'Changes apply to every meeting in this series. Skipping a single meeting, like a holiday, isn’t supported yet.'
              : 'Every meeting follows these times. Skipping a single meeting, like a holiday, isn’t supported yet.'}
          </p>
        </>
      ) : (
        <>
          <DateField id={`${ids}-date`} label="Date" value={form.date} onChange={(v) => set('date', v)} />
          <div className="row-2">
            <TimeField
              id={`${ids}-start`}
              label="Starts"
              value={form.start_time}
              onChange={(v) => set('start_time', v)}
            />
            <TimeField id={`${ids}-end`} label="Ends" value={form.end_time} onChange={(v) => set('end_time', v)} />
          </div>
          {endsNextDay(form) && form.end_time !== form.start_time && (
            <p className="hint">Ends the next day, {formatLongDate(addDays(form.date, 1))}.</p>
          )}
        </>
      )}
      {zone !== user.time_zone ? (
        <p className="hint">Times are in {zone.replaceAll('_', ' ')}, where this class was created.</p>
      ) : (
        <p className="hint">Times are in {zone.replaceAll('_', ' ')}.</p>
      )}

      <div className="field">
        <label htmlFor={`${ids}-location`}>
          Location <span className="muted">(optional)</span>
        </label>
        <input
          id={`${ids}-location`}
          className="input"
          maxLength={200}
          value={form.location}
          onChange={(e) => set('location', e.target.value)}
          placeholder="Room 214, Library 3F, Zoom"
          autoComplete="off"
        />
      </div>

      {conflict && (
        <div className="notice" role="alert">
          <strong>{conflict.message}</strong>
          <ul className="conflict-list">
            {conflict.conflicts.map((c) => (
              <li key={`${c.event_id}-${c.start}`}>
                <span
                  className="course-dot"
                  style={{ background: c.course?.color ?? 'var(--faint)' }}
                  aria-hidden="true"
                />
                {c.title}, {formatWeekday(c.start_date)} {formatMonthDay(c.start_date)}, {formatTime(c.start_time)}–
                {formatTime(c.end_time)}
              </li>
            ))}
          </ul>
          {conflict.total > conflict.conflicts.length && (
            <span>and {plural(conflict.total - conflict.conflicts.length, 'more time')}.</span>
          )}
          <div className="notice-actions">
            <button
              type="button"
              className="btn btn-secondary btn-small"
              onClick={() => void save(true)}
              disabled={saving}
            >
              Save anyway
            </button>
            <button type="button" className="btn btn-ghost btn-small" onClick={() => setConflict(null)}>
              Change the time
            </button>
          </div>
        </div>
      )}

      {error && (
        <div className="notice error" role="alert">
          {Object.keys(error.fields).length > 0 ? 'Check the highlighted fields.' : errorMessage(error)}
          {error.status !== 422 && <span>Your entries are still here. Try again when you&apos;re ready.</span>}
        </div>
      )}

      {confirmDiscard ? (
        <div className="notice" role="alert">
          <strong>Discard your changes?</strong>
          <div className="notice-actions">
            <button
              type="button"
              className="btn btn-secondary btn-small"
              onClick={() => setConfirmDiscard(false)}
              autoFocus
            >
              Keep editing
            </button>
            <button type="button" className="btn btn-danger btn-small" onClick={onClose}>
              Discard
            </button>
          </div>
        </div>
      ) : confirmDelete ? (
        <div className="notice error" role="alert">
          <strong>
            {existing?.recurring
              ? `Delete every meeting of “${existing.title}”? This can't be undone.`
              : `Delete “${existing?.title}”? This can't be undone.`}
          </strong>
          <div className="notice-actions">
            <button
              type="button"
              className="btn btn-secondary btn-small"
              onClick={() => setConfirmDelete(false)}
              autoFocus
            >
              Cancel
            </button>
            <button
              type="button"
              className="btn btn-danger-solid btn-small"
              onClick={() => void remove()}
              disabled={saving}
            >
              {existing?.recurring ? 'Delete series' : 'Delete event'}
            </button>
          </div>
        </div>
      ) : (
        <div className="form-actions">
          {existing && (
            <button
              type="button"
              className="btn btn-ghost spacer"
              onClick={() => setConfirmDelete(true)}
              disabled={saving}
            >
              Delete
            </button>
          )}
          <button type="button" className="btn btn-secondary" onClick={requestClose} disabled={saving}>
            Cancel
          </button>
          <button type="submit" className="btn btn-primary" disabled={saving}>
            {saving ? 'Saving…' : existing ? (existing.recurring ? 'Save all meetings' : 'Save changes') : 'Add event'}
          </button>
        </div>
      )}
    </form>
  )
}

function TimeField({
  id,
  label,
  value,
  onChange,
}: {
  id: string
  label: string
  value: string
  onChange: (v: string) => void
}) {
  return (
    <div className="field">
      <label htmlFor={id}>{label}</label>
      <input id={id} type="time" className="input" value={value} onChange={(e) => onChange(e.target.value)} required />
    </div>
  )
}

function DateField({
  id,
  label,
  value,
  onChange,
}: {
  id: string
  label: string
  value: string
  onChange: (v: string) => void
}) {
  return (
    <div className="field">
      <label htmlFor={id}>{label}</label>
      <input id={id} type="date" className="input" value={value} onChange={(e) => onChange(e.target.value)} required />
    </div>
  )
}
