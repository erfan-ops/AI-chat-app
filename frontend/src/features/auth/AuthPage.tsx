import { useState } from 'react'
import type { FormEvent } from 'react'
import { useMutation } from '@tanstack/react-query'
import { login, loginWithOtp, register, switchOtpMethod } from '../../api/auth'
import { setSession } from '../../session/authSession'
import { ApiError } from '../../api/client'
import { errorMessage } from '../../utils/errors'
import { ChatBubbleIcon, SparklesIcon } from '../../components/Icons'
import { Spinner } from '../../components/Spinner'
import { otpMethodLabel, parseOtpMethod } from '../../utils/otpMethod'
import type { OtpMethod, OtpRequiredResponse } from '../../types/api'
import styles from './AuthPage.module.css'

type Mode = 'login' | 'register'

const USERNAME_PATTERN = /^[A-Za-z0-9_.-]+$/
const NON_DIGITS = /[^0-9]/g

/** Public sign-in / account-creation screen. All other API endpoints require
 *  a Bearer token, so this gate is the entry point of the app. */
export function AuthPage() {
  const [mode, setMode] = useState<Mode>('login')
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  /** Registration-only: the same password typed twice. */
  const [confirmPassword, setConfirmPassword] = useState('')
  const [displayName, setDisplayName] = useState('')
  const [error, setError] = useState<string | null>(null)
  /** Set when the password was accepted but a second factor is required. The tabs
   *  stay mounted underneath, so only the form body changes. */
  const [challenge, setChallenge] = useState<OtpRequiredResponse | null>(null)
  const [code, setCode] = useState('')

  const submit = useMutation({
    mutationFn: async () => {
      if (mode === 'register') {
        await register({
          username: username.trim(),
          password,
          display_name: displayName.trim() || null,
        })
      }
      // Registering only creates the account — signing in returns the token.
      return login({ username: username.trim(), password })
    },
    onSuccess: (response) => {
      // Narrow before storing: an OTP challenge has no token, and setSession would
      // otherwise persist an empty session and render a signed-in shell.
      if ('otp_required' in response) {
        setChallenge(response)
        setCode('')
        setPassword('')
        return
      }
      setSession(response)
    },
    onError: (err) => {
      setError(errorMessage(err))
      if (err instanceof ApiError && err.status === 401) setPassword('')
    },
  })

  const verify = useMutation({
    mutationFn: () =>
      loginWithOtp({ challenge_id: challenge?.challenge_id ?? '', code }),
    onSuccess: (loginResponse) => {
      setSession(loginResponse)
    },
    // A wrong or expired code is a normal, retryable outcome here — the user is
    // not signed in yet, and no session is cleared.
    onError: (err) => {
      setError(errorMessage(err))
      setCode('')
    },
  })

  const switchDelivery = useMutation({
    mutationFn: (method: OtpMethod) =>
      switchOtpMethod({ challenge_id: challenge?.challenge_id ?? '', method }),
    onSuccess: (response) => {
      // A whole new challenge: the previous code is dead, so let it go.
      setChallenge(response)
      setCode('')
    },
    onError: (err) => setError(errorMessage(err)),
  })

  function handleVerify(event: FormEvent) {
    event.preventDefault()
    setError(null)
    if (code.length !== 6) {
      setError('Enter the 6-digit code.')
      return
    }
    verify.mutate()
  }

  function cancelChallenge() {
    setChallenge(null)
    setCode('')
    setError(null)
  }

  function handleSubmit(event: FormEvent) {
    event.preventDefault()
    setError(null)

    if (username.trim().length < 3) {
      setError('Username must be at least 3 characters.')
      return
    }
    if (mode === 'register' && !USERNAME_PATTERN.test(username.trim())) {
      setError('Username may only contain letters, numbers, dots, dashes and underscores.')
      return
    }
    if (password.length < (mode === 'register' ? 8 : 1)) {
      setError(mode === 'register' ? 'Password must be at least 8 characters.' : 'Enter your password.')
      return
    }
    if (mode === 'register' && password !== confirmPassword) {
      setError('The passwords do not match.')
      return
    }

    submit.mutate()
  }

  function switchMode(next: Mode) {
    setMode(next)
    setError(null)
    // A confirmation belongs to the form it was typed in.
    setConfirmPassword('')
  }

  const busy = submit.isPending

  return (
    <main className={styles.page}>
      <div className={styles.card}>
        <section className={styles.brand} aria-label="About">
          <div className={styles.brandLogo}>
            <ChatBubbleIcon aria-hidden="true" />
          </div>
          <h1 className={styles.brandTitle}>AI Chat</h1>
          <p className={styles.brandTagline}>
            Conversations that feel personal. Pick a character, pick a model, and start
            chatting.
          </p>
          <p className={styles.brandFootnote}>
            <SparklesIcon aria-hidden="true" />
            Streaming replies, right from your browser
          </p>
        </section>

        <section className={styles.formSection}>
          <div className={styles.tabs} role="tablist" aria-label="Account access">
            <button
              type="button"
              role="tab"
              aria-selected={mode === 'login'}
              className={`${styles.tab} ${mode === 'login' ? styles.tabActive : ''}`}
              onClick={() => switchMode('login')}
            >
              Sign in
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={mode === 'register'}
              className={`${styles.tab} ${mode === 'register' ? styles.tabActive : ''}`}
              onClick={() => switchMode('register')}
            >
              Create account
            </button>
          </div>

          {challenge ? (
            <form className={styles.form} onSubmit={handleVerify} noValidate>
              <h2 className={styles.heading}>Two-step verification</h2>

              {error && (
                <p className={styles.errorBanner} role="alert">
                  {error}
                </p>
              )}

              <p className={styles.otpHint}>
                We sent a 6-digit code by {otpMethodLabel(challenge.delivery_method)}. It
                expires in {Math.round(challenge.code_expires_in_seconds / 60)} minutes.
              </p>

              <div className={styles.field}>
                <label htmlFor="auth-otp-code" className={styles.label}>
                  Verification code
                </label>
                <input
                  id="auth-otp-code"
                  className={`${styles.input} ${styles.otpInput}`}
                  type="text"
                  inputMode="numeric"
                  autoComplete="one-time-code"
                  maxLength={6}
                  value={code}
                  onChange={(event) => setCode(event.target.value.replace(NON_DIGITS, ''))}
                  disabled={verify.isPending || switchDelivery.isPending}
                  autoFocus
                  required
                />
              </div>

              <button
                type="submit"
                className={styles.submit}
                disabled={verify.isPending || switchDelivery.isPending}
              >
                {verify.isPending ? (
                  <>
                    <Spinner size={16} label="Verifying code" />
                    Verifying…
                  </>
                ) : (
                  'Verify and sign in'
                )}
              </button>

              {/* Offered only when the account has a usable second contact, so this
                  can never lead to a dead end. The saved default is unaffected. */}
              {challenge.alternative_method && (
                <button
                  type="button"
                  className={styles.otpSwitch}
                  onClick={() => {
                    setError(null)
                    switchDelivery.mutate(parseOtpMethod(challenge.alternative_method))
                  }}
                  disabled={verify.isPending || switchDelivery.isPending}
                >
                  {switchDelivery.isPending ? (
                    <>
                      <Spinner size={15} label="Sending code" />
                      Sending…
                    </>
                  ) : (
                    `Send the code by ${otpMethodLabel(challenge.alternative_method)} instead`
                  )}
                </button>
              )}

              <button type="button" className={styles.backToSignIn} onClick={cancelChallenge}>
                Back to sign in
              </button>
            </form>
          ) : (
          <form className={styles.form} onSubmit={handleSubmit} noValidate>
            <h2 className={styles.heading}>
              {mode === 'login' ? 'Welcome back' : 'Create your account'}
            </h2>

            {error && (
              <p className={styles.errorBanner} role="alert">
                {error}
              </p>
            )}

            <div className={styles.field}>
              <label htmlFor="auth-username" className={styles.label}>
                Username
              </label>
              <input
                id="auth-username"
                className={styles.input}
                type="text"
                autoComplete="username"
                autoCapitalize="none"
                spellCheck={false}
                maxLength={32}
                value={username}
                onChange={(event) => setUsername(event.target.value)}
                disabled={busy}
                required
              />
            </div>

            {mode === 'register' && (
              <div className={styles.field}>
                <label htmlFor="auth-display-name" className={styles.label}>
                  Display name <span className={styles.optional}>optional</span>
                </label>
                <input
                  id="auth-display-name"
                  className={styles.input}
                  type="text"
                  autoComplete="name"
                  maxLength={100}
                  value={displayName}
                  onChange={(event) => setDisplayName(event.target.value)}
                  disabled={busy}
                />
              </div>
            )}

            <div className={styles.field}>
              <label htmlFor="auth-password" className={styles.label}>
                Password
              </label>
              <input
                id="auth-password"
                className={styles.input}
                type="password"
                autoComplete={mode === 'login' ? 'current-password' : 'new-password'}
                maxLength={128}
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                disabled={busy}
                required
              />
            </div>

            {mode === 'register' && (
              <div className={styles.field}>
                <label htmlFor="auth-confirm-password" className={styles.label}>
                  Confirm password
                </label>
                <input
                  id="auth-confirm-password"
                  className={styles.input}
                  type="password"
                  autoComplete="new-password"
                  maxLength={128}
                  value={confirmPassword}
                  onChange={(event) => setConfirmPassword(event.target.value)}
                  aria-invalid={confirmPassword.length > 0 && confirmPassword !== password}
                  aria-describedby="auth-confirm-password-hint"
                  disabled={busy}
                  required
                />
                {confirmPassword.length > 0 && confirmPassword !== password && (
                  <p id="auth-confirm-password-hint" className={styles.fieldError} role="alert">
                    The passwords do not match.
                  </p>
                )}
              </div>
            )}

            <button
              type="submit"
              className={styles.submit}
              disabled={busy || (mode === 'register' && password !== confirmPassword)}
            >
              {busy ? (
                <>
                  <Spinner size={16} label="Please wait" className={styles.submitSpinner} />
                  {mode === 'login' ? 'Signing in…' : 'Creating account…'}
                </>
              ) : mode === 'login' ? (
                'Sign in'
              ) : (
                'Create account'
              )}
            </button>
          </form>
          )}
        </section>
      </div>
    </main>
  )
}
