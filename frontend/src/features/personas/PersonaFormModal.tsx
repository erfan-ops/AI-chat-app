import { useState } from 'react'
import type { FormEvent } from 'react'
import { useCreatePersona } from './usePersonas'
import { Modal } from '../../components/Modal'
import { Spinner } from '../../components/Spinner'
import { pushToast } from '../../components/toastStore'
import { errorMessage } from '../../utils/errors'
import type { Persona } from '../../types/api'
import styles from './PersonaFormModal.module.css'

export interface PersonaFormModalProps {
  open: boolean
  onClose: () => void
  /** Called with the freshly created persona so the caller can select it. */
  onCreated: (persona: Persona) => void
}

/** Limits mirroring the backend PersonaCreate schema: name ≤ 100 chars,
 *  gender ≤ 10, description ≤ 1500, age 0–99. Rendered only while open, so
 *  form state starts empty on every open. */
export function PersonaFormModal({ open, onClose, onCreated }: PersonaFormModalProps) {
  const create = useCreatePersona()
  const [name, setName] = useState('')
  const [gender, setGender] = useState('')
  const [age, setAge] = useState('')
  const [description, setDescription] = useState('')

  const trimmedName = name.trim()
  const busy = create.isPending
  const canSubmit = trimmedName !== '' && !busy

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    let parsedAge: number | null = null
    const ageValue = age.trim()
    if (ageValue !== '') {
      parsedAge = Number(ageValue)
      if (!Number.isInteger(parsedAge) || parsedAge < 0 || parsedAge > 99) {
        pushToast('error', 'Age must be a whole number between 0 and 99.')
        return
      }
    }
    create.mutate(
      {
        name: trimmedName,
        gender: gender.trim() || null,
        description: description.trim() || null,
        age: parsedAge,
      },
      {
        onSuccess: (persona) => {
          onCreated(persona)
          onClose()
        },
        onError: (error) => {
          pushToast('error', errorMessage(error))
        },
      },
    )
  }

  return (
    <Modal open={open} onClose={onClose} title="New persona" size="md">
      <form onSubmit={handleSubmit}>
        <p className={styles.hint}>
          A persona is how you present yourself — the AI will know this about
          you. Only the name is required.
        </p>

        <div className={styles.field}>
          <label htmlFor="persona-name" className={styles.label}>
            Name <span className={styles.required}>*</span>
          </label>
          <input
            id="persona-name"
            type="text"
            className={styles.input}
            value={name}
            onChange={(event) => setName(event.target.value)}
            maxLength={100}
            placeholder="e.g. Alex"
            autoFocus
            disabled={busy}
          />
        </div>

        <div className={styles.row}>
          <div className={styles.field}>
            <label htmlFor="persona-gender" className={styles.label}>
              Gender <span className={styles.optional}>(optional)</span>
            </label>
            <input
              id="persona-gender"
              type="text"
              className={styles.input}
              value={gender}
              onChange={(event) => setGender(event.target.value)}
              maxLength={10}
              placeholder="e.g. male"
              disabled={busy}
            />
          </div>
          <div className={styles.field}>
            <label htmlFor="persona-age" className={styles.label}>
              Age <span className={styles.optional}>(optional)</span>
            </label>
            <input
              id="persona-age"
              type="number"
              min={0}
              max={99}
              step={1}
              inputMode="numeric"
              className={styles.input}
              value={age}
              onChange={(event) => setAge(event.target.value)}
              placeholder="e.g. 25"
              disabled={busy}
            />
          </div>
        </div>

        <div className={styles.field}>
          <label htmlFor="persona-description" className={styles.label}>
            About you <span className={styles.optional}>(optional)</span>
          </label>
          <textarea
            id="persona-description"
            className={styles.textarea}
            value={description}
            onChange={(event) => setDescription(event.target.value)}
            maxLength={1500}
            rows={4}
            placeholder="Anything you'd like the AI to know…"
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
                <Spinner size={15} label="Saving persona" className={styles.submitSpinner} />
                Saving…
              </>
            ) : (
              'Create persona'
            )}
          </button>
        </div>
      </form>
    </Modal>
  )
}
