import { useEffect, useRef, useState } from 'react'

export interface Message {
  role: 'customer' | 'agent'
  text: string
}

interface Props {
  messages: Message[]
  busy: boolean
  /** What the agent is doing right now, in the customer's words (lib/activity.ts).
   *  null when it is not busy. */
  activity: string | null
  disabled: boolean
  onSend: (text: string) => void
  /** The developer console reads along and never writes: no composer. */
  readOnly?: boolean
}

/** The customer's view. Deliberately plain: this panel is what the agent
 *  looks like, and the panel next to it is what the agent *is*. */
export function ChatPanel({ messages, busy, activity, disabled, onSend, readOnly = false }: Props) {
  const [draft, setDraft] = useState('')
  const endRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages.length, busy])

  /* The phrase changes as the trace comes in; the line is one live region so
   * a screen reader announces the change without the customer being re-read
   * the whole conversation. */
  const status = busy ? (activity ?? 'Working on it…') : null

  function submit(event: React.FormEvent) {
    event.preventDefault()
    const text = draft.trim()
    if (!text || busy || disabled) return
    onSend(text)
    setDraft('')
  }

  return (
    <section className="panel">
      <div className="panel-head">
        <h2>Conversation</h2>
        <span className="hint">{readOnly ? 'what the customer sees — read only' : 'what the customer sees'}</span>
      </div>

      <div className="panel-body">
        {messages.length === 0 && (
          <p className="empty">
            Write as a customer would. For example: "I got a text saying my
            payment was refused".
          </p>
        )}
        {messages.map((message, index) => (
          <div key={index} className={`msg ${message.role}`}>
            <span className="who">
              {message.role === 'customer' ? 'Customer' : 'Assistant'}
            </span>
            <div className="bubble">{message.text}</div>
          </div>
        ))}
        {status && (
          <p className="typing" role="status" aria-live="polite">
            <span className="typing-dot" aria-hidden="true" />
            {status}
          </p>
        )}
        <div ref={endRef} />
      </div>

      {!readOnly && (
      <form className="composer" onSubmit={submit}>
        <input
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          placeholder={disabled ? 'Start a conversation' : 'Type here…'}
          disabled={disabled || busy}
          aria-label="Message"
        />
        <button
          className="primary"
          type="submit"
          disabled={disabled || busy || !draft.trim()}
        >
          Send
        </button>
      </form>
      )}
    </section>
  )
}
