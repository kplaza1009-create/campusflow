import { BookOpen, ListChecks, Plus, Settings, Sun } from 'lucide-react'
import { NavLink, Outlet } from 'react-router'

import { useToday } from '../queries'
import { useUser } from '../session'
import { AssignmentDialogProvider } from './AssignmentDialog'
import { useAssignmentDialog } from './assignmentDialogContext'
import { Logo } from './Logo'

const NAV = [
  { to: '/', label: 'Today', icon: Sun, end: true },
  { to: '/assignments', label: 'Assignments', icon: ListChecks, end: false },
  { to: '/courses', label: 'Courses', icon: BookOpen, end: false },
  { to: '/settings', label: 'Settings', icon: Settings, end: false },
]

export function Shell() {
  return (
    <AssignmentDialogProvider>
      <ShellLayout />
    </AssignmentDialogProvider>
  )
}

function ShellLayout() {
  const user = useUser()
  const today = useToday()
  const dialog = useAssignmentDialog()
  const overdue = today.data?.overdue.length ?? 0

  return (
    <div className="shell">
      <a href="#main" className="skip-link">
        Skip to content
      </a>

      <aside className="sidebar">
        <NavLink to="/" className="brand" aria-label="CampusFlow home">
          <Logo />
          CampusFlow
        </NavLink>
        <nav className="nav" aria-label="Main">
          {NAV.map(({ to, label, icon: Icon, end }) => (
            <NavLink key={to} to={to} end={end}>
              <Icon size={16} strokeWidth={1.75} aria-hidden="true" />
              {label}
              {to === '/' && overdue > 0 && (
                <span className="count num" aria-label={`${overdue} overdue`}>
                  {overdue}
                </span>
              )}
            </NavLink>
          ))}
        </nav>
        <div className="sidebar-foot">
          <span className="email" title={user.email}>
            {user.email}
          </span>
          <span className="muted">{user.time_zone.replaceAll('_', ' ')}</span>
        </div>
      </aside>

      <header className="topbar">
        <NavLink to="/" className="brand" aria-label="CampusFlow home" style={{ padding: 0 }}>
          <Logo size={22} />
          CampusFlow
        </NavLink>
        <button type="button" className="btn btn-primary btn-small" onClick={() => dialog.open()}>
          <Plus size={16} aria-hidden="true" />
          New
        </button>
      </header>

      <main id="main" className="main" tabIndex={-1}>
        <Outlet />
      </main>

      <nav className="tabbar" aria-label="Main">
        {NAV.map(({ to, label, icon: Icon, end }) => (
          <NavLink key={to} to={to} end={end}>
            <Icon size={20} strokeWidth={1.75} aria-hidden="true" />
            {label}
          </NavLink>
        ))}
      </nav>
    </div>
  )
}
