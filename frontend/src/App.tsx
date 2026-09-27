import { useSession } from './session/authSession'
import { AuthPage } from './features/auth/AuthPage'
import { ResetPasswordPage } from './features/auth/ResetPasswordPage'
import { RESET_PASSWORD_PATH, usePathname } from './utils/path'
import { AppShell } from './AppShell'

/** Root: signed out → authentication; signed in → the chat workspace.
 *  A 401 from any API call clears the session, which returns the user here.
 *
 *  `/reset-password` is the one address that overrides that: it is for someone who
 *  cannot sign in, so it has to work before there is a session — including when the
 *  page is opened directly rather than navigated to. A signed-in visitor to it gets
 *  the app instead; they can change their password from settings. */
export default function App() {
  const session = useSession()
  const pathname = usePathname()
  if (!session && pathname === RESET_PASSWORD_PATH) return <ResetPasswordPage />
  return session ? <AppShell /> : <AuthPage />
}
