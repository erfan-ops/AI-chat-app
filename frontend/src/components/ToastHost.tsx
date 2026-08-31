import { useCallback, useEffect, useRef, useState } from 'react'
import { dismissToast, useToasts } from './toastStore'
import type { Toast, ToastKind } from './toastStore'
import styles from './Toasts.module.css'
import { AlertTriangleIcon, CheckCircleIcon, InfoIcon, XIcon } from './Icons'

const ICONS: Record<ToastKind, typeof InfoIcon> = {
  error: AlertTriangleIcon,
  success: CheckCircleIcon,
  info: InfoIcon,
}

/** Rendered once at the app root; displays the toasts pushed to toastStore. */
export function ToastHost() {
  const visibleToasts = useToasts()

  return (
    <div className={styles.host} role="status" aria-live="polite" aria-label="Notifications">
      {visibleToasts.map((toast) => (
        <ToastItem key={toast.id} toast={toast} />
      ))}
    </div>
  )
}

function ToastItem({ toast }: { toast: Toast }) {
  const [leaving, setLeaving] = useState(false)
  const leaveTimer = useRef<number | null>(null)

  const startLeave = useCallback(() => {
    setLeaving(true)
    leaveTimer.current = window.setTimeout(() => dismissToast(toast.id), 200)
  }, [toast.id])

  useEffect(() => {
    return () => {
      if (leaveTimer.current !== null) window.clearTimeout(leaveTimer.current)
    }
  }, [])

  const Icon = ICONS[toast.kind]
  const className = [styles.toast, styles[toast.kind], leaving ? styles.leaving : '']
    .filter(Boolean)
    .join(' ')

  return (
    <div className={className}>
      <Icon className={styles.icon} aria-hidden="true" />
      <p className={styles.message}>{toast.message}</p>
      <button
        type="button"
        className={styles.dismiss}
        onClick={startLeave}
        aria-label="Dismiss notification"
      >
        <XIcon aria-hidden="true" />
      </button>
    </div>
  )
}
