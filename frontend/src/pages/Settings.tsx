import { useQueryClient } from '@tanstack/react-query'
import { useId, useMemo, useState, type FormEvent, type ReactNode } from 'react'
import { toast } from 'sonner'

import { api, ApiError } from '../api'
import { errorMessage } from '../errors'
import { SESSION_KEY, useApplySession, useUser } from '../session'

function allZones(current: string): string[] {
  const zones = Intl.supportedValuesOf('timeZone')
  return zones.includes(current) ? zones : [current, ...zones]
}

/** Show 11:59 PM tonight in zone `from` as wall-clock time in zone `to`. */
function convertExample(from: string, to: string): string {
  const now = new Date()
  const date = new Intl.DateTimeFormat('en-CA', { timeZone: from }).format(now)
  const [y, m, d] = date.split('-').map(Number)
  // Find the UTC instant whose wall time in `from` is 23:59 (iterate on the offset).
  let guess = Date.UTC(y, m - 1, d, 23, 59)
  for (let i = 0; i < 2; i++) {
    const parts = Object.fromEntries(
      new Intl.DateTimeFormat('en-US', {
        timeZone: from,
        hourCycle: 'h23',
        year: 'numeric',
        month: 'numeric',
        day: 'numeric',
        hour: 'numeric',
        minute: 'numeric',
      })
        .formatToParts(guess)
        .map((p) => [p.type, Number(p.value)]),
    )
    const wall = Date.UTC(parts.year, parts.month - 1, parts.day, parts.hour, parts.minute)
    guess -= wall - Date.UTC(y, m - 1, d, 23, 59)
  }
  return new Intl.DateTimeFormat(undefined, {
    timeZone: to,
    weekday: 'short',
    hour: 'numeric',
    minute: '2-digit',
  }).format(guess)
}

const pretty = (zone: string) => zone.replaceAll('_', ' ')

export function SettingsPage() {
  const user = useUser()
  return (
    <div className="page narrow">
      <div className="title-row">
        <div>
          <h1>Settings</h1>
        </div>
      </div>
      <div>
        <TimeZoneSection />
        <RemindersSection />
        {user.is_demo ? (
          <Section
            title="Shared demo"
            about="This account is shared by everyone trying CampusFlow and rebuilt every night."
          >
            <p className="hint">
              Its password, email reminders, and deletion are turned off so one visitor can&apos;t spoil it for the
              next. Create your own account to use them.
            </p>
          </Section>
        ) : (
          <PasswordSection />
        )}
        <SignOutSection />
        {!user.is_demo && <DeleteAccountSection />}
      </div>
    </div>
  )
}

function Section({ title, about, children }: { title: string; about: string; children: ReactNode }) {
  const id = useId()
  return (
    <section className="settings-section" aria-labelledby={id}>
      <div className="about">
        <h2 id={id}>{title}</h2>
        <p>{about}</p>
      </div>
      <div>{children}</div>
    </section>
  )
}

