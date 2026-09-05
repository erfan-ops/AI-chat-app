import { Fragment, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { useConversations } from './useConversations'
import { useSession, clearSession } from '../../session/authSession'
import { resetAllStreams } from '../chat/streamingStore'
import { Avatar } from '../../components/Avatar'
import { EmptyState } from '../../components/EmptyState'
import { ErrorState } from '../../components/ErrorState'
import { Spinner } from '../../components/Spinner'
import {
  ChatBubbleIcon,
  LogOutIcon,
  PlusIcon,
} from '../../components/Icons'
import { ConversationListItem } from './ConversationListItem'
import styles from './ConversationSidebar.module.css'

export interface ConversationSidebarProps {
  activeConversationId: number | null
  onSelectConversation: (id: number) => void
  onNewConversation: () => void
}

export function ConversationSidebar({
  activeConversationId,
  onSelectConversation,
  onNewConversation,
}: ConversationSidebarProps) {
  const session = useSession()
  const queryClient = useQueryClient()
  const query = useConversations()
  const [loggingOut, setLoggingOut] = useState(false)

  const conversations = query.data?.pages.flat() ?? []
  const user = session?.user

  function handleLogout() {
    setLoggingOut(true)
    resetAllStreams()
    queryClient.clear()
    clearSession()
    // App switches to the auth screen as soon as the session is gone.
  }

  return (
    <aside className={styles.sidebar}>
      <header className={styles.header}>
        <div className={styles.brand}>
          <span className={styles.brandLogo}>
            <ChatBubbleIcon aria-hidden="true" />
          </span>
          <span className={styles.brandName}>AI Chat</span>
        </div>
        <button
          type="button"
          className={styles.logout}
          onClick={handleLogout}
          disabled={loggingOut}
          aria-label="Sign out"
          title="Sign out"
        >
          <LogOutIcon aria-hidden="true" />
        </button>
      </header>

      <div className={styles.newChatWrap}>
        <button type="button" className={styles.newChat} onClick={onNewConversation}>
          <PlusIcon aria-hidden="true" />
          New chat
        </button>
      </div>

      <div className={styles.list} role="list" aria-label="Conversations">
        {query.isPending && <SidebarSkeleton />}

        {query.isError && (
          <ErrorState
            error={query.error}
            onRetry={() => {
              void query.refetch()
            }}
            className={styles.state}
          />
        )}

        {query.isSuccess && conversations.length === 0 && (
          <EmptyState
            icon={<ChatBubbleIcon aria-hidden="true" />}
            title="No conversations yet"
            hint="Start a new chat with an AI character."
            action={
              <button type="button" className={styles.emptyAction} onClick={onNewConversation}>
                <PlusIcon aria-hidden="true" />
                Start a chat
              </button>
            }
            className={styles.state}
          />
        )}

        {conversations.map((conversation) => (
          <Fragment key={conversation.id}>
            <ConversationListItem
              conversation={conversation}
              isActive={conversation.id === activeConversationId}
              onSelect={() => onSelectConversation(conversation.id)}
            />
          </Fragment>
        ))}

        {query.hasNextPage && (
          <button
            type="button"
            className={styles.loadMore}
            onClick={() => void query.fetchNextPage()}
            disabled={query.isFetchingNextPage}
          >
            {query.isFetchingNextPage ? (
              <Spinner size={16} label="Loading more conversations" />
            ) : (
              'Load older conversations'
            )}
          </button>
        )}
      </div>

      <footer className={styles.footer}>
        <Avatar name={user?.display_name ?? user?.username ?? '?'} size={32} />
        <div className={styles.userInfo}>
          <p className={styles.userName}>{user?.display_name ?? user?.username}</p>
          {user?.display_name && <p className={styles.userHandle}>@{user.username}</p>}
        </div>
      </footer>
    </aside>
  )
}

function SidebarSkeleton() {
  return (
    <div className={styles.skeleton} aria-hidden="true">
      {Array.from({ length: 6 }, (_, i) => (
        <div key={i} className={styles.skeletonItem}>
          <span className={styles.skeletonAvatar} />
          <span className={styles.skeletonLines}>
            <span className={styles.skeletonLine} style={{ width: '55%' }} />
            <span className={styles.skeletonLine} style={{ width: '75%' }} />
          </span>
        </div>
      ))}
    </div>
  )
}
