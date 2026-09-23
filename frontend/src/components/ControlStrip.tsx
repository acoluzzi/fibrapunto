import type { TestMode } from '../lib/types'

interface Props {
  testMode: TestMode
  started: boolean
  onTestMode: (mode: TestMode) => void
  onStart: () => void
  onReset: () => void
  onProvoke: (mode: TestMode) => void
  theme: 'light' | 'dark'
  onTheme: () => void
}

/** Everything the demo needs, so nobody touches a terminal on stage.
 *
 *  §7: "failures are provoked on purpose and repeated on command, during
 *  evaluation and during the live demo." The provoke button is the live half —
 *  it flips the billing system into a failure mode mid-conversation, with a
 *  TTL so a forgotten mode expires on its own rather than ruining act two. */
export function ControlStrip({
  testMode,
  started,
  onTestMode,
  onStart,
  onReset,
  onProvoke,
  theme,
  onTheme,
}: Props) {
  return (
    <header className="strip">
      <span className="brand">Fibrapunto</span>

      <div className="field">
        <label htmlFor="mode">Billing</label>
        <select
          id="mode"
          value={testMode}
          disabled={started}
          onChange={(event) => onTestMode(event.target.value as TestMode)}
          title="Billing system mode for this session (§7)"
        >
          <option value="">normal</option>
          <option value="slow">slow</option>
          <option value="timeout">not responding</option>
          <option value="notfound">contract absent</option>
          <option value="duplicate">duplicate contract</option>
        </select>
      </div>

      <button onClick={onStart} disabled={started}>
        New Chat
      </button>
      <button onClick={onReset}>Restart</button>

      <div className="field">
        <label>Provoke now</label>
        <button
          onClick={() => onProvoke('timeout')}
          title="A failure mid-conversation"
        >
          Failure
        </button>
        <button onClick={() => onProvoke('')} title="Reset">
          Reset
        </button>
      </div>

      <span className="spacer" />

      <a href="/operator">Operator view</a>
      <button onClick={onTheme} title="Light / dark theme">
        {theme === 'dark' ? '☾' : '☀'}
      </button>
    </header>
  )
}
