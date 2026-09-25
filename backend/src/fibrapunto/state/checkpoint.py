"""What survives a restart: RuntimeCheckpointV1.

An explicit list, not `ConversationState.model_dump()`. Serialising the whole
state would persist whatever someone adds to it next — a transcript, a raw
value — without anyone deciding to. Each field here was chosen.

**Kept:** the current domain and status; the collected, validated facts; a
pending read-back or escalation offer; the retry and progress counters; the
cases already opened; the model and prompt versions.

**Left out, and where it comes from instead:**

  * turns             — platform.turn, stored redacted (§9) in the same
                        transaction as this checkpoint.
  * told_to_customer  — rebuilt from the agent's turns.
  * capability_calls  — rebuilt from the trace, best-effort. Nothing that
                        decides reads it: the triggers read `lookup_outcomes`,
                        which is kept.
  * raw sensitive values — the free-text fields that carry the customer's own
                        words are redacted as they are written, whatever the
                        inbound-redaction toggle was set to.

The agent still has no memory *across* conversations (§3): a checkpoint is one
conversation's state, loaded only to continue that conversation.
"""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from fibrapunto.domains.registry import DomainKey
from fibrapunto.privacy import redaction
from fibrapunto.state.models import (
    BillingFacts,
    CaseSummary,
    CollectedState,
    ContractCandidate,
    ConversationState,
    ConversationStatus,
    OfferFacts,
    OpenedCase,
    PendingConfirmation,
    PendingEscalation,
    PendingOffer,
    PendingVerification,
    Turn,
    TurnRole,
)

SCHEMA_VERSION = 1


class UnsupportedCheckpoint(Exception):
    """A checkpoint written by a schema this build does not understand. The
    conversation is refused, not guessed at."""


def _scrub(text: str | None) -> str | None:
    return redaction.for_storage(text)[0] if text else text


class CheckpointFacts(BaseModel):
    contract_number: str | None = None
    contract_number_source: str | None = None
    billing: BillingFacts | None = None
    lookup_outcomes: list[str] = Field(default_factory=list)
    stated_reason: str | None = None
    disputed_point: str | None = None
    financial_request: str | None = None
    disambiguation_candidates: list[ContractCandidate] = Field(default_factory=list)
    handover_reference: str | None = None
    handovers: dict[str, OpenedCase] = Field(default_factory=dict)
    offer: OfferFacts | None = None
    offer_status: str | None = None
    verification: dict[str, str] = Field(default_factory=dict)
    existing_cases: list[CaseSummary] = Field(default_factory=list)
    transferred: bool = False
    sensitive_data_seen: list[str] = Field(default_factory=list)


class Counters(BaseModel):
    turn_count: int = 0
    successful_capability_calls: int = 0
    format_reasks: int = 0
    disambiguation_turns: int = 0
    no_progress_streak: int = 0


