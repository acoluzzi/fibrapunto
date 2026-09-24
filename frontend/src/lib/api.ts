import type { AgentReply, Domain, TestMode } from "./types";

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
  createSession: (testMode: TestMode) =>
    req<{ conversation_id: string; domain: string }>("/api/sessions", {
      method: "POST",
      headers: json,
      body: JSON.stringify({ test_mode: testMode || null }),
    }),

  sendTurn: (id: string, text: string) =>
    req<AgentReply>(`/api/sessions/${id}/turns`, {
      method: "POST",
      headers: json,
      body: JSON.stringify({ text }),
    }),

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
  operatorRecord: (reference: string) =>
    req<any>(`/api/operator/records/${reference}`),

  // §7: provoke a failure mid-conversation, from a button rather than a
  // terminal. Same-origin through the proxy (vite / nginx) because the stub
  // sends no CORS headers; a direct call to :8081 is blocked by the browser.
  // No ttl_seconds: the stub's default (120s) is the one the demo script
  // and `make billing-mode` quote, so there is a single number to keep true.
  billingMode: (mode: TestMode) =>
    req<{ mode: string; expires_in_s: number }>("/billing/_control/mode", {
      method: "POST",
      headers: json,
      body: JSON.stringify({ mode: mode || "normal" }),
    }),
};
