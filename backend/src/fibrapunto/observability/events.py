"""Conversation-level trace events (§17.10).

    "Conversation-level logging sufficient to reconstruct which domain fired,
    which capabilities were called, and with what outcomes."

Three of these event types carry the whole demo, and they are the reason the
trace panel is convincing rather than decorative:

  * `routing_decision` — which domain fired, and why (§4)
  * `capability_result` — which capability, which **typed outcome** (§5.1)
  * `perimeter_denied` — the authorization boundary, rendered (§8)

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

    perimeter_check = "perimeter.check"
    perimeter_denied = "perimeter.denied"

    guardrail_triggered = "guardrail.triggered"
    outbound_filter_applied = "privacy.outbound_filter_applied"

    escalation_triggered = "escalation.triggered"
    handover_created = "handover.created"

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
