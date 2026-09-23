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
  /* Read the theme that is actually *rendered*, not a guessed default.
   *
   * Until the toggle is pressed there is no data-theme attribute, and the
   * stylesheet falls back to the OS setting via prefers-color-scheme. Assuming
   * "light" here meant that on a dark-mode machine the page was dark while this
   * state said light — so the first press computed "dark", which is what was
   * already on screen, and nothing appeared to happen until the second. */
  const [theme, setTheme] = useState<"light" | "dark">(() => {
    const chosen = document.documentElement.dataset.theme;
    if (chosen === "light" || chosen === "dark") return chosen;
    return window.matchMedia("(prefers-color-scheme: dark)").matches
      ? "dark"
      : "light";
  });

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
