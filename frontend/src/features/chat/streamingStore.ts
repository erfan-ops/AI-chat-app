/**
 * Module-level store for in-flight AI replies.
 *
 * A stream must survive switching conversations (the fetch keeps running in the
 * background), so the stream state lives here — outside of any component — and
 * the chat view subscribes to it. Server state (persisted messages) stays in
 * the React Query cache; this store only holds the transient streaming bubble.
 */

import { useSyncExternalStore } from 'react'

export type StreamStatus = 'pending' | 'streaming'

export interface ConversationStream {
  conversationId: number
  status: StreamStatus
  /** Accumulated reply text (empty while `pending` — provider not started yet). */
  text: string
}

const streams = new Map<number, ConversationStream>()
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

export function getConversationStream(conversationId: number): ConversationStream | undefined {
  return streams.get(conversationId)
}

/** The user message was sent; the AI has not started replying yet. */
export function beginStream(conversationId: number): void {
  streams.set(conversationId, { conversationId, status: 'pending', text: '' })
  emit()
}

/** Append one `message.delta` chunk to the in-flight reply. */
export function appendStreamDelta(conversationId: number, delta: string): void {
  const existing = streams.get(conversationId)
  if (!existing) return
  streams.set(conversationId, {
    ...existing,
    status: 'streaming',
    text: existing.text + delta,
  })
  emit()
}

/** The reply completed (persisted server-side); remove the transient bubble. */
export function finishStream(conversationId: number): void {
  if (!streams.delete(conversationId)) return
  emit()
}

/** The reply failed; remove the transient bubble (the error is toasted separately). */
export function failStream(conversationId: number): void {
  if (!streams.delete(conversationId)) return
  emit()
}

/** Sign-out cleanup: drop every in-flight stream. */
export function resetAllStreams(): void {
  streams.clear()
  emit()
}

/** Reactively returns the in-flight stream for a conversation, if any. */
export function useConversationStream(conversationId: number): ConversationStream | undefined {
  return useSyncExternalStore(
    subscribe,
    () => streams.get(conversationId),
    () => streams.get(conversationId),
  )
}
