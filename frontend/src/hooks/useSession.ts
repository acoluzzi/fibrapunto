import { useCallback, useEffect, useRef, useState } from "react";
import { describeActivity } from "../lib/activity";
import { ApiError, api } from "../lib/api";
import type { AgentReply, Domain, SessionSummary, TraceEvent } from "../lib/types";
import type { Message } from "../panels/ChatPanel";
import {
  ACTIVE_STORAGE_KEY,
  mergeStored,
  readActive,
  readCached,
  writeActive,
  writeCached,
} from "../lib/transcripts";

/** This browser's conversations, one of them on screen, plus its live trace.
 *
 *  The backend owns them now (migration 006): each belongs to this browser's
 *  anonymous identity — an HttpOnly cookie the page never sees — and survives
 *  a reload or a backend restart. On load the page identifies itself, lists
 *  its conversations and reopens the latest, starting one only if there is
 *  none. Reopening fetches the stored transcript (redacted, §9); the trace
 *  comes back by replay when the event stream reconnects.
 *
 *  Each submitted message gets one client_turn_id. If sending fails, the same
 *  id is used to retry, so a request that did reach the backend is answered
 *  from its stored reply rather than run twice.
 *
 *  Replies and trace events arrive on the same SSE channel deliberately: it
 *  preserves the interleaving between "the agent said X" and "the capability
 *  returned outcome Y", and that interleaving is what makes the trace panel
 *  convincing rather than decorative.
 *
 *  The customer's page also keeps its own copy of each conversation in the
 *  browser (lib/transcripts.ts): shown at once on a switch, and used to put
 *  back what the customer typed where the stored transcript holds the
 *  security-question placeholder. The observer never reads that copy.
 *
 *  An observer follows the chat tab: `turn.received` on the stream marks the
 *  conversation busy, `agent.reply` reloads the transcript and the replies, and
 *  the conversation the chat tab has on screen is followed through
 *  localStorage, so "New conversation" pressed there switches the console. */

export interface ConversationSummary {
  id: string;
  label: string;
}

interface FailedTurn {
  conversationId: string;
  clientTurnId: string;
  text: string;
}

/** Who is looking. The customer's page opens their latest conversation or
 *  starts one; the developer console only watches — it lists the same
 *  browser's conversations (same cookie) and never starts one, and it keeps
 *  the list fresh so a conversation begun in the chat tab appears here. */
export type SessionMode = "customer" | "observer";

