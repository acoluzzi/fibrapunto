import type { AgentReply } from "./types";
import type { Message } from "../panels/ChatPanel";

/** The customer's own copy of their conversations, kept in this browser.
 *
 *  The backend stores the transcript redacted (§9): the answer to the security
 *  question is replaced by a placeholder before anything is written, and the
 *  reopened conversation shows that placeholder where the customer typed
 *  "Torino". That is right for the record and wrong for the person reading
 *  their own chat back. So the customer's page keeps what *they* typed, on
 *  *their* device, and uses it only to fill the placeholder on reopen — and
 *  to put the conversation on screen at once, before the backend answers.
 *
 *  Nothing here is sent anywhere. The developer console deliberately does not
 *  read this cache: it shows the transcript as the backend holds it, which is
 *  the point of the placeholder. localStorage can be missing or throw (private
 *  window, cleared site data), so every access is guarded and the page works
 *  without it. */

const PREFIX = "fibrapunto.conversation.";
const ACTIVE_KEY = "fibrapunto.active";
/** engine.VERIFICATION_PLACEHOLDER, as the backend stores it. Matched by
 *  prefix so a wording change on that side degrades to "not filled in". */
const PLACEHOLDER_PREFIX = "[answer to the security question";
/** Oldest conversations are dropped past this, so the cache cannot grow
 *  unbounded on a browser that has been used for a year of demos. */
const KEEP = 30;

export interface CachedConversation {
  messages: Message[];
  replies: AgentReply[];
  savedAt: number;
}

export function readCached(id: string): CachedConversation | null {
  try {
    const raw = window.localStorage.getItem(PREFIX + id);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as CachedConversation;
    if (!Array.isArray(parsed.messages) || !Array.isArray(parsed.replies)) return null;
    return parsed;
  } catch {
    return null;
  }
}

export function writeCached(id: string, messages: Message[], replies: AgentReply[]): void {
  try {
    const entry: CachedConversation = { messages, replies, savedAt: Date.now() };
    window.localStorage.setItem(PREFIX + id, JSON.stringify(entry));
    prune();
  } catch {
    /* no storage, or full: the backend copy is still there */
  }
}

function prune(): void {
  const entries: { key: string; savedAt: number }[] = [];
  for (let i = 0; i < window.localStorage.length; i += 1) {
    const key = window.localStorage.key(i);
    if (!key?.startsWith(PREFIX)) continue;
    try {
      const parsed = JSON.parse(window.localStorage.getItem(key) ?? "") as CachedConversation;
      entries.push({ key, savedAt: parsed.savedAt ?? 0 });
    } catch {
      entries.push({ key, savedAt: 0 });
    }
  }
  entries
    .sort((a, b) => b.savedAt - a.savedAt)
    .slice(KEEP)
    .forEach(({ key }) => window.localStorage.removeItem(key));
}

export function isPlaceholder(text: string): boolean {
  return text.startsWith(PLACEHOLDER_PREFIX);
}

/** The backend's transcript, with each placeholder replaced by what this
 *  browser saw the customer type. Aligned by counting customer messages, not
 *  by position: a colleague's message posted into the conversation appears in
 *  the stored copy and not in the cached one, and must not shift the match. */
export function mergeStored(server: Message[], cached: Message[] | null): Message[] {
  if (!cached) return server;
  const local = cached.filter((m) => m.role === "customer");
  let k = 0;
  return server.map((m) => {
    if (m.role !== "customer") return m;
    const mine = local[k];
    k += 1;
    return isPlaceholder(m.text) && mine ? { ...m, text: mine.text } : m;
  });
}

/** Which conversation the customer's tab has on screen, for the console to
 *  follow. Written by the customer's page, read by the observer. */
export function readActive(): string | null {
  try {
    return window.localStorage.getItem(ACTIVE_KEY);
  } catch {
    return null;
  }
}

export function writeActive(id: string): void {
  try {
    window.localStorage.setItem(ACTIVE_KEY, id);
  } catch {
    /* the console then falls back to the latest conversation */
  }
}

export const ACTIVE_STORAGE_KEY = ACTIVE_KEY;
