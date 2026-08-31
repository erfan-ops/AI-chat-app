import { API_BASE_URL, apiRequest, ApiError, buildUrl, extractErrorDetail } from './client'
import { getSession } from '../session/authSession'
import { parseSseStream } from './sse'
import type {
  Message,
  MessageCompletedEvent,
  MessageCreateRequest,
  MessageCreatedEvent,
  MessageDeltaEvent,
  StreamErrorEvent,
} from '../types/api'

export interface ListMessagesParams {
  /** 1..200, default 50. */
  limit?: number
  /** Exclusive cursor: returns messages older than this id (backwards paging). */
  before_id?: number
}

/** Returns messages in chronological order. */
export function listMessages(
  conversationId: number,
  params: ListMessagesParams = {},
): Promise<Message[]> {
  return apiRequest<Message[]>(
    buildUrl(`/conversations/${conversationId}/messages`, {
      limit: params.limit ?? 50,
      before_id: params.before_id,
    }),
  )
}

export interface SendMessageStreamHandlers {
  onCreated: (event: MessageCreatedEvent) => void
  onDelta: (event: MessageDeltaEvent) => void
  onCompleted: (event: MessageCompletedEvent) => void
  onStreamError: (event: StreamErrorEvent) => void
}

/**
 * Sends a message and consumes the streamed AI reply.
 *
 * The endpoint persists the user message up front, then streams SSE:
 * `message.created` → `message.delta`* → `message.completed`, or `error`
 * (on error the user message stays persisted but no assistant message is saved).
 *
 * Resolves with the completed assistant message; rejects with an ApiError on
 * HTTP failures and on stream errors. Requires a fetch streaming implementation
 * (ReadableStream) — supported by all current browsers.
 */
export async function sendMessageStream(
  conversationId: number,
  content: string,
  replyToId: number | null,
  handlers: SendMessageStreamHandlers,
  signal?: AbortSignal,
): Promise<Message> {
  const token = getSession()?.accessToken ?? null

  const body: MessageCreateRequest = { content, reply_to_id: replyToId }
  let response: Response
  try {
    response = await fetch(`${API_BASE_URL}/conversations/${conversationId}/messages`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
      body: JSON.stringify(body),
      signal,
    })
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') throw error
    throw new ApiError(0, 'Could not reach the server. Check your connection and try again.')
  }

  if (!response.ok) {
    throw new ApiError(response.status, await extractErrorDetail(response))
  }
  if (!response.body) {
    throw new ApiError(0, 'Your browser does not support streamed responses.')
  }

  for await (const event of parseSseStream(response.body)) {
    switch (event.event) {
      case 'message.created':
        handlers.onCreated(JSON.parse(event.data) as MessageCreatedEvent)
        break
      case 'message.delta':
        handlers.onDelta(JSON.parse(event.data) as MessageDeltaEvent)
        break
      case 'message.completed': {
        const completed = JSON.parse(event.data) as MessageCompletedEvent
        handlers.onCompleted(completed)
        return completed.message
      }
      case 'error': {
        const streamError = JSON.parse(event.data) as StreamErrorEvent
        handlers.onStreamError(streamError)
        throw new ApiError(502, streamError.detail)
      }
    }
  }

  // The stream ended without a completion event.
  throw new ApiError(0, 'The connection closed before the reply was finished.')
}
