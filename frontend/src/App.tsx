import { useState } from "react";
import { ControlStrip } from "./components/ControlStrip";
import { useSession } from "./hooks/useSession";
import { api } from "./lib/api";
import { ChatPanel } from "./panels/ChatPanel";
import { MetricsPanel } from "./panels/MetricsPanel";
import { SystemTracePanel } from "./panels/SystemTracePanel";
import { Operatore } from "./routes/Operatore";
import type { TestMode } from "./lib/types";

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

  if (window.location.pathname.startsWith("/operatore")) {
    return <Operatore />;
  }

  function toggleTheme() {
    const next = theme === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    setTheme(next);
  }

  return (
    <div className="app">
      <ControlStrip
        testMode={session.testMode}
        started={session.sessionId !== null}
        onTestMode={session.setTestMode}
        onStart={session.start}
        onReset={session.reset}
        onProvoke={(mode: TestMode) => api.billingMode(mode).catch(() => undefined)}
        theme={theme}
        onTheme={toggleTheme}
      />

      {session.error && (
        <div style={{ padding: "8px 14px", color: "var(--critical)", fontSize: 12 }}>
          {session.error}
        </div>
      )}

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
