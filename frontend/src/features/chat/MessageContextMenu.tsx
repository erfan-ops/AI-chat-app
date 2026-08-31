import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'
import type { Message } from '../../types/api'
import { CopyIcon, ReplyIcon } from '../../components/Icons'
import { pushToast } from '../../components/toastStore'
import styles from './MessageContextMenu.module.css'

const EDGE_MARGIN = 8

export interface MenuPosition {
  /** Viewport coordinates of the gesture that opened the menu. */
  x: number
  y: number
}

export interface MessageContextMenuProps extends MenuPosition {
  message: Message
  onReply: (message: Message) => void
  onClose: () => void
}

/** Small context menu for a message: Reply and Copy. Rendered fixed at the
 *  gesture position, clamped to the viewport; closes on outside press, Escape,
 *  scroll, or resize. */
export function MessageContextMenu({
  message,
  x,
  y,
  onReply,
  onClose,
}: MessageContextMenuProps) {
  const menuRef = useRef<HTMLDivElement>(null)
  const [pos, setPos] = useState<MenuPosition>({ x, y })

  // Clamp to the viewport before paint (the initial render already happened at
  // the raw coordinates; useLayoutEffect corrects it without a visible flash).
  useLayoutEffect(() => {
    const el = menuRef.current
    if (!el) return
    const rect = el.getBoundingClientRect()
    setPos({
      x: Math.min(Math.max(EDGE_MARGIN, x), window.innerWidth - rect.width - EDGE_MARGIN),
      y: Math.min(Math.max(EDGE_MARGIN, y), window.innerHeight - rect.height - EDGE_MARGIN),
    })
  }, [x, y])

  // Land keyboard users on the first item.
  useLayoutEffect(() => {
    menuRef.current?.querySelector<HTMLButtonElement>('[role="menuitem"]')?.focus()
  }, [])

  useEffect(() => {
    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === 'Escape') onClose()
    }
    function handlePointerDown(event: PointerEvent) {
      const el = menuRef.current
      if (el && !el.contains(event.target as Node)) onClose()
    }
    document.addEventListener('keydown', handleKeyDown)
    document.addEventListener('pointerdown', handlePointerDown)
    document.addEventListener('scroll', onClose, true)
    window.addEventListener('resize', onClose)
    return () => {
      document.removeEventListener('keydown', handleKeyDown)
      document.removeEventListener('pointerdown', handlePointerDown)
      document.removeEventListener('scroll', onClose, true)
      window.removeEventListener('resize', onClose)
    }
  }, [onClose])

  const handleCopy = useCallback(async () => {
    onClose()
    try {
      await navigator.clipboard.writeText(message.content)
      pushToast('success', 'Copied to clipboard')
    } catch {
      // Clipboard API unavailable (e.g. insecure context) — fall back to the
      // legacy textarea trick.
      try {
        const textarea = document.createElement('textarea')
        textarea.value = message.content
        textarea.style.position = 'fixed'
        textarea.style.opacity = '0'
        document.body.appendChild(textarea)
        textarea.select()
        const ok = document.execCommand('copy')
        textarea.remove()
        if (!ok) throw new Error('copy command failed')
        pushToast('success', 'Copied to clipboard')
      } catch {
        pushToast('error', 'Could not copy the message')
      }
    }
  }, [message.content, onClose])

  return (
    <div
      ref={menuRef}
      className={styles.menu}
      role="menu"
      aria-label="Message actions"
      style={{ left: pos.x, top: pos.y }}
    >
      <button
        type="button"
        role="menuitem"
        className={styles.item}
        onClick={() => {
          onClose()
          onReply(message)
        }}
      >
        <ReplyIcon aria-hidden="true" />
        Reply
      </button>
      <button type="button" role="menuitem" className={styles.item} onClick={handleCopy}>
        <CopyIcon aria-hidden="true" />
        Copy
      </button>
    </div>
  )
}
