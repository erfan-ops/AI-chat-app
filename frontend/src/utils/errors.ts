import { ApiError } from '../api/client'

/**
 * Turns any thrown value into a short, user-friendly message.
 * The backend already returns human-readable `detail` strings, so we surface
 * those as-is and only translate client-side failures ourselves.
 */
export function errorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 0) return 'Could not reach the server. Check your connection and try again.'
    if (error.status === 401) return 'Your session has expired. Please sign in again.'
    return error.message
  }
  if (error instanceof Error && error.message) return error.message
  return 'Something went wrong. Please try again.'
}
