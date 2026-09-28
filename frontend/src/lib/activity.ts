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

/** The agent's first model call of the turn, before any capability: it is
 *  reading the message in the light of the domain the router chose. */
const COMPOSING_IN_DOMAIN: Record<string, string> = {
  billing: "Looking into your billing question…",
  cancellation: "Going over your cancellation request…",
  modem: "Looking into your connection…",
  fallback: "Working out who can help with this…",
};

export function describeActivity(events: TraceEvent[]): string {
  const latest = events[events.length - 1];
  if (!latest || BETWEEN_TURNS.has(latest.name)) return WAITING;

  switch (latest.name) {
    case "turn.received":
    case "privacy.redaction_applied":
      return "Reading your message…";

    case "llm.request":
      return latest.role === "router" ? "Working out how to help…" : describeComposing(latest, events);

    case "llm.response":
      // The model has answered; what follows is either a capability call
      // (announced on its own event) or the reply leaving through the guards.
      return (latest.tool_calls as string[] | undefined)?.length
        ? "Checking what is needed…"
        : "Double-checking the reply…";

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

/** What the turn in flight has done so far. The agent's model call is the
 *  longest wait of the turn, so its phrase should say what the model is
 *  working from, not just that it is working. */
interface TurnSoFar {
  domain: string | null;
  /** The last capability that returned, and how. */
  capability: { name: string; outcome: string } | null;
  /** A guard rejected the first draft: this call is the rewrite. */
  repairing: boolean;
  handoverConfirmed: boolean;
  verification: string | null;
  escalated: boolean;
}

function turnSoFar(events: TraceEvent[]): TurnSoFar {
  const turn: TurnSoFar = {
    domain: null,
    capability: null,
    repairing: false,
    handoverConfirmed: false,
    verification: null,
    escalated: false,
  };
  // Walk back to the start of the turn; the latest event is the request
  // itself, so it is skipped.
  for (let i = events.length - 2; i >= 0; i--) {
    const event = events[i];
    if (BETWEEN_TURNS.has(event.name) || event.name === "turn.received") break;
    switch (event.name) {
      case "domain.entered":
        turn.domain ??= String(event.domain);
        break;
      case "capability.result":
        turn.capability ??= { name: String(event.capability), outcome: String(event.outcome) };
        break;
      case "guardrail.triggered":
        turn.repairing = true;
        break;
      case "handover.confirmation_received":
        turn.handoverConfirmed = true;
        break;
      case "identity.verification":
        turn.verification ??= String(event.result);
        break;
      case "escalation.triggered":
        turn.escalated = true;
        break;
    }
  }
  return turn;
}

function describeComposing(request: TraceEvent, events: TraceEvent[]): string {
  const turn = turnSoFar(events);

  if (turn.repairing) return "Rewording the reply…";

  // A capability has returned: the model is reading its result, and what the
  // result was is what the customer is waiting to hear.
  if (turn.capability) {
    const { name, outcome } = turn.capability;
    if (outcome === "serviceUnavailable") return "Working around a slow billing system…";
    switch (name) {
      case "billing_lookup":
        return outcome === "found"
          ? "Going through your billing details…"
          : "Checking the contract number with you…";
      case "create_handover_record":
        return outcome.startsWith("created")
          ? "Confirming your case details…"
          : "Explaining what happened with your case…";
      case "transfer_to_human":
        return outcome === "transferred"
          ? "Arranging the transfer…"
          : "Explaining who can help next…";
      default:
        return "Going through what we found…";
    }
  }

  if (turn.handoverConfirmed) return "Opening a case for a colleague…";
  if (turn.verification === "asked") return "Preparing a security question…";
  if (turn.verification === "verified") return "Identity confirmed, carrying on…";
  if (turn.escalated) return "Working out the next step with a colleague…";

  // The closing call: no capability can follow it, so this is the reply.
  if (request.tools_available === false) return "Writing your reply…";

  return (turn.domain && COMPOSING_IN_DOMAIN[turn.domain]) ?? "Preparing a reply…";
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
