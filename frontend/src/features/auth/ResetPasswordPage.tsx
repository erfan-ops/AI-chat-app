import { useState } from 'react'
import type { FormEvent } from 'react'
import { useMutation } from '@tanstack/react-query'
import {
  completePasswordReset,
  requestPasswordReset,
  verifyPasswordResetCode,
} from '../../api/auth'
import { ApiError } from '../../api/client'
import { errorMessage } from '../../utils/errors'
import { otpMethodLabel, otpMethodName } from '../../utils/otpMethod'
import { navigate } from '../../utils/path'
import { ChatBubbleIcon, CheckCircleIcon, SparklesIcon } from '../../components/Icons'
import { Spinner } from '../../components/Spinner'
import type { OtpMethod } from '../../types/api'
import styles from './AuthPage.module.css'

/**
 * Forgot password: identify the account, receive a code, prove it, choose a new
 * password.
 *
 * This page holds the *flow*, never the security. Whether a code was right, whether
 * it is still current, and whether the password may be changed at all are decided by
 * the API: the last step is only accepted with the authorization the API issued after
 * it verified the code itself. So the state kept here — which step is on screen, the
 * challenge id, the authorization, what the user typed — is a script for the user to
 * follow, not a claim the server believes. Nothing in it can change a password on its
 * own, and reloading the page loses it, which is why the last step explains that the
 * authorization lasts ten minutes.
 *
 * The answers it gets are deliberately uninformative: the first call reports which
 * methods the account can be recovered by and says nothing about whether the account
 * exists. This page therefore also says nothing about that — it offers what it was
 * given, and if no code ever arrives the only honest thing left is to try another way
 * or start again.
 */

type Step = 'identify' | 'method' | 'code' | 'password' | 'done'

const USERNAME_OR_EMAIL_MAX = 200
const PASSWORD_MIN = 8
const NON_DIGITS = /[^0-9]/g

/** What each method means at the choice step, in the page's own words. */
const METHOD_DETAIL: Record<OtpMethod, string> = {
  SMS: 'A 6-digit code sent to the number on your account.',
  EMAIL: 'A 6-digit code sent to the address on your account.',
  TOTP: 'A code from the authenticator app you already use to sign in.',
}

