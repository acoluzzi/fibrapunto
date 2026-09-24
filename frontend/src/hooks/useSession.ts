import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../lib/api";
import type { AgentReply, Domain, TraceEvent } from "../lib/types";
import type { Message } from "../panels/ChatPanel";

/** Every conversation opened on this page, one of them on screen, plus its
 *  live trace.
 *
 *  Replies and trace events arrive on the same SSE channel deliberately: it
 *  preserves the interleaving between "the agent said X" and "the capability
 *  returned outcome Y", and that interleaving is what makes the trace panel
 *  convincing rather than decorative.
 *
 *  "New conversation" adds one rather than replacing the last: the backend
 *  keeps every session, so an earlier one can be reopened — read, or carried
 *  on. Its transcript is kept here, in memory (the backend stores it redacted,
 *  §9, and never hands raw customer text back); its trace is replayed by the
 *  backend when the stream reconnects. A page reload starts afresh. */

export interface ConversationSummary {
  id: string;
  label: string;
}

interface Conversation {
  id: string;
  startedAt: Date;
  messages: Message[];
  replies: AgentReply[];
}

export function useSession() {
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [events, setEvents] = useState<TraceEvent[]>([]);
  const [domains, setDomains] = useState<Domain[]>([]);
  const [perimeter, setPerimeter] = useState<Record<string, any> | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const streamRef = useRef<EventSource | null>(null);

  useEffect(() => {
    api.domains().then(setDomains).catch(() => undefined);
    api.perimeterProbe().then(setPerimeter).catch(() => undefined);
  }, []);

  /** Put one conversation on screen: its trace comes back by replay. */
  const show = useCallback((id: string) => {
    streamRef.current?.close();
    setActiveId(id);
    setEvents([]);
    setError(null);

    const stream = new EventSource(`/api/sessions/${id}/events`);
    stream.onmessage = (message) => {
      const event = JSON.parse(message.data) as TraceEvent;
      setEvents((current) => [...current, event]);
    };
    // A dropped stream must not take the conversation with it: the reply
    // itself comes back on the POST, so the trace degrading is cosmetic.
    stream.onerror = () => stream.close();
    streamRef.current = stream;
  }, []);

  const start = useCallback(async () => {
    setError(null);
    try {
      const { conversation_id } = await api.createSession();
      setConversations((current) => [
        ...current,
        { id: conversation_id, startedAt: new Date(), messages: [], replies: [] },
      ]);
      show(conversation_id);
    } catch (cause) {
      setError(String(cause));
    }
  }, [show]);

  /* A conversation is ready when the page is: the customer just types. The
   * ref stops React's development double-mount opening two sessions, and the
   * operator view — same app shell — is not a conversation. */
  const opened = useRef(false);
  useEffect(() => {
    if (opened.current || window.location.pathname.startsWith("/operator")) return;
    opened.current = true;
    void start();
  }, [start]);

  const update = useCallback((id: string, change: (c: Conversation) => Conversation) => {
    setConversations((current) => current.map((c) => (c.id === id ? change(c) : c)));
  }, []);

  const send = useCallback(
    async (text: string) => {
      // Captured now: if the user switches away while the agent is thinking,
      // the reply still lands in the conversation it belongs to.
      const id = activeId;
      if (!id) return;
      update(id, (c) => ({ ...c, messages: [...c.messages, { role: "customer", text }] }));
      setBusyId(id);
      setError(null);
      try {
        const reply = await api.sendTurn(id, text);
        update(id, (c) => ({
          ...c,
          messages: [...c.messages, { role: "agent", text: reply.text }],
          replies: [...c.replies, reply],
        }));
      } catch (cause) {
        setError(String(cause));
      } finally {
        setBusyId(null);
      }
    },
    [activeId, update],
  );

  useEffect(() => () => streamRef.current?.close(), []);

  const active = conversations.find((c) => c.id === activeId);
  const summaries: ConversationSummary[] = conversations.map((c, index) => {
    const first = c.messages.find((m) => m.role === "customer")?.text ?? "new";
    const time = c.startedAt.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    const words = first.length > 32 ? `${first.slice(0, 32)}…` : first;
    return { id: c.id, label: `#${index + 1} · ${time} · ${words}` };
  });

  return {
    sessionId: activeId,
    messages: active?.messages ?? [],
    replies: active?.replies ?? [],
    events,
    domains,
    perimeter,
    busy: busyId !== null && busyId === activeId,
    error,
    start,
    send,
    conversations: summaries,
    select: show,
  };
}
