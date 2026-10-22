import { useQuery, useQueryClient } from '@tanstack/react-query'

import { api, setCsrfToken, type Session, type User } from './api'

export const SESSION_KEY = ['session'] as const

export function useSession() {
  return useQuery({
    queryKey: SESSION_KEY,
    queryFn: async () => {
      const s = await api.session()
      setCsrfToken(s.csrf_token)
      return s
    },
    staleTime: Infinity,
    retry: 1,
  })
}

/** The signed-in user. Only call inside routes wrapped by RequireAuth. */
export function useUser(): User {
  const { data } = useSession()
  if (!data?.user) throw new Error('useUser called without a signed-in user')
  return data.user
}

export function useApplySession() {
  const qc = useQueryClient()
  return (s: Session) => {
    setCsrfToken(s.csrf_token)
    // A different account may sign in on this tab: drop every cached record.
    qc.removeQueries({ predicate: (q) => q.queryKey[0] !== SESSION_KEY[0] })
    qc.setQueryData(SESSION_KEY, s)
  }
}
