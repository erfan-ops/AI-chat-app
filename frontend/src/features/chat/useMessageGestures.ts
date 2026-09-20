import { useCallback, useMemo, useRef, useState } from 'react'

/** A hold this long (without moving) opens the message menu. Kept as a fallback
 *  for users used to holding — a plain tap now opens the same menu. */
const LONG_PRESS_MS = 500
const MOVE_TOLERANCE_PX = 10
/** Horizontal travel that claims the gesture as a swipe (and cancels the hold). */
const SWIPE_ARM_PX = 12
/** Release past this distance replies to the message. */
const SWIPE_TRIGGER_PX = 56
/** The bubble follows the finger up to here, then stiffens so it cannot be
 *  dragged out of the row. */
const SWIPE_MAX_PX = 64
const SWIPE_RESISTANCE = 0.15

export interface MessageGestureOptions {
  /** Open the reply/copy menu at these viewport coordinates. */
  onOpenMenu: (x: number, y: number) => void
  /** Reply to this message — fired when a right-swipe passes the trigger. */
  onReply: () => void
}

/**
 * Touch gestures for one message row: tap or hold opens the actions menu, and a
 * right-swipe replies.
 *
 * Mouse pointers are ignored throughout — desktop keeps the native right-click.
 * All three gestures share one set of pointer handlers on purpose: a row can only
 * carry one `onPointerDown`, so separate hooks would silently overwrite each other.
 */
export function useMessageGestures({ onOpenMenu, onReply }: MessageGestureOptions) {
  const [swipeOffset, setSwipeOffset] = useState(0)
  const timerRef = useRef<number | null>(null)
  /** The hold already opened the menu, so the click that follows must not. */
  const heldRef = useRef(false)
  /** A swipe claimed this gesture, so it must not also count as a tap. */
  const swipedRef = useRef(false)
  /** Coordinates of the last long-press, so the menu opens where the finger was. */
  const pointerTypeRef = useRef('mouse')
  const originRef = useRef<{ x: number; y: number } | null>(null)
  const axisRef = useRef<'none' | 'horizontal' | 'vertical'>('none')

  const clearTimer = useCallback(() => {
    if (timerRef.current !== null) {
      window.clearTimeout(timerRef.current)
      timerRef.current = null
    }
  }, [])

  const reset = useCallback(() => {
    clearTimer()
    originRef.current = null
    axisRef.current = 'none'
    setSwipeOffset(0)
  }, [clearTimer])

  const gestureHandlers = useMemo(
    () => ({
      onPointerDown(event: React.PointerEvent) {
        pointerTypeRef.current = event.pointerType
        if (event.pointerType === 'mouse') return
        const { clientX, clientY } = event
        originRef.current = { x: clientX, y: clientY }
        axisRef.current = 'none'
        heldRef.current = false
        swipedRef.current = false
        timerRef.current = window.setTimeout(() => {
          heldRef.current = true
          onOpenMenu(clientX, clientY)
          if ('vibrate' in navigator) navigator.vibrate?.(10)
        }, LONG_PRESS_MS)
      },

      onPointerMove(event: React.PointerEvent) {
        const origin = originRef.current
        if (!origin) return
        const dx = event.clientX - origin.x
        const dy = event.clientY - origin.y

        if (axisRef.current === 'none') {
          const horizontal = Math.abs(dx)
          const vertical = Math.abs(dy)
          if (vertical > MOVE_TOLERANCE_PX && vertical >= horizontal) {
            // The user is scrolling: stand down and let the browser pan.
            axisRef.current = 'vertical'
            clearTimer()
            return
          }
          if (horizontal > SWIPE_ARM_PX && horizontal > vertical) {
            axisRef.current = 'horizontal'
            swipedRef.current = true
            clearTimer()
          } else {
            if (horizontal > MOVE_TOLERANCE_PX || vertical > MOVE_TOLERANCE_PX) clearTimer()
            return
          }
        }
        if (axisRef.current !== 'horizontal') return

        // Only a right-swipe replies, and the bubble stops short of the row edge.
        const travel = Math.max(0, dx)
        setSwipeOffset(
          travel <= SWIPE_MAX_PX
            ? travel
            : SWIPE_MAX_PX + (travel - SWIPE_MAX_PX) * SWIPE_RESISTANCE,
        )
      },

      onPointerUp(event: React.PointerEvent) {
        const origin = originRef.current
        if (axisRef.current === 'horizontal' && origin && event.clientX - origin.x >= SWIPE_TRIGGER_PX) {
          onReply()
        }
        reset()
      },

      onPointerCancel: reset,
      onPointerLeave: reset,

      onClick(event: React.MouseEvent) {
        // Desktop: right-click is the menu, a left-click does nothing.
        if (pointerTypeRef.current === 'mouse') return
        // The hold already opened it, or a swipe just replied.
        if (heldRef.current || swipedRef.current) return
        // Taps on the quote (or any other control) belong to that control.
        if ((event.target as HTMLElement).closest('button')) return
        onOpenMenu(event.clientX, event.clientY)
      },
    }),
    [clearTimer, onOpenMenu, onReply, reset],
  )

  return {
    gestureHandlers,
    /** Pixels the bubble should be shifted while a swipe is in progress. */
    swipeOffset,
    /** 0–1, for fading the reply affordance in as the finger travels. */
    swipeProgress: Math.min(1, swipeOffset / SWIPE_TRIGGER_PX),
    /** True when a just-fired hold already opened the menu — lets the native
     *  contextmenu that follows (mobile browsers fire one after a hold) skip
     *  opening a second instance. */
    wasTriggered: () => heldRef.current,
  }
}
