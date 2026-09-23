import { useMemo, useState } from 'react'
import { CharacterFormModal } from '../characters/CharacterFormModal'
import { useModels } from '../models/useModels'
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
import {
  CheckIcon,
  ChevronLeftIcon,
  ChevronRightIcon,
  PlusIcon,
  SparklesIcon,
  UserIcon,
} from '../../components/Icons'
import type { AIModel, Character, Persona } from '../../types/api'
import styles from './NewConversationModal.module.css'

export interface NewConversationModalProps {
  open: boolean
  onClose: () => void
  /** Called with the id of the freshly created conversation. */
  onCreated: (conversationId: number) => void
}

const EMPTY_CHARACTERS: Character[] = []
const EMPTY_MODELS: AIModel[] = []
const EMPTY_PERSONAS: Persona[] = []

/** The wizard's steps, in order. */
const STEPS = [
  { key: 'character', label: 'Character' },
  { key: 'model', label: 'Model' },
  { key: 'persona', label: 'Persona' },
] as const

const LAST_STEP = STEPS.length - 1

/**
 * New-conversation wizard: character → model → persona, one step at a time.
 *
 * All three lists come from the API — nothing is hardcoded. The model is
 * preselected from the user's default (when active), otherwise the first active
 * model; a persona is optional and defaults to "No persona". Every step is
 * reachable once the character is chosen, and revisiting a step never disturbs
 * the other selections (they are independent — no selection constrains another).
 * Form state starts fresh each open: the parent remounts this component with a
 * new `key` whenever the modal opens.
 */
