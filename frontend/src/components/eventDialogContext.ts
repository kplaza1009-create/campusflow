import { createContext, useContext } from 'react'

export type EventDialogOptions = {
  eventId?: number // edit an existing event
  date?: string // defaults for a new event
  startTime?: string // "HH:MM"
}

export type EventDialogApi = { open: (options?: EventDialogOptions) => void }

export const EventDialogContext = createContext<EventDialogApi | null>(null)

export function useEventDialog(): EventDialogApi {
  const ctx = useContext(EventDialogContext)
  if (!ctx) throw new Error('useEventDialog needs EventDialogProvider')
  return ctx
}
