"""Conversation-level trace events (§17.10).

    "Conversation-level logging sufficient to reconstruct which domain fired,
    which capabilities were called, and with what outcomes."

Three of these event types carry the whole demo, and they are the reason the
trace panel is convincing rather than decorative:

  * `routing_decision` — which domain fired, and why (§4)
  * `capability_result` — which capability, which **typed outcome** (§5.1)
  * `perimeter_denied` — the authorization boundary, rendered (§8)

Two more are emitted *before* the thing they name happens, not after:
`capability.invoked` and `llm.request`. They carry no outcome — the result
event does — and exist so that the live stream says what the agent is doing
while it does it. The chat panel reads them to replace "the assistant is
working" with "looking up your contract" (frontend/src/lib/activity.ts).

Field names are deliberately OpenTelemetry-span-shaped (`name`, `attributes`,
`start`, `duration_ms`). Not because we use OTel — we chose not to, and
docs/DECISIONS.md #20 says why — but so that the migration is mechanical rather
than a rewrite. "I chose not to, and here is the path" is a better answer to a
reviewer than not having considered it.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class EventType(StrEnum):
    session_started = "session.started"
    turn_received = "turn.received"
    redaction_applied = "privacy.redaction_applied"

    routing_decision = "router.decision"
    domain_entered = "domain.entered"
    domain_switched = "domain.switched"

    llm_request = "llm.request"
    llm_response = "llm.response"

    capability_invoked = "capability.invoked"
    capability_result = "capability.result"
    # Not a §5 capability: the recommendation system is consulted, never
    # depended on (offers are held or dropped, and the turn goes on either
    # way). Traced under its own name so a reader does not look for the
    # `capability.result` that will never follow it.
    offer_lookup = "offer.lookup"

    perimeter_check = "perimeter.check"
    perimeter_denied = "perimeter.denied"

    guardrail_triggered = "guardrail.triggered"
    outbound_filter_applied = "privacy.outbound_filter_applied"

    escalation_triggered = "escalation.triggered"
    handover_confirmation_received = "handover.confirmation_received"
    handover_created = "handover.created"
    customer_notified = "customer.notified"
    identity_verification = "identity.verification"

    agent_reply = "agent.reply"
    session_ended = "session.ended"


class TraceEvent(BaseModel):
    """One span. Rendered by the UI's "what happened in the system" panel."""

    name: EventType
    conversation_id: UUID
    turn_seq: int | None = None
    start: datetime = Field(default_factory=lambda: datetime.now(UTC))
    duration_ms: int | None = None
    attributes: dict[str, Any] = Field(default_factory=dict)

    def for_stream(self) -> dict[str, Any]:
        """The SSE payload. Same shape the UI renders from."""
        return {
            "name": self.name.value,
            "turn": self.turn_seq,
            "at": self.start.isoformat(),
            "duration_ms": self.duration_ms,
            **self.attributes,
        }
