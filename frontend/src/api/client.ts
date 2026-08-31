/**
 * Thin fetch wrapper for the AI Chat backend.
 * - Resolves the base URL from VITE_API_BASE_URL (see .env).
 * - Attaches the JWT access token to authenticated requests.
 * - Normalizes every failure into an {@link ApiError} with a user-readable
 *   message (`detail` strings from the backend are already human-readable).
 * - Treats a 401 as an expired/invalid session: clears the local session,
 *   which switches the app back to the sign-in screen.
 */

import { clearSession, getSession } from '../session/authSession'
import { pushToast } from '../components/toastStore'

/**
 * Where API requests go. Defaults to the same-origin "/api" prefix, which the
 * Vite dev and preview servers proxy to the backend (see vite.config.ts).
 * Set VITE_API_BASE_URL to an absolute URL when the app is served from a
 * static host without that proxy (the backend must allow that origin in CORS).
 */
export const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL ?? '/api').replace(/\/+$/, '')

export class ApiError extends Error {
  /** HTTP status, or 0 for network-level failures. */
  readonly status: number

  constructor(status: number, message: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

export interface RequestOptions {
  method?: 'GET' | 'POST' | 'PATCH' | 'DELETE'
  /** JSON-serializable request body. */
  body?: unknown
  signal?: AbortSignal
  /** Attach the bearer token (default true; false for auth endpoints). */
  authenticated?: boolean
}

export async function apiRequest<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { method = 'GET', body, signal, authenticated = true } = options

  const headers: Record<string, string> = {}
  if (body !== undefined) headers['Content-Type'] = 'application/json'
  const token = authenticated ? getSession()?.accessToken ?? null : null
  if (token) headers['Authorization'] = `Bearer ${token}`

  let response: Response
  try {
    response = await fetch(`${API_BASE_URL}${path}`, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
      signal,
    })
  } catch {
    // Network-level failure (offline, DNS, refused connection, aborted CORS preflight).
    throw new ApiError(0, 'Could not reach the server. Check your connection and try again.')
  }

  if (response.status === 401 && authenticated) {
    if (clearSession()) {
      pushToast('info', 'Your session has expired. Please sign in again.')
    }
    throw new ApiError(401, 'Your session has expired. Please sign in again.')
  }

  if (!response.ok) {
    throw new ApiError(response.status, await extractErrorDetail(response))
  }

  if (response.status === 204) return undefined as T
  return (await response.json()) as T
}

/** The backend returns `{"detail": string}` for application errors and
 *  `{"detail": [{msg, loc, ...}]}` for validation errors. */
export async function extractErrorDetail(response: Response): Promise<string> {
  try {
    const body: unknown = await response.json()
    if (typeof body === 'object' && body !== null) {
      const detail = (body as { detail?: unknown }).detail
      if (typeof detail === 'string') return detail
      if (Array.isArray(detail)) {
        const first = detail[0]
        if (first && typeof first === 'object' && 'msg' in first && typeof first.msg === 'string') {
          return first.msg
        }
      }
    }
  } catch {
    // Non-JSON error body; fall through.
  }
  return `Request failed (${response.status})`
}

export function buildUrl(path: string, query: Record<string, string | number | null | undefined>): string {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(query)) {
    if (value !== undefined && value !== null) search.set(key, String(value))
  }
  const qs = search.toString()
  return qs ? `${path}?${qs}` : path
}
