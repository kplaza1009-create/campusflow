import { useId, useState, type FormEvent } from 'react'
import { Link } from 'react-router'

import { ApiError, api } from '../api'
import { Logo } from '../components/Logo'
import { errorMessage } from '../errors'
import { useApplySession } from '../session'

type Mode = 'login' | 'register'

export function AuthPage({ mode }: { mode: Mode }) {
  const apply = useApplySession()
  const ids = useId()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<ApiError | null>(null)
  const timeZone = Intl.DateTimeFormat().resolvedOptions().timeZone || 'America/New_York'
  const isLogin = mode === 'login'

  const submit = async (e: FormEvent) => {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      apply(isLogin ? await api.login(email, password) : await api.register(email, password, timeZone))
    } catch (err) {
      setError(err instanceof ApiError ? err : new ApiError(0, errorMessage(err), null))
      setBusy(false)
    }
  }

  const message = error && (Object.keys(error.fields).length ? 'Check the highlighted fields.' : error.message)

  return (
    <div className="auth">
      <main className="auth-panel">
        <div className="auth-card">
          <Link to="/login" className="brand" style={{ padding: 0 }} aria-label="CampusFlow">
            <Logo size={28} />
            CampusFlow
          </Link>
          <header>
            <h1>{isLogin ? 'Sign in' : 'Create your account'}</h1>
            <p>{isLogin ? 'Pick up where you left off.' : 'Keep every course and deadline in one place.'}</p>
          </header>

          <form className="form" onSubmit={submit} noValidate>
            <div className="field">
              <label htmlFor={`${ids}-email`}>Email</label>
              <input
                id={`${ids}-email`}
                type="email"
                className="input"
                autoComplete="email"
                required
                autoFocus
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                aria-invalid={!!error?.fields.email}
              />
              {error?.fields.email && <span className="error">{error.fields.email}</span>}
            </div>
            <div className="field">
              <label htmlFor={`${ids}-password`}>Password</label>
              <input
                id={`${ids}-password`}
                type="password"
                className="input"
                autoComplete={isLogin ? 'current-password' : 'new-password'}
                required
                minLength={isLogin ? undefined : 10}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                aria-invalid={!!error?.fields.password}
                aria-describedby={isLogin ? undefined : `${ids}-pw-hint`}
              />
              {!isLogin && (
                <span className="hint" id={`${ids}-pw-hint`}>
                  At least 10 characters.
                </span>
              )}
              {error?.fields.password && <span className="error">{error.fields.password}</span>}
            </div>

            {!isLogin && (
              <p className="muted" style={{ fontSize: 12, lineHeight: '16px' }}>
                Your time zone is set to {timeZone.replaceAll('_', ' ')} from this device. You can change it later in
                Settings.
              </p>
            )}

            {message && (
              <div className="notice error" role="alert">
                {message}
              </div>
            )}

            <button type="submit" className="btn btn-primary" disabled={busy} style={{ width: '100%' }}>
              {busy ? (isLogin ? 'Signing in…' : 'Creating account…') : isLogin ? 'Sign in' : 'Create account'}
            </button>
          </form>

          <p className="muted">
            {isLogin ? 'New here? ' : 'Already have an account? '}
            <Link to={isLogin ? '/register' : '/login'}>{isLogin ? 'Create an account' : 'Sign in'}</Link>
          </p>
        </div>
      </main>

      <aside className="auth-aside" aria-label="About CampusFlow">
        <div className="preview">
          <h2>Know what&apos;s due before it&apos;s late.</h2>
          <p>
            Add your courses and assignments once. CampusFlow shows what&apos;s overdue, what&apos;s due today, and
            what&apos;s coming up this week, in your own time zone.
          </p>
          <div className="panel" aria-hidden="true">
            <p className="day-label" style={{ paddingLeft: 16 }}>
              Example
            </p>
            <ul className="list">
              {SAMPLE.map((s) => (
                <li
                  key={s.title}
                  className="a-row"
                  style={{ gridTemplateColumns: 'minmax(0,1fr) auto', paddingLeft: 16 }}
                >
                  <span className="a-main" style={{ cursor: 'default' }}>
                    <span className="a-title">{s.title}</span>
                    <span className="a-meta">
                      <span className="course-dot" style={{ background: s.color }} />
                      {s.course}
                    </span>
                  </span>
                  <span className="a-due">
                    <span className="when">{s.when}</span>
                    {s.late && <span className="late">{s.late}</span>}
                  </span>
                </li>
              ))}
            </ul>
          </div>
        </div>
      </aside>
    </div>
  )
}

const SAMPLE = [
  {
    title: 'ER diagram for the library schema',
    course: 'CS248',
    color: '#2563eb',
    when: 'Yesterday',
    late: '1 day overdue',
  },
  { title: 'Problem set 6', course: 'MTH 202', color: '#0d9488', when: 'Today, 11:59 PM', late: null },
  { title: 'Response paper: chapter 4', course: 'ENG 110', color: '#db2777', when: 'Thu', late: null },
]