export function NewConversationModal({ open, onClose, onCreated }: NewConversationModalProps) {
  const charactersQuery = useCharacters()
  const modelsQuery = useModels()
  const personasQuery = usePersonas()
  const session = useSession()
  const create = useCreateConversation()

  const [characterId, setCharacterId] = useState<number | null>(null)
  const [modelId, setModelId] = useState<number | null>(null)
  // null means "no persona" — the default; a persona is always optional.
  const [personaId, setPersonaId] = useState<number | null>(null)
  const [title, setTitle] = useState('')
  const [personaFormOpen, setPersonaFormOpen] = useState(false)
  const [characterFormOpen, setCharacterFormOpen] = useState(false)
  const [stepIndex, setStepIndex] = useState(0)

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
  const selectedModel = models.find((model) => model.id === effectiveModelId)
  const selectedPersona = personas.find((persona) => persona.id === personaId)

  const busy = create.isPending
  const canCreate = characterId !== null && !busy

  // The character is the only required selection, so it is what gates the steps
  // after it. Everything before a step is already answered by the time it opens.
  function canOpenStep(index: number): boolean {
    return index === 0 || characterId !== null
  }

  /** What each step has chosen so far — shown in the stepper, null while unset. */
  const stepValues: (string | null)[] = [
    selectedCharacter?.name ?? null,
    selectedModel ? (selectedModel.display_name ?? selectedModel.model_name) : null,
    // "No persona" is a real answer, not a missing one.
    personaId === null ? 'No persona' : (selectedPersona?.name ?? 'No persona'),
  ]

  function goToStep(index: number) {
    if (index === stepIndex || !canOpenStep(index)) return
    setStepIndex(index)
  }

  const isLastStep = stepIndex === LAST_STEP
  // The model step can always continue: with no model the server uses its default.
  const canContinue = stepIndex === 0 ? characterId !== null : true

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
        <ol className={styles.stepper} aria-label="New chat steps">
          {STEPS.map((step, index) => {
            const current = index === stepIndex
            const done = canOpenStep(index) && stepValues[index] !== null
            return (
              <li key={step.key}>
                <button
                  type="button"
                  className={`${styles.step} ${current ? styles.stepCurrent : ''}`}
                  aria-current={current ? 'step' : undefined}
                  disabled={!canOpenStep(index)}
                  onClick={() => goToStep(index)}
                >
                  <span
                    className={`${styles.stepBadge} ${done ? styles.stepBadgeDone : ''}`}
                    aria-hidden="true"
                  >
                    {done ? <CheckIcon /> : index + 1}
                  </span>
                  <span className={styles.stepText}>
                    <span className={styles.stepLabel}>{step.label}</span>
                    {stepValues[index] && (
                      <span className={styles.stepValue}>{stepValues[index]}</span>
                    )}
                  </span>
                </button>
              </li>
            )
          })}
        </ol>

        <div className={styles.stepPanel}>
          {stepIndex === 0 && (
            <CharacterStep
              query={charactersQuery}
              characters={characters}
              selectedId={characterId}
              onSelect={setCharacterId}
              onCreateNew={() => setCharacterFormOpen(true)}
            />
          )}

          {stepIndex === 1 && (
            <ModelStep
              query={modelsQuery}
              models={models}
              selectedId={effectiveModelId}
              onSelect={setModelId}
            />
          )}

          {stepIndex === 2 && (
            <>
              <PersonaStep
                query={personasQuery}
                personas={personas}
                selectedId={personaId}
                onSelect={setPersonaId}
                onCreateNew={() => setPersonaFormOpen(true)}
              />

              <div className={styles.titleField}>
                <label htmlFor="nc-title" className={styles.titleLabel}>
                  Chat name <span className={styles.optional}>(optional)</span>
                </label>
                <input
                  id="nc-title"
                  type="text"
                  className={styles.titleInput}
                  placeholder={`Chat with ${selectedCharacter?.name ?? 'the AI'}`}
                  maxLength={255}
                  value={title}
                  onChange={(event) => setTitle(event.target.value)}
                  disabled={busy}
                />
              </div>
            </>
          )}
        </div>

        <footer className={styles.footer}>
          {stepIndex > 0 ? (
            <button
              type="button"
              className={styles.back}
              onClick={() => setStepIndex(stepIndex - 1)}
              disabled={busy}
            >
              <ChevronLeftIcon aria-hidden="true" />
              Back
            </button>
          ) : (
            <span />
          )}

          <div className={styles.footerActions}>
            <button type="button" className={styles.cancel} onClick={onClose} disabled={busy}>
              Cancel
            </button>
            {isLastStep ? (
              <button
                type="button"
                className={styles.create}
                onClick={handleCreate}
                disabled={!canCreate}
              >
                {busy ? (
                  <>
                    <Spinner
                      size={15}
                      label="Creating conversation"
                      className={styles.createSpinner}
                    />
                    Starting…
                  </>
                ) : (
                  <>
                    <SparklesIcon aria-hidden="true" />
                    Start chatting
                  </>
                )}
              </button>
            ) : (
              <button
                type="button"
                className={styles.create}
                onClick={() => goToStep(stepIndex + 1)}
                disabled={!canContinue || busy}
              >
                Next
                <ChevronRightIcon aria-hidden="true" />
              </button>
            )}
          </div>
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

      {characterFormOpen && (
        <CharacterFormModal
          open={characterFormOpen}
          onClose={() => setCharacterFormOpen(false)}
          onCreated={(character) => {
            // Selecting the new character immediately beats re-finding it in the list.
            setCharacterId(character.id)
            setCharacterFormOpen(false)
          }}
        />
      )}
    </>
  )
}

