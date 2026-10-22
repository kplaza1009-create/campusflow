import { CircleAlert } from 'lucide-react'
import type { ReactNode } from 'react'

import { ApiError } from '../api'

export function SkeletonRows({ rows = 3 }: { rows?: number }) {
  return (
    <div aria-hidden="true">
      {Array.from({ length: rows }, (_, i) => (
        <div className="skeleton-row" key={i}>
          <span className="bar circle" />
          <span className="bar" style={{ width: `${55 - i * 9}%` }} />
          <span className="bar" />
        </div>
      ))}
    </div>
  )
}

export function Loading({ label, rows }: { label: string; rows?: number }) {
  return (
    <div role="status">
      <span className="visually-hidden">{label}</span>
      <SkeletonRows rows={rows} />
    </div>
  )
}

export function BlockError({ what, error, retry }: { what: string; error: unknown; retry: () => void }) {
  const detail = error instanceof ApiError && error.status === 0 ? ' Check your connection.' : ''
  return (
    <div className="block-error" role="alert">
      <CircleAlert size={16} aria-hidden="true" />
      <span>
        Couldn&apos;t load {what}.{detail}
      </span>
      <button type="button" className="link-button" onClick={retry}>
        Try again
      </button>
    </div>
  )
}

export function Empty({ children, action, compact }: { children: ReactNode; action?: ReactNode; compact?: boolean }) {
  return (
    <div className={compact ? 'empty compact' : 'empty'}>
      <p>{children}</p>
      {action}
    </div>
  )
}
