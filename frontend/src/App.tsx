import { useState } from "react";
import { ControlStrip } from "./components/ControlStrip";
import { Switchboard } from "./components/Switchboard";
import { useSession } from "./hooks/useSession";
import { ChatPanel } from "./panels/ChatPanel";
import { MetricsPanel } from "./panels/MetricsPanel";
import { SystemTracePanel } from "./panels/SystemTracePanel";
import { Operator } from "./routes/Operator";

export function App() {
  const session = useSession();
  /* This must agree with the stylesheet's default, or the first press appears
   * to do nothing: it would compute the theme that is already on screen.
   *
   * styles.css declares light on :root and applies dark only under
   * [data-theme="dark"] — the OS setting is deliberately not consulted, so a
   * demo looks the same on any laptop. Hence "light" when the attribute is
   * absent. Changing one of these two without the other reintroduces the
   * two-press bug. */
  const [theme, setTheme] = useState<"light" | "dark">(() =>
    document.documentElement.dataset.theme === "dark" ? "dark" : "light",
  );
  /* A switch that fails silently looks exactly like one that worked until the
   * agent answers normally, on stage. Say so in the strip instead. */
  const [switchError, setSwitchError] = useState<string | null>(null);

  if (window.location.pathname.startsWith("/operator")) {
    return <Operator />;
  }

  function toggleTheme() {
    const next = theme === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    setTheme(next);
  }

  return (
    <div className="app">
      <ControlStrip
        onNewConversation={session.start}
        theme={theme}
        onTheme={toggleTheme}
      />
      <Switchboard onError={setSwitchError} />

      {[session.error, switchError].filter(Boolean).map((message) => (
        <div
          key={message}
          style={{ padding: "8px 14px", color: "var(--critical)", fontSize: 12 }}
        >
          {message}
        </div>
      ))}

      <div className="panels">
        <ChatPanel
          messages={session.messages}
          busy={session.busy}
          disabled={session.sessionId === null}
          onSend={session.send}
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
