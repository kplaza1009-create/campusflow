import { useMutation, useQuery, useQueryClient, type QueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'

import { api, type Assignment } from './api'
import { errorMessage } from './errors'

export const keys = {
  courses: (archived: boolean) => ['courses', { archived }] as const,
  today: ['today'] as const,
  assignments: (query: string) => ['assignments', query] as const,
}

/** After any write, refetch every view that could show the changed records. */
export function invalidateWork(qc: QueryClient) {
  return Promise.all([
    qc.invalidateQueries({ queryKey: ['today'] }),
    qc.invalidateQueries({ queryKey: ['assignments'] }),
    qc.invalidateQueries({ queryKey: ['courses'] }),
    qc.invalidateQueries({ queryKey: ['calendar'] }),
  ])
}

export function useCalendar(start: string, days = 7) {
  return useQuery({
    queryKey: ['calendar', start, days],
    queryFn: () => api.calendar(start, days),
    placeholderData: (prev) => prev, // keep the old week visible while the next one loads
  })
}

export function useCourses(includeArchived = false) {
  return useQuery({ queryKey: keys.courses(includeArchived), queryFn: () => api.courses(includeArchived) })
}

export function useToday() {
  return useQuery({ queryKey: keys.today, queryFn: api.today, refetchInterval: 5 * 60_000 })
}

export function useAssignments(query: URLSearchParams) {
  const q = query.toString()
  return useQuery({
    queryKey: keys.assignments(q),
    queryFn: () => api.assignments(new URLSearchParams(q)),
    placeholderData: (prev) => prev, // keep the old list visible while filters change
  })
}

/** Completion is saved before it is shown: the check only fills once the server agrees. */
export function useSetCompleted() {
  const qc = useQueryClient()

  const reopen = (a: Assignment) =>
    api
      .setCompleted(a.id, false)
      .then(() => invalidateWork(qc))
      .catch((err: unknown) => toast.error(`Couldn't reopen it. ${errorMessage(err)}`))

  return useMutation({
    mutationFn: ({ a, completed }: { a: Assignment; completed: boolean }) => api.setCompleted(a.id, completed),
    onSuccess: async (saved, { a, completed }) => {
      await invalidateWork(qc)
      if (completed) {
        toast.success(`Marked “${saved.title}” done`, { action: { label: 'Undo', onClick: () => reopen(a) } })
      }
    },
    onError: (err, { completed }) => {
      toast.error(`Couldn't ${completed ? 'mark it done' : 'reopen it'}. ${errorMessage(err)}`)
    },
  })
}
