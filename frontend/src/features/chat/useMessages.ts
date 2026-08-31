import { useInfiniteQuery, useQueryClient } from '@tanstack/react-query'
import { listMessages } from '../../api/messages'
import type { Message } from '../../types/api'

const PAGE_SIZE = 50

export function messagesQueryKey(conversationId: number) {
  return ['messages', conversationId] as const
}

/**
 * A conversation's messages, oldest first. Pages backwards with the API's
 * `before_id` cursor: the first page is the newest 50 messages, each further
 * page fetches the 50 older messages preceding it.
 */
export function useMessages(conversationId: number) {
  return useInfiniteQuery({
    queryKey: messagesQueryKey(conversationId),
    queryFn: ({ pageParam }) =>
      listMessages(conversationId, { limit: PAGE_SIZE, before_id: pageParam ?? undefined }),
    initialPageParam: null as number | null,
    getNextPageParam: (lastPage) =>
      lastPage.length === PAGE_SIZE ? (lastPage[0]?.id ?? undefined) : undefined,
    select: (data) => ({
      pages: data.pages,
      pageParams: data.pageParams,
      // Chronological order: pages arrive newest-first, each page ascending.
      messages: [...data.pages].reverse().flat(),
      hasMore: data.pages.at(-1)?.length === PAGE_SIZE,
    }),
  })
}

/** Optimistically appends a local message (the user's just-sent text) to the
 *  newest page of the message cache; the next server refetch replaces it with
 *  the persisted record. */
export function appendMessageToCache(
  queryClient: ReturnType<typeof useQueryClient>,
  conversationId: number,
  message: Message,
): void {
  queryClient.setQueryData<{
    pages: Message[][]
    pageParams: (number | null)[]
  }>(messagesQueryKey(conversationId), (data) => {
    if (!data) return data
    const pages = data.pages.map((page) => [...page])
    const lastPage = pages[pages.length - 1]
    if (lastPage) lastPage.push(message)
    else pages.push([message])
    return { ...data, pages }
  })
}