class RuntimeCheckpointV1(BaseModel):
    schema_version: Literal[1] = 1
    conversation_id: UUID
    test_mode: str | None = None

    current_domain: DomainKey
    status: ConversationStatus
    facts: CheckpointFacts
    pending_confirmation: PendingConfirmation | None = None
    pending_escalation: PendingEscalation | None = None
    pending_offer: PendingOffer | None = None
    pending_verification: PendingVerification | None = None
    threatened_to_leave: bool = False
    escalations_offered: list[str] = Field(default_factory=list)
    counters: Counters

    terse: bool = False
    offer_human_now: bool = False
    human_requested: bool = False
    pending_trigger: str | None = None

    agent_model: str = ""
    agent_model_served: str | None = None
    prompt_sha: str = ""

    @classmethod
    def from_state(cls, state: ConversationState) -> RuntimeCheckpointV1:
        c = state.collected
        pending = state.pending_confirmation
        if pending is not None:
            pending = pending.model_copy(
                update={
                    "summary": _scrub(pending.summary) or "",
                    "stated_reason": _scrub(pending.stated_reason),
                    "disputed_point": _scrub(pending.disputed_point),
                }
            )
        return cls(
            conversation_id=state.id,
            test_mode=state.test_mode,
            current_domain=state.current_domain,
            status=state.status,
            facts=CheckpointFacts(
                contract_number=c.contract_number,
                contract_number_source=c.contract_number_source,
                billing=c.billing,
                lookup_outcomes=list(c.lookup_outcomes),
                stated_reason=_scrub(c.stated_reason),
                disputed_point=_scrub(c.disputed_point),
                financial_request=c.financial_request,
                disambiguation_candidates=list(c.disambiguation_candidates),
                handover_reference=c.handover_reference,
                handovers=dict(c.handovers),
                offer=c.offer,
                offer_status=c.offer_status,
                verification=dict(c.verification),
                existing_cases=list(c.existing_cases),
                transferred=c.transferred,
                sensitive_data_seen=list(c.sensitive_data_seen),
            ),
            pending_confirmation=pending,
            pending_escalation=state.pending_escalation,
            pending_offer=state.pending_offer,
            pending_verification=state.pending_verification,
            threatened_to_leave=state.threatened_to_leave,
            escalations_offered=list(state.escalations_offered),
            counters=Counters(
                turn_count=state.turn_count,
                successful_capability_calls=state.successful_capability_calls,
                format_reasks=state.format_reasks,
                disambiguation_turns=state.disambiguation_turns,
                no_progress_streak=state.no_progress_streak,
            ),
            terse=state.terse,
            offer_human_now=state.offer_human_now,
            human_requested=state.human_requested,
            pending_trigger=state.pending_trigger,
            agent_model=state.agent_model,
            agent_model_served=state.agent_model_served,
            prompt_sha=state.prompt_sha,
        )

    def to_state(self, turns: list[tuple]) -> ConversationState:
        """Rebuild the runtime state from this checkpoint and the stored
        (redacted) transcript: `(role, text)` or `(role, text, domain)` per
        turn, in order. The domain matters — the cancellation rules read it."""
        f = self.facts
        rebuilt = [
            Turn(
                role=TurnRole(t[0]),
                text=t[1],
                domain=DomainKey(t[2]) if len(t) > 2 and t[2] else None,
            )
            for t in turns
        ]
        state = ConversationState(
            id=self.conversation_id,
            test_mode=self.test_mode,
            current_domain=self.current_domain,
            status=self.status,
            turns=rebuilt,
            collected=CollectedState(
                contract_number=f.contract_number,
                contract_number_source=f.contract_number_source,
                billing=f.billing,
                lookup_outcomes=list(f.lookup_outcomes),
                stated_reason=f.stated_reason,
                disputed_point=f.disputed_point,
                financial_request=f.financial_request,
                disambiguation_candidates=list(f.disambiguation_candidates),
                # What the customer was already told (§12.2): the agent's turns.
                told_to_customer=[t.text for t in rebuilt if t.role is TurnRole.agent],
                handover_reference=f.handover_reference,
                handovers=dict(f.handovers),
                offer=f.offer,
                offer_status=f.offer_status,
                verification=dict(f.verification),
                existing_cases=list(f.existing_cases),
                transferred=f.transferred,
                sensitive_data_seen=list(f.sensitive_data_seen),
            ),
            pending_confirmation=self.pending_confirmation,
            pending_escalation=self.pending_escalation,
            pending_offer=self.pending_offer,
            pending_verification=self.pending_verification,
            threatened_to_leave=self.threatened_to_leave,
            escalations_offered=list(self.escalations_offered),
            terse=self.terse,
            offer_human_now=self.offer_human_now,
            human_requested=self.human_requested,
            pending_trigger=self.pending_trigger,
            agent_model=self.agent_model,
            agent_model_served=self.agent_model_served,
            prompt_sha=self.prompt_sha,
            **self.counters.model_dump(),
        )
        return state


def load(schema_version: int, payload: dict) -> RuntimeCheckpointV1:
    """Parse a stored checkpoint, or refuse it. Never a best guess."""
    if schema_version != SCHEMA_VERSION or payload.get("schema_version") != SCHEMA_VERSION:
        raise UnsupportedCheckpoint(
            f"checkpoint schema {schema_version} is not supported by this build "
            f"(expected {SCHEMA_VERSION})"
        )
    return RuntimeCheckpointV1.model_validate(payload)
