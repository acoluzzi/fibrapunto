import type { AgentReply, Domain, TestMode } from "./types";

const json = { "content-type": "application/json" };

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, init);
  if (!response.ok) {
    throw new Error(`${init?.method ?? "GET"} ${path} → ${response.status}`);
  }
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

  operatorQueues: () => req<any[]>("/api/operator/queues"),
  operatorRecords: () => req<any[]>("/api/operator/records"),
  operatorRecord: (reference: string) =>
    req<any>(`/api/operator/records/${reference}`),

  // §7: provoke a failure mid-conversation, from a button rather than a
  // terminal. The TTL means a forgotten mode expires on its own.
  billingMode: (mode: TestMode) =>
    fetch("http://localhost:8081/_control/mode", {
      method: "POST",
      headers: json,
      body: JSON.stringify({ mode: mode || "normal", ttl_seconds: 180 }),
    }),
};
