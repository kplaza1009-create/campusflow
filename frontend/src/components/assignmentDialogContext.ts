import { createContext, useContext } from 'react'

import type { Assignment } from '../api'

export type AssignmentDialogApi = { open: (a?: Assignment | null) => void }

export const AssignmentDialogContext = createContext<AssignmentDialogApi | null>(null)

export function useAssignmentDialog(): AssignmentDialogApi {
  const ctx = useContext(AssignmentDialogContext)
  if (!ctx) throw new Error('useAssignmentDialog needs AssignmentDialogProvider')
  return ctx
}
