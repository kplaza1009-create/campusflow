// Typed client for the CampusFlow API.
//
// The session lives in an HTTP-only cookie the browser sends automatically. For
// state-changing requests we also send the CSRF token the server issued with the
// session, which a cross-site page cannot read.

export type User = {
  id: number
  email: string
  time_zone: string
  email_reminders: boolean
  email_delivery: 'capture' | 'resend' // how this server handles reminder email
  is_demo: boolean // the shared demo account
  demo_reset_utc_hour: number // the demo is rebuilt every night at this hour (UTC)
}

export type AppNotification = {
  id: number
  title: string
  body: string
  assignment_id: number | null
  created_at: string
  read_at: string | null
}

export type ReminderStatus =
  'pending' | 'processing' | 'captured' | 'accepted' | 'delivered' | 'skipped' | 'cancelled' | 'failed'

export type Reminder = {
  id: number
  channel: 'in_app' | 'email'
  status: ReminderStatus
  send_at: string
  attempts: number
  next_attempt_at: string
  detail: string | null
  provider_status: string | null
  finished_at: string | null
}
export type Session = { user: User | null; csrf_token: string | null }

export type Course = {
  id: number
  name: string
  code: string | null
  instructor: string | null
  color: string
  archived: boolean
  open_assignments: number
}

export type CourseBrief = Pick<Course, 'id' | 'name' | 'code' | 'color' | 'archived'>

export type Assignment = {
  id: number
  title: string
  description: string | null
  course: CourseBrief
  due_date: string // local calendar date in the account's time zone
  due_time: string | null // local "HH:MM:SS", null for date-only
  due_at: string | null // exact UTC instant, null for date-only
  all_day: boolean
  deadline: string // the instant it becomes overdue
  overdue: boolean
  completed: boolean
  completed_at: string | null
  reminder_minutes: number | null
  time_note: 'ambiguous_earlier' | null
}

export type AssignmentInput = {
  title: string
  course_id: number
  description: string | null
  due_date: string
  due_time: string | null
  reminder_minutes: number | null
  allow_duplicate?: boolean
}

export type TodayData = {
  today: string
  time_zone: string
  overdue: Assignment[]
  due_today: Assignment[]
  upcoming: Assignment[]
  events: Occurrence[]
}

export type EventKind = 'class' | 'study' | 'meeting' | 'other'

export type OneTimeSchedule = {
  type: 'once'
  start_date: string
  start_time: string
  end_date: string
  end_time: string
}

export type WeeklySchedule = {
  type: 'weekly'
  weekdays: number[] // 0 = Monday
  start_time: string
  end_time: string // earlier than start_time means it ends the next day
  first_date: string
  last_date: string
}

export type CalendarEvent = {
  id: number
  title: string
  kind: EventKind
  course: CourseBrief | null
  location: string | null
  recurring: boolean
  schedule: OneTimeSchedule | WeeklySchedule
  time_zone: string // a weekly class keeps the zone it was created in
  ends_next_day: boolean
  time_note: 'ambiguous_earlier' | null
}

export type EventInput = {
  title: string
  kind: EventKind
  course_id: number | null
  location: string | null
  schedule: OneTimeSchedule | WeeklySchedule
  allow_conflicts?: boolean
}

/** One meeting of an event, in the account's current time zone. */
export type Occurrence = {
  event_id: number
  title: string
  kind: EventKind
  course: CourseBrief | null
  location: string | null
  recurring: boolean
  start: string
  end: string
  start_date: string
  start_time: string
  end_date: string
  end_time: string
  conflicts_with: number[]
}

export type CalendarData = {
  start: string
  end: string
  time_zone: string
  occurrences: Occurrence[]
  deadlines: Assignment[]
}

export type ScheduleConflict = {
  code: 'schedule_conflict'
  message: string
  conflicts: Occurrence[]
  total: number
}

export type DuplicateWarning = {
  code: 'likely_duplicate'
  message: string
  matches: Assignment[]
}

export class ApiError extends Error {
  readonly status: number
  readonly body: unknown
  readonly fields: Record<string, string>

  constructor(status: number, message: string, body: unknown, fields: Record<string, string> = {}) {
    super(message)
    this.status = status
    this.body = body
    this.fields = fields
  }

  get scheduleConflict(): ScheduleConflict | null {
    const b = this.body as Partial<ScheduleConflict> | null
    return this.status === 409 && b?.code === 'schedule_conflict' ? (b as ScheduleConflict) : null
  }

  get duplicate(): DuplicateWarning | null {
    const b = this.body as Partial<DuplicateWarning> | null
    return this.status === 409 && b?.code === 'likely_duplicate' ? (b as DuplicateWarning) : null
  }
}

let csrfToken: string | null = null
export function setCsrfToken(token: string | null) {
  csrfToken = token
}

type Options = { idempotencyKey?: string; signal?: AbortSignal }

const UNSAFE = new Set(['POST', 'PUT', 'PATCH', 'DELETE'])

export async function request<T>(method: string, path: string, body?: unknown, opts: Options = {}): Promise<T> {
  const headers: Record<string, string> = {}
  if (body !== undefined) headers['Content-Type'] = 'application/json'
  if (UNSAFE.has(method) && csrfToken) headers['X-CSRF-Token'] = csrfToken
  if (opts.idempotencyKey) headers['Idempotency-Key'] = opts.idempotencyKey

  let res: Response
  try {
    res = await fetch(path, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
      credentials: 'same-origin',
      signal: opts.signal,
    })
  } catch (err) {
    if ((err as Error).name === 'AbortError') throw err
    throw new ApiError(0, "Couldn't reach CampusFlow. Check your connection and try again.", null)
  }

  if (res.status === 204) return undefined as T
  const data: unknown = await res.json().catch(() => null)
  if (!res.ok) throw toError(res.status, data)
  return data as T
}

