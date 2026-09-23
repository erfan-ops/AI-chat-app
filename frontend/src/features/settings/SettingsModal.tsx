import { useCallback, useEffect, useState } from 'react'
import type { FormEvent } from 'react'
import { useMutation } from '@tanstack/react-query'
import { disableOtp, startOtpEnable, updateMe, verifyOtpEnable } from '../../api/auth'
import { useSession, updateSessionUser } from '../../session/authSession'
import { THEMES, resolveTheme, setTheme, useTheme } from '../../theme/themeStore'
import type { Theme } from '../../theme/themeStore'
import { useModels } from '../models/useModels'
import { Modal } from '../../components/Modal'
import { Spinner } from '../../components/Spinner'
import { pushToast } from '../../components/toastStore'
import { errorMessage } from '../../utils/errors'
import type { OtpChallenge, User } from '../../types/api'
import styles from './SettingsModal.module.css'

/** Local part of an Iranian mobile: 10 digits, starting with 9. */
const LOCAL_MOBILE_PATTERN = /^9[0-9]{9}$/
/** Mirrors the backend's username rules (USERNAME_PATTERN in schemas/users.py). */
const USERNAME_PATTERN = /^[A-Za-z0-9_.-]{3,32}$/
/** Separators are tolerated while typing; the API gets the digits only. */
const NON_DIGITS = /[^0-9]/g

/** 9123456789 → 912 345 6789, as the field displays it. */
function groupDigits(digits: string): string {
  return [digits.slice(0, 3), digits.slice(3, 6), digits.slice(6, 10)].filter(Boolean).join(' ')
}

/** 9123456789 → +98 912 345 6789, for display once verified. */
function displayMobile(local: string): string {
  return `+98 ${groupDigits(local)}`
}

function formatCountdown(seconds: number): string {
  const minutes = Math.floor(seconds / 60)
  const rest = seconds % 60
  return `${minutes}:${String(rest).padStart(2, '0')}`
}

const THEME_LABELS: Record<Theme, string> = {
  light: 'Light',
  dark: 'Dark',
  system: 'System',
}

export interface SettingsModalProps {
  open: boolean
  onClose: () => void
}

/** Profile (display name, default model) and security (two-step verification)
 *  settings. Rendered only while open, so state starts fresh on every open. */
