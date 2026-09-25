import { useEffect, useRef, useState } from 'react'
import { resolveTheme, useTheme } from '../../theme/themeStore'
import { Spinner } from '../../components/Spinner'
import styles from './AuthPage.module.css'

/**
 * Sign in with Google, through Google Identity Services.
 *
 * GIS renders the button and runs the account chooser itself — this component only
 * hosts it, hands it the public client id, and forwards the credential it returns.
 * Nothing here reads or decodes that credential: it is opaque, and only the backend
 * can turn it into a session (it verifies the signature, issuer and audience before
 * believing anything in it).
 *
 * The GIS script is loaded on demand rather than from `index.html`, so a deployment
 * without a client id configured never fetches a script it cannot use.
 */

/** The slice of the GIS API this component uses. */
interface GoogleAccounts {
  accounts: {
    id: {
      initialize(options: {
        client_id: string
        callback: (response: { credential?: string }) => void
        auto_select?: boolean
        cancel_on_tap_outside?: boolean
        use_fedcm_for_prompt?: boolean
      }): void
      renderButton(
        parent: HTMLElement,
        options: {
          type?: 'standard' | 'icon'
          theme?: 'outline' | 'filled_blue' | 'filled_black'
          size?: 'large' | 'medium' | 'small'
          text?: 'signin_with' | 'signup_with' | 'continue_with' | 'signin'
          shape?: 'rectangular' | 'pill' | 'circle' | 'square'
          logo_alignment?: 'left' | 'center'
          width?: number
        },
      ): void
    }
  }
}

declare global {
  interface Window {
    google?: GoogleAccounts
  }
}

const SCRIPT_SRC = 'https://accounts.google.com/gsi/client'

/** The public OAuth client id. Absent (or empty) means the feature is off here. */
const CLIENT_ID = import.meta.env.VITE_GOOGLE_CLIENT_ID ?? ''

let scriptPromise: Promise<void> | null = null

/** Load GIS once per page, however many times this component mounts. */
function loadGoogleScript(): Promise<void> {
  if (window.google?.accounts?.id) return Promise.resolve()
  scriptPromise ??= new Promise<void>((resolve, reject) => {
    const script = document.createElement('script')
    script.src = SCRIPT_SRC
    script.async = true
    script.defer = true
    script.onload = () => resolve()
    script.onerror = () => {
      // Let a later attempt try again rather than caching the failure forever.
      scriptPromise = null
      reject(new Error('Could not reach Google. Check your connection and try again.'))
    }
    document.head.appendChild(script)
  })
  return scriptPromise
}

export interface GoogleSignInButtonProps {
  /** The verified credential, straight from Google. */
  onCredential: (credential: string) => void
  disabled?: boolean
}

export function GoogleSignInButton({ onCredential, disabled = false }: GoogleSignInButtonProps) {
  const host = useRef<HTMLDivElement>(null)
  const [failed, setFailed] = useState<string | null>(null)
  const [ready, setReady] = useState(false)
  const theme = useTheme()
  // The callback identity changes every render; GIS is initialized once, so it reads
  // the latest one through a ref instead of being re-initialized. The assignment runs
  // in an effect so it happens outside render, and is declared first so it is current
  // before the initialization below.
  const callback = useRef(onCredential)
  useEffect(() => {
    callback.current = onCredential
  })

  useEffect(() => {
    if (!CLIENT_ID) return
    let cancelled = false

    loadGoogleScript()
      .then(() => {
        if (cancelled || !host.current || !window.google?.accounts?.id) return
        const id = window.google.accounts.id
        id.initialize({
          client_id: CLIENT_ID,
          callback: (response) => {
            if (response.credential) callback.current(response.credential)
          },
          // No One Tap / auto-select: the button is the only way in, so the user is
          // never signed in from a prompt they did not ask for.
          auto_select: false,
          cancel_on_tap_outside: true,
        })
        host.current.replaceChildren()
        id.renderButton(host.current, {
          type: 'standard',
          // Outline reads correctly on the light card; on dark it would be a white
          // slab, so the filled variant follows the app's theme instead.
          theme: resolveTheme() === 'dark' ? 'filled_black' : 'outline',
          size: 'large',
          text: 'continue_with',
          shape: 'rectangular',
          logo_alignment: 'left',
          width: 320,
        })
        setReady(true)
      })
      .catch((error: Error) => {
        if (!cancelled) setFailed(error.message)
      })

    return () => {
      cancelled = true
    }
    // `theme` is a dependency on purpose: the button is re-rendered when the theme
    // changes, since GIS bakes the colours into the iframe it builds.
  }, [theme])

  // Nothing configured: no button, no error, and no request to Google.
  if (!CLIENT_ID) return null

  if (failed) {
    return (
      <button type="button" className={styles.googleFallback} onClick={() => setFailed(null)}>
        {failed}
      </button>
    )
  }

  return (
    <div className={styles.googleRow}>
      <div ref={host} className={styles.googleButton} aria-label="Continue with Google" />
      {!ready && <Spinner size={16} label="Loading Google sign-in" />}
      {disabled && <span className={styles.googleDisabled} aria-hidden="true" />}
    </div>
  )
}