function toError(status: number, data: unknown): ApiError {
  const detail = (data as { detail?: unknown } | null)?.detail
  // FastAPI validation errors: a list of { loc: [...], msg }.
  if (Array.isArray(detail)) {
    const fields: Record<string, string> = {}
    for (const item of detail as { loc?: unknown[]; msg?: string }[]) {
      const key = String(item.loc?.at(-1) ?? 'form')
      fields[key] ??= (item.msg ?? 'Invalid value').replace(/^Value error, /, '')
    }
    return new ApiError(status, 'Some fields need attention.', data, fields)
  }
  if (typeof detail === 'string') return new ApiError(status, detail, data)
  if (status === 409 && (data as { message?: string } | null)?.message) {
    return new ApiError(status, (data as { message: string }).message, data)
  }
  const fallback =
    status >= 500 ? 'Something went wrong on our side. Your changes were not saved.' : 'That request failed.'
  return new ApiError(status, fallback, data)
}

export const api = {
  session: () => request<Session>('GET', '/api/auth/session'),
  login: (email: string, password: string) => request<Session>('POST', '/api/auth/login', { email, password }),
  register: (email: string, password: string, time_zone: string) =>
    request<Session>('POST', '/api/auth/register', { email, password, time_zone }),
  logout: () => request<void>('POST', '/api/auth/logout'),
  demoLogin: () => request<Session>('POST', '/api/auth/demo'),

  updateSettings: (changes: { time_zone?: string; email_reminders?: boolean }) =>
    request<User>('PATCH', '/api/account', changes),
  changePassword: (current_password: string, new_password: string) =>
    request<void>('POST', '/api/account/password', { current_password, new_password }),
  deleteAccount: (password: string) => request<void>('DELETE', '/api/account', { password }),

  courses: (includeArchived = false) =>
    request<Course[]>('GET', `/api/courses${includeArchived ? '?include_archived=true' : ''}`),
  createCourse: (body: CourseInput, idempotencyKey: string) =>
    request<Course>('POST', '/api/courses', body, { idempotencyKey }),
  updateCourse: (id: number, body: CourseInput & { archived: boolean }) =>
    request<Course>('PATCH', `/api/courses/${id}`, body),
  deletionImpact: (id: number) =>
    request<{ assignments: number; events: number }>('GET', `/api/courses/${id}/deletion-impact`),
  deleteCourse: (id: number, expected: { assignments: number; events: number }) =>
    request<void>(
      'DELETE',
      `/api/courses/${id}?expected_assignments=${expected.assignments}&expected_events=${expected.events}`,
    ),

  calendar: (start: string, days = 7) => request<CalendarData>('GET', `/api/calendar?start=${start}&days=${days}`),
  event: (id: number) => request<CalendarEvent>('GET', `/api/events/${id}`),
  createEvent: (body: EventInput, idempotencyKey: string) =>
    request<CalendarEvent>('POST', '/api/events', body, { idempotencyKey }),
  updateEvent: (id: number, body: EventInput) => request<CalendarEvent>('PUT', `/api/events/${id}`, body),
  deleteEvent: (id: number) => request<void>('DELETE', `/api/events/${id}`),

  today: () => request<TodayData>('GET', '/api/today'),
  assignments: (query: URLSearchParams) => request<Assignment[]>('GET', `/api/assignments?${query}`),
  createAssignment: (body: AssignmentInput, idempotencyKey: string) =>
    request<Assignment>('POST', '/api/assignments', body, { idempotencyKey }),
  updateAssignment: (id: number, body: AssignmentInput) => request<Assignment>('PUT', `/api/assignments/${id}`, body),
  setCompleted: (id: number, completed: boolean) =>
    request<Assignment>('POST', `/api/assignments/${id}/completion`, { completed }),
  deleteAssignment: (id: number) => request<void>('DELETE', `/api/assignments/${id}`),
  assignment: (id: number) => request<Assignment>('GET', `/api/assignments/${id}`),
  reminders: (assignmentId: number) => request<Reminder[]>('GET', `/api/assignments/${assignmentId}/reminders`),

  notifications: () => request<{ unread: number; items: AppNotification[] }>('GET', '/api/notifications'),
  markRead: (id: number) => request<AppNotification>('POST', `/api/notifications/${id}/read`),
  markAllRead: () => request<void>('POST', '/api/notifications/read-all'),
}

export type CourseInput = { name: string; code: string | null; instructor: string | null; color: string }

export const EVENT_KINDS: { value: EventKind; label: string }[] = [
  { value: 'class', label: 'Class' },
  { value: 'study', label: 'Study session' },
  { value: 'meeting', label: 'Meeting' },
  { value: 'other', label: 'Other' },
]

export const COURSE_COLORS = [
  { value: '#2563eb', name: 'Blue' },
  { value: '#0d9488', name: 'Teal' },
  { value: '#16a34a', name: 'Green' },
  { value: '#ca8a04', name: 'Amber' },
  { value: '#ea580c', name: 'Orange' },
  { value: '#dc2626', name: 'Red' },
  { value: '#db2777', name: 'Pink' },
  { value: '#7c3aed', name: 'Violet' },
  { value: '#475569', name: 'Slate' },
]

export const REMINDER_OPTIONS: { value: number | null; label: string }[] = [
  { value: null, label: 'No reminder' },
  { value: 60, label: '1 hour before' },
  { value: 180, label: '3 hours before' },
  { value: 1440, label: '1 day before' },
  { value: 2880, label: '2 days before' },
  { value: 10080, label: '1 week before' },
]
