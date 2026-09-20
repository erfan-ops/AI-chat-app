import { useCallback, useEffect, useRef, useState } from 'react'
import type { KeyboardEvent } from 'react'
import { useDeleteConversation, useRenameConversation } from './useConversations'
import { CharacterProfileModal } from '../characters/CharacterProfileModal'
import { useCharacter } from '../characters/useCharacters'
import type { Conversation } from '../../types/api'
import { formatRelativeTime } from '../../utils/dates'
import { errorMessage } from '../../utils/errors'
import { Avatar } from '../../components/Avatar'
import { Modal } from '../../components/Modal'
import { Spinner } from '../../components/Spinner'
import { pushToast } from '../../components/toastStore'
import { PencilIcon, TrashIcon } from '../../components/Icons'
import styles from './ConversationListItem.module.css'

export interface ConversationListItemProps {
  conversation: Conversation
  isActive: boolean
  onSelect: () => void
}

/** A conversation row in the sidebar with hover actions: rename and delete.
 *  Without a user-provided title the character's name identifies the chat. */
export function ConversationListItem({ conversation, isActive, onSelect }: ConversationListItemProps) {
  const [editing, setEditing] = useState(false)
  const [draftTitle, setDraftTitle] = useState('')
  const [confirmingDelete, setConfirmingDelete] = useState(false)
  const rename = useRenameConversation()
  const remove = useDeleteConversation()
  const inputRef = useRef<HTMLInputElement>(null)
  const [profileOpen, setProfileOpen] = useState(false)
  // Stable identity: Modal's effect depends on onClose and ConversationListItem
  // re-renders whenever the conversation list refetches.
  const closeProfile = useCallback(() => setProfileOpen(false), [])

  const character = useCharacter(conversation.character?.id)
  const characterName = conversation.character?.name ?? 'Assistant'
  const displayTitle = conversation.title?.trim() || characterName
  const modelLabel = conversation.model?.display_name ?? conversation.model?.model_name ?? null

  useEffect(() => {
    if (editing) {
      inputRef.current?.focus()
      inputRef.current?.select()
    }
  }, [editing])

  function startEditing() {
    setDraftTitle(conversation.title?.trim() || '')
    setEditing(true)
  }

  function commitRename() {
    const nextTitle = draftTitle.trim()
    setEditing(false)
    if (nextTitle === '' || nextTitle === (conversation.title?.trim() || '')) return
    rename.mutate(
      { id: conversation.id, title: nextTitle },
      {
        onError: (error) => {
          pushToast('error', errorMessage(error))
        },
      },
    )
  }

  function handleRenameKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key === 'Enter') commitRename()
    else if (event.key === 'Escape') setEditing(false)
  }

  function handleDelete() {
    setConfirmingDelete(false)
    remove.mutate(conversation.id, {
      onError: (error) => {
        pushToast('error', errorMessage(error))
      },
    })
  }

  return (
    <>
      <div
        role="listitem"
        className={`${styles.item} ${isActive ? styles.itemActive : ''} ${editing ? styles.itemEditing : ''}`}
      >
        {conversation.character && (
          <button
            type="button"
            className={styles.avatarButton}
            onClick={() => setProfileOpen(true)}
            disabled={editing}
            // The Avatar is aria-hidden, so this is the button's only accessible
            // name. No `title`: Playwright's getByTitle matches substring, and the
            // e2e scripts select rows with getByTitle(<character name>).first().
            aria-label={`View ${characterName} profile`}
          >
            <Avatar name={characterName} src={character?.avatar_url} size={42} />
          </button>
        )}

        <button
          type="button"
          className={styles.main}
          onClick={editing ? undefined : onSelect}
          disabled={editing}
          aria-current={isActive ? 'true' : undefined}
          title={displayTitle}
        >
          <span className={styles.texts}>
            {editing ? (
              <input
                ref={inputRef}
                className={styles.renameInput}
                value={draftTitle}
                onChange={(event) => setDraftTitle(event.target.value)}
                onBlur={commitRename}
                onKeyDown={handleRenameKeyDown}
                maxLength={255}
                aria-label="Conversation name"
                onClick={(event) => event.stopPropagation()}
              />
            ) : (
              <>
                <span className={styles.title}>{displayTitle}</span>
                <span className={styles.meta}>
                  {conversation.user_persona && (
                    <span className={styles.persona}>as {conversation.user_persona.name}</span>
                  )}
                  {modelLabel && <span className={styles.model}>{modelLabel}</span>}
                  {conversation.last_message_at && (
                    <span className={styles.time}>
                      {formatRelativeTime(conversation.last_message_at)}
                    </span>
                  )}
                </span>
              </>
            )}
          </span>
        </button>

        {!editing && (
          <span className={styles.actions}>
            <button
              type="button"
              className={styles.actionButton}
              onClick={startEditing}
              aria-label={`Rename ${displayTitle}`}
              title="Rename"
            >
              <PencilIcon aria-hidden="true" />
            </button>
            <button
              type="button"
              className={`${styles.actionButton} ${styles.deleteButton}`}
              onClick={() => setConfirmingDelete(true)}
              aria-label={`Delete ${displayTitle}`}
              title="Delete"
            >
              <TrashIcon aria-hidden="true" />
            </button>
          </span>
        )}
      </div>

      <Modal
        open={confirmingDelete}
        onClose={() => setConfirmingDelete(false)}
        title="Delete conversation?"
        size="sm"
      >
        <p className={styles.confirmText}>
          “{displayTitle}” and its messages will be removed from your chat list. This
          cannot be undone.
        </p>
        <div className={styles.confirmActions}>
          <button
            type="button"
            className={styles.confirmCancel}
            onClick={() => setConfirmingDelete(false)}
            disabled={remove.isPending}
          >
            Cancel
          </button>
          <button
            type="button"
            className={styles.confirmDelete}
            onClick={handleDelete}
            disabled={remove.isPending}
          >
            {remove.isPending && <Spinner size={14} label="Deleting" className={styles.confirmSpinner} />}
            Delete
          </button>
        </div>
      </Modal>

      <CharacterProfileModal
        open={profileOpen}
        onClose={closeProfile}
        name={characterName}
        character={character}
      />
    </>
  )
}
