import { ControlStrip } from "./components/ControlStrip";
import { useSession } from "./hooks/useSession";
import { useTheme } from "./hooks/useTheme";
import { ChatPanel } from "./panels/ChatPanel";
import { Analyst } from "./routes/Analyst";
import { Console } from "./routes/Console";
import { Operator } from "./routes/Operator";

/** Four views, one app shell, routed on the path.
 *
 *  `/` is the customer's: the conversation and nothing else — what the
 *  assistant looks like. The other three are gated (api/auth.py) and show
 *  what it *is*: the developer console (`/console`) the trace, the live
 *  switches and the session metrics; the operator view (`/operator`) the
 *  cases; the analyst view (`/analyst`) the aggregates, the campaigns and
 *  the offers budget. */
export function App() {
  const path = window.location.pathname;
  if (path.startsWith("/operator")) return <Operator />;
  if (path.startsWith("/console")) return <Console />;
  if (path.startsWith("/analyst")) return <Analyst />;
  return <Customer />;
}

/** The customer's page. Deliberately plain: messages, and the ability to
 *  move between this browser's conversations. */
function Customer() {
  const session = useSession("customer");
  const [theme, toggleTheme] = useTheme();

  return (
    <div className="app">
      <ControlStrip
        conversations={session.conversations}
        activeId={session.sessionId}
        onSelect={session.select}
        onNewConversation={session.start}
        theme={theme}
        onTheme={toggleTheme}
      />

      {session.error && (
        <div className="banner error">
          {session.error}
          {/* Same client_turn_id: if the first attempt did reach the backend,
              the stored reply comes back instead of a second model call. */}
          {session.retryable && (
            <button style={{ marginLeft: 10 }} onClick={() => void session.retry()}>
              Retry
            </button>
          )}
        </div>
      )}

      <div className="panels single">
        <ChatPanel
          messages={session.messages}
          busy={session.busy}
          activity={session.activity}
          disabled={session.sessionId === null}
          onSend={session.send}
        />
      </div>
    </div>
  );
}