export function SettingsModal({ open, onClose }: SettingsModalProps) {
  const session = useSession()
  const models = useModels()
  const theme = useTheme()
  const user = session?.user

  const [username, setUsername] = useState(user?.username ?? '')
  const [displayName, setDisplayName] = useState(user?.display_name ?? '')
  const [modelId, setModelId] = useState<number | ''>(user?.default_model_id ?? '')
  const [mobileInput, setMobileInput] = useState('')
  const [challenge, setChallenge] = useState<OtpChallenge | null>(null)
  const [code, setCode] = useState('')
  const [secondsLeft, setSecondsLeft] = useState(0)

  const trimmedUsername = username.trim()
  const usernameValid = USERNAME_PATTERN.test(trimmedUsername)
  // Only sent when it actually changed — and never as an empty string, which
  // the API would reject as too short.
  const usernameChanged = usernameValid && trimmedUsername !== user?.username

  const saveProfile = useMutation({
    mutationFn: () =>
      updateMe({
        username: usernameChanged ? trimmedUsername : undefined,
        display_name: displayName.trim() || undefined,
        default_model_id: modelId === '' ? undefined : modelId,
      }),
    onSuccess: (updated: User) => {
      updateSessionUser(updated)
      pushToast('success', 'Settings saved')
    },
    onError: (error) => pushToast('error', errorMessage(error)),
  })

  const sendCode = useMutation({
    mutationFn: () => startOtpEnable({ mobile_number: mobileInput }),
    onSuccess: (issued) => {
      setChallenge(issued)
      setCode('')
      setSecondsLeft(issued.code_expires_in_seconds)
    },
    onError: (error) => pushToast('error', errorMessage(error)),
  })

  const confirmCode = useMutation({
    mutationFn: () => verifyOtpEnable({ challenge_id: challenge?.challenge_id ?? '', code }),
    onSuccess: (updated: User) => {
      updateSessionUser(updated)
      setChallenge(null)
      setCode('')
      setMobileInput('')
      pushToast('success', 'Two-step verification is on')
    },
    // A wrong code keeps the form usable so the user can just retype it.
    onError: (error) => pushToast('error', errorMessage(error)),
  })

  const turnOff = useMutation({
    mutationFn: disableOtp,
    onSuccess: (updated: User) => {
      updateSessionUser(updated)
      setChallenge(null)
      pushToast('success', 'Two-step verification is off')
    },
    onError: (error) => pushToast('error', errorMessage(error)),
  })

  // Countdown to expiry: the code is only valid for two minutes.
  useEffect(() => {
    if (!challenge) return
    const timer = window.setInterval(() => {
      setSecondsLeft((left) => {
        if (left <= 1) {
          setChallenge(null)
          pushToast('info', 'That code expired — request a new one.')
          return 0
        }
        return left - 1
      })
    }, 1000)
    return () => window.clearInterval(timer)
  }, [challenge])

  // Stable identity: Modal re-runs its effect (re-focusing the panel) whenever
  // onClose changes, which would break typing in the fields below.
  const handleClose = useCallback(() => onClose(), [onClose])

  function saveProfileSettings(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    saveProfile.mutate()
  }

  function submitMobile(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!LOCAL_MOBILE_PATTERN.test(mobileInput)) {
      pushToast('error', 'Enter the 10 digits after +98, starting with 9.')
      return
    }
    sendCode.mutate()
  }

  const anyPending = saveProfile.isPending || sendCode.isPending || confirmCode.isPending

  return (
    <Modal open={open} onClose={handleClose} title="Settings" size="md">
      <form onSubmit={saveProfileSettings} noValidate>
        <h3 className={styles.sectionTitle}>Profile</h3>

        <div className={styles.field}>
          <label htmlFor="settings-username" className={styles.label}>
            Username
          </label>
          <input
            id="settings-username"
            type="text"
            className={styles.input}
            value={username}
            onChange={(event) => setUsername(event.target.value)}
            maxLength={32}
            autoComplete="username"
            spellCheck={false}
            aria-describedby="settings-username-hint"
            disabled={saveProfile.isPending}
          />
          <p id="settings-username-hint" className={styles.fieldHint}>
            3–32 characters: letters, digits, underscores, dots or dashes. You sign
            in with this, so it has to be unique.
          </p>
        </div>

        <div className={styles.field}>
          <label htmlFor="settings-display-name" className={styles.label}>
            Display name
          </label>
          <input
            id="settings-display-name"
            type="text"
            className={styles.input}
            value={displayName}
            onChange={(event) => setDisplayName(event.target.value)}
            maxLength={100}
            placeholder={user?.username ?? 'Your name'}
            disabled={saveProfile.isPending}
          />
        </div>

        <div className={styles.field}>
          <label htmlFor="settings-default-model" className={styles.label}>
            Default model
          </label>
          <select
            id="settings-default-model"
            className={styles.input}
            value={modelId}
            onChange={(event) =>
              setModelId(event.target.value === '' ? '' : Number(event.target.value))
            }
            disabled={models.isPending || saveProfile.isPending}
          >
            <option value="">No default (use the first active model)</option>
            {models.data?.map((model) => (
              <option key={model.id} value={model.id}>
                {model.display_name ?? model.model_name}
              </option>
            ))}
          </select>
          <p className={styles.fieldHint}>
            Pre-selected when you start a new chat. Inactive models are never listed.
          </p>
        </div>

        <div className={styles.footer}>
          <button
            type="submit"
            className={styles.submit}
            disabled={saveProfile.isPending || !usernameValid}
          >
            {saveProfile.isPending ? (
              <>
                <Spinner size={15} label="Saving settings" className={styles.submitSpinner} />
                Saving…
              </>
            ) : (
              'Save profile'
            )}
          </button>
        </div>
      </form>

      <hr className={styles.divider} />

      <h3 className={styles.sectionTitle}>Appearance</h3>
      <div className={styles.field}>
        <div className={styles.themeRow} role="radiogroup" aria-label="Theme">
          {THEMES.map((option) => (
            <button
              key={option}
              type="button"
              role="radio"
              aria-checked={theme === option}
              className={`${styles.themeOption} ${
                theme === option ? styles.themeOptionSelected : ''
              }`}
              onClick={() => setTheme(option)}
            >
              {THEME_LABELS[option]}
            </button>
          ))}
        </div>
        <p className={styles.fieldHint}>
          {theme === 'system'
            ? `Following your device — currently ${resolveTheme()}.`
            : 'Applies to this browser only.'}
        </p>
      </div>

      <hr className={styles.divider} />

      <h3 className={styles.sectionTitle}>Two-step verification</h3>

      {user?.otp_enabled ? (
        <>
          <p className={styles.status}>
            <span className={styles.statusOn}>On</span>
            {user.mobile_number && <> · codes go to {displayMobile(user.mobile_number)}</>}
          </p>
          <p className={styles.fieldHint}>
            You will be asked for a code sent by SMS each time you sign in.
          </p>
          <div className={styles.footer}>
            <button
              type="button"
              className={styles.danger}
              onClick={() => turnOff.mutate()}
              disabled={turnOff.isPending}
            >
              {turnOff.isPending ? 'Turning off…' : 'Turn off'}
            </button>
          </div>
        </>
      ) : challenge ? (
        <form onSubmit={(event) => { event.preventDefault(); confirmCode.mutate() }} noValidate>
          <p className={styles.status}>
            Code sent to {challenge.mobile_hint} · expires in {formatCountdown(secondsLeft)}
          </p>
          <div className={styles.field}>
            <label htmlFor="settings-otp-code" className={styles.label}>
              Enter the 6-digit code
            </label>
            <input
              id="settings-otp-code"
              type="text"
              className={`${styles.input} ${styles.codeInput}`}
              value={code}
              onChange={(event) => setCode(event.target.value.replace(NON_DIGITS, ''))}
              inputMode="numeric"
              autoComplete="one-time-code"
              maxLength={6}
              autoFocus
              disabled={confirmCode.isPending}
            />
          </div>
          <div className={styles.footer}>
            <button
              type="button"
              className={styles.cancel}
              onClick={() => setChallenge(null)}
              disabled={confirmCode.isPending}
            >
              Cancel
            </button>
            <button
              type="submit"
              className={styles.submit}
              disabled={code.length !== 6 || confirmCode.isPending}
            >
              {confirmCode.isPending ? (
                <>
                  <Spinner size={15} label="Verifying code" className={styles.submitSpinner} />
                  Verifying…
                </>
              ) : (
                'Confirm'
              )}
            </button>
          </div>
        </form>
      ) : (
        <form onSubmit={submitMobile} noValidate>
          <p className={styles.fieldHint}>
            We will text you a code to confirm the number. Two-step verification only
            turns on once that code is verified.
          </p>
          <div className={styles.field}>
            <label htmlFor="settings-mobile" className={styles.label}>
              Mobile number
            </label>
            <div className={styles.mobileRow}>
              {/* Fixed prefix: the stored number is the 10-digit local part. */}
              <span className={styles.prefix} aria-hidden="true">
                +98
              </span>
              <input
                id="settings-mobile"
                type="tel"
                className={`${styles.input} ${styles.mobileInput}`}
                value={groupDigits(mobileInput)}
                onChange={(event) =>
                  setMobileInput(event.target.value.replace(NON_DIGITS, '').slice(0, 10))
                }
                inputMode="numeric"
                placeholder="912 345 6789"
                aria-describedby="settings-mobile-hint"
                disabled={sendCode.isPending}
              />
            </div>
            <p id="settings-mobile-hint" className={styles.fieldHint}>
              The number without the country code — 10 digits starting with 9.
            </p>
          </div>
          <div className={styles.footer}>
            <button
              type="submit"
              className={styles.submit}
              disabled={!LOCAL_MOBILE_PATTERN.test(mobileInput) || sendCode.isPending || anyPending}
            >
              {sendCode.isPending ? (
                <>
                  <Spinner size={15} label="Sending code" className={styles.submitSpinner} />
                  Sending…
                </>
              ) : (
                'Send code'
              )}
            </button>
          </div>
        </form>
      )}
    </Modal>
  )
}
