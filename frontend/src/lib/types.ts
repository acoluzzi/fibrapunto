export type Outcome =
  | "found" | "notFound" | "serviceUnavailable" | "multipleMatches" | "rejected"
  | "created" | "createdWithoutContractLink" | "failed"
  | "transferred" | "noAgentAvailable";

export interface CapabilityCall {
  capability: string;
  outcome: Outcome;
  duration_ms: number;
}

export interface Routing {
  domain: string;
  switched: boolean;
  decided_by: string;
  confidence: number | null;
  sentiment: string | null;
}

export interface AgentReply {
  text: string;
  conversation_id: string;
  turn_index: number;
  domain: string;
  routing: Routing;
  capability_calls: CapabilityCall[];
  status: string;
  handover_reference: string | null;
  contract_number: string | null;
  model_served: string | null;
  prompt_sha: string;
}

export interface TraceEvent {
  name: string;
  turn: number | null;
  at: string;
  duration_ms: number | null;
  [key: string]: unknown;
}

export interface Domain {
  key: string;
  enabled: boolean;
  scope_statement: string;
  capabilities: string[];
  always_escalates: boolean;
  prompt_sha: string | null;
}

export type TestMode = "" | "timeout" | "slow";
