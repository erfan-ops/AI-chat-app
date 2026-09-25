import { useCallback, useEffect, useRef, useState } from 'react'
import type { FormEvent } from 'react'
import { useMutation } from '@tanstack/react-query'
import QRCode from 'react-qr-code'
import {
  changePassword as changePasswordRequest,
  disableOtp,
  startOtpEnable,
  startTotpEnable,
  updateMe,
  verifyOtpEnable,
} from '../../api/auth'
import { useSession, updateSessionUser } from '../../session/authSession'
import { THEMES, resolveTheme, setTheme, useTheme } from '../../theme/themeStore'
import type { Theme } from '../../theme/themeStore'
import { useModels } from '../models/useModels'
import { AvatarPicker } from '../characters/AvatarPicker'
import { Modal } from '../../components/Modal'
import { Spinner } from '../../components/Spinner'
import { pushToast } from '../../components/toastStore'
import { errorMessage } from '../../utils/errors'
import {
  OTP_METHODS,
  otpMethodLabel,
  otpMethodName,
  parseOtpMethod,
} from '../../utils/otpMethod'
import type { OtpChallenge, OtpMethod, SentOtpMethod, TotpEnrollment, User } from '../../types/api'
import styles from './SettingsModal.module.css'

/** Local part of an Iranian mobile: 10 digits, starting with 9. */
const LOCAL_MOBILE_PATTERN = /^9[0-9]{9}$/
/** Mirrors the backend's username rules (USERNAME_PATTERN in schemas/users.py). */
const USERNAME_PATTERN = /^[A-Za-z0-9_.-]{3,32}$/
/** Enough of a check to catch typos before a code is sent; the API decides. */
const EMAIL_PATTERN = /^[^\s@]+@[^\s@.]+(\.[^\s@.]+)+$/
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

/** What the two-step section is waiting on: a code we sent, or one the user's own
 *  authenticator app will show for the enrolment that was just provisioned. */
