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
  /** What this turn's model calls cost in USD, as the provider reported it
   *  (§16). null when the provider did not report — never 0 for unknown. */
  turn_cost_usd?: number | null;
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

/** The billing system's live state, as the stub's switch holds it (§7). */
export type BillingState = "normal" | "slow" | "timeout" | "unauthorized" | "malformed";

/** The other modules the agent depends on, as the backend's switches hold
 *  them (faults.py). */
export interface Faults {
  crm: "up" | "down";
  colleagues: "schedule" | "available" | "unavailable";
  model: "up" | "down";
  credential_store: "up" | "down";
  trace_store: "up" | "down";
}

/** The three gated views (api/auth.py). Each has its own password and its
 *  own cookie; a session in one opens nothing else. */
export type Role = "operator" | "developer" | "analyst";

/** The recommendation system's discount budget.
 *
 *  spent is everything committed: held (accepted by a customer, awaiting a
 *  colleague) plus confirmed (given away for good). remaining is the
 *  complement of spent, and is what the next offer is measured against. */
export interface OfferBudget {
  total_eur: string;
  spent_eur: string;
  held_eur: string;
  confirmed_eur: string;
  remaining_eur: string;
  accepted: number;
  confirmed: number;
  discounts_available: boolean;
}

/** One catalogue entry with what it has done, as the analyst sees it. */
export interface Campaign {
  offer_id: string;
  title: string;
  description: string;
  monthly_discount_eur: string;
  duration_months: number;
  cost_eur: string;
  plans: string[] | null;
  accepted: number;
  confirmed: number;
  declined: number;
  held_eur: string;
  spent_eur: string;
}

/** Everything counted, never a row (api/analyst.py). */
export interface AnalystOverview {
  conversations: {
    total: number;
    from_eval_runs: number;
    by_domain: { domain: string; n: number }[];
    by_status: { status: string; n: number }[];
  };
  turns: {
    customer: number;
    agent: number;
    latency_p50_ms: number | null;
    latency_p95_ms: number | null;
    cost_usd: string | null;
    costed_turns: number;
  };
  events: Record<string, number>;
  capability_outcomes: { outcome: string | null; n: number }[];
  escalations: { kind: string | null; n: number }[];
  cases: { queue: string; status: string; n: number; complete: number }[];
  offers_on_cases: { status: string; n: number }[];
}

/** One of this browser's conversations, as GET /api/sessions lists it. */
export interface SessionSummary {
  conversation_id: string;
  created_at: string;
  last_activity_at: string;
  domain: string;
  status: string;
  label: string;
}

/** A conversation reopened from the backend: its stored (redacted) transcript
 *  and the replies it received. */
export interface StoredSession {
  conversation_id: string;
  messages: { role: "customer" | "agent"; text: string }[];
  replies: AgentReply[];
}

/** One row of the §1 table, as evals/metrics.py computed it. A float is a
 *  ratio (shown as a percentage), an integer a count, null "not measured". */
export interface EvalMetric {
  name: string;
  value: number | null;
  target: string;
  passed: boolean | null;
}

/** A recorded eval run (§14): what `make eval` measured, kept in
 *  platform.eval_run so the dashboard can show it. judge_model is null for a
 *  run without a judge (`make eval-quick`). */
export interface EvalRun {
  run_id: string;
  git_sha: string | null;
  agent_model: string;
  judge_model: string | null;
  dataset_sha: string | null;
  prompt_sha: string | null;
  case_count: number | null;
  started_at: string | null;
  created_at: string;
  metrics: EvalMetric[];
}
