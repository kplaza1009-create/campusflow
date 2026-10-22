import { Plus, Search, SlidersHorizontal } from 'lucide-react'
import { useEffect, useId, useMemo, useState } from 'react'
import { Link, useSearchParams } from 'react-router'

import { AssignmentRow } from '../components/AssignmentRow'
import { useAssignmentDialog } from '../components/assignmentDialogContext'
import { BlockError, Empty, Loading } from '../components/States'
import { plural, todayIn } from '../dates'
import { useAssignments, useCourses } from '../queries'
import { useUser } from '../session'

// Filters live in the URL, so the back button, reloads, and shared links keep them.
const DEFAULTS = { status: 'open', sort: 'due_asc' } as const
const FILTER_KEYS = ['q', 'course_id', 'status', 'overdue', 'due_from', 'due_to', 'sort', 'include_archived']

export function AssignmentsPage() {
  const user = useUser()
  const dialog = useAssignmentDialog()
  const courses = useCourses(true)
  const [params, setParams] = useSearchParams()
  const [filtersOpen, setFiltersOpen] = useState(false)
  const ids = useId()

  const get = (key: string) => params.get(key) ?? (DEFAULTS as Record<string, string>)[key] ?? ''
  const update = (key: string, value: string) =>
    setParams(
      (p) => {
        const next = new URLSearchParams(p)
        if (!value || value === (DEFAULTS as Record<string, string>)[key]) next.delete(key)
        else next.set(key, value)
        return next
      },
      { replace: true },
    )

  // Search updates the URL a moment after typing stops.
  const [search, setSearch] = useState(get('q'))
  useEffect(() => {
    const t = setTimeout(() => {
      if (search.trim() !== get('q')) update('q', search.trim())
    }, 250)
    return () => clearTimeout(t)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [search])

  const apiQuery = useMemo(() => {
    const q = new URLSearchParams()
    for (const key of FILTER_KEYS) {
      const v = params.get(key) ?? (DEFAULTS as Record<string, string>)[key]
      if (v) q.set(key, key === 'overdue' || key === 'include_archived' ? 'true' : v)
    }
    return q
  }, [params])

  const list = useAssignments(apiQuery)
  const filtered = FILTER_KEYS.some((k) => params.has(k) && k !== 'sort')
  const today = todayIn(user.time_zone)
  const activeCourses = (courses.data ?? []).filter((c) => !c.archived || String(c.id) === get('course_id'))
  const rows = list.data ?? []
  const overdueCount = rows.filter((a) => a.overdue).length

  return (
    <div className="page">
      <div className="title-row">
        <div>
          <h1>Assignments</h1>
          <p aria-live="polite">
            {list.data
              ? `${plural(rows.length, 'assignment')} shown${overdueCount ? ` · ${overdueCount} overdue` : ''}`
              : ' '}
          </p>
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

      <div className="toolbar" role="search" aria-label="Filter assignments">
        <div className="toolbar-row">
          <div className="search">
            <Search size={15} aria-hidden="true" />
            <label htmlFor={`${ids}-q`} className="visually-hidden">
              Search titles
            </label>
            <input
              id={`${ids}-q`}
              type="search"
              className="input"
              placeholder="Search titles"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
          </div>
          <div className="segmented" role="group" aria-label="Status">
            {[
              ['open', 'Open'],
              ['completed', 'Done'],
              ['all', 'All'],
            ].map(([value, label]) => (
              <button
                key={value}
                type="button"
                aria-pressed={get('status') === value}
                onClick={() => update('status', value)}
              >
                {label}
              </button>
            ))}
          </div>
          <button
            type="button"
            className="btn btn-secondary filters-toggle"
            aria-expanded={filtersOpen}
            aria-controls={`${ids}-more`}
            onClick={() => setFiltersOpen((o) => !o)}
          >
            <SlidersHorizontal size={15} aria-hidden="true" />
            Filters
          </button>
        </div>

        <div className="toolbar-row collapsible" id={`${ids}-more`} data-open={filtersOpen}>
          <label htmlFor={`${ids}-course`} className="visually-hidden">
            Course
          </label>
          <select
            id={`${ids}-course`}
            className="select"
            value={get('course_id')}
            onChange={(e) => update('course_id', e.target.value)}
          >
            <option value="">All courses</option>
            {activeCourses.map((c) => (
              <option key={c.id} value={c.id}>
                {c.code ? `${c.code} · ${c.name}` : c.name}
                {c.archived ? ' (archived)' : ''}
              </option>
            ))}
          </select>

          <span className="range">
            <label htmlFor={`${ids}-from`}>Due</label>
            <input
              id={`${ids}-from`}
              type="date"
              className="input"
              aria-label="Due from"
              value={get('due_from')}
              max={get('due_to') || undefined}
              onChange={(e) => update('due_from', e.target.value)}
            />
            <span aria-hidden="true">to</span>
            <input
              type="date"
              className="input"
              aria-label="Due to"
              value={get('due_to')}
              min={get('due_from') || undefined}
              onChange={(e) => update('due_to', e.target.value)}
            />
          </span>

          <label htmlFor={`${ids}-sort`} className="visually-hidden">
            Sort
          </label>
          <select
            id={`${ids}-sort`}
            className="select"
            value={get('sort')}
            onChange={(e) => update('sort', e.target.value)}
          >
            <option value="due_asc">Soonest deadline first</option>
            <option value="due_desc">Latest deadline first</option>
          </select>

          <label className="check-label">
            <input
              type="checkbox"
              checked={params.has('overdue')}
              onChange={(e) => update('overdue', e.target.checked ? '1' : '')}
            />
            Overdue only
          </label>
          <label className="check-label">
            <input
              type="checkbox"
              checked={params.has('include_archived')}
              onChange={(e) => update('include_archived', e.target.checked ? '1' : '')}
            />
            Include archived courses
          </label>
        </div>

        {filtered && (
          <div className="result-meta">
            <span>Filters applied</span>
            <button
              type="button"
              className="link-button"
              onClick={() => {
                setSearch('')
                setParams(new URLSearchParams(), { replace: true })
              }}
            >
              Clear filters
            </button>
          </div>
        )}
      </div>

      <section className="panel" aria-label="Assignment list" aria-busy={list.isFetching}>
        {list.isPending ? (
          <Loading label="Loading assignments" rows={5} />
        ) : list.isError ? (
          <BlockError what="assignments" error={list.error} retry={() => list.refetch()} />
        ) : rows.length > 0 ? (
          <ul className="list">
            {rows.map((a) => (
              <AssignmentRow key={a.id} a={a} today={today} />
            ))}
          </ul>
        ) : courses.data?.length === 0 ? (
          <Empty
            action={
              <Link className="btn btn-primary" to="/courses?new=1">
                Add a course
              </Link>
            }
          >
            Assignments belong to courses. Add a course first.
          </Empty>
        ) : filtered ? (
          <Empty>No assignments match these filters.</Empty>
        ) : get('status') === 'open' ? (
          <Empty
            action={
              <button type="button" className="btn btn-secondary" onClick={() => dialog.open()}>
                Add an assignment
              </button>
            }
          >
            No open assignments. Add one here, or press N anywhere.
          </Empty>
        ) : (
          <Empty>No assignments yet.</Empty>
        )}
      </section>
    </div>
  )
}
