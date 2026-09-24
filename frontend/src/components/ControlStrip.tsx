interface Props {
  onNewConversation: () => void
  theme: 'light' | 'dark'
  onTheme: () => void
}

/** The top bar. A conversation is open as soon as the page loads, so the only
 *  conversation control is starting a fresh one. The failure switches live in
 *  the Switchboard below it. */
export function ControlStrip({ onNewConversation, theme, onTheme }: Props) {
  return (
    <header className="strip">
      <span className="brand">Fibrapunto</span>

      <button onClick={onNewConversation}>New conversation</button>

      <span className="spacer" />

      <a href="/operator">Operator view</a>
      <button onClick={onTheme} title="Light / dark theme">
        {theme === 'dark' ? '☾' : '☀'}
      </button>
    </header>
  )
}
