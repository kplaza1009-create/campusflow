import { useQueryClient } from '@tanstack/react-query'
import { X } from 'lucide-react'
import { useCallback, useEffect, useId, useLayoutEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { Link } from 'react-router'
import { toast } from 'sonner'

import {
  ApiError,
  api,
  REMINDER_OPTIONS,
  type Assignment,
  type AssignmentInput,
  type Course,
  type DuplicateWarning,
} from '../api'
import { formatTime, relativeDay, todayIn } from '../dates'
import { invalidateWork, useCourses } from '../queries'
import { useUser } from '../session'
import { errorMessage } from '../errors'
import { isTyping, remember, remembered } from '../remember'
import { AssignmentDialogContext } from './assignmentDialogContext'

// Remembered between sessions on this device, so adding several assignments
// for one course takes one field instead of three.
const LAST_COURSE = 'campusflow:last-course'
const LAST_REMINDER = 'campusflow:last-reminder'

export function AssignmentDialogProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<{ open: boolean; editing: Assignment | null; session: number }>({
    open: false,
    editing: null,
    session: 0,
  })
  const open = useCallback(
    (a?: Assignment | null) => setState((s) => ({ open: true, editing: a ?? null, session: s.session + 1 })),
    [],
  )
  const close = useCallback(() => setState((s) => ({ ...s, open: false })), [])

  // "n" anywhere (outside a text field) starts a new assignment.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'n' || e.metaKey || e.ctrlKey || e.altKey || isTyping(e.target) || state.open) return
      e.preventDefault()
      open()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, state.open])

  const value = useMemo(() => ({ open }), [open])
  return (
    <AssignmentDialogContext.Provider value={value}>
      {children}
      {state.open && <AssignmentDialog key={state.session} editing={state.editing} onClose={close} />}
    </AssignmentDialogContext.Provider>
  )
}

type FormState = {
  title: string
  course_id: string
  due_date: string
  has_time: boolean
  due_time: string
  reminder: string // "" = no reminder
  description: string
}

function initialForm(editing: Assignment | null, defaultCourse: string, today: string): FormState {
  if (editing) {
    return {
      title: editing.title,
      course_id: String(editing.course.id),
      due_date: editing.due_date,
      has_time: !!editing.due_time,
      due_time: editing.due_time?.slice(0, 5) ?? '23:59',
      reminder: editing.reminder_minutes === null ? '' : String(editing.reminder_minutes),
      description: editing.description ?? '',
    }
  }
  return {
    title: '',
    course_id: defaultCourse,
    due_date: today,
    has_time: false,
    due_time: '23:59',
    reminder: remembered(LAST_REMINDER) ?? '1440',
    description: '',
  }
}

function AssignmentDialog({ editing, onClose }: { editing: Assignment | null; onClose: () => void }) {
  const courses = useCourses()
  const ref = useRef<HTMLDialogElement>(null)
  const ids = useId()
  // The form registers its close handler here, so Escape and the X button can
  // ask before discarding edits.
  const requestClose = useRef<() => void>(onClose)

  // A layout effect, so the dialog is open before the form's effects focus a field.
  useLayoutEffect(() => {
    const d = ref.current
    if (d && !d.open) d.showModal()
  }, [])

  const active = courses.data ?? []

  return (
    <dialog
      ref={ref}
      className="sheet"
      aria-labelledby={`${ids}-heading`}
      onCancel={(e) => {
        e.preventDefault() // Escape: ask before throwing away edits
        requestClose.current()
      }}
      onClose={onClose}
    >
      <div className="sheet-head">
        <h2 id={`${ids}-heading`}>{editing ? 'Edit assignment' : 'New assignment'}</h2>
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
        {courses.isPending ? (
          <p className="muted" role="status">
            Loading your courses…
          </p>
        ) : courses.isError ? (
          <div className="notice error" role="alert">
            Couldn&apos;t load your courses, so this form can&apos;t open yet.
            <div className="notice-actions">
              <button type="button" className="btn btn-secondary btn-small" onClick={() => courses.refetch()}>
                Try again
              </button>
            </div>
          </div>
        ) : active.length === 0 && !editing ? (
          <div className="empty compact" style={{ padding: 0 }}>
            <p>Assignments belong to a course. Add your first course, then come back here.</p>
            <Link className="btn btn-primary" to="/courses?new=1" onClick={onClose}>
              Add a course
            </Link>
          </div>
        ) : (
          <AssignmentForm editing={editing} courses={active} onClose={onClose} closeRef={requestClose} />
        )}
      </div>
    </dialog>
  )
}

