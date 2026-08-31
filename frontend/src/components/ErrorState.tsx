import styles from './ErrorState.module.css'
import { AlertTriangleIcon } from './Icons'
import { errorMessage } from '../utils/errors'

export interface ErrorStateProps {
  /** Any thrown value (typically an ApiError). */
  error: unknown
  onRetry?: () => void
  className?: string
}

/** Inline failure state with an optional retry action. */
export function ErrorState({ error, onRetry, className }: ErrorStateProps) {
  return (
    <div className={`${styles.wrap} ${className ?? ''}`} role="alert">
      <AlertTriangleIcon className={styles.icon} aria-hidden="true" />
      <p className={styles.message}>{errorMessage(error)}</p>
      {onRetry && (
        <button type="button" className={styles.retry} onClick={onRetry}>
          Try again
        </button>
      )}
    </div>
  )
}
