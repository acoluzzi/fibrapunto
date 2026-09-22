import { useEffect, useRef, useState } from "react";

export interface Message {
  role: "customer" | "agent";
  text: string;
}

interface Props {
  messages: Message[];
  busy: boolean;
  disabled: boolean;
  onSend: (text: string) => void;
}

/** The customer's view. Deliberately plain: this panel is what the agent
 *  looks like, and the panel next to it is what the agent *is*. */
export function ChatPanel({ messages, busy, disabled, onSend }: Props) {
  const [draft, setDraft] = useState("");
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages.length, busy]);

  function submit(event: React.FormEvent) {
    event.preventDefault();
    const text = draft.trim();
    if (!text || busy || disabled) return;
    onSend(text);
    setDraft("");
  }

  return (
    <section className="panel">
      <div className="panel-head">
        <h2>Conversazione</h2>
        <span className="hint">quello che vede il cliente</span>
      </div>

      <div className="panel-body">
        {messages.length === 0 && (
          <p className="empty">
            Scriva come farebbe un cliente. Per esempio: «Mi è arrivato un SMS
            che dice pagamento respinto».
          </p>
        )}
        {messages.map((message, index) => (
          <div key={index} className={`msg ${message.role}`}>
            <span className="who">
              {message.role === "customer" ? "Cliente" : "Assistente"}
            </span>
            <div className="bubble">{message.text}</div>
          </div>
        ))}
        {busy && <p className="typing">L'assistente sta verificando…</p>}
        <div ref={endRef} />
      </div>

      <form className="composer" onSubmit={submit}>
        <input
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          placeholder={disabled ? "Avvii una conversazione" : "Scriva qui…"}
          disabled={disabled || busy}
          aria-label="Messaggio"
        />
        <button className="primary" type="submit" disabled={disabled || busy || !draft.trim()}>
          Invia
        </button>
      </form>
    </section>
  );
}