export function useSession(mode: SessionMode = "customer") {
  const [summaries, setSummaries] = useState<SessionSummary[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [replies, setReplies] = useState<AgentReply[]>([]);
  const [events, setEvents] = useState<TraceEvent[]>([]);
  const [domains, setDomains] = useState<Domain[]>([]);
  const [perimeter, setPerimeter] = useState<Record<string, any> | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [failed, setFailed] = useState<FailedTurn | null>(null);
  const streamRef = useRef<EventSource | null>(null);
  const activeRef = useRef<string | null>(null);
  /* How many replies the transcript on screen has, for the observer to tell a
   * replayed `agent.reply` (turn already loaded) from a live one. */
  const repliesCountRef = useRef(0);
  useEffect(() => {
    repliesCountRef.current = replies.length;
  }, [replies]);

  useEffect(() => {
    api.domains().then(setDomains).catch(() => undefined);
    api.perimeterProbe().then(setPerimeter).catch(() => undefined);
  }, []);

  const refreshList = useCallback(async () => {
    const { items } = await api.sessions();
    setSummaries(items);
    return items;
  }, []);

  /** Put one conversation on screen: stored transcript, then live trace. */
  const show = useCallback(
    async (id: string) => {
      if (activeRef.current === id && streamRef.current) return; // already on screen
      streamRef.current?.close();
      activeRef.current = id;
      setActiveId(id);
      setEvents([]);
      setError(null);
      setFailed(null);
      setBusyId(null);
      // The customer's own copy first, so the switch is instant; the backend's
      // copy replaces it below. The observer starts empty on purpose.
      const cached = mode === "customer" ? readCached(id) : null;
      setMessages(cached?.messages ?? []);
      setReplies(cached?.replies ?? []);
      if (mode === "customer") writeActive(id);
      try {
        const stored = await api.session(id);
        if (activeRef.current !== id) return; // switched again meanwhile
        const messages = mergeStored(stored.messages, cached?.messages ?? null);
        setMessages(messages);
        setReplies(stored.replies);
        if (mode === "customer") writeCached(id, messages, stored.replies);
      } catch (cause) {
        // Keep the cached copy on screen if there is one: better than nothing
        // while the backend is away, and the error says why it is stale.
        if (!cached) {
          setMessages([]);
          setReplies([]);
        }
        setError(String(cause));
        return;
      }
      const reload = () =>
        api
          .session(id)
          .then((stored) => {
            if (activeRef.current !== id) return;
            setMessages(stored.messages);
            setReplies(stored.replies);
          })
          .catch(() => undefined);
      const stream = new EventSource(`/api/sessions/${id}/events`);
      stream.onmessage = (message) => {
        const event = JSON.parse(message.data) as TraceEvent;
        setEvents((current) => [...current, event]);
        if (activeRef.current !== id) return;
        // A colleague's decision posted into this conversation: it is stored as
        // a turn, so reload the transcript rather than appending — replays of
        // the same event on reconnect then cannot show it twice.
        if (event.name === "customer.notified") {
          void reload();
          return;
        }
        // The observer reads along: the customer's tab sends the turn, this
        // tab only sees the stream. A turn beyond the replies already loaded
        // is live; anything else is the replay of what is already on screen.
        if (mode !== "observer") return;
        const live = event.turn !== null && event.turn > repliesCountRef.current;
        if (event.name === "turn.received" && live) setBusyId(id);
        if (event.name === "agent.reply" && live) {
          setBusyId((current) => (current === id ? null : current));
          void reload();
        }
        if (event.name === "session.ended") setBusyId((current) => (current === id ? null : current));
      };
    // A dropped stream must not take the conversation with it: the reply
    // itself comes back on the POST, so the trace degrading is cosmetic.
      stream.onerror = () => stream.close();
      streamRef.current = stream;
    },
    [mode],
  );

  const start = useCallback(async () => {
    setError(null);
    try {
      const { conversation_id } = await api.createSession();
      await refreshList();
      await show(conversation_id);
    } catch (cause) {
      setError(String(cause));
    }
  }, [refreshList, show]);

  /* On load: identify this browser, reopen its latest conversation, or start
   * one (an observer never starts one). The ref stops React's development
   * double-mount doing it twice. */
  const opened = useRef(false);
  useEffect(() => {
    if (opened.current) return;
    opened.current = true;
    (async () => {
      try {
        await api.customerAccess();
        const items = await refreshList();
        const followed = mode === "observer" ? readActive() : null;
        const first = items.find((s) => s.conversation_id === followed) ?? items[0];
        if (first) await show(first.conversation_id);
        else if (mode === "customer") await start();
      } catch (cause) {
        setError(String(cause));
      }
    })();
  }, [mode, refreshList, show, start]);

  /* An observer's list follows the chat tab: a conversation started there
   * shows up here without a reload. Not for the customer, whose own actions
   * are the only thing that changes their list. */
  useEffect(() => {
    if (mode !== "observer") return;
    const timer = window.setInterval(() => void refreshList().catch(() => undefined), 5000);
    return () => window.clearInterval(timer);
  }, [mode, refreshList]);

  /* And it follows which conversation the chat tab has on screen: the
   * customer's page writes it to localStorage, and the `storage` event fires
   * in every other tab of the same origin. On stage, "New conversation" in
   * the chat moves the console with it. */
  useEffect(() => {
    if (mode !== "observer") return;
    const follow = (event: StorageEvent) => {
      if (event.key !== ACTIVE_STORAGE_KEY || !event.newValue) return;
      const id = event.newValue;
      refreshList()
        .then((items) => {
          if (items.some((s) => s.conversation_id === id)) void show(id);
        })
        .catch(() => undefined);
    };
    window.addEventListener("storage", follow);
    return () => window.removeEventListener("storage", follow);
  }, [mode, refreshList, show]);

  /* The customer's copy, kept current: every message and reply that lands on
   * screen is written under the conversation it belongs to. */
  useEffect(() => {
    if (mode !== "customer" || !activeId || messages.length === 0) return;
    writeCached(activeId, messages, replies);
  }, [mode, activeId, messages, replies]);

  const submit = useCallback(
    async (turn: FailedTurn, echo: boolean) => {
      const { conversationId, clientTurnId, text } = turn;
      if (echo) setMessages((current) => [...current, { role: "customer", text }]);
      setBusyId(conversationId);
      setError(null);
      setFailed(null);
      try {
        const reply = await api.sendTurn(conversationId, text, clientTurnId);
        if (activeRef.current === conversationId) {
          setMessages((current) => [...current, { role: "agent", text: reply.text }]);
          setReplies((current) => [...current, reply]);
        }
        void refreshList().catch(() => undefined); // labels follow the domain
      } catch (cause) {
        setError(String(cause));
        // Retryable unless the backend said the conversation is not this
        // browser's (404) or the message itself was refused (409 reuse, 422).
        const status = cause instanceof ApiError ? cause.status : 0;
        if (![401, 404, 422].includes(status)) setFailed(turn);
      } finally {
        setBusyId(null);
      }
    },
    [refreshList],
  );

  const send = useCallback(
    async (text: string) => {
      if (!activeId) return;
      await submit({ conversationId: activeId, clientTurnId: crypto.randomUUID(), text }, true);
    },
    [activeId, submit],
  );

  /** Resend the failed message with the same client_turn_id. */
  const retry = useCallback(async () => {
    if (failed) await submit(failed, false);
  }, [failed, submit]);

  useEffect(() => () => streamRef.current?.close(), []);

  const conversations: ConversationSummary[] = summaries.map((s) => ({
    id: s.conversation_id,
    label: s.label,
  }));

  const busy = busyId !== null && busyId === activeId;

  return {
    sessionId: activeId,
    messages,
    replies,
    events,
    domains,
    perimeter,
    busy,
    // What the agent is doing right now, phrased for the customer. Derived
    // from the live trace, so it is only as current as the event stream: if
    // the stream dropped, this stays on its first phrase until the reply
    // lands — a stale phrase, never a wrong one.
    activity: busy ? describeActivity(events) : null,
    error,
    retryable: failed !== null && failed.conversationId === activeId,
    retry,
    start,
    send,
    conversations,
    select: show,
  };
}
