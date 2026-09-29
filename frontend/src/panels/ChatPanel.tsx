import { type KeyboardEvent, useCallback, useEffect, useRef, useState } from "react";
import { Mark } from "../components/Brand";

export interface Message {
  role: "customer" | "agent";
  text: string;
}

interface Props {
  messages: Message[];
  busy: boolean;
  /** What the agent is doing right now, in the customer's words (lib/activity.ts).
   *  null when it is not busy. */
  activity: string | null;
  disabled: boolean;
  onSend: (text: string) => void;
  /** The case this conversation opened, if any: shown as a banner so the
   *  reference and what happens next are never lost in the scrollback. */
  caseReference?: string | null;
  /** A send that failed, shown inside the thread where it happened. */
  error?: string | null;
  onRetry?: (() => void) | null;
  /** The developer console reads along and never writes: no composer. */
  readOnly?: boolean;
  /** Embedded in the console's panel grid rather than the customer's shell. */
  embedded?: boolean;
}

/** What the assistant can do, said up front, and three ways to start. The
 *  examples are scoped to the domains the assistant actually handles; a
 *  customer can tell in a few seconds whether this is for them. */
const SUGGESTIONS = [
  "I got a text saying my payment was refused",
  "Why is my bill higher this month?",
  "I want to cancel my contract",
];

/** Colleague decisions, status changes and a colleague's own message are
 *  posted into the conversation from templates (escalation/engine.py), never
 *  by the model. They open with the same words, which is how the chat tells a
 *  notice from a reply. */
const NOTICE = /^(An update on case no\. |A colleague on case no\. )/;

export function ChatPanel({
  messages,
  busy,
  activity,
  disabled,
  onSend,
  caseReference = null,
  error = null,
  onRetry = null,
  readOnly = false,
  embedded = false,
}: Props) {
  const [draft, setDraft] = useState("");
  const bodyRef = useRef<HTMLDivElement>(null);
  const endRef = useRef<HTMLDivElement>(null);
  const [pinned, setPinned] = useState(true);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  /* Follow the conversation only while the reader is at the bottom. Someone
   * who scrolled up to re-read is left where they are, with a button to
   * come back down. */
  const onScroll = useCallback(() => {
    const node = bodyRef.current;
    if (!node) return;
    setPinned(node.scrollHeight - node.scrollTop - node.clientHeight < 48);
  }, []);
  useEffect(() => {
    if (pinned) endRef.current?.scrollIntoView({ block: "end" });
  }, [messages.length, busy, error, pinned]);

  useEffect(() => {
    const node = textareaRef.current;
    if (!node) return;
    node.style.height = "auto";
    node.style.height = `${Math.min(node.scrollHeight, 160)}px`;
  }, [draft]);

  const status = busy ? (activity ?? "Working on it…") : null;
  const canSend = !disabled && !busy && !readOnly;

  function send(text: string) {
    const trimmed = text.trim();
    if (!trimmed || !canSend) return;
    onSend(trimmed);
    setDraft("");
    setPinned(true);
  }

  function onKey(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      send(draft);
    }
  }

  function download() {
    const lines = messages.map((m) => `${m.role === "customer" ? "You" : "Fibrapunto"}: ${m.text}`);
    const blob = new Blob([lines.join("\n\n")], { type: "text/plain;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `fibrapunto-conversation${caseReference ? `-${caseReference}` : ""}.txt`;
    a.click();
    URL.revokeObjectURL(url);
  }

  const head = embedded ? (
    <div className="panel-head">
      <h2>Conversation</h2>
      <span className="hint">what the customer sees — read only</span>
    </div>
  ) : (
    <div className="chat-head">
      <div className="who">
        <span className="avatar assistant"><Mark /></span>
        <div>
          <div className="name">Fibrapunto assistant</div>
          <div className="status">{busy ? "typing" : "online · billing, payments, cancellations"}</div>
        </div>
      </div>
      {messages.length > 0 && (
        <button className="ghost small" onClick={download} title="Save this conversation as a text file">
          Save transcript
        </button>
      )}
    </div>
  );

  return (
    <section className={embedded ? "panel" : "chat"}>
      {head}

      {caseReference && (
        <div className="case-banner">
          <span aria-hidden="true">◆</span>
          <span>
            Case <span className="ref">{caseReference}</span> is with a colleague.
          </span>
          <span className="muted">You will hear back here and by email.</span>
        </div>
      )}

      <div className="chat-body" ref={bodyRef} onScroll={onScroll}>
        {messages.length === 0 && !readOnly && (
          <div className="welcome">
            <p className="lead">Hello, I'm the Fibrapunto assistant.</p>
            <p>
              I can explain your bill, check a payment or direct debit, and register a
              cancellation. I'll hand you to a colleague whenever a person is needed.
            </p>
            <div className="suggestions">
              {SUGGESTIONS.map((s) => (
                <button key={s} disabled={!canSend} onClick={() => send(s)}>
                  {s}
                </button>
              ))}
            </div>
          </div>
        )}
        {messages.length === 0 && readOnly && <p className="empty">Nothing said yet.</p>}

        {messages.map((message, index) => {
          if (message.role === "agent" && NOTICE.test(message.text)) {
            return (
              <div key={index} className="msg system">
                <div className="notice">
                  <span className="icon" aria-hidden="true">◆</span>
                  <span>{message.text}</span>
                </div>
              </div>
            );
          }
          return (
            <div key={index} className={`msg ${message.role}`}>
              <span className={`avatar ${message.role === "customer" ? "customer" : "assistant"}`}>
                {message.role === "customer" ? "You" : <Mark />}
              </span>
              <div className="bubble">{message.text}</div>
            </div>
          );
        })}

        {status && (
          <p className="typing" role="status" aria-live="polite">
            <span className="typing-dots" aria-hidden="true"><i /><i /><i /></span>
            {status}
          </p>
        )}

        {error && (
          <div className="msg system error">
            <div className="notice">
              <span className="icon" aria-hidden="true">■</span>
              <span>{error}</span>
              {/* Same client_turn_id: if the first attempt did reach the
                  backend, the stored reply comes back instead of a second
                  model call. */}
              {onRetry && (
                <button className="small" onClick={onRetry}>
                  Retry
                </button>
              )}
            </div>
          </div>
        )}

        <div ref={endRef} />
        {!pinned && (
          <button
            className="jump-down"
            onClick={() => {
              setPinned(true);
              endRef.current?.scrollIntoView({ block: "end" });
            }}
          >
            ↓ Latest
          </button>
        )}
      </div>

      {!readOnly && (
        <>
          <form
            className="composer"
            onSubmit={(event) => {
              event.preventDefault();
              send(draft);
            }}
          >
            <textarea
              ref={textareaRef}
              rows={1}
              value={draft}
              onChange={(event) => setDraft(event.target.value)}
              onKeyDown={onKey}
              placeholder={disabled ? "Start a conversation" : "Write a message…"}
              disabled={disabled || busy}
              aria-label="Message"
            />
            <button className="primary" type="submit" disabled={!canSend || !draft.trim()}>
              Send
            </button>
          </form>
          <div className="composer-hint">Enter to send · Shift+Enter for a new line</div>
        </>
      )}
    </section>
  );
}