function AssignmentForm({
  editing,
  courses: active,
  onClose,
  closeRef,
}: {
  editing: Assignment | null
  courses: Course[]
  onClose: () => void
  closeRef: { current: () => void }
}) {
  const user = useUser()
  const qc = useQueryClient()
  const ids = useId()
  const today = todayIn(user.time_zone)

  const [initial] = useState<FormState>(() => {
    const last = remembered(LAST_COURSE)
    const defaultCourse = active.some((c) => String(c.id) === last) ? last! : String(active[0]?.id ?? '')
    return initialForm(editing, defaultCourse, today)
  })
  const [form, setForm] = useState<FormState>(initial)
  // One key per attempt to create this assignment: a retry after a timeout or a
  // double-click reuses it, so the server can't create two records.
  const [idempotencyKey] = useState(() => crypto.randomUUID())
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<ApiError | null>(null)
  const [duplicate, setDuplicate] = useState<DuplicateWarning | null>(null)
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

  const set = <K extends keyof FormState>(key: K, value: FormState[K]) => {
    setForm((f) => ({ ...f, [key]: value }))
    setDuplicate(null)
  }

  const body = (f: FormState, allowDuplicate: boolean): AssignmentInput => ({
    title: f.title,
    course_id: Number(f.course_id),
    description: f.description.trim() ? f.description : null,
    due_date: f.due_date,
    due_time: f.has_time ? f.due_time : null,
    reminder_minutes: f.reminder === '' ? null : Number(f.reminder),
    allow_duplicate: allowDuplicate,
  })

  const save = async (allowDuplicate = false) => {
    if (saving) return
    setSaving(true)
    setError(null)
    try {
      const saved = editing
        ? await api.updateAssignment(editing.id, body(form, allowDuplicate))
        : await api.createAssignment(body(form, allowDuplicate), idempotencyKey)
      if (!editing) {
        remember(LAST_COURSE, form.course_id)
        remember(LAST_REMINDER, form.reminder)
      }
      await invalidateWork(qc)
      const note =
        saved.time_note === 'ambiguous_earlier' && saved.due_time
          ? `${formatTime(saved.due_time)} happens twice that night because clocks fall back. We used the first one.`
          : undefined
      toast.success(editing ? 'Changes saved' : `Added “${saved.title}”`, { description: note })
      onClose()
    } catch (err) {
      // Nothing is cleared: the form keeps every value so the user can retry.
      if (err instanceof ApiError && err.duplicate) setDuplicate(err.duplicate)
      else setError(err instanceof ApiError ? err : new ApiError(0, errorMessage(err), null))
    } finally {
      setSaving(false)
    }
  }

  const remove = async () => {
    if (!editing) return
    setSaving(true)
    setError(null)
    try {
      await api.deleteAssignment(editing.id)
      await invalidateWork(qc)
      toast.success(`Deleted “${editing.title}”`)
      onClose()
    } catch (err) {
      setError(err instanceof ApiError ? err : new ApiError(0, errorMessage(err), null))
      setConfirmDelete(false)
    } finally {
      setSaving(false)
    }
  }

  const fieldError = (name: string) => error?.fields[name]
  const titleId = `${ids}-title`

  return (
    <form
      className="form"
      noValidate
      onSubmit={(e) => {
        e.preventDefault()
        void save()
      }}
      onKeyDown={(e) => {
        if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) {
          e.preventDefault()
          void save()
        }
      }}
    >
      <div className="field">
        <label htmlFor={titleId}>Title</label>
        <input
          id={titleId}
          ref={titleRef}
          className="input"
          required
          maxLength={200}
          value={form.title}
          onChange={(e) => set('title', e.target.value)}
          aria-invalid={!!fieldError('title')}
          aria-describedby={fieldError('title') ? `${titleId}-err` : undefined}
          placeholder="Problem set 4"
          autoComplete="off"
        />
        {fieldError('title') && (
          <span className="error" id={`${titleId}-err`}>
            {fieldError('title')}
          </span>
        )}
      </div>

      <div className="row-2">
        <div className="field">
          <label htmlFor={`${ids}-course`}>Course</label>
          <select
            id={`${ids}-course`}
            className="select"
            value={form.course_id}
            onChange={(e) => set('course_id', e.target.value)}
            aria-invalid={!!fieldError('course_id')}
          >
            {editing?.course.archived && <option value={editing.course.id}>{editing.course.name} (archived)</option>}
            {active.map((c) => (
              <option key={c.id} value={c.id}>
                {c.code ? `${c.code} · ${c.name}` : c.name}
              </option>
            ))}
          </select>
          {fieldError('course_id') && <span className="error">{fieldError('course_id')}</span>}
        </div>

        <div className="field">
          <label htmlFor={`${ids}-date`}>Due date</label>
          <input
            id={`${ids}-date`}
            type="date"
            className="input"
            required
            value={form.due_date}
            onChange={(e) => set('due_date', e.target.value)}
            aria-invalid={!!fieldError('due_date')}
          />
          {fieldError('due_date') && <span className="error">{fieldError('due_date')}</span>}
        </div>
      </div>

      <div className="field">
        <label className="check-label">
          <input type="checkbox" checked={form.has_time} onChange={(e) => set('has_time', e.target.checked)} />
          Due at a specific time
        </label>
        {form.has_time ? (
          <div className="row-2">
            <input
              type="time"
              className="input"
              aria-label="Due time"
              value={form.due_time}
              onChange={(e) => set('due_time', e.target.value)}
              aria-invalid={!!fieldError('due_time')}
            />
            <span className="hint" style={{ alignSelf: 'center' }}>
              {user.time_zone.replaceAll('_', ' ')} time
            </span>
          </div>
        ) : (
          <span className="hint">Without a time, it&apos;s due by the end of the day (11:59 PM).</span>
        )}
        {fieldError('due_time') && <span className="error">{fieldError('due_time')}</span>}
      </div>

      <div className="field">
        <label htmlFor={`${ids}-reminder`}>Reminder</label>
        <select
          id={`${ids}-reminder`}
          className="select"
          value={form.reminder}
          onChange={(e) => set('reminder', e.target.value)}
        >
          {REMINDER_OPTIONS.map((o) => (
            <option key={o.label} value={o.value === null ? '' : String(o.value)}>
              {o.label}
            </option>
          ))}
        </select>
        <span className="hint">Saved with the assignment. Reminder delivery arrives in the next release.</span>
      </div>

      <div className="field">
        <label htmlFor={`${ids}-notes`}>
          Notes <span className="muted">(optional)</span>
        </label>
        <textarea
          id={`${ids}-notes`}
          className="textarea"
          rows={2}
          maxLength={5000}
          value={form.description}
          onChange={(e) => set('description', e.target.value)}
          placeholder="Chapters, submission link, group members…"
        />
      </div>

      {duplicate && (
        <div className="notice" role="alert">
          <strong>This looks like one you already have.</strong>
          <span>
            {duplicate.matches
              .map(
                (m) =>
                  `“${m.title}” in ${m.course.code ?? m.course.name}, due ${relativeDay(m.due_date, today)}${m.completed ? ' (done)' : ''}`,
              )
              .join('; ')}
            .
          </span>
          <div className="notice-actions">
            <button
              type="button"
              className="btn btn-secondary btn-small"
              onClick={() => void save(true)}
              disabled={saving}
            >
              Save anyway
            </button>
            <button type="button" className="btn btn-ghost btn-small" onClick={() => setDuplicate(null)}>
              Keep editing
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
          <strong>Delete “{editing?.title}”? This can&apos;t be undone.</strong>
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
              Delete assignment
            </button>
          </div>
        </div>
      ) : (
        <div className="form-actions">
          {editing && (
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
          <button type="submit" className="btn btn-primary" disabled={saving} aria-busy={saving}>
            {saving ? 'Saving…' : editing ? 'Save changes' : 'Add assignment'}
          </button>
        </div>
      )}
    </form>
  )
}