export function ResetPasswordPage() {
  const [step, setStep] = useState<Step>('identify')
  const [identifier, setIdentifier] = useState('')
  /** What the server said the account can be recovered by. */
  const [methods, setMethods] = useState<OtpMethod[]>([])
  const [method, setMethod] = useState<OtpMethod | null>(null)
  /** Both are the server's; the page only carries them between steps. */
  const [challengeId, setChallengeId] = useState<string | null>(null)
  const [resetToken, setResetToken] = useState<string | null>(null)
  const [codeExpiresIn, setCodeExpiresIn] = useState<number | null>(null)
  const [authorizationExpiresIn, setAuthorizationExpiresIn] = useState<number | null>(null)
  const [code, setCode] = useState('')
  const [newPassword, setNewPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')
  const [error, setError] = useState<string | null>(null)

  /** Everything back to the first step. The server needs nothing undone: an
   *  abandoned challenge expires on its own, and an authorization nobody spends is
   *  spent by time. */
  function restart() {
    setStep('identify')
    setMethods([])
    setMethod(null)
    setChallengeId(null)
    setResetToken(null)
    setCodeExpiresIn(null)
    setAuthorizationExpiresIn(null)
    setCode('')
    setNewPassword('')
    setConfirmPassword('')
    setError(null)
  }

  /** Step one: which account, and how it can be reached. Sends nothing. */
  const lookup = useMutation({
    mutationFn: (value: string) => requestPasswordReset({ identifier: value }),
    onSuccess: (options) => {
      setMethods(options.methods)
      setStep('method')
    },
    onError: (err) => setError(errorMessage(err)),
  })

  /** Step two: send the code. Also the resend, when one has been asked for. */
  const send = useMutation({
    mutationFn: (chosen: OtpMethod) =>
      requestPasswordReset({ identifier: identifier.trim(), method: chosen }),
    onSuccess: (options, chosen) => {
      setMethod(chosen)
      setChallengeId(options.challenge_id)
      setCodeExpiresIn(options.code_expires_in_seconds)
      setCode('')
      setError(null)
      setStep('code')
    },
    onError: (err) => setError(errorMessage(err)),
  })

  /** Step three: prove the code. Returns the authorization, changes nothing. */
  const verify = useMutation({
    mutationFn: () =>
      verifyPasswordResetCode({ challenge_id: challengeId ?? '', code }),
    onSuccess: (authorization) => {
      setResetToken(authorization.reset_token)
      setAuthorizationExpiresIn(authorization.expires_in_seconds)
      setStep('password')
    },
    onError: (err) => {
      setError(errorMessage(err))
      setCode('')
    },
  })

  /** Step four: the new password, against the authorization. */
  const complete = useMutation({
    mutationFn: () =>
      completePasswordReset({ reset_token: resetToken ?? '', new_password: newPassword }),
    onSuccess: () => {
      setStep('done')
      setNewPassword('')
      setConfirmPassword('')
    },
    onError: (err) => {
      // An expired or already-used authorization cannot be retried: the only way on
      // is a fresh code. Say so, rather than leaving the user tapping the button.
      setError(errorMessage(err))
      if (err instanceof ApiError && err.status === 400) {
        setResetToken(null)
        setStep('code')
      }
    },
  })

  function handleIdentify(event: FormEvent) {
    event.preventDefault()
    setError(null)
    if (identifier.trim().length < 1) {
      setError('Enter your username or the email address on your account.')
      return
    }
    lookup.mutate(identifier.trim())
  }

  function handleCode(event: FormEvent) {
    event.preventDefault()
    setError(null)
    if (code.length !== 6) {
      setError('Enter the 6-digit code.')
      return
    }
    if (!challengeId) {
      // No verification was ever started, so there is nothing to check against. This
      // refuses — it grants nothing — and it is what the API would have said, worded
      // the same way, so a person who never receives a code is told the same thing.
      setError('That verification code is incorrect or has expired.')
      setCode('')
      return
    }
    verify.mutate()
  }

  function handlePassword(event: FormEvent) {
    event.preventDefault()
    setError(null)
    if (newPassword.length < PASSWORD_MIN) {
      setError(`Password must be at least ${PASSWORD_MIN} characters.`)
      return
    }
    if (newPassword !== confirmPassword) {
      setError('The passwords do not match.')
      return
    }
    complete.mutate()
  }

  function backToSignIn() {
    navigate('/')
  }

  const busy = send.isPending || verify.isPending || complete.isPending

  return (
    <main className={styles.page}>
      <div className={styles.card}>
        <section className={styles.brand} aria-label="About">
          <div className={styles.brandLogo}>
            <ChatBubbleIcon aria-hidden="true" />
          </div>
          <h1 className={styles.brandTitle}>AI Chat</h1>
          <p className={styles.brandTagline}>
            A new password takes a minute. Pick up the conversation right where you
            left it.
          </p>
          <p className={styles.brandFootnote}>
            <SparklesIcon aria-hidden="true" />
            Your chats and characters are waiting
          </p>
        </section>

        <section className={styles.formSection}>
          {step === 'identify' && (
            <form className={styles.form} onSubmit={handleIdentify} noValidate>
              <p className={styles.progress}>Step 1 of 4</p>
              <h2 className={styles.heading}>Reset your password</h2>

              {error && (
                <p className={styles.errorBanner} role="alert">
                  {error}
                </p>
              )}

              <p className={styles.otpHint}>
                Enter your username or the email address on your account, and we will
                show you how to receive a verification code.
              </p>

              <div className={styles.field}>
                <label htmlFor="reset-identifier" className={styles.label}>
                  Username or email
                </label>
                <input
                  id="reset-identifier"
                  className={styles.input}
                  type="text"
                  autoComplete="username"
                  autoCapitalize="none"
                  spellCheck={false}
                  maxLength={USERNAME_OR_EMAIL_MAX}
                  value={identifier}
                  onChange={(event) => setIdentifier(event.target.value)}
                  disabled={lookup.isPending}
                  autoFocus
                  required
                />
              </div>

              <button type="submit" className={styles.submit} disabled={lookup.isPending}>
                {lookup.isPending ? (
                  <>
                    <Spinner size={16} label="Please wait" className={styles.submitSpinner} />
                    Checking…
                  </>
                ) : (
                  'Continue'
                )}
              </button>

              <button type="button" className={styles.backToSignIn} onClick={backToSignIn}>
                Back to sign in
              </button>
            </form>
          )}

          {step === 'method' && (
            <form
              className={styles.form}
              onSubmit={(event) => {
                event.preventDefault()
                if (method) send.mutate(method)
              }}
              noValidate
            >
              <p className={styles.progress}>Step 2 of 4</p>
              <h2 className={styles.heading}>How should we send the code?</h2>

              {error && (
                <p className={styles.errorBanner} role="alert">
                  {error}
                </p>
              )}

              {methods.map((option) => (
                <button
                  key={option}
                  type="button"
                  className={styles.methodChoice}
                  onClick={() => {
                    setError(null)
                    setMethod(option)
                    send.mutate(option)
                  }}
                  disabled={busy}
                  aria-pressed={method === option}
                >
                  <span className={styles.methodName}>{otpMethodName(option)}</span>
                  <span className={styles.methodDetail}>{METHOD_DETAIL[option]}</span>
                </button>
              ))}

              {send.isPending && (
                <p className={styles.otpHint}>
                  <Spinner size={15} label="Sending" /> Sending the code…
                </p>
              )}

              <button
                type="button"
                className={styles.backToSignIn}
                onClick={restart}
                disabled={busy}
              >
                Use a different account
              </button>
            </form>
          )}

          {step === 'code' && (
            <form className={styles.form} onSubmit={handleCode} noValidate>
              <p className={styles.progress}>Step 3 of 4</p>
              <h2 className={styles.heading}>Enter the verification code</h2>

              {error && (
                <p className={styles.errorBanner} role="alert">
                  {error}
                </p>
              )}

              <p className={styles.otpHint}>
                {method === 'TOTP'
                  ? 'Enter the 6-digit code from your authenticator app.'
                  : `We sent a 6-digit code by ${otpMethodLabel(method)} to the contact on your account.`}{' '}
                {codeExpiresIn !== null && codeExpiresIn >= 60
                  ? `It expires in ${Math.round(codeExpiresIn / 60)} minutes.`
                  : ''}
              </p>

              <div className={styles.field}>
                <label htmlFor="reset-code" className={styles.label}>
                  {method === 'TOTP' ? 'Authenticator code' : 'Verification code'}
                </label>
                <input
                  id="reset-code"
                  className={`${styles.input} ${styles.otpInput}`}
                  type="text"
                  inputMode="numeric"
                  autoComplete="one-time-code"
                  maxLength={6}
                  value={code}
                  onChange={(event) => setCode(event.target.value.replace(NON_DIGITS, ''))}
                  disabled={busy}
                  autoFocus
                  required
                />
              </div>

              <button type="submit" className={styles.submit} disabled={busy}>
                {verify.isPending ? (
                  <>
                    <Spinner size={16} label="Verifying code" />
                    Verifying…
                  </>
                ) : (
                  'Verify code'
                )}
              </button>

              {/* A resend is a new send: the same limits apply, and a refusal says
                  how long to wait. */}
              {method && (
                <button
                  type="button"
                  className={styles.otpSwitch}
                  onClick={() => {
                    setError(null)
                    send.mutate(method)
                  }}
                  disabled={busy}
                >
                  {send.isPending ? (
                    <>
                      <Spinner size={15} label="Sending a new code" />
                      Sending…
                    </>
                  ) : (
                    'Send a new code'
                  )}
                </button>
              )}

              <button type="button" className={styles.backToSignIn} onClick={restart} disabled={busy}>
                Start over
              </button>
            </form>
          )}

          {step === 'password' && (
            <form className={styles.form} onSubmit={handlePassword} noValidate>
              <p className={styles.progress}>Step 4 of 4</p>
              <h2 className={styles.heading}>Choose a new password</h2>

              {error && (
                <p className={styles.errorBanner} role="alert">
                  {error}
                </p>
              )}

              <p className={styles.otpHint}>
                {authorizationExpiresIn !== null
                  ? `You have ${Math.round(authorizationExpiresIn / 60)} minutes to finish. `
                  : ''}
                Signing in elsewhere will need the new password from now on.
              </p>

              <div className={styles.field}>
                <label htmlFor="reset-new-password" className={styles.label}>
                  New password
                </label>
                <input
                  id="reset-new-password"
                  className={styles.input}
                  type="password"
                  autoComplete="new-password"
                  maxLength={128}
                  value={newPassword}
                  onChange={(event) => setNewPassword(event.target.value)}
                  disabled={complete.isPending}
                  autoFocus
                  required
                />
              </div>

              <div className={styles.field}>
                <label htmlFor="reset-confirm-password" className={styles.label}>
                  Confirm new password
                </label>
                <input
                  id="reset-confirm-password"
                  className={styles.input}
                  type="password"
                  autoComplete="new-password"
                  maxLength={128}
                  value={confirmPassword}
                  onChange={(event) => setConfirmPassword(event.target.value)}
                  aria-invalid={confirmPassword.length > 0 && confirmPassword !== newPassword}
                  aria-describedby="reset-confirm-hint"
                  disabled={complete.isPending}
                  required
                />
                {confirmPassword.length > 0 && confirmPassword !== newPassword && (
                  <p id="reset-confirm-hint" className={styles.fieldError} role="alert">
                    The passwords do not match.
                  </p>
                )}
              </div>

              <button type="submit" className={styles.submit} disabled={complete.isPending}>
                {complete.isPending ? (
                  <>
                    <Spinner size={16} label="Saving the new password" />
                    Saving…
                  </>
                ) : (
                  'Change password'
                )}
              </button>

              <button
                type="button"
                className={styles.backToSignIn}
                onClick={restart}
                disabled={complete.isPending}
              >
                Start over
              </button>
            </form>
          )}

          {step === 'done' && (
            <div className={styles.form}>
              <div className={styles.done}>
                <div className={styles.doneIcon}>
                  <CheckCircleIcon aria-hidden="true" />
                </div>
                <h2 className={styles.heading}>Password changed</h2>
                <p className={styles.doneText}>
                  Sign in with your new password. Any code that was in flight for the old
                  one no longer works.
                </p>
              </div>
              <button type="button" className={styles.submit} onClick={backToSignIn}>
                Back to sign in
              </button>
            </div>
          )}
        </section>
      </div>
    </main>
  )
}
