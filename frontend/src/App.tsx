import { AppHeader } from "./components/AppHeader";
import { Toasts } from "./components/Toast";
import { useSession } from "./hooks/useSession";
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
  const view = path.startsWith("/operator") ? (
    <Operator />
  ) : path.startsWith("/console") ? (
    <Console />
  ) : path.startsWith("/analyst") ? (
    <Analyst />
  ) : (
    <Customer />
  );
  return (
    <>
      {view}
      <Toasts />
    </>
  );
}

/** The customer's page. Deliberately plain: messages, and the ability to
 *  move between this browser's conversations. */
function Customer() {
  const session = useSession("customer");
  const last = session.replies.at(-1);

  return (
    <div className="app">
      <AppHeader view="customer">
        {/* Only worth showing once there is something to choose between. */}
        {session.conversations.length > 1 && (
          <div className="field">
            <label htmlFor="conversation">Conversation</label>
            <select
              id="conversation"
              value={session.sessionId ?? ""}
              onChange={(event) => session.select(event.target.value)}
            >
              {session.conversations.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.label}
                </option>
              ))}
            </select>
          </div>
        )}
        <button className="small" onClick={() => void session.start()}>
          New conversation
        </button>
      </AppHeader>

      <div className="chat-shell">
        <ChatPanel
          messages={session.messages}
          busy={session.busy}
          activity={session.activity}
          disabled={session.sessionId === null}
          onSend={session.send}
          caseReference={last?.handover_reference ?? null}
          error={session.error}
          onRetry={session.retryable ? () => void session.retry() : null}
        />
      </div>
    </div>
  );
}
