import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { listModels } from '../../api/models'
import { useCharacters } from '../characters/useCharacters'
import { usePersonas } from '../personas/usePersonas'
import { PersonaFormModal } from '../personas/PersonaFormModal'
import { useCreateConversation } from '../conversations/useConversations'
import { useSession } from '../../session/authSession'
import { Modal } from '../../components/Modal'
import { Avatar } from '../../components/Avatar'
import { EmptyState } from '../../components/EmptyState'
import { ErrorState } from '../../components/ErrorState'
import { Spinner } from '../../components/Spinner'
import { pushToast } from '../../components/toastStore'
import { errorMessage } from '../../utils/errors'
import { CheckIcon, PlusIcon, SparklesIcon, UserIcon } from '../../components/Icons'
import type { AIModel, Character, Persona } from '../../types/api'
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
const EMPTY_PERSONAS: Persona[] = []

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
  const personasQuery = usePersonas()
  const session = useSession()
  const create = useCreateConversation()

  const [characterId, setCharacterId] = useState<number | null>(null)
  const [modelId, setModelId] = useState<number | null>(null)
  // null means "no persona" — the default; a persona is always optional.
  const [personaId, setPersonaId] = useState<number | null>(null)
  const [title, setTitle] = useState('')
  const [personaFormOpen, setPersonaFormOpen] = useState(false)

  const characters = charactersQuery.data ?? EMPTY_CHARACTERS
  const models = modelsQuery.data ?? EMPTY_MODELS
  const personas = personasQuery.data ?? EMPTY_PERSONAS

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
      {
        character_id: characterId,
        model_id: effectiveModelId,
        user_persona_id: personaId,
        title: title.trim() || null,
      },
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
    <>
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

        <section className={`${styles.section} ${styles.personaSection}`} aria-labelledby="nc-persona">
          <h3 id="nc-persona" className={styles.sectionTitle}>
            Who are you? <span className={styles.optional}>(optional)</span>
          </h3>

          {personasQuery.isPending && (
            <div className={styles.loading}>
              <Spinner size={22} label="Loading personas" />
            </div>
          )}

          {personasQuery.isError && (
            <p className={styles.personaError}>
              Couldn&apos;t load your personas.{' '}
              <button
                type="button"
                className={styles.personaRetry}
                onClick={() => {
                  void personasQuery.refetch()
                }}
              >
                Retry
              </button>
            </p>
          )}

          {personasQuery.isSuccess && (
            <div className={styles.personaRow} role="radiogroup" aria-label="Persona">
              <button
                type="button"
                role="radio"
                aria-checked={personaId === null}
                className={`${styles.personaChip} ${personaId === null ? styles.personaChipSelected : ''}`}
                onClick={() => setPersonaId(null)}
              >
                <span className={styles.personaChipName}>No persona</span>
                {personaId === null && (
                  <span className={styles.personaChipCheck} aria-hidden="true">
                    <CheckIcon />
                  </span>
                )}
              </button>

              {personas.map((persona) => {
                const selected = persona.id === personaId
                const details = [persona.gender, persona.age != null ? String(persona.age) : null]
                  .filter(Boolean)
                  .join(' · ')
                return (
                  <button
                    key={persona.id}
                    type="button"
                    role="radio"
                    aria-checked={selected}
                    className={`${styles.personaChip} ${selected ? styles.personaChipSelected : ''}`}
                    onClick={() => setPersonaId(persona.id)}
                  >
                    <span className={styles.personaChipInfo}>
                      <span className={styles.personaChipName}>{persona.name}</span>
                      {details && <span className={styles.personaChipDetails}>{details}</span>}
                    </span>
                    {selected && (
                      <span className={styles.personaChipCheck} aria-hidden="true">
                        <CheckIcon />
                      </span>
                    )}
                  </button>
                )
              })}

              <button
                type="button"
                className={styles.newPersonaChip}
                onClick={() => setPersonaFormOpen(true)}
              >
                <PlusIcon aria-hidden="true" />
                New persona
              </button>
            </div>
          )}
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

    {personaFormOpen && (
      <PersonaFormModal
        open={personaFormOpen}
        onClose={() => setPersonaFormOpen(false)}
        onCreated={(persona) => {
          // Selecting the new persona immediately beats re-finding it in the list.
          setPersonaId(persona.id)
          setPersonaFormOpen(false)
        }}
      />
    )}
  </>
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
