import { useCallback, useMemo, useRef } from 'react'

const LONG_PRESS_MS = 500
const MOVE_TOLERANCE_PX = 10

/** Long-press trigger for touch/pen pointers (mouse goes through the native
 *  contextmenu event instead). Fires `onFire(x, y)` after LONG_PRESS_MS while
 *  the pointer stays within a small movement threshold; the gesture is
 *  cancelled on release, cancel, or stray movement. */
export function useLongPress(onFire: (x: number, y: number) => void) {
  const timerRef = useRef<number | null>(null)
  const triggeredRef = useRef(false)
  const originRef = useRef<{ x: number; y: number } | null>(null)

  const cancel = useCallback(() => {
    if (timerRef.current !== null) {
      window.clearTimeout(timerRef.current)
      timerRef.current = null
    }
    triggeredRef.current = false
    originRef.current = null
  }, [])

  const pressHandlers = useMemo(() => {
    return {
      onPointerDown(event: React.PointerEvent) {
        if (event.pointerType === 'mouse') return
        const { clientX, clientY } = event
        originRef.current = { x: clientX, y: clientY }
        triggeredRef.current = false
        timerRef.current = window.setTimeout(() => {
          triggeredRef.current = true
          onFire(clientX, clientY)
          if ('vibrate' in navigator) navigator.vibrate?.(10)
        }, LONG_PRESS_MS)
      },
      onPointerMove(event: React.PointerEvent) {
        const origin = originRef.current
        if (timerRef.current === null || !origin) return
        if (
          Math.abs(event.clientX - origin.x) > MOVE_TOLERANCE_PX ||
          Math.abs(event.clientY - origin.y) > MOVE_TOLERANCE_PX
        ) {
          cancel()
        }
      },
      onPointerUp: cancel,
      onPointerCancel: cancel,
      onPointerLeave: cancel,
    }
  }, [cancel, onFire])

  /** True when a just-fired long-press already opened the menu — lets the
   *  native contextmenu that follows (mobile browsers fire one on hold) skip
   *  opening a second instance. */
  const wasTriggered = useCallback(() => triggeredRef.current, [])

  return { pressHandlers, wasTriggered }
}
