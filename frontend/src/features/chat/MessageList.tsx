import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { useMessages } from './useMessages'
import { useConversationStream } from './streamingStore'
import { formatDayLabel, formatTime, isSameDay } from '../../utils/dates'
import { Avatar } from '../../components/Avatar'
import { EmptyState } from '../../components/EmptyState'
import { ErrorState } from '../../components/ErrorState'
import { Spinner } from '../../components/Spinner'
import { ArrowDownIcon, ChatBubbleIcon } from '../../components/Icons'
import { MessageContextMenu } from './MessageContextMenu'
import type { MenuPosition } from './MessageContextMenu'
import { useLongPress } from './useLongPress'
import type { Message } from '../../types/api'
import styles from './MessageList.module.css'

const BOTTOM_THRESHOLD_PX = 90
const EMPTY_MESSAGES: Message[] = []

export interface MessageListProps {
  conversationId: number
  characterName: string
  characterAvatarUrl?: string | null
  /** Called when the user picks Reply from a message's context menu. */
  onReply: (message: Message) => void
}

interface MenuState extends MenuPosition {
  message: Message
}

export function MessageList({
  conversationId,
  characterName,
  characterAvatarUrl,
  onReply,
}: MessageListProps) {
  const query = useMessages(conversationId)
  const stream = useConversationStream(conversationId)
  const scrollRef = useRef<HTMLDivElement>(null)
  const topSentinelRef = useRef<HTMLDivElement>(null)
  const [atBottom, setAtBottom] = useState(true)
  const [menu, setMenu] = useState<MenuState | null>(null)
  const prevScrollHeightRef = useRef(0)
  const prevFirstIdRef = useRef<number | null>(null)

  const messages = useMemo(() => query.data?.messages ?? EMPTY_MESSAGES, [query.data])
  const hasOlder = query.data?.hasMore ?? false
  const { fetchNextPage, isFetchingNextPage } = query

  // Reply quotes look up the referenced message by id. A user-replied message is
  // always on a loaded page; the AI's reply target may be older than the loaded
  // window (its context extends further back) — the quote then degrades to none.
  const messagesById = useMemo(() => {
    const map = new Map<number, Message>()
    for (const message of messages) map.set(message.id, message)
    return map
  }, [messages])

  function scrollToBottom(behavior: ScrollBehavior) {
    const el = scrollRef.current
    if (el) el.scrollTo({ top: el.scrollHeight, behavior })
  }

  function scrollToMessage(messageId: number) {
    const root = scrollRef.current
    if (!root) return
    root
      .querySelector<HTMLElement>(`[data-message-id="${messageId}"]`)
      ?.scrollIntoView({ block: 'nearest' })
  }

  function handleScroll() {
    const el = scrollRef.current
    if (!el) return
    setAtBottom(el.scrollHeight - el.scrollTop - el.clientHeight < BOTTOM_THRESHOLD_PX)
  }

  // Load the previous page when the top sentinel approaches the viewport.
  useEffect(() => {
    const root = scrollRef.current
    const sentinel = topSentinelRef.current
    if (!root || !sentinel || !hasOlder) return
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries[0]?.isIntersecting && !isFetchingNextPage) {
          void fetchNextPage()
        }
      },
      { root, rootMargin: '300px 0px' },
    )
    observer.observe(sentinel)
    return () => observer.disconnect()
  }, [hasOlder, isFetchingNextPage, fetchNextPage])

  // Keep the visible content anchored when older messages are prepended.
  useLayoutEffect(() => {
    const el = scrollRef.current
    if (!el) return
    const firstId = messages[0]?.id ?? null
    if (
      prevFirstIdRef.current !== null &&
      firstId !== null &&
      firstId !== prevFirstIdRef.current &&
      firstId < prevFirstIdRef.current
    ) {
      el.scrollTop += el.scrollHeight - prevScrollHeightRef.current
    }
    prevScrollHeightRef.current = el.scrollHeight
    prevFirstIdRef.current = firstId
  }, [messages])

  // Follow new content while the user is near the bottom.
  useEffect(() => {
    if (atBottom) scrollToBottom('auto')
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [messages.length, stream?.text, stream?.status, atBottom])

  // First paint of a conversation starts at the newest message.
  useLayoutEffect(() => {
    scrollToBottom('auto')
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [conversationId])

  const showEmptyState = messages.length === 0 && !stream
  const showLoading = query.isPending && messages.length === 0 && !stream

  return (
    <>
      <div className={styles.scrollArea} ref={scrollRef} onScroll={handleScroll}>
        <div className={styles.content}>
          <div ref={topSentinelRef} className={styles.sentinel} aria-hidden="true" />

          {query.isError && (
            <ErrorState
              error={query.error}
              onRetry={() => {
                void query.refetch()
              }}
              className={styles.state}
            />
          )}

          {showLoading && (
            <div className={styles.state}>
              <Spinner size={26} label="Loading messages" />
            </div>
          )}

          {showEmptyState && (
            <EmptyState
              icon={<ChatBubbleIcon aria-hidden="true" />}
              title={`Say hello to ${characterName}`}
              hint="Your conversation starts here."
              className={styles.empty}
            />
          )}

          {messages.map((message, index) => {
            const previous = messages[index - 1]
            const next = messages[index + 1]
            const newDay = !previous || !isSameDay(previous.created_at, message.created_at)
            const groupStart = newDay || !previous || previous.role !== message.role
            const groupEnd =
              !next || next.role !== message.role || !isSameDay(next.created_at, message.created_at)

            return (
              <MessageRow
                key={message.id}
                message={message}
                characterName={characterName}
                characterAvatarUrl={characterAvatarUrl}
                dayLabel={newDay ? formatDayLabel(message.created_at) : null}
                groupStart={groupStart}
                groupEnd={groupEnd}
                repliedTo={
                  message.reply_to_id != null ? messagesById.get(message.reply_to_id) : undefined
                }
                onScrollToMessage={scrollToMessage}
                onOpenMenu={(x, y) => setMenu({ message, x, y })}
              />
            )
          })}

          {stream && (
            <StreamingRow
              characterName={characterName}
              characterAvatarUrl={characterAvatarUrl}
              text={stream.text}
              pending={stream.status === 'pending' && stream.text === ''}
            />
          )}

          {query.isFetchingNextPage && (
            <div className={styles.loadingOlder}>
              <Spinner size={16} label="Loading older messages" />
            </div>
          )}
        </div>

        {!atBottom && messages.length > 0 && (
          <button
            type="button"
            className={styles.jumpDown}
            onClick={() => scrollToBottom('smooth')}
            aria-label="Scroll to the latest message"
          >
            <ArrowDownIcon aria-hidden="true" />
          </button>
        )}
      </div>

      {menu && (
        <MessageContextMenu
          message={menu.message}
          x={menu.x}
          y={menu.y}
          onReply={(message) => {
            setMenu(null)
            onReply(message)
          }}
          onClose={() => setMenu(null)}
        />
      )}
    </>
  )
}

/* -------------------------------------------------------------------------- */

interface MessageRowProps {
  message: Message
  characterName: string
  characterAvatarUrl?: string | null
  dayLabel: string | null
  groupStart: boolean
  groupEnd: boolean
  /** The message this one replies to, when it is on a loaded page. */
  repliedTo?: Message
  onScrollToMessage: (messageId: number) => void
  onOpenMenu: (x: number, y: number) => void
}

function MessageRow({
  message,
  characterName,
  characterAvatarUrl,
  dayLabel,
  groupStart,
  groupEnd,
  repliedTo,
  onScrollToMessage,
  onOpenMenu,
}: MessageRowProps) {
  const mine = message.role === 'user'
  const showAvatar = !mine && groupEnd
  const { pressHandlers, wasTriggered } = useLongPress(onOpenMenu)

  // Right-click opens the menu. Mobile long-press opens it via useLongPress;
  // the browser's own contextmenu (fired after the hold) must not open a second.
  function handleContextMenu(event: React.MouseEvent) {
    event.preventDefault()
    if (wasTriggered()) return
    onOpenMenu(event.clientX, event.clientY)
  }

  return (
    <>
      {dayLabel && <DaySeparator label={dayLabel} />}
      <div
        className={`${styles.row} ${mine ? styles.rowMine : styles.rowTheirs} ${groupStart ? styles.rowGroupStart : ''}`}
        data-message-id={message.id}
        onContextMenu={handleContextMenu}
        {...pressHandlers}
      >
        <span className={styles.avatarSlot}>
          {showAvatar && (
            <Avatar name={characterName} src={characterAvatarUrl} size={30} />
          )}
        </span>
        <div className={`${styles.bubble} ${mine ? styles.bubbleMine : styles.bubbleTheirs}`}>
          {repliedTo && (
            <button
              type="button"
              className={`${styles.quote} ${mine ? styles.quoteMine : styles.quoteTheirs}`}
              onClick={() => onScrollToMessage(repliedTo.id)}
              title="Jump to the original message"
            >
              <span className={styles.quoteName}>
                {repliedTo.role === 'user' ? 'You' : characterName}
              </span>
              <span className={styles.quoteText}>{repliedTo.content}</span>
            </button>
          )}
          <p className={styles.text}>{message.content}</p>
          {groupEnd && <span className={styles.time}>{formatTime(message.created_at)}</span>}
        </div>
      </div>
    </>
  )
}

function StreamingRow({
  characterName,
  characterAvatarUrl,
  text,
  pending,
}: {
  characterName: string
  characterAvatarUrl?: string | null
  text: string
  pending: boolean
}) {
  return (
    <div className={`${styles.row} ${styles.rowTheirs} ${styles.rowGroupStart}`}>
      <span className={styles.avatarSlot}>
        <Avatar name={characterName} src={characterAvatarUrl} size={30} />
      </span>
      <div className={`${styles.bubble} ${styles.bubbleTheirs} ${styles.streamingBubble}`}>
        {pending || text === '' ? (
          <span className={styles.dots} aria-label={`${characterName} is typing`}>
            <span className={styles.dot} />
            <span className={styles.dot} />
            <span className={styles.dot} />
          </span>
        ) : (
          <p className={styles.text}>
            {text}
            <span className={styles.caret} aria-hidden="true" />
          </p>
        )}
      </div>
    </div>
  )
}

function DaySeparator({ label }: { label: string }) {
  return (
    <div className={styles.day}>
      <span className={styles.dayLabel}>{label}</span>
    </div>
  )
}
