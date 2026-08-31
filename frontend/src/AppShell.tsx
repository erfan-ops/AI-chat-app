import { useCallback, useEffect, useState } from 'react'
import { ConversationSidebar } from './features/conversations/ConversationSidebar'
import { ChatView, NoConversationPlaceholder } from './features/chat/ChatView'
import { NewConversationModal } from './features/newConversation/NewConversationModal'
import styles from './AppShell.module.css'

const ACTIVE_CONVERSATION_KEY = 'ai-chat.activeConversation'

function readInitialConversationId(): number | null {
  try {
    const raw = sessionStorage.getItem(ACTIVE_CONVERSATION_KEY)
    const id = raw === null ? NaN : Number(raw)
    return Number.isInteger(id) && id > 0 ? id : null
  } catch {
    return null
  }
}

/**
 * Main two-pane layout: conversation sidebar + active chat.
 * Holds the pure-UI state (which conversation is open, whether the sidebar
 * drawer is visible on narrow screens, whether the new-chat modal is open).
 * All server state lives in the React Query cache. The open conversation is
 * remembered in sessionStorage so a reload returns to the same chat.
 */
export function AppShell() {
  const [activeConversationId, setActiveConversationId] = useState<number | null>(
    readInitialConversationId,
  )
  const [sidebarOpen, setSidebarOpen] = useState(true)
  const [newChatOpen, setNewChatOpen] = useState(false)
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

  const handleCreated = useCallback((id: number) => {
    setActiveConversationId(id)
    setSidebarOpen(false)
  }, [])

  return (
    <div className={`${styles.shell} ${sidebarOpen ? styles.shellSidebarOpen : ''}`}>
      <div className={styles.sidebarPane}>
        <ConversationSidebar
          activeConversationId={activeConversationId}
          onSelectConversation={selectConversation}
          onNewConversation={openNewChat}
        />
      </div>

      <main className={styles.chatPane}>
        {activeConversationId === null ? (
          <NoConversationPlaceholder />
        ) : (
          <ChatView
            key={activeConversationId}
            conversationId={activeConversationId}
            onBack={backToConversations}
          />
        )}
      </main>

      <NewConversationModal
        key={newChatSession}
        open={newChatOpen}
        onClose={() => setNewChatOpen(false)}
        onCreated={handleCreated}
      />
    </div>
  )
}
