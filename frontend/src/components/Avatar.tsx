import { useState } from 'react'
import styles from './Avatar.module.css'

/** Deterministic pastel background per name, so the same character keeps the
 *  same color across the app (including between sessions). */
const AVATAR_COLORS = [
  '#f59e0b',
  '#10b981',
  '#3b82f6',
  '#8b5cf6',
  '#ec4899',
  '#ef4444',
  '#14b8a6',
  '#f97316',
]

function colorFor(name: string): string {
  let hash = 0
  for (let i = 0; i < name.length; i++) {
    hash = (hash * 31 + name.charCodeAt(i)) | 0
  }
  return AVATAR_COLORS[Math.abs(hash) % AVATAR_COLORS.length] ?? '#4f46e5'
}

export interface AvatarProps {
  name: string
  /** Optional image URL (from the API); falls back to the initial. */
  src?: string | null
  /** Diameter in pixels. */
  size?: number
  className?: string
}

/** Circular avatar: the character's image when available, otherwise a colored
 *  disc with the first letter of the name. */
export function Avatar({ name, src, size = 40, className }: AvatarProps) {
  const [imageFailed, setImageFailed] = useState(false)
  const showImage = src !== null && src !== undefined && src !== '' && !imageFailed

  return (
    <span
      className={`${styles.avatar} ${className ?? ''}`}
      style={{ width: size, height: size }}
      aria-hidden="true"
    >
      {showImage ? (
        <img
          src={src}
          alt=""
          className={styles.image}
          onError={() => setImageFailed(true)}
          draggable={false}
        />
      ) : (
        <span
          className={styles.initial}
          style={{ backgroundColor: colorFor(name), fontSize: Math.round(size * 0.42) }}
        >
          {name.trim().charAt(0).toUpperCase() || '?'}
        </span>
      )}
    </span>
  )
}
