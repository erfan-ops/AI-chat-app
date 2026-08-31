/**
 * Minimal toast store: a module-level state + subscription.
 * The view lives in ToastHost.tsx; callers use pushToast().
 */

import { useSyncExternalStore } from 'react'

export type ToastKind = 'error' | 'success' | 'info'

export interface Toast {
  id: number
  kind: ToastKind
  message: string
}

const DISMISS_AFTER_MS = 6000

let nextId = 1
let toasts: Toast[] = []
const listeners = new Set<() => void>()

function emit(): void {
  for (const listener of listeners) listener()
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener)
  return () => {
    listeners.delete(listener)
  }
}

export function pushToast(kind: ToastKind, message: string): void {
  const toast: Toast = { id: nextId++, kind, message }
  toasts = [...toasts, toast]
  emit()
  window.setTimeout(() => dismissToast(toast.id), DISMISS_AFTER_MS)
}

export function dismissToast(id: number): void {
  toasts = toasts.filter((toast) => toast.id !== id)
  emit()
}

export function useToasts(): Toast[] {
  return useSyncExternalStore(
    subscribe,
    () => toasts,
    () => toasts,
  )
}
