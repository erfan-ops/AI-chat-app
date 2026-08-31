import { useEffect, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { getConversation } from '../../api/conversations'
import { useCharacter } from '../characters/useCharacters'
import { useConversationStream } from './streamingStore'
import { MessageList } from './MessageList'
import { MessageComposer } from './MessageComposer'
import { Avatar } from '../../components/Avatar'
import { EmptyState } from '../../components/EmptyState'
import { ErrorState } from '../../components/ErrorState'
import { Spinner } from '../../components/Spinner'
import { ChatBubbleIcon, ChevronLeftIcon } from '../../components/Icons'
import { ApiError } from '../../api/client'
import { pushToast } from '../../components/toastStore'
import type { Message } from '../../types/api'
import styles from './ChatView.module.css'

export interface ChatViewProps {
  conversationId: number
  /** Shown on narrow screens to go back to the conversation list. */
  onBack: () => void
}

/** The active conversation: header (character + model), message history and
 *  the message composer. The conversation detail query also catches the case
 *  where the conversation was deleted or is no longer reachable (404). */
export function ChatView({ conversationId, onBack }: ChatViewProps) {
  const conversationQuery = useQuery({
    queryKey: ['conversation', conversationId],
    queryFn: () => getConversation(conversationId),
  })
  const character = useCharacter(conversationQuery.data?.character?.id)
  const stream = useConversationStream(conversationId)
  // The message the composer is replying to (set from a message's context menu).
  // Reset when the conversation changes — adjusted during render, so a stale
  // quote from the previous conversation never shows.
  const [replyTo, setReplyTo] = useState<Message | null>(null)
  const [replyConversationId, setReplyConversationId] = useState(conversationId)
  if (replyConversationId !== conversationId) {
    setReplyConversationId(conversationId)
    setReplyTo(null)
  }

  useEffect(() => {
    if (conversationQuery.error instanceof ApiError && conversationQuery.error.status === 404) {
      pushToast('info', 'This conversation is no longer available.')
      onBack()
    }
  }, [conversationQuery.error, onBack])

  if (conversationQuery.isPending) {
    return (
      <section className={styles.chat}>
        <div className={styles.center}>
          <Spinner size={26} label="Opening conversation" />
        </div>
      </section>
    )
  }

  if (conversationQuery.isError || !conversationQuery.data) {
    return (
      <section className={styles.chat}>
        <div className={styles.center}>
          <ErrorState
            error={conversationQuery.error}
            onRetry={() => {
              void conversationQuery.refetch()
            }}
          />
        </div>
      </section>
    )
  }

  const conversation = conversationQuery.data
  const characterName = conversation.character?.name ?? 'Companion'
  const modelLabel = conversation.model?.display_name ?? conversation.model?.model_name ?? null
  const isTyping = stream !== undefined

  return (
    <section className={styles.chat}>
      <header className={styles.header}>
        <button
          type="button"
          className={styles.back}
          onClick={onBack}
          aria-label="Back to conversations"
        >
          <ChevronLeftIcon aria-hidden="true" />
        </button>
        <Avatar name={characterName} src={character?.avatar_url} size={38} />
        <div className={styles.headerTexts}>
          <h1 className={styles.headerTitle}>{conversation.title?.trim() || characterName}</h1>
          <p className={styles.headerSubtitle}>
            {isTyping ? (
              <span className={styles.typing}>{characterName} is typing…</span>
            ) : (
              modelLabel ?? 'AI companion'
            )}
          </p>
        </div>
      </header>

      {conversationQuery.isSuccess && (
        <MessageList
          key={conversation.id}
          conversationId={conversation.id}
          characterName={characterName}
          characterAvatarUrl={character?.avatar_url}
          onReply={setReplyTo}
        />
      )}

      <MessageComposer
        conversationId={conversation.id}
        characterName={characterName}
        replyTo={replyTo}
        onCancelReply={() => setReplyTo(null)}
      />
    </section>
  )
}

/** Placeholder shown when no conversation is open (desktop layout). */
export function NoConversationPlaceholder() {
  return (
    <section className={styles.chat}>
      <div className={styles.center}>
        <EmptyState
          icon={<ChatBubbleIcon aria-hidden="true" />}
          title="Pick a conversation"
          hint="Choose a conversation on the left, or start a new chat."
        />
      </div>
    </section>
  )
}
