import { useSession } from './session/authSession'
import { AuthPage } from './features/auth/AuthPage'
import { AppShell } from './AppShell'

/** Root: signed out → authentication; signed in → the chat workspace.
 *  A 401 from any API call clears the session, which returns the user here. */
export default function App() {
  const session = useSession()
  return session ? <AppShell /> : <AuthPage />
}
