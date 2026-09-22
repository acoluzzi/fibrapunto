import type { TestMode } from "../lib/types";

interface Props {
  testMode: TestMode;
  started: boolean;
  onTestMode: (mode: TestMode) => void;
  onStart: () => void;
  onReset: () => void;
  onProvoke: (mode: TestMode) => void;
  theme: "light" | "dark";
  onTheme: () => void;
}

/** Everything the demo needs, so nobody touches a terminal on stage.
 *
 *  §7: "failures are provoked on purpose and repeated on command, during
 *  evaluation and during the live demo." The provoke button is the live half —
 *  it flips the billing system into a failure mode mid-conversation, with a
 *  TTL so a forgotten mode expires on its own rather than ruining act two. */
export function ControlStrip({
  testMode, started, onTestMode, onStart, onReset, onProvoke, theme, onTheme,
}: Props) {
  return (
    <header className="strip">
      <span className="brand">Fibrapunto</span>

      <div className="field">
        <label htmlFor="mode">Fatturazione</label>
        <select
          id="mode"
          value={testMode}
          disabled={started}
          onChange={(event) => onTestMode(event.target.value as TestMode)}
          title="Modalità del sistema di fatturazione per questa sessione (§7)"
        >
          <option value="">normale</option>
          <option value="slow">lenta</option>
          <option value="timeout">non risponde</option>
          <option value="notfound">contratto assente</option>
          <option value="duplicate">contratti omonimi</option>
        </select>
      </div>

      <button onClick={onStart} disabled={started}>
        Nuova conversazione
      </button>
      <button onClick={onReset}>Ricomincia</button>

      <div className="field">
        <label>Provoca ora</label>
        <button onClick={() => onProvoke("timeout")} title="§7: guasto a metà conversazione">
          guasto
        </button>
        <button onClick={() => onProvoke("")} title="Ripristina">
          ripristina
        </button>
      </div>

      <span className="spacer" />

      <a href="/operatore">Vista operatore</a>
      <button onClick={onTheme} title="Tema chiaro/scuro">
        {theme === "dark" ? "☾" : "☀"}
      </button>
    </header>
  );
}
