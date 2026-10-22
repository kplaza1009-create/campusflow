import { ApiError } from './api'

export function errorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 401) return 'Your session has ended. Sign in again to save changes.'
    return error.message
  }
  return 'Something went wrong. Your changes were not saved.'
}