function CharacterStep({
  query,
  characters,
  selectedId,
  onSelect,
  onCreateNew,
}: {
  query: ReturnType<typeof useCharacters>
  characters: Character[]
  selectedId: number | null
  onSelect: (id: number) => void
  onCreateNew: () => void
}) {
  return (
    <section className={styles.section} aria-labelledby="nc-characters">
      <h3 id="nc-characters" className={styles.sectionTitle}>
        Who do you want to talk to?
      </h3>

      {query.isPending && (
        <div className={styles.loading}>
          <Spinner size={22} label="Loading characters" />
        </div>
      )}

      {query.isError && (
        <ErrorState
          error={query.error}
          onRetry={() => {
            void query.refetch()
          }}
        />
      )}

      {query.isSuccess && characters.length === 0 && (
        <EmptyState
          icon={<UserIcon aria-hidden="true" />}
          title="No characters available"
          hint="Create the first one below to get started."
        />
      )}

      {characters.length > 0 && (
        <div className={styles.characterGrid} role="radiogroup" aria-label="Character">
          {characters.map((character) => {
            const selected = character.id === selectedId
            return (
              <button
                key={character.id}
                type="button"
                role="radio"
                aria-checked={selected}
                className={`${styles.characterCard} ${selected ? styles.characterCardSelected : ''}`}
                onClick={() => onSelect(character.id)}
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

      {query.isSuccess && (
        <button type="button" className={styles.newCharacterChip} onClick={onCreateNew}>
          <PlusIcon aria-hidden="true" />
          New character
        </button>
      )}
    </section>
  )
}

function ModelStep({
  query,
  models,
  selectedId,
  onSelect,
}: {
  query: ReturnType<typeof useModels>
  models: AIModel[]
  selectedId: number | null
  onSelect: (id: number) => void
}) {
  return (
    <section className={styles.section} aria-labelledby="nc-model">
      <h3 id="nc-model" className={styles.sectionTitle}>
        Which model?
      </h3>

      {query.isPending && (
        <div className={styles.loading}>
          <Spinner size={22} label="Loading models" />
        </div>
      )}

      {query.isError && (
        <ErrorState
          error={query.error}
          onRetry={() => {
            void query.refetch()
          }}
        />
      )}

      {query.isSuccess && models.length === 0 && (
        <p className={styles.noModels}>No models available — the server will use its default.</p>
      )}

      {models.length > 0 && (
        <div className={styles.modelList} role="radiogroup" aria-label="AI model">
          {models.map((model) => (
            <ModelOption
              key={model.id}
              model={model}
              selected={model.id === selectedId}
              onSelect={() => onSelect(model.id)}
            />
          ))}
        </div>
      )}
    </section>
  )
}

function PersonaStep({
  query,
  personas,
  selectedId,
  onSelect,
  onCreateNew,
}: {
  query: ReturnType<typeof usePersonas>
  personas: Persona[]
  selectedId: number | null
  onSelect: (id: number | null) => void
  onCreateNew: () => void
}) {
  return (
    <section className={styles.section} aria-labelledby="nc-persona">
      <h3 id="nc-persona" className={styles.sectionTitle}>
        Who are you? <span className={styles.optional}>(optional)</span>
      </h3>

      {query.isPending && (
        <div className={styles.loading}>
          <Spinner size={22} label="Loading personas" />
        </div>
      )}

      {query.isError && (
        <p className={styles.personaError}>
          Couldn&apos;t load your personas.{' '}
          <button
            type="button"
            className={styles.personaRetry}
            onClick={() => {
              void query.refetch()
            }}
          >
            Retry
          </button>
        </p>
      )}

      {query.isSuccess && (
        <div className={styles.personaRow} role="radiogroup" aria-label="Persona">
          <button
            type="button"
            role="radio"
            aria-checked={selectedId === null}
            className={`${styles.personaChip} ${selectedId === null ? styles.personaChipSelected : ''}`}
            onClick={() => onSelect(null)}
          >
            <span className={styles.personaChipName}>No persona</span>
            {selectedId === null && (
              <span className={styles.personaChipCheck} aria-hidden="true">
                <CheckIcon />
              </span>
            )}
          </button>

          {personas.map((persona) => {
            const selected = persona.id === selectedId
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
                onClick={() => onSelect(persona.id)}
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

          <button type="button" className={styles.newPersonaChip} onClick={onCreateNew}>
            <PlusIcon aria-hidden="true" />
            New persona
          </button>
        </div>
      )}
    </section>
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
