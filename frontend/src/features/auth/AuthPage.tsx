import { useState } from 'react'
import type { FormEvent } from 'react'
import { useMutation } from '@tanstack/react-query'
import { login, register } from '../../api/auth'
import { setSession } from '../../session/authSession'
import { ApiError } from '../../api/client'
import { errorMessage } from '../../utils/errors'
import { ChatBubbleIcon, SparklesIcon } from '../../components/Icons'
import { Spinner } from '../../components/Spinner'
import styles from './AuthPage.module.css'

type Mode = 'login' | 'register'

const USERNAME_PATTERN = /^[A-Za-z0-9_.-]+$/

/** Public sign-in / account-creation screen. All other API endpoints require
 *  a Bearer token, so this gate is the entry point of the app. */
export function AuthPage() {
  const [mode, setMode] = useState<Mode>('login')
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [displayName, setDisplayName] = useState('')
  const [error, setError] = useState<string | null>(null)

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
    onSuccess: (loginResponse) => {
      setSession(loginResponse)
    },
    onError: (err) => {
      setError(errorMessage(err))
      if (err instanceof ApiError && err.status === 401) setPassword('')
    },
  })

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

    submit.mutate()
  }

  function switchMode(next: Mode) {
    setMode(next)
    setError(null)
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

            <button type="submit" className={styles.submit} disabled={busy}>
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
        </section>
      </div>
    </main>
  )
}
