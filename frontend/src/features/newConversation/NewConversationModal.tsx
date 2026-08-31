import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { listModels } from '../../api/models'
import { useCharacters } from '../characters/useCharacters'
import { useCreateConversation } from '../conversations/useConversations'
import { useSession } from '../../session/authSession'
import { Modal } from '../../components/Modal'
import { Avatar } from '../../components/Avatar'
import { EmptyState } from '../../components/EmptyState'
import { ErrorState } from '../../components/ErrorState'
import { Spinner } from '../../components/Spinner'
import { pushToast } from '../../components/toastStore'
import { errorMessage } from '../../utils/errors'
import { CheckIcon, SparklesIcon, UserIcon } from '../../components/Icons'
import type { AIModel, Character } from '../../types/api'
import styles from './NewConversationModal.module.css'

export interface NewConversationModalProps {
  open: boolean
  onClose: () => void
  /** Called with the id of the freshly created conversation. */
  onCreated: (conversationId: number) => void
}

const modelsQueryKey = ['models'] as const
const EMPTY_CHARACTERS: Character[] = []
const EMPTY_MODELS: AIModel[] = []

/**
 * Character + model selection for a new conversation. Both lists come from
 * the API — nothing is hardcoded. The model is preselected from the user's
 * default (when active), otherwise the first active model; the user can
 * override it. Form state starts fresh each open: the parent remounts this
 * component with a new `key` whenever the modal opens.
 */
export function NewConversationModal({ open, onClose, onCreated }: NewConversationModalProps) {
  const charactersQuery = useCharacters()
  const modelsQuery = useQuery({ queryKey: modelsQueryKey, queryFn: listModels })
  const session = useSession()
  const create = useCreateConversation()

  const [characterId, setCharacterId] = useState<number | null>(null)
  const [modelId, setModelId] = useState<number | null>(null)
  const [title, setTitle] = useState('')

  const characters = charactersQuery.data ?? EMPTY_CHARACTERS
  const models = modelsQuery.data ?? EMPTY_MODELS

  // Derived during render: the user's default model when it is still active,
  // otherwise the first active model. An explicit pick overrides it.
  const preselectedModelId = useMemo(() => {
    const defaultModelId = session?.user.default_model_id
    if (defaultModelId != null && models.some((model) => model.id === defaultModelId)) {
      return defaultModelId
    }
    return models[0]?.id ?? null
  }, [models, session?.user.default_model_id])
  const effectiveModelId = modelId ?? preselectedModelId

  const selectedCharacter = characters.find((character) => character.id === characterId)

  const busy = create.isPending
  const canCreate = characterId !== null && !busy

  function handleCreate() {
    if (characterId === null) return
    create.mutate(
      { character_id: characterId, model_id: effectiveModelId, title: title.trim() || null },
      {
        onSuccess: (conversation) => {
          onCreated(conversation.id)
          onClose()
        },
        onError: (error) => {
          pushToast('error', errorMessage(error))
        },
      },
    )
  }

  return (
    <Modal open={open} onClose={onClose} title="New chat" size="lg">
      <div className={styles.layout}>
        <section className={styles.section} aria-labelledby="nc-characters">
          <h3 id="nc-characters" className={styles.sectionTitle}>
            Who do you want to talk to?
          </h3>

          {charactersQuery.isPending && (
            <div className={styles.loading}>
              <Spinner size={22} label="Loading characters" />
            </div>
          )}

          {charactersQuery.isError && (
            <ErrorState
              error={charactersQuery.error}
              onRetry={() => {
                void charactersQuery.refetch()
              }}
            />
          )}

          {charactersQuery.isSuccess && characters.length === 0 && (
            <EmptyState
              icon={<UserIcon aria-hidden="true" />}
              title="No characters available"
              hint="The server doesn't have any active characters yet."
            />
          )}

          {characters.length > 0 && (
            <div className={styles.characterGrid} role="radiogroup" aria-label="Character">
              {characters.map((character) => {
                const selected = character.id === characterId
                return (
                  <button
                    key={character.id}
                    type="button"
                    role="radio"
                    aria-checked={selected}
                    className={`${styles.characterCard} ${selected ? styles.characterCardSelected : ''}`}
                    onClick={() => setCharacterId(character.id)}
                  >
                    <Avatar name={character.name} src={character.avatar_url} size={52} />
                    <span className={styles.characterName}>{character.name}</span>
                    {selected && (
                      <span className={styles.characterCheck} aria-hidden="true">
                        <CheckIcon />
                      </span>
                    )}
                  </button>
                )
              })}
            </div>
          )}
        </section>

        <section className={styles.section} aria-labelledby="nc-model">
          <h3 id="nc-model" className={styles.sectionTitle}>
            Which model?
          </h3>

          {modelsQuery.isPending && (
            <div className={styles.loading}>
              <Spinner size={22} label="Loading models" />
            </div>
          )}

          {modelsQuery.isError && (
            <ErrorState
              error={modelsQuery.error}
              onRetry={() => {
                void modelsQuery.refetch()
              }}
            />
          )}

          {modelsQuery.isSuccess && models.length === 0 && (
            <p className={styles.noModels}>
              No models available — the server will use its default.
            </p>
          )}

          {models.length > 0 && (
            <div className={styles.modelList} role="radiogroup" aria-label="AI model">
              {models.map((model) => (
                <ModelOption
                  key={model.id}
                  model={model}
                  selected={model.id === effectiveModelId}
                  onSelect={() => setModelId(model.id)}
                />
              ))}
            </div>
          )}

          <div className={styles.titleField}>
            <label htmlFor="nc-title" className={styles.titleLabel}>
              Chat name <span className={styles.optional}>(optional)</span>
            </label>
            <input
              id="nc-title"
              type="text"
              className={styles.titleInput}
              placeholder={`Chat with ${selectedCharacter?.name ?? 'your companion'}`}
              maxLength={255}
              value={title}
              onChange={(event) => setTitle(event.target.value)}
              disabled={busy}
            />
          </div>
        </section>
      </div>

      <footer className={styles.footer}>
        <button type="button" className={styles.cancel} onClick={onClose} disabled={busy}>
          Cancel
        </button>
        <button
          type="button"
          className={styles.create}
          onClick={handleCreate}
          disabled={!canCreate}
        >
          {busy ? (
            <>
              <Spinner size={15} label="Creating conversation" className={styles.createSpinner} />
              Starting…
            </>
          ) : (
            <>
              <SparklesIcon aria-hidden="true" />
              Start chatting
            </>
          )}
        </button>
      </footer>
    </Modal>
  )
}

function ModelOption({
  model,
  selected,
  onSelect,
}: {
  model: AIModel
  selected: boolean
  onSelect: () => void
}) {
  const label = model.display_name ?? model.model_name
  const details = [model.provider, model.context_window != null ? `${model.context_window.toLocaleString()} tokens context` : null]
    .filter(Boolean)
    .join(' · ')

  return (
    <button
      type="button"
      role="radio"
      aria-checked={selected}
      className={`${styles.modelOption} ${selected ? styles.modelOptionSelected : ''}`}
      onClick={onSelect}
    >
      <span className={styles.modelInfo}>
        <span className={styles.modelName}>{label}</span>
        {details && <span className={styles.modelDetails}>{details}</span>}
      </span>
      <span className={`${styles.modelRadio} ${selected ? styles.modelRadioOn : ''}`} aria-hidden="true">
        {selected && <CheckIcon />}
      </span>
    </button>
  )
}