function TimeZoneSection() {
  const user = useUser()
  const qc = useQueryClient()
  const ids = useId()
  const [zone, setZone] = useState(user.time_zone)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const zones = useMemo(() => allZones(user.time_zone), [user.time_zone])
  const device = Intl.DateTimeFormat().resolvedOptions().timeZone
  const changed = zone !== user.time_zone

  const save = async (e: FormEvent) => {
    e.preventDefault()
    setSaving(true)
    setError(null)
    try {
      const updated = await api.updateSettings({ time_zone: zone })
      qc.setQueryData(SESSION_KEY, (s: { user: unknown; csrf_token: string | null } | undefined) =>
        s ? { ...s, user: updated } : s,
      )
      await qc.invalidateQueries({ predicate: (q) => q.queryKey[0] !== SESSION_KEY[0] })
      toast.success(`Time zone set to ${pretty(updated.time_zone)}`)
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setSaving(false)
    }
  }

  return (
    <Section title="Time zone" about="Deadlines, today's date, and when work becomes overdue all follow this zone.">
      <form className="form" onSubmit={save}>
        <div className="field">
          <label htmlFor={`${ids}-tz`}>Time zone</label>
          <select id={`${ids}-tz`} className="select" value={zone} onChange={(e) => setZone(e.target.value)}>
            {zones.map((z) => (
              <option key={z} value={z}>
                {pretty(z)}
              </option>
            ))}
          </select>
          {device && device !== zone && (
            <span className="hint">
              This device is set to {pretty(device)}.{' '}
              <button type="button" className="link-button" onClick={() => setZone(device)}>
                Use it
              </button>
            </span>
          )}
        </div>

        {changed && (
          <div className="example" aria-live="polite">
            <span>
              <b>Date-only deadlines keep their date.</b> Something due Friday is still due Friday, by 11:59 PM in{' '}
              {pretty(zone)}.
            </span>
            <span>
              <b>Deadlines with a time keep their exact moment.</b> 11:59 PM tonight in {pretty(user.time_zone)} will
              show as {convertExample(user.time_zone, zone)}.
            </span>
          </div>
        )}

        {error && (
          <div className="notice error" role="alert">
            {error}
          </div>
        )}
        {changed && (
          <div className="form-actions">
            <button
              type="button"
              className="btn btn-secondary"
              onClick={() => setZone(user.time_zone)}
              disabled={saving}
            >
              Cancel
            </button>
            <button type="submit" className="btn btn-primary" disabled={saving}>
              {saving ? 'Saving…' : 'Save time zone'}
            </button>
          </div>
        )}
      </form>
    </Section>
  )
}

function RemindersSection() {
  const user = useUser()
  const qc = useQueryClient()
  const ids = useId()
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // The box shows the saved value, and only changes once the server has saved it.
  const toggle = async (on: boolean) => {
    setSaving(true)
    setError(null)
    try {
      const updated = await api.updateSettings({ email_reminders: on })
      qc.setQueryData(SESSION_KEY, (s: { user: unknown; csrf_token: string | null } | undefined) =>
        s ? { ...s, user: updated } : s,
      )
      await qc.invalidateQueries({ queryKey: ['reminders'] })
      toast.success(on ? 'Email reminders on' : 'Email reminders off')
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setSaving(false)
    }
  }

  return (
    <Section
      title="Reminders"
      about="Reminders always appear in CampusFlow under Notifications. You can get them by email too."
    >
      <div className="form">
        <div className="field">
          <label className="check-label" htmlFor={`${ids}-email`}>
            <input
              id={`${ids}-email`}
              type="checkbox"
              checked={user.email_reminders}
              // Unavailable: still let someone turn it off if it was on before.
              disabled={saving || user.is_demo || (user.email_delivery === 'unavailable' && !user.email_reminders)}
              onChange={(e) => void toggle(e.target.checked)}
              aria-describedby={`${ids}-email-hint`}
            />
            Email me reminders at {user.email}
          </label>
          <span className="hint" id={`${ids}-email-hint`}>
            {user.is_demo
              ? 'The shared demo never sends email.'
              : user.email_delivery === 'unavailable'
                ? 'This deployment can only email its owner (it uses Resend’s free plan), so your reminders stay in the app.'
                : user.email_delivery === 'capture'
                  ? 'This server is in local test mode: reminder emails are saved to a file, not sent.'
                  : 'Sent through Resend. The first one may land in spam.'}{' '}
            Each assignment&apos;s reminder time is set when you add or edit it.
          </span>
        </div>
        {saving && (
          <span className="hint" role="status">
            Saving…
          </span>
        )}
        {error && (
          <div className="notice error" role="alert">
            {error}
          </div>
        )}
      </div>
    </Section>
  )
}

