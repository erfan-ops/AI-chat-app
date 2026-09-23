/**
 * Theme preference (light / dark / system).
 *
 * The choice lives in localStorage and is applied as `data-theme` on `<html>`,
 * which is what `styles/tokens.css` keys the dark palette off. "System" resolves
 * against the OS setting and keeps following it while the page is open.
 *
 * This is a per-browser preference, not server state: it applies before sign-in
 * and is deliberately not synced to the account.
 */

import { useSyncExternalStore } from 'react'

export type Theme = 'light' | 'dark' | 'system'
export type ResolvedTheme = 'light' | 'dark'

export const THEMES: readonly Theme[] = ['light', 'dark', 'system']

const STORAGE_KEY = 'ai-chat.theme'
/** Mirrors the pre-paint script in index.html — keep the two in sync. */
const DARK_QUERY = '(prefers-color-scheme: dark)'

const darkMedia = window.matchMedia(DARK_QUERY)
const listeners = new Set<() => void>()

function isTheme(value: unknown): value is Theme {
  return value === 'light' || value === 'dark' || value === 'system'
}

function load(): Theme {
  try {
    const stored = localStorage.getItem(STORAGE_KEY)
    return isTheme(stored) ? stored : 'system'
  } catch {
    return 'system'
  }
}

let current: Theme = load()

/** The theme actually in effect once "system" has been resolved. */
export function resolveTheme(theme: Theme = current): ResolvedTheme {
  if (theme === 'system') return darkMedia.matches ? 'dark' : 'light'
  return theme
}

function apply(): void {
  document.documentElement.dataset.theme = resolveTheme()
}

function emit(): void {
  for (const listener of listeners) listener()
}

/** Repaint when the OS setting changes, but only while "system" is selected. */
darkMedia.addEventListener('change', () => {
  if (current === 'system') apply()
})

export function getTheme(): Theme {
  return current
}

export function setTheme(theme: Theme): void {
  current = theme
  try {
    localStorage.setItem(STORAGE_KEY, theme)
  } catch {
    // Storage unavailable — the choice just won't survive a reload.
  }
  apply()
  emit()
}

export function subscribeTheme(listener: () => void): () => void {
  listeners.add(listener)
  return () => {
    listeners.delete(listener)
  }
}

/** Reactively returns the saved preference ("system" included). */
export function useTheme(): Theme {
  return useSyncExternalStore(
    subscribeTheme,
    () => current,
    () => current,
  )
}

// Module load happens before React renders; index.html has already applied the
// same value before first paint, so this only re-asserts it.
apply()
