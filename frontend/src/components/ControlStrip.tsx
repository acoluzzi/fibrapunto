import type { ConversationSummary } from '../hooks/useSession'

interface Props {
  conversations: ConversationSummary[]
  activeId: string | null
  onSelect: (id: string) => void
  onNewConversation: () => void
  theme: 'light' | 'dark'
  onTheme: () => void
}

/** The top bar. A conversation is open as soon as the page loads. "New
 *  conversation" adds another without losing the ones before it; the selector
 *  reopens any of them, to read or to carry on. The failure switches live in
 *  the Switchboard below. */
export function ControlStrip({
  conversations,
  activeId,
  onSelect,
  onNewConversation,
  theme,
  onTheme,
}: Props) {
  return (
    <header className="strip">
      <span className="brand">Fibrapunto</span>

      {/* Only worth showing once there is something to choose between. */}
      {conversations.length > 1 && (
        <div className="field">
          <label htmlFor="conversation">Conversation</label>
          <select
            id="conversation"
            value={activeId ?? ''}
            onChange={(event) => onSelect(event.target.value)}
          >
            {conversations.map((c) => (
              <option key={c.id} value={c.id}>
                {c.label}
              </option>
            ))}
          </select>
        </div>
      )}

      <button onClick={onNewConversation}>New conversation</button>

      <span className="spacer" />

      <a href="/operator">Operator view</a>
      <button onClick={onTheme} title="Light / dark theme">
        {theme === 'dark' ? '☾' : '☀'}
      </button>
    </header>
  )
}
