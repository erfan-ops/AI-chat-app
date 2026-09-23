import { useCallback, useEffect, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { getConversation } from '../../api/conversations'
import { CharacterProfileModal } from '../characters/CharacterProfileModal'
import { useCharacter } from '../characters/useCharacters'
import { useConversationStream } from './streamingStore'
import { MessageList } from './MessageList'
import { MessageComposer } from './MessageComposer'
import { Avatar } from '../../components/Avatar'
import { EmptyState } from '../../components/EmptyState'
import { ErrorState } from '../../components/ErrorState'
import { Spinner } from '../../components/Spinner'
import { ChatBubbleIcon, ChevronLeftIcon, SidebarIcon } from '../../components/Icons'
import { ApiError } from '../../api/client'
import { pushToast } from '../../components/toastStore'
import type { Message } from '../../types/api'
import styles from './ChatView.module.css'

export interface ChatViewProps {
  conversationId: number
  /** Shown on narrow screens to go back to the conversation list. */
  onBack: () => void
  /** Whether the conversation panel is collapsed (wide screens only). */
  sidebarHidden?: boolean
  /** Collapses/expands the conversation panel (wide screens only). */
  onToggleSidebar?: () => void
}

/** Panel toggle for wide screens — hidden on narrow ones, where the back
 *  button plays that role. */
function SidebarToggle({
  sidebarHidden,
  onToggleSidebar,
  floating = false,
}: {
  sidebarHidden: boolean
  onToggleSidebar: () => void
  /** Corner-positioned variant, for the view that has no header. */
  floating?: boolean
}) {
  return (
    <button
      type="button"
      className={`${styles.sidebarToggle} ${floating ? styles.sidebarToggleFloating : ''}`}
      onClick={onToggleSidebar}
      aria-controls="conversations-panel"
      aria-expanded={!sidebarHidden}
      aria-label={sidebarHidden ? 'Show conversations' : 'Hide conversations'}
      title={sidebarHidden ? 'Show conversations' : 'Hide conversations'}
    >
      <SidebarIcon aria-hidden="true" />
    </button>
  )
}

/** The active conversation: header (character + model), message history and
 *  the message composer. The conversation detail query also catches the case
 *  where the conversation was deleted or is no longer reachable (404). */
export function ChatView({
  conversationId,
  onBack,
  sidebarHidden = false,
  onToggleSidebar,
}: ChatViewProps) {
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
  // Clicking the header avatar opens the character profile. ChatView remounts on
  // every conversation switch (AppShell keys it by id), so this never leaks.
  const [profileOpen, setProfileOpen] = useState(false)
  // Stable identity: Modal's effect depends on onClose, and this component
  // re-renders on every stream delta — an inline arrow would re-run that effect
  // (and steal focus) continuously while a reply streams.
  const closeProfile = useCallback(() => setProfileOpen(false), [])

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
  const characterName = conversation.character?.name ?? 'Assistant'
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
        {onToggleSidebar && (
          <SidebarToggle sidebarHidden={sidebarHidden} onToggleSidebar={onToggleSidebar} />
        )}
        {conversation.character ? (
          <button
            type="button"
            className={styles.avatarButton}
            onClick={() => setProfileOpen(true)}
            // The Avatar is aria-hidden, so this is the button's only accessible
            // name. No `title`: Playwright's getByTitle matches substring and the
            // e2e scripts select conversations with getByTitle(<character name>).
            aria-label={`View ${characterName} profile`}
          >
            <Avatar name={characterName} src={character?.avatar_url} size={38} />
          </button>
        ) : (
          <Avatar name={characterName} src={character?.avatar_url} size={38} />
        )}
        <div className={styles.headerTexts}>
          <h1 className={styles.headerTitle}>{conversation.title?.trim() || characterName}</h1>
          <p className={styles.headerSubtitle}>
            {isTyping ? (
              <span className={styles.typing}>{characterName} is typing…</span>
            ) : (
              <>
                {modelLabel ?? 'AI chat'}
                {conversation.user_persona && <> · as {conversation.user_persona.name}</>}
              </>
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

      <CharacterProfileModal
        open={profileOpen}
        onClose={closeProfile}
        name={characterName}
        character={character}
      />
    </section>
  )
}

export interface NoConversationPlaceholderProps {
  sidebarHidden?: boolean
  onToggleSidebar?: () => void
}

/** Placeholder shown when no conversation is open (desktop layout). Keeps the
 *  panel toggle available, so a collapsed sidebar can always be brought back. */
export function NoConversationPlaceholder({
  sidebarHidden = false,
  onToggleSidebar,
}: NoConversationPlaceholderProps = {}) {
  return (
    <section className={styles.chat}>
      {onToggleSidebar && (
        <SidebarToggle
          sidebarHidden={sidebarHidden}
          onToggleSidebar={onToggleSidebar}
          floating
        />
      )}
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
