/**
 * The application's whole routing story: one address that is not the app.
 *
 * There is no router here — every screen is a tab or a dialog, and sign-in state
 * decides which of the two shells renders. `/reset-password` is the single exception:
 * it has to survive being opened *directly* (from a bookmark, a password manager, or
 * a link someone was sent), because the person using it cannot sign in to get there.
 * The web server already serves the SPA for any path (`try_files {path} /index.html`
 * in the Caddyfile), so all this has to do is read the address and move between it and
 * the app.
 *
 * Same shape as `themeStore`: a module-level value, `useSyncExternalStore`, and a
 * plain function to change it.
 */

import { useSyncExternalStore } from 'react'

/** Where a signed-out visitor is sent to recover their account. */
export const RESET_PASSWORD_PATH = '/reset-password'

const listeners = new Set<() => void>()

function emit(): void {
  for (const listener of listeners) listener()
}

function subscribe(listener: () => void): () => void {
  // The browser's back button is a navigation nobody called for.
  const onPopState = () => listener()
  window.addEventListener('popstate', onPopState)
  listeners.add(listener)
  return () => {
    window.removeEventListener('popstate', onPopState)
    listeners.delete(listener)
  }
}

/** The current path. */
export function getPathname(): string {
  return window.location.pathname
}

/** Go to `path` without a page load, so the app keeps its state. */
export function navigate(path: string): void {
  if (window.location.pathname === path) return
  window.history.pushState(null, '', path)
  emit()
}

/** The current path, re-rendering on navigation. */
export function usePathname(): string {
  return useSyncExternalStore(subscribe, getPathname)
}
