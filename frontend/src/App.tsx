import { QueryCache, QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { BrowserRouter, Navigate, Outlet, Route, Routes, useLocation } from 'react-router'
import { Toaster } from 'sonner'

import { ApiError } from './api'
import { Shell } from './components/Shell'
import { AssignmentsPage } from './pages/Assignments'
import { AuthPage } from './pages/Auth'
import { CoursesPage } from './pages/Courses'
import { NotificationsPage } from './pages/Notifications'
import { SettingsPage } from './pages/Settings'
import { TodayPage } from './pages/Today'
import { WeekPage } from './pages/Week'
import { SESSION_KEY, useSession } from './session'

const queryClient: QueryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      // Don't retry client errors (401, 404); retry network and server errors twice.
      retry: (count, err) => !(err instanceof ApiError && err.status >= 400 && err.status < 500) && count < 2,
    },
  },
  // A 401 while loading data means the session ended: show the sign-in page.
  // Mutations don't do this, so an open form keeps its input.
  queryCache: new QueryCache({
    onError: (err, query) => {
      if (err instanceof ApiError && err.status === 401 && query.queryKey[0] !== SESSION_KEY[0]) {
        queryClient.setQueryData(SESSION_KEY, { user: null, csrf_token: null })
      }
    },
  }),
})

function RequireAuth() {
  const session = useSession()
  const location = useLocation()
  if (session.isPending) return <BootScreen />
  if (session.isError) return <BootError retry={() => session.refetch()} />
  if (!session.data.user) return <Navigate to="/login" replace state={{ from: location.pathname + location.search }} />
  return <Outlet />
}

function GuestOnly() {
  const session = useSession()
  const location = useLocation()
  if (session.isPending) return <BootScreen />
  if (session.isError) return <BootError retry={() => session.refetch()} />
  if (session.data.user) {
    const from = (location.state as { from?: string } | null)?.from
    return <Navigate to={from && !from.startsWith('/login') ? from : '/'} replace />
  }
  return <Outlet />
}

function BootScreen() {
  return (
    <div role="status" style={{ padding: 40 }}>
      <span className="visually-hidden">Loading CampusFlow</span>
    </div>
  )
}

function BootError({ retry }: { retry: () => void }) {
  return (
    <main className="auth-panel" style={{ minHeight: '100dvh' }}>
      <div className="auth-card">
        <h1>Can&apos;t reach CampusFlow</h1>
        <p className="muted">The server didn&apos;t respond. Check your connection, then try again.</p>
        <button type="button" className="btn btn-primary" onClick={retry}>
          Try again
        </button>
      </div>
    </main>
  )
}

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <Routes>
          <Route element={<GuestOnly />}>
            <Route path="/login" element={<AuthPage mode="login" />} />
            <Route path="/register" element={<AuthPage mode="register" />} />
          </Route>
          <Route element={<RequireAuth />}>
            <Route element={<Shell />}>
              <Route index element={<TodayPage />} />
              <Route path="/week" element={<WeekPage />} />
              <Route path="/assignments" element={<AssignmentsPage />} />
              <Route path="/courses" element={<CoursesPage />} />
              <Route path="/notifications" element={<NotificationsPage />} />
              <Route path="/settings" element={<SettingsPage />} />
            </Route>
          </Route>
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </BrowserRouter>
      <Toaster position="bottom-right" closeButton theme="system" />
    </QueryClientProvider>
  )
}
