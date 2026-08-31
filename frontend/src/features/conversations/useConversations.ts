import { useInfiniteQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  createConversation,
  deleteConversation,
  listConversations,
  renameConversation,
} from '../../api/conversations'
import type { ConversationCreateRequest } from '../../types/api'

const PAGE_SIZE = 30

export const conversationsQueryKey = ['conversations'] as const

/** The user's conversations, newest activity first (server-side ordering),
 *  paged with limit/offset via a "Load more" affordance. */
export function useConversations() {
  return useInfiniteQuery({
    queryKey: conversationsQueryKey,
    queryFn: ({ pageParam }) =>
      listConversations({ limit: PAGE_SIZE, offset: pageParam }),
    initialPageParam: 0,
    getNextPageParam: (lastPage, _allPages, lastPageParam) =>
      lastPage.length === PAGE_SIZE ? lastPageParam + PAGE_SIZE : undefined,
  })
}

export function useCreateConversation() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (request: ConversationCreateRequest) => createConversation(request),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: conversationsQueryKey })
    },
  })
}

export function useRenameConversation() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ id, title }: { id: number; title: string }) => renameConversation(id, title),
    onSuccess: (conversation) => {
      void queryClient.invalidateQueries({ queryKey: conversationsQueryKey })
      void queryClient.invalidateQueries({ queryKey: ['conversation', conversation.id] })
    },
  })
}

export function useDeleteConversation() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (id: number) => deleteConversation(id),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: conversationsQueryKey })
    },
  })
}