function PasswordSection() {
  const ids = useId()
  const [current, setCurrent] = useState('')
  const [next, setNext] = useState('')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<ApiError | null>(null)

  const save = async (e: FormEvent) => {
    e.preventDefault()
    setSaving(true)
    setError(null)
    try {
      await api.changePassword(current, next)
      setCurrent('')
      setNext('')
      toast.success('Password changed. Other devices were signed out.')
    } catch (err) {
      setError(err instanceof ApiError ? err : new ApiError(0, errorMessage(err), null))
    } finally {
      setSaving(false)
    }
  }

  return (
    <Section title="Password" about="Changing it signs you out everywhere else.">
      <form className="form" onSubmit={save} noValidate>
        <div className="field">
          <label htmlFor={`${ids}-cur`}>Current password</label>
          <input
            id={`${ids}-cur`}
            type="password"
            className="input"
            autoComplete="current-password"
            value={current}
            onChange={(e) => setCurrent(e.target.value)}
            aria-invalid={error?.status === 400 || !!error?.fields.current_password}
          />
          {error?.fields.current_password && <span className="error">Enter your current password.</span>}
        </div>
        <div className="field">
          <label htmlFor={`${ids}-new`}>New password</label>
          <input
            id={`${ids}-new`}
            type="password"
            className="input"
            autoComplete="new-password"
            minLength={10}
            value={next}
            onChange={(e) => setNext(e.target.value)}
            aria-invalid={!!error?.fields.new_password}
            aria-describedby={`${ids}-new-hint`}
          />
          <span className="hint" id={`${ids}-new-hint`}>
            At least 10 characters. A few unrelated words works well.
          </span>
          {error?.fields.new_password && <span className="error">{error.fields.new_password}</span>}
        </div>
        {error && !Object.keys(error.fields).length && (
          <div className="notice error" role="alert">
            {errorMessage(error)}
          </div>
        )}
        <div className="form-actions">
          <button type="submit" className="btn btn-primary" disabled={saving}>
            {saving ? 'Saving…' : 'Change password'}
          </button>
        </div>
      </form>
    </Section>
  )
}

function SignOutSection() {
  const apply = useApplySession()
  const [busy, setBusy] = useState(false)
  return (
    <Section title="Sign out" about="Ends your session on this device.">
      <button
        type="button"
        className="btn btn-secondary"
        disabled={busy}
        onClick={async () => {
          setBusy(true)
          try {
            await api.logout()
            apply({ user: null, csrf_token: null })
          } catch (err) {
            toast.error(`Couldn't sign out. ${errorMessage(err)}`)
            setBusy(false)
          }
        }}
      >
        {busy ? 'Signing out…' : 'Sign out'}
      </button>
    </Section>
  )
}

function DeleteAccountSection() {
  const ids = useId()
  const apply = useApplySession()
  const user = useUser()
  const [open, setOpen] = useState(false)
  const [password, setPassword] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  return (
    <Section title="Delete account" about="Permanently deletes your account and every course and assignment in it.">
      {!open ? (
        <button type="button" className="btn btn-danger" onClick={() => setOpen(true)}>
          Delete account…
        </button>
      ) : (
        <form
          className="form"
          onSubmit={async (e) => {
            e.preventDefault()
            setBusy(true)
            setError(null)
            try {
              await api.deleteAccount(password)
              apply({ user: null, csrf_token: null })
            } catch (err) {
              setError(errorMessage(err))
              setBusy(false)
            }
          }}
        >
          <p>
            This deletes <strong>{user.email}</strong> and everything in it. It can&apos;t be undone.
          </p>
          <div className="field">
            <label htmlFor={`${ids}-pw`}>Enter your password to confirm</label>
            <input
              id={`${ids}-pw`}
              type="password"
              className="input"
              autoComplete="current-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoFocus
            />
          </div>
          {error && (
            <div className="notice error" role="alert">
              {error}
            </div>
          )}
          <div className="form-actions">
            <button type="button" className="btn btn-secondary" onClick={() => setOpen(false)} disabled={busy}>
              Cancel
            </button>
            <button type="submit" className="btn btn-danger-solid" disabled={!password || busy}>
              {busy ? 'Deleting…' : 'Delete my account'}
            </button>
          </div>
        </form>
      )}
    </Section>
  )
}
