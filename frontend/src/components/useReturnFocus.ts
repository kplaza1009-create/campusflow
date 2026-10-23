import { useCallback, useEffect, useRef } from 'react'

/**
 * Puts focus back on whatever had it when a dialog opened, once the dialog closes.
 * A <dialog> does this itself on close(), but ours unmount while still open, which
 * would otherwise drop keyboard users at the top of the page. Call the returned
 * function just before opening.
 */
export function useReturnFocus(open: boolean): () => void {
  const opener = useRef<HTMLElement | null>(null)
  useEffect(() => {
    if (open) return
    const el = opener.current
    opener.current = null
    if (el?.isConnected) el.focus()
  }, [open])
  return useCallback(() => {
    opener.current = document.activeElement instanceof HTMLElement ? document.activeElement : null
  }, [])
}
