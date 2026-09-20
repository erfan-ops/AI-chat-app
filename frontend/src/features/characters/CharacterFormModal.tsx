import { useState } from 'react'
import type { FormEvent } from 'react'
import { useCreateCharacter } from './useCharacters'
import { Modal } from '../../components/Modal'
import { Spinner } from '../../components/Spinner'
import { pushToast } from '../../components/toastStore'
import { errorMessage } from '../../utils/errors'
import type { Character } from '../../types/api'
import styles from './CharacterFormModal.module.css'

export interface CharacterFormModalProps {
  open: boolean
  onClose: () => void
  /** Called with the freshly created character so the caller can select it. */
  onCreated: (character: Character) => void
}

/** Limits mirroring the backend CharacterCreate schema: name ≤ 100,
 *  description ≤ 500, avatar_url ≤ 1000, system_prompt ≤ 20000. Rendered only
 *  while open, so form state starts empty on every open. */
export function CharacterFormModal({ open, onClose, onCreated }: CharacterFormModalProps) {
  const create = useCreateCharacter()
  const [name, setName] = useState('')
  const [systemPrompt, setSystemPrompt] = useState('')
  const [avatarUrl, setAvatarUrl] = useState('')
  const [description, setDescription] = useState('')

  const trimmedName = name.trim()
  const busy = create.isPending
  const canSubmit = trimmedName !== '' && !busy

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    create.mutate(
      {
        name: trimmedName,
        system_prompt: systemPrompt.trim() || null,
        avatar_url: avatarUrl.trim() || null,
        description: description.trim() || null,
      },
      {
        onSuccess: (character) => {
          onCreated(character)
          onClose()
        },
        onError: (error) => {
          pushToast('error', errorMessage(error))
        },
      },
    )
  }

  return (
    <Modal open={open} onClose={onClose} title="New character" size="md">
      <form onSubmit={handleSubmit}>
        <p className={styles.hint}>
          A character is the AI you&apos;ll talk to. Only the name is required —
          the personality tells the AI who they are.
        </p>

        <div className={styles.field}>
          <label htmlFor="character-name" className={styles.label}>
            Name <span className={styles.required}>*</span>
          </label>
          <input
            id="character-name"
            type="text"
            className={styles.input}
            value={name}
            onChange={(event) => setName(event.target.value)}
            maxLength={100}
            placeholder="e.g. Maya"
            autoFocus
            disabled={busy}
          />
        </div>

        <div className={styles.field}>
          <label htmlFor="character-system-prompt" className={styles.label}>
            Personality <span className={styles.optional}>(optional)</span>
          </label>
          <textarea
            id="character-system-prompt"
            className={styles.textarea}
            value={systemPrompt}
            onChange={(event) => setSystemPrompt(event.target.value)}
            maxLength={20000}
            rows={8}
            placeholder="e.g. You are Maya — warm and playful, with a dry sense of humor. Never break character."
            disabled={busy}
          />
          <p className={styles.fieldHint}>
            Written as instructions to the AI, in their voice. The richer this is,
            the more consistent they feel — leave blank to use a generic default.
          </p>
        </div>

        <div className={styles.field}>
          <label htmlFor="character-avatar-url" className={styles.label}>
            Avatar URL <span className={styles.optional}>(optional)</span>
          </label>
          <input
            id="character-avatar-url"
            type="text"
            className={styles.input}
            value={avatarUrl}
            onChange={(event) => setAvatarUrl(event.target.value)}
            maxLength={1000}
            placeholder="https://example.com/maya.png"
            disabled={busy}
          />
        </div>

        <div className={styles.field}>
          <label htmlFor="character-description" className={styles.label}>
            Description <span className={styles.optional}>(optional)</span>
          </label>
          <textarea
            id="character-description"
            className={styles.textarea}
            value={description}
            onChange={(event) => setDescription(event.target.value)}
            maxLength={500}
            rows={3}
            placeholder="A short summary of who they are…"
            disabled={busy}
          />
        </div>

        <div className={styles.footer}>
          <button type="button" className={styles.cancel} onClick={onClose} disabled={busy}>
            Cancel
          </button>
          <button type="submit" className={styles.submit} disabled={!canSubmit}>
            {busy ? (
              <>
                <Spinner size={15} label="Saving character" className={styles.submitSpinner} />
                Saving…
              </>
            ) : (
              'Create character'
            )}
          </button>
        </div>
      </form>
    </Modal>
  )
}
