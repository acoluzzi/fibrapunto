import type { TraceEvent } from "./types";

/** What the assistant is doing right now, in the customer's words.
 *
 *  Read from the same live trace the system panel renders (§17.10): the
 *  backend announces each model call and each capability call *before* it
 *  makes it, so the last event of the turn in flight is what is happening
 *  now. Nothing here is a second source of truth — it is the trace, phrased
 *  for the person waiting on it.
 *
 *  The phrasing is deliberately plain: "your contract", "a colleague", never
 *  a system's name, a domain's name or an outcome code. The panel next door
 *  shows those. */

const WAITING = "Sending your message…";
const DEFAULT = "Working on it…";

/** The events that close or precede a turn. When one of these is the latest
 *  event, the turn in flight has not started producing its own yet. */
const BETWEEN_TURNS = new Set([
  "session.started",
  "session.ended",
  "agent.reply",
  "customer.notified",
]);

const CAPABILITY_STARTED: Record<string, string> = {
  billing_lookup: "Looking up your contract…",
  create_handover_record: "Opening a case for a colleague…",
  transfer_to_human: "Finding an available colleague…",
};

export function describeActivity(events: TraceEvent[]): string {
  const latest = events[events.length - 1];
  if (!latest || BETWEEN_TURNS.has(latest.name)) return WAITING;

  switch (latest.name) {
    case "turn.received":
    case "privacy.redaction_applied":
      return "Reading your message…";

    case "llm.request":
      return latest.role === "router"
        ? "Working out how to help…"
        : afterCapability(events)
          ? "Going through what we found…"
          : "Preparing a reply…";

    case "router.decision":
    case "domain.switched":
    case "domain.entered":
      return "Working out how to help…";

    case "capability.invoked":
      return CAPABILITY_STARTED[String(latest.capability)] ?? DEFAULT;

    case "capability.result":
      return describeResult(String(latest.capability), String(latest.outcome));

    case "offer.lookup":
      return "Checking for offers available to you…";

    case "identity.verification":
      switch (latest.result) {
        case "asked":
          return "Preparing a security question…";
        case "verified":
          return "Identity confirmed, carrying on…";
        default:
          return "Checking your answer…";
      }

    case "handover.confirmation_received":
      return "Opening a case for a colleague…";

    case "handover.created":
      return "Your case is open, finishing up…";

    case "escalation.triggered":
      return "Checking whether a colleague should take over…";

    case "guardrail.triggered":
    case "privacy.outbound_filter_applied":
      return "Double-checking the reply…";

    default:
      return DEFAULT;
  }
}

/** Once a capability has been called this turn, the next model call is not
 *  composing from scratch: it is reading the result. */
function afterCapability(events: TraceEvent[]): boolean {
  for (let i = events.length - 1; i >= 0; i--) {
    const name = events[i].name;
    if (BETWEEN_TURNS.has(name) || name === "turn.received") return false;
    if (name === "capability.invoked") return true;
  }
  return false;
}

function describeResult(capability: string, outcome: string): string {
  if (outcome === "serviceUnavailable") return "The billing system is slow to respond…";
  switch (capability) {
    case "billing_lookup":
      return outcome === "found" ? "Going through your billing details…" : "Checking the contract number…";
    case "create_handover_record":
      return outcome.startsWith("created") ? "Your case is open, finishing up…" : "Finishing up…";
    case "transfer_to_human":
      return outcome === "transferred" ? "Putting you through…" : "Checking who is available…";
    default:
      return DEFAULT;
  }
}
