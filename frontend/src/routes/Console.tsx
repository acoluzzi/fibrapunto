import { useState } from "react";
import { AppHeader } from "../components/AppHeader";
import { Gate } from "../components/Gate";
import { Switchboard } from "../components/Switchboard";
import { useSession } from "../hooks/useSession";
import { ChatPanel } from "../panels/ChatPanel";
import { MetricsPanel } from "../panels/MetricsPanel";
import { SystemTracePanel } from "../panels/SystemTracePanel";

/** The developer console: what the assistant *is*, next to what it looks like.
 *
 *  Everything that used to sit beside the customer's chat lives here — the
 *  live failure switches (§7), the system trace (§17.10) and the session
 *  metrics — for the same browser's conversations, read along and never
 *  written to. Opens in its own tab from the chat, so on stage the
 *  conversation and its trace can be side by side. Gated: the switches act
 *  on every conversation at once, and the trace is not for customers. */
export function Console() {
  return (
    <Gate
      role="developer"
      title="Developer console"
      blurb="The system trace, the live failure switches and the session metrics. The switches act on every conversation at once."
    >
      {({ logout }) => <ConsoleView onLogout={logout} />}
    </Gate>
  );
}

function ConsoleView({ onLogout }: { onLogout: () => void }) {
  const session = useSession("observer");
  /* A switch that fails silently looks exactly like one that worked until the
   * agent answers normally, on stage. Say so in the strip instead. */
  const [switchError, setSwitchError] = useState<string | null>(null);

  return (
    <div className="app">
      <AppHeader view="console" role="developer console" onLogout={onLogout}>
        {session.conversations.length > 0 ? (
          <div className="field">
            <label htmlFor="console-conversation">Conversation</label>
            <select
              id="console-conversation"
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
        ) : (
          <span className="muted" style={{ fontSize: 12 }}>
            No conversation in this browser yet — start one from the chat.
          </span>
        )}
      </AppHeader>

      <Switchboard onError={setSwitchError} />

      {session.error && <div className="banner error">{session.error}</div>}
      {switchError && <div className="banner error">{switchError}</div>}

      <div className="panels">
        <ChatPanel
          messages={session.messages}
          busy={session.busy}
          activity={session.activity}
          disabled
          readOnly
          embedded
          onSend={() => undefined}
          caseReference={session.replies.at(-1)?.handover_reference ?? null}
        />
        <SystemTracePanel events={session.events} />
        <MetricsPanel
          replies={session.replies}
          events={session.events}
          domains={session.domains}
          perimeter={session.perimeter}
        />
      </div>
    </div>
  );
}
