import { Modal } from '../../components/Modal'
import { Avatar } from '../../components/Avatar'
import type { Character } from '../../types/api'
import styles from './CharacterProfileModal.module.css'

export interface CharacterProfileModalProps {
  open: boolean
  onClose: () => void
  /** Display name — the conversation carries it even when the record isn't cached. */
  name: string
  /** Full record from the shared characters cache; undefined while loading or absent. */
  character?: Character
}

/** Read-only character card: large avatar, name (the dialog title) and the public
 *  description. `system_prompt` holds the AI's instructions and is never displayed. */
export function CharacterProfileModal({
  open,
  onClose,
  name,
  character,
}: CharacterProfileModalProps) {
  const description = character?.description?.trim() ?? ''

  return (
    <Modal open={open} onClose={onClose} title={name} size="sm">
      <div className={styles.profile}>
        <Avatar name={name} src={character?.avatar_url} size={148} />
        {description !== '' ? (
          <p className={styles.description}>{description}</p>
        ) : character !== undefined ? (
          <p className={styles.empty}>No description yet.</p>
        ) : null}
      </div>
    </Modal>
  )
}
