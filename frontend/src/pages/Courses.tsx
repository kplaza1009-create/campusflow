import { useQueryClient } from '@tanstack/react-query'
import { ChevronRight, Plus } from 'lucide-react'
import { useEffect, useId, useRef, useState } from 'react'
import { useSearchParams } from 'react-router'
import { toast } from 'sonner'

import { ApiError, api, COURSE_COLORS, type Course, type CourseInput } from '../api'
import { BlockError, Empty, Loading } from '../components/States'
import { errorMessage } from '../errors'
import { plural } from '../dates'
import { invalidateWork, useCourses } from '../queries'

export function CoursesPage() {
  const courses = useCourses(true)
  const [params, setParams] = useSearchParams()
  // "?new=1" (from Today or the assignment form) opens the new-course form.
  const [creatingState, setCreating] = useState(false)
  const creating = creatingState || params.has('new')
  const closeCreate = () => {
    setCreating(false)
    if (params.has('new')) setParams({}, { replace: true })
  }
  const [editingId, setEditingId] = useState<number | null>(null)
  const [deleting, setDeleting] = useState<Course | null>(null)

  const all = courses.data ?? []
  const active = all.filter((c) => !c.archived)
  const archived = all.filter((c) => c.archived)

  const row = (c: Course) => (
    <li key={c.id}>
      {editingId === c.id ? (
        <div className="course-edit">
          <CourseForm
            course={c}
            onDone={() => setEditingId(null)}
            onDelete={() => {
              setEditingId(null)
              setDeleting(c)
            }}
          />
        </div>
      ) : (
        <div className="course-row">
          <span className="course-dot" style={{ background: c.color }} aria-hidden="true" />
          <div style={{ minWidth: 0 }}>
            <div className="name">{c.name}</div>
            <div className="sub">{[c.code, c.instructor].filter(Boolean).join(' · ') || 'No code or instructor'}</div>
          </div>
          <span className="open num">{c.archived ? 'Archived' : `${c.open_assignments} open`}</span>
          <button type="button" className="btn btn-ghost btn-small" onClick={() => setEditingId(c.id)} aria-label={`Edit ${c.name}`}>
            Edit
          </button>
        </div>
      )}
    </li>
  )

  return (
    <div className="page">
      <div className="title-row">
        <div>
          <h1>Courses</h1>
          <p aria-live="polite">
            {courses.data ? `${active.length} active${archived.length ? ` · ${archived.length} archived` : ''}` : ' '}
          </p>
        </div>
        <div className="actions">
          {!creating && (
            <button type="button" className="btn btn-primary" onClick={() => setCreating(true)}>
              <Plus size={16} aria-hidden="true" />
              New course
            </button>
          )}
        </div>
      </div>

      {creating && (
        <section className="panel panel-pad" aria-labelledby="new-course">
          <h2 id="new-course" style={{ marginBottom: 16 }}>
            New course
          </h2>
          <CourseForm onDone={closeCreate} />
        </section>
      )}

      <section className="panel" aria-label="Active courses">
        {courses.isPending ? (
          <Loading label="Loading courses" rows={3} />
        ) : courses.isError ? (
          <BlockError what="courses" error={courses.error} retry={() => courses.refetch()} />
        ) : active.length === 0 ? (
          <Empty>
            {archived.length
              ? 'All your courses are archived. Add a course for this term, or restore one below.'
              : 'No courses yet. Add each class you’re taking: its name, an optional code like CS248, and a color you’ll recognize in lists.'}
          </Empty>
        ) : (
          <ul className="list">{active.map(row)}</ul>
        )}
      </section>

      {archived.length > 0 && (
        <details className="archived section">
          <summary>
            <ChevronRight size={16} aria-hidden="true" />
            Archived courses ({archived.length})
          </summary>
          <p className="muted">Archived courses keep their assignments. They&apos;re hidden from Today, lists, and course pickers.</p>
          <div className="panel">
            <ul className="list">{archived.map(row)}</ul>
          </div>
        </details>
      )}

      {deleting && <DeleteCourseDialog course={deleting} onClose={() => setDeleting(null)} />}
    </div>
  )
}

