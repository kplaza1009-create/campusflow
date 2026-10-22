import { useQueryClient } from '@tanstack/react-query'
import { Link } from 'react-router'
import { toast } from 'sonner'

import { api, ApiError, type AppNotification } from '../api'
import { useAssignmentDialog } from '../components/assignmentDialogContext'
import { BlockError, Empty, Loading } from '../components/States'
import { formatInstant, plural } from '../dates'
import { errorMessage } from '../errors'
import { useNotifications } from '../queries'
import { useUser } from '../session'

export function NotificationsPage() {
  const user = useUser()
  const qc = useQueryClient()
  const dialog = useAssignmentDialog()
  const { data, error, refetch } = useNotifications()
  const refresh = () => qc.invalidateQueries({ queryKey: ['notifications'] })

  const open = async (n: AppNotification) => {
    try {
      if (!n.read_at) await api.markRead(n.id).then(refresh)
      if (n.assignment_id === null) {
        toast.info('That assignment was deleted.')
        return
      }
      dialog.open(await api.assignment(n.assignment_id))
    } catch (err) {
      if (err instanceof ApiError && err.status === 404) toast.info('That assignment was deleted.')
      else toast.error(`Couldn't open it. ${errorMessage(err)}`)
    }
  }

  const markAll = () =>
    api
      .markAllRead()
      .then(refresh)
      .catch((err: unknown) => toast.error(`Couldn't mark them read. ${errorMessage(err)}`))

  const unread = data?.unread ?? 0
  const channels = user.email_reminders ? 'here and by email' : 'here'

  return (
    <div className="page narrow">
      <div className="title-row">
        <div>
          <h1>Notifications</h1>
          <p aria-live="polite">{data ? (unread ? plural(unread, 'unread reminder') : 'All caught up') : ' '}</p>
        </div>
        {unread > 0 && (
          <div className="actions">
            <button type="button" className="btn btn-secondary" onClick={markAll}>
              Mark all read
            </button>
          </div>
        )}
      </div>

      <section className="panel" aria-label="Reminders" aria-busy={!data}>
        {error ? (
          <BlockError what="notifications" error={error} retry={() => void refetch()} />
        ) : !data ? (
          <Loading label="Loading notifications" />
        ) : data.items.length === 0 ? (
          <Empty action={<Link to="/assignments">Go to assignments</Link>}>
            No reminders yet. When an assignment&apos;s reminder time comes, it shows up {channels}.
          </Empty>
        ) : (
          <ul className="note-list">
            {data.items.map((n) => (
              <li key={n.id}>
                <button
                  type="button"
                  className={n.read_at ? 'note-row' : 'note-row is-unread'}
                  onClick={() => void open(n)}
                >
                  <span className="note-dot" aria-hidden="true" />
                  <span className="note-main">
                    <span className="a-title">
                      {!n.read_at && <span className="visually-hidden">Unread: </span>}
                      {n.title}
                    </span>
                    <span className="a-meta">{n.body}</span>
                  </span>
                  <span className="note-time num">{formatInstant(n.created_at, user.time_zone)}</span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </section>

      <p className="hint">
        Reminders arrive {channels}.{' '}
        {!user.email_reminders && (
          <>
            <Link to="/settings">Turn on email reminders</Link>.{' '}
          </>
        )}
        For assignments without a time, day-based reminders arrive at 9:00 AM. A reminder is skipped if the assignment
        is done, or if its deadline passes before it can be delivered.
      </p>
    </div>
  )
}
