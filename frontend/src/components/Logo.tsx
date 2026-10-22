/** CampusFlow mark: a course line flowing into a deadline dot. */
export function Logo({ size = 24 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" aria-hidden="true" focusable="false">
      <rect x="1" y="1" width="22" height="22" rx="6" fill="var(--accent)" />
      <path
        d="M16.5 7.6A6 6 0 1 0 16.5 16.4"
        fill="none"
        stroke="var(--surface)"
        strokeWidth="2.4"
        strokeLinecap="round"
      />
      <circle cx="17.2" cy="12" r="1.9" fill="var(--surface)" />
    </svg>
  )
}
