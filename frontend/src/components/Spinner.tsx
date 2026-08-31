import styles from './Spinner.module.css'

export interface SpinnerProps {
  /** Diameter in pixels. */
  size?: number
  /** Visible text for assistive technology. */
  label?: string
  className?: string
}

export function Spinner({ size = 22, label = 'Loading…', className }: SpinnerProps) {
  return (
    <span
      className={`${styles.spinner} ${className ?? ''}`}
      style={{ width: size, height: size, borderWidth: Math.max(2, Math.round(size / 9)) }}
      role="status"
      aria-label={label}
    />
  )
}