function CourseForm({ course, onDone, onDelete }: { course?: Course; onDone: () => void; onDelete?: () => void }) {
  const qc = useQueryClient()
  const ids = useId()
  const [form, setForm] = useState<CourseInput>({
    name: course?.name ?? '',
    code: course?.code ?? '',
    instructor: course?.instructor ?? '',
    color: course?.color ?? COURSE_COLORS[0].value,
  })
  const [idempotencyKey] = useState(() => crypto.randomUUID())
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<ApiError | null>(null)

  const save = async (archived = course?.archived ?? false) => {
    setSaving(true)
    setError(null)
    try {
      const body = { ...form, code: form.code || null, instructor: form.instructor || null }
      if (course) {
        await api.updateCourse(course.id, { ...body, archived })
        if (archived !== course.archived) {
          toast.success(
            archived ? `Archived ${course.name}. Its assignments are kept.` : `Restored ${course.name}.`,
          )
        } else toast.success('Course saved')
      } else {
        const created = await api.createCourse(body, idempotencyKey)
        toast.success(`Added ${created.name}`)
      }
      await invalidateWork(qc)
      onDone()
    } catch (err) {
      setError(err instanceof ApiError ? err : new ApiError(0, errorMessage(err), null))
    } finally {
      setSaving(false)
    }
  }

  const set = (key: keyof CourseInput, value: string) => setForm((f) => ({ ...f, [key]: value }))

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
        <label htmlFor={`${ids}-name`}>Course name</label>
        <input
          id={`${ids}-name`}
          className="input"
          autoFocus
          required
          maxLength={120}
          value={form.name}
          onChange={(e) => set('name', e.target.value)}
          aria-invalid={!!error?.fields.name}
          placeholder="Database Systems I"
          autoComplete="off"
        />
        {error?.fields.name && <span className="error">{error.fields.name}</span>}
      </div>
      <div className="row-2">
        <div className="field">
          <label htmlFor={`${ids}-code`}>
            Code <span className="muted">(optional)</span>
          </label>
          <input
            id={`${ids}-code`}
            className="input"
            maxLength={40}
            value={form.code ?? ''}
            onChange={(e) => set('code', e.target.value)}
            placeholder="CS248"
            autoComplete="off"
          />
        </div>
        <div className="field">
          <label htmlFor={`${ids}-instructor`}>
            Instructor <span className="muted">(optional)</span>
          </label>
          <input
            id={`${ids}-instructor`}
            className="input"
            maxLength={120}
            value={form.instructor ?? ''}
            onChange={(e) => set('instructor', e.target.value)}
            autoComplete="off"
          />
        </div>
      </div>
      <div className="field">
        <span className="label" id={`${ids}-color`}>
          Color
        </span>
        <div className="swatches" role="radiogroup" aria-labelledby={`${ids}-color`}>
          {COURSE_COLORS.map((c) => (
            <button
              key={c.value}
              type="button"
              role="radio"
              className="swatch"
              aria-checked={form.color === c.value}
              aria-label={c.name}
              style={{ background: c.value }}
              onClick={() => set('color', c.value)}
            />
          ))}
        </div>
      </div>

      {error && (
        <div className="notice error" role="alert">
          {Object.keys(error.fields).length ? 'Check the highlighted fields.' : errorMessage(error)}
        </div>
      )}

      <div className="form-actions">
        {course && (
          <span className="spacer" style={{ display: 'flex', gap: 8 }}>
            <button type="button" className="btn btn-secondary btn-small" onClick={() => void save(!course.archived)} disabled={saving}>
              {course.archived ? 'Restore' : 'Archive'}
            </button>
            <button type="button" className="btn btn-danger btn-small" onClick={onDelete} disabled={saving}>
              Delete…
            </button>
          </span>
        )}
        <button type="button" className="btn btn-secondary" onClick={onDone} disabled={saving}>
          Cancel
        </button>
        <button type="submit" className="btn btn-primary" disabled={saving}>
          {saving ? 'Saving…' : course ? 'Save course' : 'Add course'}
        </button>
      </div>
    </form>
  )
}

function DeleteCourseDialog({ course, onClose }: { course: Course; onClose: () => void }) {
  const qc = useQueryClient()
  const ref = useRef<HTMLDialogElement>(null)
  const ids = useId()
  const [impact, setImpact] = useState<number | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [understood, setUnderstood] = useState(false)
  const [deleting, setDeleting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const loadImpact = () => {
    setLoadError(null)
    api
      .deletionImpact(course.id)
      .then((r) => setImpact(r.assignments))
      .catch((err: unknown) => setLoadError(errorMessage(err)))
  }

  useEffect(() => {
    ref.current?.showModal()
    loadImpact()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const confirm = async () => {
    if (impact === null) return
    setDeleting(true)
    setError(null)
    try {
      await api.deleteCourse(course.id, impact)
      await invalidateWork(qc)
      toast.success(`Deleted ${course.name}${impact ? ` and ${plural(impact, 'assignment')}` : ''}`)
      onClose()
    } catch (err) {
      setError(errorMessage(err))
      if (err instanceof ApiError && err.status === 409) {
        setUnderstood(false)
        loadImpact() // the count changed since the user confirmed; show the new one
      }
    } finally {
      setDeleting(false)
    }
  }

  return (
    <dialog
      ref={ref}
      className="sheet"
      aria-labelledby={`${ids}-h`}
      onCancel={(e) => {
        e.preventDefault()
        if (!deleting) onClose()
      }}
    >
      <div className="sheet-head">
        <h2 id={`${ids}-h`}>Delete {course.name}?</h2>
      </div>
      <div className="sheet-body form">
        {loadError ? (
          <div className="notice error" role="alert">
            Couldn&apos;t check what this would delete. {loadError}
            <div className="notice-actions">
              <button type="button" className="btn btn-secondary btn-small" onClick={loadImpact}>
                Try again
              </button>
            </div>
          </div>
        ) : impact === null ? (
          <p className="muted" role="status">
            Checking what this would delete…
          </p>
        ) : (
          <>
            <p>
              This permanently deletes the course
              {impact > 0 ? (
                <>
                  {' '}
                  and <strong className="num">{plural(impact, 'assignment')}</strong> in it
                </>
              ) : null}
              . This can&apos;t be undone.
            </p>
            {!course.archived && (
              <p className="muted">To keep the work but hide the course, archive it instead.</p>
            )}
            <label className="check-label">
              <input type="checkbox" checked={understood} onChange={(e) => setUnderstood(e.target.checked)} />
              {impact > 0 ? `Yes, delete the course and its ${plural(impact, 'assignment')}` : 'Yes, delete this course'}
            </label>
          </>
        )}
        {error && (
          <div className="notice error" role="alert">
            {error}
          </div>
        )}
        <div className="form-actions">
          <button type="button" className="btn btn-secondary" onClick={onClose} disabled={deleting} autoFocus>
            Cancel
          </button>
          <button
            type="button"
            className="btn btn-danger-solid"
            onClick={() => void confirm()}
            disabled={!understood || impact === null || deleting}
          >
            {deleting ? 'Deleting…' : 'Delete course'}
          </button>
        </div>
      </div>
    </dialog>
  )
}
