import { useMutation, useQueryClient } from '@tanstack/react-query'
import { sendMessageStream } from '../../api/messages'
import { ApiError } from '../../api/client'
import { conversationsQueryKey } from '../conversations/useConversations'
import { pushToast } from '../../components/toastStore'
import { errorMessage } from '../../utils/errors'
import { appendMessageToCache, messagesQueryKey } from './useMessages'
import {
  appendStreamDelta,
  beginStream,
  failStream,
  finishStream,
} from './streamingStore'
import type { Message } from '../../types/api'

const OPTIMISTIC_ID_BASE = -1_000_000

export interface SendMessageVariables {
  content: string
  /** The message being replied to, or null for a normal message. */
  replyToId: number | null
}

/**
 * Sends a message and drives the streamed AI reply.
 *
 * Every side effect lives inside `mutationFn` (not the observer callbacks) so
 * the stream keeps updating the shared caches even if the user switches
 * conversations mid-reply and this hook's component unmounts.
 */
export function useSendMessage(conversationId: number) {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: async ({ content, replyToId }: SendMessageVariables) => {
      // Optimistic user bubble; the persisted record arrives via `message.created`.
      const optimistic: Message = {
        id: OPTIMISTIC_ID_BASE - Date.now(),
        conversation_id: conversationId,
        role: 'user',
        content,
        created_at: new Date().toISOString(),
        reply_to_id: replyToId,
      }
      appendMessageToCache(queryClient, conversationId, optimistic)
      beginStream(conversationId)

      const refresh = () => {
        void queryClient.invalidateQueries({ queryKey: messagesQueryKey(conversationId) })
        void queryClient.invalidateQueries({ queryKey: ['conversation', conversationId] })
        void queryClient.invalidateQueries({ queryKey: conversationsQueryKey })
      }

      try {
        return await sendMessageStream(conversationId, content, replyToId, {
          onCreated: refresh,
          onDelta: ({ content: delta }) => appendStreamDelta(conversationId, delta),
          onCompleted: (event) => {
            finishStream(conversationId)
            refresh()
            return event.message
          },
          onStreamError: () => {
            // The stream ended in an `error` event; the user message stays
            // persisted but no assistant reply exists. Handled by the catch.
          },
        })
      } catch (error) {
        failStream(conversationId)
        refresh() // reconcile with the server (user message is persisted)
        if (error instanceof ApiError && error.status === 401) {
          // Session cleared by the API client; the user is back on sign-in.
          throw error
        }
        pushToast('error', errorMessage(error))
        throw error
      }
    },
  })
}
