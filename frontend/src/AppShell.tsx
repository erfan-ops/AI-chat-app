import { useCallback, useEffect, useState } from 'react'
import { ConversationSidebar } from './features/conversations/ConversationSidebar'
import { ChatView, NoConversationPlaceholder } from './features/chat/ChatView'
import { NewConversationModal } from './features/newConversation/NewConversationModal'
import { SettingsModal } from './features/settings/SettingsModal'
import styles from './AppShell.module.css'

const ACTIVE_CONVERSATION_KEY = 'ai-chat.activeConversation'
const SIDEBAR_HIDDEN_KEY = 'ai-chat.sidebarHidden'

function readInitialConversationId(): number | null {
  try {
    const raw = sessionStorage.getItem(ACTIVE_CONVERSATION_KEY)
    const id = raw === null ? NaN : Number(raw)
    return Number.isInteger(id) && id > 0 ? id : null
  } catch {
    return null
  }
}

/** Desktop preference: start with the conversation panel collapsed or not.
 *  Kept in localStorage (unlike the active conversation) so it survives a
 *  browser restart — it is a workspace preference, not session state. */
function readInitialSidebarHidden(): boolean {
  try {
    return localStorage.getItem(SIDEBAR_HIDDEN_KEY) === '1'
  } catch {
    return false
  }
}

/**
 * Main two-pane layout: conversation sidebar + active chat.
 * Holds the pure-UI state (which conversation is open, whether the sidebar
 * drawer is visible on narrow screens, whether the conversation panel is
 * collapsed on wide ones, whether a modal is open).
 * All server state lives in the React Query cache. The open conversation is
 * remembered in sessionStorage so a reload returns to the same chat.
 */
export function AppShell() {
  const [activeConversationId, setActiveConversationId] = useState<number | null>(
    readInitialConversationId,
  )
  // Narrow screens: the sidebar is a drawer that closes once a chat is picked.
  const [sidebarOpen, setSidebarOpen] = useState(true)
  // Wide screens: the user can collapse the panel to see only the chat. Separate
  // from `sidebarOpen` so picking a conversation does not hide it on desktop.
  const [sidebarHidden, setSidebarHidden] = useState(readInitialSidebarHidden)
  const [newChatOpen, setNewChatOpen] = useState(false)
  const [settingsOpen, setSettingsOpen] = useState(false)
  // Bumped on every open so the picker remounts with a fresh form.
  const [newChatSession, setNewChatSession] = useState(0)

  useEffect(() => {
    if (activeConversationId !== null) {
      try {
        sessionStorage.setItem(ACTIVE_CONVERSATION_KEY, String(activeConversationId))
      } catch {
        // Storage unavailable — the selection simply won't survive a reload.
      }
    }
    // On unmount (sign-out) drop the remembered selection so the next user
    // starts fresh. Reloads do not run this cleanup, so the chat is restored.
    return () => {
      try {
        sessionStorage.removeItem(ACTIVE_CONVERSATION_KEY)
      } catch {
        // ignore
      }
    }
  }, [activeConversationId])

  const selectConversation = useCallback((id: number) => {
    setActiveConversationId(id)
    setSidebarOpen(false) // narrow screens: reveal the chat
  }, [])

  const backToConversations = useCallback(() => {
    setActiveConversationId(null)
    setSidebarOpen(true)
  }, [])

  const openNewChat = useCallback(() => {
    setNewChatSession((session) => session + 1)
    setNewChatOpen(true)
  }, [])

  const toggleSidebar = useCallback(() => {
    setSidebarHidden((hidden) => {
      try {
        localStorage.setItem(SIDEBAR_HIDDEN_KEY, hidden ? '0' : '1')
      } catch {
        // Storage unavailable — the preference just won't survive a reload.
      }
      return !hidden
    })
  }, [])

  const handleCreated = useCallback((id: number) => {
    setActiveConversationId(id)
    setSidebarOpen(false)
  }, [])

  return (
    <div
      className={`${styles.shell} ${sidebarOpen ? styles.shellSidebarOpen : ''} ${
        sidebarHidden ? styles.shellSidebarHidden : ''
      }`}
    >
      <div className={styles.sidebarPane} id="conversations-panel">
        <ConversationSidebar
          activeConversationId={activeConversationId}
          onSelectConversation={selectConversation}
          onNewConversation={openNewChat}
          onOpenSettings={() => setSettingsOpen(true)}
        />
      </div>

      <main className={styles.chatPane}>
        {activeConversationId === null ? (
          <NoConversationPlaceholder
            sidebarHidden={sidebarHidden}
            onToggleSidebar={toggleSidebar}
          />
        ) : (
          <ChatView
            key={activeConversationId}
            conversationId={activeConversationId}
            onBack={backToConversations}
            sidebarHidden={sidebarHidden}
            onToggleSidebar={toggleSidebar}
          />
        )}
      </main>

      <NewConversationModal
        key={newChatSession}
        open={newChatOpen}
        onClose={() => setNewChatOpen(false)}
        onCreated={handleCreated}
      />

      <SettingsModal open={settingsOpen} onClose={() => setSettingsOpen(false)} />
    </div>
  )
}
