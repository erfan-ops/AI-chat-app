import { useCallback, useEffect, useRef, useState } from 'react'
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
          theme?: 'outline' | 'filled_blue' | 'filled_black' | 'outline_dark'
          size?: 'large' | 'medium' | 'small'
          text?: 'signin_with' | 'signup_with' | 'continue_with' | 'signin'
          shape?: 'rectangular' | 'pill' | 'circle' | 'square'
          logo_alignment?: 'left' | 'center'
          width?: number
          /** Forces the label's language; otherwise the browser's is used. */
          locale?: string
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

// `hl` is how the library's own language is chosen: without it Google follows the
// browser's preferences, which can label the button in a language the rest of this
// interface is not written in.
const SCRIPT_SRC = 'https://accounts.google.com/gsi/client?hl=en'

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

  /** Draw Google's button to fit this layout and this theme.
   *
   * Everything here is a knob the library actually offers: the theme has a dark
   * variant that matches the app's panel colour, `pill` matches the app's other
   * rounded controls, and `locale: 'en'` keeps the label in the language the rest of
   * the UI is written in rather than the browser's. */
  const drawn = useRef('')

  const draw = useCallback(() => {
    if (!host.current || !window.google?.accounts?.id) return
    const theme = resolveTheme() === 'dark' ? 'outline_dark' : 'outline'
    // Measured on the row rather than on the host: the host holds the button, so its
    // width can depend on what was drawn there last, while the row is simply as wide as
    // the column of fields this button belongs under. The library caps this at 400px.
    const row = host.current.parentElement ?? host.current
    const width = Math.min(400, Math.max(200, Math.round(row.clientWidth)))
    // Drawing is not free (it builds an element) and the observer below can fire more
    // than once for the same size, so nothing is redrawn unless something changed.
    const shape = `${theme}:${width}`
    if (drawn.current === shape) return
    drawn.current = shape
    window.google.accounts.id.renderButton(host.current, {
      type: 'standard',
      // Light: white with a hairline border, like every other surface on the card.
      // Dark: Google's own dark fill, within a shade of --bg-panel.
      theme,
      size: 'large',
      text: 'continue_with',
      shape: 'pill',
      logo_alignment: 'left',
      // Belt and braces with the script's `hl` parameter above: the button asks for
      // English too, so neither the library nor the button defaults to the browser's
      // language.
      locale: 'en',
      width,
    })
  }, [])

  useEffect(() => {
    if (!CLIENT_ID) return
    let cancelled = false
    let cleanup = () => {}

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
        draw()

        // Google bakes the colours into the element it builds, so a theme or width
        // change means drawing it again. This is also what keeps the button exactly as
        // wide as the form controls beside it at any window size.
        const observer = new ResizeObserver(() => draw())
        if (host.current?.parentElement) observer.observe(host.current.parentElement)
        cleanup = () => observer.disconnect()
        setReady(true)
      })
      .catch((error: Error) => {
        if (!cancelled) setFailed(error.message)
      })

    return () => {
      cancelled = true
      cleanup()
    }
    // `theme` is a dependency on purpose: the button is drawn again when the theme
    // changes, since GIS bakes the colours into what it builds.
  }, [theme, draw])

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
    <div className={`${styles.googleRow} ${disabled ? styles.googleRowBusy : ''}`}>
      <div ref={host} className={styles.googleButton} aria-label="Continue with Google" />
      {!ready && <Spinner size={16} label="Loading Google sign-in" />}
    </div>
  )
}
