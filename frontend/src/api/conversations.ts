import { apiRequest, buildUrl } from './client'
import type {
  Conversation,
  ConversationCreateRequest,
  ConversationUpdateRequest,
} from '../types/api'

export interface ListConversationsParams {
  /** 1..100, default 20. */
  limit?: number
  offset?: number
}

export function listConversations(params: ListConversationsParams = {}): Promise<Conversation[]> {
  return apiRequest<Conversation[]>(
    buildUrl('/conversations', { limit: params.limit ?? 30, offset: params.offset ?? 0 }),
  )
}

export function createConversation(request: ConversationCreateRequest): Promise<Conversation> {
  return apiRequest<Conversation>('/conversations', { method: 'POST', body: request })
}

export function getConversation(conversationId: number): Promise<Conversation> {
  return apiRequest<Conversation>(`/conversations/${conversationId}`)
}

export function renameConversation(
  conversationId: number,
  title: string,
): Promise<Conversation> {
  const body: ConversationUpdateRequest = { title }
  return apiRequest<Conversation>(`/conversations/${conversationId}`, {
    method: 'PATCH',
    body,
  })
}

/** Soft delete: marks the conversation DELETED server-side. */
export function deleteConversation(conversationId: number): Promise<void> {
  return apiRequest<void>(`/conversations/${conversationId}`, { method: 'DELETE' })
}