type Pending =
  | { kind: 'code'; challenge: OtpChallenge }
  | { kind: 'enrollment'; enrollment: TotpEnrollment }

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
  const [emailInput, setEmailInput] = useState('')
  /** Which channel the setup form is verifying right now. */
  const [method, setMethod] = useState<OtpMethod>('SMS')
  /** True while verifying an *additional* contact on an account that already has 2FA. */
  /** Why the verification form is open: adding the missing contact, or replacing one. */
  const [contactForm, setContactForm] = useState<'add' | 'change' | null>(null)
  const [currentPassword, setCurrentPassword] = useState('')
  const [newPassword, setNewPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')
  const [pending, setPending] = useState<Pending | null>(null)
  const [code, setCode] = useState('')
  const [secondsLeft, setSecondsLeft] = useState(0)
  const [avatarUrl, setAvatarUrl] = useState<string | null>(user?.avatar_url ?? null)
  const [avatarUploading, setAvatarUploading] = useState(false)
  // While the crop or the upload is in progress, closing the dialog would throw the
  // selection away without saying so — Escape and overlay clicks do nothing instead.
  const guardCloseRef = useRef(false)

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

  /** Saving the picture is its own action — the upload already happened, so it is
   *  written the moment Cloudinary returns a URL (or cleared when it is removed). */
  const saveAvatar = useMutation({
    mutationFn: (next: string | null) => updateMe({ avatar_url: next }),
    onSuccess: (updated: User, next) => {
      updateSessionUser(updated)
      setAvatarUrl(updated.avatar_url)
      pushToast('success', next ? 'Profile picture updated' : 'Profile picture removed')
    },
    onError: (error) => pushToast('error', errorMessage(error)),
  })

  const changePassword = useMutation({
    mutationFn: () =>
      changePasswordRequest({ current_password: currentPassword, new_password: newPassword }),
    onSuccess: () => {
      setCurrentPassword('')
      setNewPassword('')
      setConfirmPassword('')
      pushToast('success', 'Password updated')
    },
    onError: (error) => pushToast('error', errorMessage(error)),
  })

  const sendCode = useMutation({
    mutationFn: () =>
      startOtpEnable(
        sentMethod === 'EMAIL'
          ? { method: sentMethod, email: emailInput.trim() }
          : { method: sentMethod, mobile_number: mobileInput },
      ),
    onSuccess: (issued) => {
      setPending({ kind: 'code', challenge: issued })
      setCode('')
      setSecondsLeft(issued.code_expires_in_seconds)
    },
    onError: (error) => pushToast('error', errorMessage(error)),
  })

  /** Provision an authenticator: a secret is generated and stored, and comes back
   *  once to be scanned. Nothing is enabled until a code from it is confirmed. */
  const startAuthenticator = useMutation({
    mutationFn: startTotpEnable,
    onSuccess: (enrollment) => {
      setPending({ kind: 'enrollment', enrollment })
      setCode('')
      setSecondsLeft(enrollment.code_expires_in_seconds)
    },
    onError: (error) => pushToast('error', errorMessage(error)),
  })

  const challengeId = pending
    ? pending.kind === 'code'
      ? pending.challenge.challenge_id
      : pending.enrollment.challenge_id
    : ''

  const confirmCode = useMutation({
    mutationFn: () => verifyOtpEnable({ challenge_id: challengeId, code }),
    onSuccess: (updated: User) => {
      updateSessionUser(updated)
      // The secret goes with the state that held it: it is never persisted anywhere.
      setPending(null)
      setCode('')
      setMobileInput('')
      setEmailInput('')
      setContactForm(null)
      pushToast('success', 'Two-step verification is on')
    },
    // A wrong code keeps the form usable so the user can just retype it.
    onError: (error) => pushToast('error', errorMessage(error)),
  })

  /** Changing the saved default never requires a code: both contacts are verified. */
  const setDefaultMethod = useMutation({
    mutationFn: (next: OtpMethod) => updateMe({ preferred_otp_method: next }),
    onSuccess: (updated: User) => {
      updateSessionUser(updated)
      pushToast('success', `Codes will be sent by ${otpMethodLabel(updated.preferred_otp_method)}`)
    },
    onError: (error) => pushToast('error', errorMessage(error)),
  })

  const turnOff = useMutation({
    mutationFn: disableOtp,
    onSuccess: (updated: User) => {
      updateSessionUser(updated)
      setPending(null)
      pushToast('success', 'Two-step verification is off')
    },
    onError: (error) => pushToast('error', errorMessage(error)),
  })

  // Countdown to expiry: a code we sent lasts two minutes, an enrolment ten.
  useEffect(() => {
    if (!pending) return
    const timer = window.setInterval(() => {
      setSecondsLeft((left) => {
        if (left <= 1) {
          setPending(null)
          pushToast('info', 'That expired — start again.')
          return 0
        }
        return left - 1
      })
    }, 1000)
    return () => window.clearInterval(timer)
  }, [pending])

  // Stable identity: Modal re-runs its effect (re-focusing the panel) whenever
  // onClose changes, which would break typing in the fields below.
  const handleClose = useCallback(() => {
    if (guardCloseRef.current) return
    onClose()
  }, [onClose])

  function saveProfileSettings(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    saveProfile.mutate()
  }

  /** Matched here rather than by the API: by the time it is sent there is only one
   *  new password, so a mismatch is a typing mistake to catch in the form. */
  const passwordsMatch = newPassword === confirmPassword
  const passwordFormValid =
    currentPassword.length > 0 && newPassword.length >= 8 && passwordsMatch

  function submitPassword(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!passwordsMatch) {
      pushToast('error', 'The new passwords do not match.')
      return
    }
    changePassword.mutate()
  }

  function submitDestination(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (method === 'TOTP') {
      // Nothing to type in first: the app has to be provisioned before it can show
      // a code, so this call returns the QR code instead of sending anything.
      startAuthenticator.mutate()
      return
    }
    if (method === 'EMAIL') {
      if (!EMAIL_PATTERN.test(emailInput.trim())) {
        pushToast('error', 'Enter a valid email address.')
        return
      }
    } else if (!LOCAL_MOBILE_PATTERN.test(mobileInput)) {
      pushToast('error', 'Enter the 10 digits after +98, starting with 9.')
      return
    }
    sendCode.mutate()
  }

  const anyPending =
    saveProfile.isPending ||
    avatarUploading ||
    sendCode.isPending ||
    startAuthenticator.isPending ||
    confirmCode.isPending

  // TOTP sends nothing, so a "send the code by" chooser may only offer the other two.
  const sentMethod: SentOtpMethod = method === 'TOTP' ? 'SMS' : method
  const hasMobile = Boolean(user?.mobile_number)
  const hasEmail = Boolean(user?.email)
  const hasAuthenticator = Boolean(user?.authenticator_enrolled)
  const verified: Record<OtpMethod, boolean> = {
    SMS: hasMobile,
    EMAIL: hasEmail,
    TOTP: hasAuthenticator,
  }
  // Everything not set up yet can still be added; the first of them is where the
  // Cancel button puts the form back to.
  const canAdd = OTP_METHODS.filter((option) => !verified[option])
  const missingMethod: OtpMethod | null = canAdd[0] ?? null
  const enrolled = OTP_METHODS.filter((option) => verified[option])
  // A default is a choice between methods, so there is nothing to choose with one.
  const canSwitchDefault = enrolled.length > 1
  const showSetupForm = !user?.otp_enabled || contactForm !== null
  const defaultMethod = parseOtpMethod(user?.preferred_otp_method)

  /** Open the form to add or replace a contact. */
  function addMethod(next: OtpMethod) {
    setMethod(next)
    setMobileInput('')
    setEmailInput('')
    setContactForm('change')
  }

  /** The current contact for the method being changed, for the form's own wording. */
  const replacing =
    contactForm === 'change'
      ? method === 'EMAIL'
        ? user?.email
        : user?.mobile_number
          ? displayMobile(user.mobile_number)
          : null
      : null

  /** What the setup form is asking for, per method. */
  const setupHint =
    method === 'TOTP'
      ? 'Scan the QR code with an authenticator app, then enter the code it shows. Two-step verification only turns on once that code is verified.'
      : contactForm === 'change'
        ? `We will send a code to the new ${method === 'EMAIL' ? 'address' : 'number'}.${
            replacing ? ` Your current one (${replacing}) keeps working until you confirm it.` : ''
          }`
        : contactForm === 'add'
          ? 'Verify the second contact and you will be able to switch between them at sign-in.'
          : `We will send you a code to confirm the ${method === 'EMAIL' ? 'address' : 'number'}. Two-step verification only turns on once that code is verified.`

  return (
    <Modal open={open} onClose={handleClose} title="Settings" size="md">
      <form onSubmit={saveProfileSettings} noValidate>
        <h3 className={styles.sectionTitle}>Profile</h3>

        <div className={styles.field}>
          <span className={styles.label}>Profile picture</span>
          {/* The same picker the character form uses: choose a file, crop it 1:1,
              upload straight to Cloudinary. Saving happens on upload, so there is
              nothing for "Save profile" to add — hence the separate mutation. */}
          <AvatarPicker
            value={avatarUrl}
            onChange={(next) => saveAvatar.mutate(next)}
            onUploadingChange={setAvatarUploading}
            onCroppingChange={(cropping) => {
              guardCloseRef.current = cropping
            }}
            disabled={saveAvatar.isPending}
            name={user?.display_name ?? user?.username ?? 'You'}
            kind="user"
          />
        </div>

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
            disabled={saveProfile.isPending || avatarUploading || !usernameValid}
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

      <form onSubmit={submitPassword} noValidate>
        <h3 className={styles.sectionTitle}>Password</h3>

        <div className={styles.field}>
          <label htmlFor="settings-current-password" className={styles.label}>
            Current password
          </label>
          <input
            id="settings-current-password"
            type="password"
            className={styles.input}
            value={currentPassword}
            onChange={(event) => setCurrentPassword(event.target.value)}
            autoComplete="current-password"
            disabled={changePassword.isPending}
          />
        </div>

        <div className={styles.field}>
          <label htmlFor="settings-new-password" className={styles.label}>
            New password
          </label>
          <input
            id="settings-new-password"
            type="password"
            className={styles.input}
            value={newPassword}
            onChange={(event) => setNewPassword(event.target.value)}
            autoComplete="new-password"
            aria-describedby="settings-new-password-hint"
            disabled={changePassword.isPending}
          />
          <p id="settings-new-password-hint" className={styles.fieldHint}>
            At least 8 characters.
          </p>
        </div>

        <div className={styles.field}>
          <label htmlFor="settings-confirm-password" className={styles.label}>
            Confirm new password
          </label>
          <input
            id="settings-confirm-password"
            type="password"
            className={styles.input}
            value={confirmPassword}
            onChange={(event) => setConfirmPassword(event.target.value)}
            autoComplete="new-password"
            aria-invalid={confirmPassword.length > 0 && !passwordsMatch}
            aria-describedby="settings-confirm-password-hint"
            disabled={changePassword.isPending}
          />
          {confirmPassword.length > 0 && !passwordsMatch && (
            <p id="settings-confirm-password-hint" className={styles.fieldError} role="alert">
              The new passwords do not match.
            </p>
          )}
        </div>

        <div className={styles.footer}>
          <button
            type="submit"
            className={styles.submit}
            disabled={!passwordFormValid || changePassword.isPending}
          >
            {changePassword.isPending ? (
              <>
                <Spinner size={15} label="Updating password" className={styles.submitSpinner} />
                Updating…
              </>
            ) : (
              'Update password'
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

      {pending ? (
        <form onSubmit={(event) => { event.preventDefault(); confirmCode.mutate() }} noValidate>
          {pending.kind === 'enrollment' ? (
            <>
              <p className={styles.status}>
                Scan this with your authenticator app · expires in{' '}
                {formatCountdown(secondsLeft)}
              </p>
              {/* The URI the server generated, rendered locally: the secret never
                  leaves this component, and is dropped when the step ends. */}
              <div className={styles.qrWrap}>
                <QRCode
                  value={pending.enrollment.otpauth_uri}
                  size={156}
                  bgColor="transparent"
                  fgColor="currentColor"
                  title="Authenticator setup QR code"
                />
              </div>
              <p className={styles.fieldHint}>
                Can&apos;t scan it? Enter this key in the app by hand:
              </p>
              <code className={styles.secretKey}>{pending.enrollment.secret}</code>
            </>
          ) : (
            <p className={styles.status}>
              Code sent by {otpMethodLabel(pending.challenge.method)} to{' '}
              {pending.challenge.destination_hint} · expires in {formatCountdown(secondsLeft)}
            </p>
          )}
          <div className={styles.field}>
            <label htmlFor="settings-otp-code" className={styles.label}>
              {pending.kind === 'enrollment'
                ? 'Enter the code your app shows'
                : 'Enter the 6-digit code'}
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
              onClick={() => setPending(null)}
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
      ) : showSetupForm ? (
        <form onSubmit={submitDestination} noValidate>
          <p className={styles.fieldHint}>{setupHint}</p>

          {/* Choosing a method makes sense when nothing is verified yet or when
              adding one — a change is already aimed at one contact. */}
          {(!user?.otp_enabled || contactForm === 'add') && (
            <div className={styles.field}>
              <span className={styles.label}>Second factor</span>
              <div className={styles.themeRow} role="radiogroup" aria-label="Delivery method">
                {OTP_METHODS.filter((option) => !verified[option])
                  .map((option) => (
                    <button
                      key={option}
                      type="button"
                      role="radio"
                      aria-checked={method === option}
                      className={`${styles.themeOption} ${
                        method === option ? styles.themeOptionSelected : ''
                      }`}
                      onClick={() => setMethod(option)}
                    >
                      {otpMethodName(option)}
                    </button>
                  ))}
              </div>
            </div>
          )}

          {/* An authenticator needs nothing typed in here: submitting shows the QR
              code to scan instead of sending anything. */}
          {method === 'TOTP' ? null : method === 'EMAIL' ? (
            <div className={styles.field}>
              <label htmlFor="settings-email" className={styles.label}>
                Email address
              </label>
              <input
                id="settings-email"
                type="email"
                className={styles.input}
                value={emailInput}
                onChange={(event) => setEmailInput(event.target.value)}
                placeholder="you@example.com"
                autoComplete="email"
                spellCheck={false}
                aria-describedby="settings-email-hint"
                disabled={sendCode.isPending}
              />
              <p id="settings-email-hint" className={styles.fieldHint}>
                Codes are sent to this address. It is only stored once the code is
                confirmed.
              </p>
            </div>
          ) : (
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
          )}

          <div className={styles.footer}>
            {contactForm !== null && (
              <button
                type="button"
                className={styles.cancel}
                onClick={() => {
                  setContactForm(null)
                  setMethod(missingMethod ?? 'SMS')
                }}
                disabled={sendCode.isPending}
              >
                Cancel
              </button>
            )}
            <button
              type="submit"
              className={styles.submit}
              disabled={
                (method === 'EMAIL'
                  ? !EMAIL_PATTERN.test(emailInput.trim())
                  : method === 'SMS'
                    ? !LOCAL_MOBILE_PATTERN.test(mobileInput)
                    : false) ||
                sendCode.isPending ||
                startAuthenticator.isPending ||
                anyPending
              }
            >
              {sendCode.isPending || startAuthenticator.isPending ? (
                <>
                  <Spinner
                    size={15}
                    label={method === 'TOTP' ? 'Preparing the QR code' : 'Sending code'}
                    className={styles.submitSpinner}
                  />
                  {method === 'TOTP' ? 'Preparing…' : 'Sending…'}
                </>
              ) : method === 'TOTP' ? (
                'Show QR code'
              ) : (
                'Send code'
              )}
            </button>
          </div>
        </form>
      ) : (
        user && (
          <>
            <p className={styles.status}>
              <span className={styles.statusOn}>On</span>
              <>
                {' '}
                ·{' '}
                {defaultMethod === 'TOTP'
                  ? 'codes come from your authenticator app'
                  : `codes go by ${otpMethodLabel(defaultMethod)}`}
              </>
            </p>
            <ul className={styles.contacts}>
              {user.mobile_number && (
                <li className={styles.contact}>
                  <span className={styles.contactValue}>
                    <span className={styles.contactLabel}>Mobile</span>
                    {displayMobile(user.mobile_number)}
                  </span>
                  <button
                    type="button"
                    className={styles.contactAction}
                    onClick={() => addMethod('SMS')}
                  >
                    Change
                  </button>
                </li>
              )}
              {user.email && (
                <li className={styles.contact}>
                  <span className={styles.contactValue}>
                    <span className={styles.contactLabel}>Email</span>
                    {user.email}
                  </span>
                  <button
                    type="button"
                    className={styles.contactAction}
                    onClick={() => addMethod('EMAIL')}
                  >
                    Change
                  </button>
                </li>
              )}
              {user.authenticator_enrolled && (
                <li className={styles.contact}>
                  <span className={styles.contactValue}>
                    <span className={styles.contactLabel}>Authenticator</span>
                    An app on your own device
                  </span>
                  <button
                    type="button"
                    className={styles.contactAction}
                    onClick={() => addMethod('TOTP')}
                  >
                    Replace
                  </button>
                </li>
              )}
            </ul>

            {canSwitchDefault ? (
              <div className={styles.field}>
                <span className={styles.label}>Send codes to</span>
                <div
                  className={styles.themeRow}
                  role="radiogroup"
                  aria-label="Default delivery method"
                >
                  {enrolled.map((option) => (
                    <button
                      key={option}
                      type="button"
                      role="radio"
                      aria-checked={defaultMethod === option}
                      className={`${styles.themeOption} ${
                        defaultMethod === option ? styles.themeOptionSelected : ''
                      }`}
                      onClick={() => setDefaultMethod.mutate(option)}
                      disabled={setDefaultMethod.isPending}
                    >
                      {otpMethodName(option)}
                    </button>
                  ))}
                </div>
                <p className={styles.fieldHint}>
                  Used by default at sign-in. Any other verified method is offered there
                  as a one-off choice, which never changes this.
                </p>
              </div>
            ) : (
              <p className={styles.fieldHint}>
                {defaultMethod === 'TOTP'
                  ? 'You will be asked for a code from your authenticator app each time you sign in.'
                  : `You will be asked for a code sent by ${otpMethodLabel(defaultMethod)} each time you sign in.`}
              </p>
            )}

            <div className={styles.footer}>
              {canAdd.map((option) => (
                <button
                  key={option}
                  type="button"
                  className={styles.cancel}
                  onClick={() => {
                    setMethod(option)
                    setContactForm('add')
                  }}
                  disabled={turnOff.isPending}
                >
                  {option === 'TOTP'
                    ? 'Add an authenticator'
                    : option === 'EMAIL'
                      ? 'Add an email'
                      : 'Add a mobile number'}
                </button>
              ))}
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
        )
      )}
    </Modal>
  )
}
