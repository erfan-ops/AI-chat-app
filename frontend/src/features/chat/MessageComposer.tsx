import { useCallback, useEffect, useRef, useState } from 'react'
import type { KeyboardEvent } from 'react'
import { useSendMessage } from './useSendMessage'
import { useConversationStream } from './streamingStore'
import { SendIcon, XIcon } from '../../components/Icons'
import { Spinner } from '../../components/Spinner'
import type { Message } from '../../types/api'
import styles from './MessageComposer.module.css'

const MAX_CONTENT_LENGTH = 32_000 // matches the backend's MessageCreate limit

export interface MessageComposerProps {
  conversationId: number
  characterName: string
  /** The message the next send will reply to (null = plain message). */
  replyTo: Message | null
  onCancelReply: () => void
}

/** The message input: Enter sends, Shift+Enter inserts a newline, the input
 *  auto-grows up to six lines, and sending is disabled while the character is
 *  already replying. The rest of the UI stays interactive during sends. */
export function MessageComposer({
  conversationId,
  characterName,
  replyTo,
  onCancelReply,
}: MessageComposerProps) {
  const [value, setValue] = useState('')
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const send = useSendMessage(conversationId)
  const stream = useConversationStream(conversationId)

  const replying = stream !== undefined
  const trimmed = value.trim()
  const canSend = trimmed !== '' && !replying && !send.isPending && trimmed.length <= MAX_CONTENT_LENGTH

  const submit = useCallback(() => {
    if (!canSend) return
    send.mutate(
      { content: trimmed, replyToId: replyTo?.id ?? null },
      {
        onError: () => {
          // Restore the text so the user can retry without retyping.
          setValue(trimmed)
        },
      },
    )
    setValue('')
    onCancelReply()
    textareaRef.current?.focus()
  }, [canSend, send, trimmed, replyTo, onCancelReply])

  // Auto-grow: start at one line, up to six.
  useEffect(() => {
    const textarea = textareaRef.current
    if (!textarea) return
    textarea.style.height = 'auto'
    textarea.style.height = `${Math.min(textarea.scrollHeight, 6 * 24 + 2 * 12)}px`
  }, [value])

  // Refocus after a reply finishes (the composer stays disabled in between).
  useEffect(() => {
    if (!replying && !send.isPending) textareaRef.current?.focus()
  }, [replying, send.isPending])

  function handleKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault()
      submit()
    }
  }

  const placeholder = replying
    ? `${characterName} is replying…`
    : `Message ${characterName}`

  return (
    <div className={styles.wrap}>
      {replyTo && (
        <div className={styles.replyChip}>
          <div className={styles.replyChipText}>
            <span className={styles.replyChipName}>
              Replying to {replyTo.role === 'user' ? 'you' : characterName}
            </span>
            <span className={styles.replyChipSnippet}>{replyTo.content}</span>
          </div>
          <button
            type="button"
            className={styles.replyChipClose}
            onClick={onCancelReply}
            aria-label="Cancel reply"
          >
            <XIcon aria-hidden="true" />
          </button>
        </div>
      )}
      <div className={`${styles.composer} ${replying ? styles.composerBusy : ''}`}>
        <textarea
          ref={textareaRef}
          className={styles.input}
          rows={1}
          value={value}
          placeholder={placeholder}
          onChange={(event) => setValue(event.target.value)}
          onKeyDown={handleKeyDown}
          disabled={replying || send.isPending}
          maxLength={MAX_CONTENT_LENGTH}
          aria-label={`Message ${characterName}`}
        />
        <button
          type="button"
          className={styles.send}
          onClick={submit}
          disabled={!canSend}
          aria-label={`Send message to ${characterName}`}
          title="Send (Enter)"
        >
          {replying || send.isPending ? (
            <Spinner size={17} label={`Waiting for ${characterName} to reply`} className={styles.sendSpinner} />
          ) : (
            <SendIcon aria-hidden="true" />
          )}
        </button>
      </div>
      <p className={styles.hint}>Enter to send · Shift+Enter for a new line</p>
    </div>
  )
}
