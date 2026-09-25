import type {
  AgentReply,
  BillingState,
  Domain,
  Faults,
  OfferBudget,
  SessionSummary,
  StoredSession,
} from "./types";

const json = { "content-type": "application/json" };

/* The backend states its failures in the body (see the ReasoningLayerUnavailable
 * handler). Surfacing only the status code would mean the one thing most likely
 * to go wrong on stage — the model provider unreachable — reads as "500" and
 * sends you to the container logs. Prefer what the server said. */
/** Carries the status, so a caller can tell "not signed in" from "broken". */
export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
  }
}

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, init);
  if (!response.ok) {
    let detail = "";
    try {
      const body = await response.json();
      detail = [body.message ?? body.detail, body.hint ?? body.provider_said]
        .filter(Boolean)
        .join(" — ");
    } catch {
      /* not JSON; the status code is all there is */
    }
    throw new ApiError(
      detail || `${init?.method ?? "GET"} ${path} → ${response.status}`,
      response.status,
    );
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

export const api = {
  // Always a normal session: the modules' state is the live switchboard, not
  // something fixed when the conversation starts. (The per-session header is
  // kept on the backend for the eval harness only.)
  createSession: () =>
    req<{ conversation_id: string; domain: string }>("/api/sessions", {
      method: "POST",
      headers: json,
      body: JSON.stringify({}),
    }),

  // One client_turn_id per submitted message, reused on retry: the backend
  // returns the stored reply instead of calling the model a second time.
  sendTurn: (id: string, text: string, clientTurnId: string) =>
    req<AgentReply>(`/api/sessions/${id}/turns`, {
      method: "POST",
      headers: json,
      body: JSON.stringify({ client_turn_id: clientTurnId, text }),
    }),

  // The anonymous browser identity (an HttpOnly cookie the page never sees).
  // It lets this browser find its own conversations again; it is not a login.
  customerAccess: () => req<{ ok: boolean }>("/api/customer/access", { method: "POST" }),
  sessions: () => req<{ items: SessionSummary[]; next_cursor: string | null }>("/api/sessions"),
  session: (id: string) => req<StoredSession>(`/api/sessions/${id}`),

  domains: () => req<Domain[]>("/api/domains"),

  // §8: the same check under both identities, so the panel can show the diff.
  perimeterProbe: (field = "email") =>
    req<Record<string, any>>(`/api/_admin/perimeter-probe?field=${field}`, {
      method: "POST",
    }),

  // The session cookie is HttpOnly: the page never sees it, it only asks the
  // server whether it is signed in.
  operatorSession: () => req<{ operator: boolean }>("/api/operator/session"),
  operatorLogin: (password: string) =>
    req<void>("/api/operator/login", {
      method: "POST",
      headers: json,
      body: JSON.stringify({ password }),
    }),
  operatorLogout: () => req<void>("/api/operator/logout", { method: "POST" }),

  operatorQueues: () => req<any[]>("/api/operator/queues"),
  operatorRecords: () => req<any[]>("/api/operator/records"),
  // A colleague's decision on an offer the customer accepted. The customer is
  // told in their conversation, and by email (queued, not sent: demo).
  operatorDecideOffer: (reference: string, decision: "confirm" | "decline", reason?: string) =>
    req<any>(`/api/operator/records/${reference}/offer`, {
      method: "POST",
      headers: json,
      body: JSON.stringify({ decision, reason: reason || null }),
    }),

  operatorRecord: (reference: string) =>
    req<any>(`/api/operator/records/${reference}`),

  // §7: the live switches. Billing's lives in the stub (the external system),
  // reached same-origin through the proxy because the stub sends no CORS
  // headers. ttl_seconds null: sticky until the switch is moved back — a
  // switch on screen must not change on its own.
  billingState: () => req<{ mode: BillingState }>("/billing/_control/mode"),
  setBillingState: (mode: BillingState) =>
    req<{ mode: BillingState }>("/billing/_control/mode", {
      method: "POST",
      headers: json,
      body: JSON.stringify({ mode, ttl_seconds: null }),
    }),

  offerBudget: () => req<OfferBudget>("/recommendations/budget"),
  recommendationsState: () => req<{ mode: "up" | "down" }>("/recommendations/_control/mode"),
  setRecommendationsState: (mode: "up" | "down") =>
    req<{ mode: string }>("/recommendations/_control/mode", {
      method: "POST",
      headers: json,
      body: JSON.stringify({ mode }),
    }),

  faults: () => req<Faults>("/api/faults"),
  setFault: (change: Partial<Faults>) =>
    req<Faults>("/api/faults", {
      method: "POST",
      headers: json,
      body: JSON.stringify(change),
    }),
};
