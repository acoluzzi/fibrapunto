import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../lib/api";
import type { AgentReply, Domain, TraceEvent } from "../lib/types";
import type { Message } from "../panels/ChatPanel";

/** One conversation, plus its live trace.
 *
 *  Replies and trace events arrive on the same SSE channel deliberately: it
 *  preserves the interleaving between "the agent said X" and "the capability
 *  returned outcome Y", and that interleaving is what makes the trace panel
 *  convincing rather than decorative. */
export function useSession() {
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [replies, setReplies] = useState<AgentReply[]>([]);
  const [events, setEvents] = useState<TraceEvent[]>([]);
  const [domains, setDomains] = useState<Domain[]>([]);
  const [perimeter, setPerimeter] = useState<Record<string, any> | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const streamRef = useRef<EventSource | null>(null);

  useEffect(() => {
    api.domains().then(setDomains).catch(() => undefined);
    api.perimeterProbe().then(setPerimeter).catch(() => undefined);
  }, []);

  const start = useCallback(async () => {
    setError(null);
    try {
      const { conversation_id } = await api.createSession();
      setSessionId(conversation_id);
      setMessages([]);
      setReplies([]);
      setEvents([]);

      streamRef.current?.close();
      const stream = new EventSource(`/api/sessions/${conversation_id}/events`);
      stream.onmessage = (message) => {
        const event = JSON.parse(message.data) as TraceEvent;
        setEvents((current) => [...current, event]);
      };
      // A dropped stream must not take the conversation with it: the reply
      // itself comes back on the POST, so the trace degrading is cosmetic.
      stream.onerror = () => stream.close();
      streamRef.current = stream;
    } catch (cause) {
      setError(String(cause));
    }
  }, []);

  /* A conversation is ready when the page is: the customer just types. The
   * ref stops React's development double-mount opening two sessions, and the
   * operator view — same app shell — is not a conversation. */
  const opened = useRef(false);
  useEffect(() => {
    if (opened.current || window.location.pathname.startsWith("/operator")) return;
    opened.current = true;
    void start();
  }, [start]);

  const send = useCallback(
    async (text: string) => {
      if (!sessionId) return;
      setMessages((current) => [...current, { role: "customer", text }]);
      setBusy(true);
      setError(null);
      try {
        const reply = await api.sendTurn(sessionId, text);
        setMessages((current) => [...current, { role: "agent", text: reply.text }]);
        setReplies((current) => [...current, reply]);
      } catch (cause) {
        setError(String(cause));
      } finally {
        setBusy(false);
      }
    },
    [sessionId],
  );

  useEffect(() => () => streamRef.current?.close(), []);

  return {
    sessionId, messages, replies, events, domains, perimeter,
    busy, error, start, send,
  };
}
