/**
 * Client-side session store: the JWT access token + user profile from login,
 * persisted in localStorage so a page refresh keeps the user signed in.
 * A 401 response from the API calls clearSession() (see api/client.ts).
 */

import { useSyncExternalStore } from 'react'
import type { LoginResponse, User } from '../types/api'

export interface Session {
  accessToken: string
  user: User
  /** Epoch milliseconds; derived from the API's `expires_in` (minutes). */
  expiresAt: number
}

const STORAGE_KEY = 'ai-chat.session'

let current: Session | null = load()
const listeners = new Set<() => void>()

function load(): Session | null {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) return null
    const parsed: unknown = JSON.parse(raw)
    if (
      typeof parsed !== 'object' ||
      parsed === null ||
      typeof (parsed as Session).accessToken !== 'string' ||
      typeof (parsed as Session).expiresAt !== 'number' ||
      (parsed as Session).user === undefined
    ) {
      return null
    }
    const session = parsed as Session
    if (session.expiresAt <= Date.now()) {
      localStorage.removeItem(STORAGE_KEY)
      return null
    }
    return session
  } catch {
    return null
  }
}

function persist(): void {
  try {
    if (current) localStorage.setItem(STORAGE_KEY, JSON.stringify(current))
  } catch {
    // Storage may be unavailable (private mode); the session then lives in memory.
  }
}

function emit(): void {
  for (const listener of listeners) listener()
}

export function getSession(): Session | null {
  return current
}

export function setSession(login: LoginResponse): void {
  current = {
    accessToken: login.access_token,
    user: login.user,
    expiresAt: Date.now() + login.expires_in * 60_000,
  }
  persist()
  emit()
}

/** Replace the stored profile after a settings change (display name, 2FA state).
 *  Assigns a new object because `useSyncExternalStore` compares by identity. */
export function updateSessionUser(user: User): void {
  if (!current) return // never resurrect a cleared or expired session
  current = { ...current, user }
  persist()
  emit()
}

/** Returns true when a session was actually cleared (false if already signed out). */
export function clearSession(): boolean {
  const hadSession = current !== null
  current = null
  try {
    localStorage.removeItem(STORAGE_KEY)
  } catch {
    // ignore
  }
  emit()
  return hadSession
}

export function subscribeSession(listener: () => void): () => void {
  listeners.add(listener)
  return () => {
    listeners.delete(listener)
  }
}

/** Reactively returns the current session (or null when signed out). */
export function useSession(): Session | null {
  return useSyncExternalStore(
    subscribeSession,
    () => current,
    () => current,
  )
}
