import type { ConversationSummary } from "../hooks/useSession";
import { ThemeButton } from "./ThemeButton";

interface Props {
  conversations: ConversationSummary[];
  activeId: string | null;
  onSelect: (id: string) => void;
  onNewConversation: () => void;
  theme: "light" | "dark";
  onTheme: () => void;
}

/** The customer's top bar. A conversation is open as soon as the page loads.
 *  "New conversation" adds another without losing the ones before it; the
 *  selector reopens any of them, to read or to carry on.
 *
 *  The three gated views open from here. The console opens in its own tab so
 *  the conversation and its trace can sit side by side on stage; each view
 *  asks for its own password. */
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
            value={activeId ?? ""}
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

      <nav className="views" aria-label="Other views">
        <a href="/operator">Operator</a>
        <a href="/analyst">Analyst</a>
        <a className="button" href="/console" target="_blank" rel="noopener">
          Developer console ↗
        </a>
      </nav>
      <ThemeButton theme={theme} onToggle={onTheme} />
    </header>
  );
}
