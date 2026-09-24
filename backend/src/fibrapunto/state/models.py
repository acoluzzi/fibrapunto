"""Conversation state.

The design decision that matters here: **collected facts do not live in the
transcript.** They live in `CollectedState`, populated by tool side effects.

That is what makes §15's mid-conversation switch work. When a customer says
"…actually, I want to cancel" halfway through a billing conversation, the
cancellation domain's first render already contains the contract number. It is
not re-asked because there is nothing to re-ask — rather than because an
instruction told the model to remember.

It is also what makes §1's handover completeness reachable: the §13 payload is
built from these fields, so a missing one is a ValidationError at build time
rather than a thin case record discovered by the receiving team.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from fibrapunto.capabilities.runtime import CapabilityCall
from fibrapunto.domains.registry import DomainKey


class TurnRole(StrEnum):
    customer = "customer"
    agent = "agent"


class ConversationStatus(StrEnum):
    active = "active"
    resolved = "resolved"
    """The customer knows what happened and needs nobody else (§1, outcome 1)."""

    handed_over = "handed_over"
    """A handover record exists and the customer has the reference (§1, outcome 2)."""

    escalated_no_record = "escalated_no_record"
    """§12.4: escalation was warranted but the record could not be created. The
    agent said so plainly and claimed nothing. Distinct from handed_over because
    conflating them would let a failure be counted as a success."""

    abandoned = "abandoned"


class Turn(BaseModel):
    role: TurnRole
    text: str
    at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    domain: DomainKey | None = None


class BillingFacts(BaseModel):
    """What a successful lookup returned, kept for the rest of the conversation.

    The grounding guard checks every figure in a reply against this. A figure
    that is not here and not in the customer's own words is an invention, and
    is blocked rather than counted (§1 "no invention: 0").

    They are facts about **one contract**. `contract_number` says which, and
    the tool node drops the whole object when a lookup concerns a different
    one — whatever that lookup's outcome. Kept past that point, contract A's
    amount would ground a sentence about contract B, and travel into B's
    handover record as if it were B's.
    """

    contract_number: str | None = None
    status: str
    failure_reason: str | None = None
    amount: Decimal | None = None
    invoice_month: str | None = None
    next_retry_date: date | None = None
    payment_link: str | None = None
    lock_in_until: date | None = None
    remaining_instalments: int | None = None
    fetched_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ContractCandidate(BaseModel):
    contract_number: str
    service_address: str


class PendingConfirmation(BaseModel):
    """§4.1 instruction 5: read the summary back and wait.

    A structural gate rather than a hope. Set by the tool node when the model
    tries to open a billing dispute before the customer has agreed to the
    summary; while it is set, the router keeps a bare "yes" in this domain, and
    the customer's affirmative answer opens the case from what is stored here —
    so the customer's own agreement is what makes the record trustworthy to the
    operator who receives it, and the model cannot skip the step by being
    persuasive about it.
    """

    summary: str
    asked_at_turn: int
    satisfied: bool = False
    stated_reason: str | None = None
    disputed_point: str | None = None


class OpenedCase(BaseModel):
    reference: str
    contract_linked: bool
    """Kept so a repeated call returns the same outcome variant."""


class CollectedState(BaseModel):
    """Facts, not transcript. Survives a domain switch intact (§15)."""

    contract_number: str | None = None
    contract_number_source: str | None = None

    billing: BillingFacts | None = None
    lookup_outcomes: list[str] = Field(default_factory=list)

    stated_reason: str | None = None
    """The customer's own words (§12.2, §13). Never paraphrased into the record."""

    disputed_point: str | None = None
    """What the customer actually objects to (§13, billing disputes).

    Distinct from the subject line: the subject is the headline ("September
    charge disputed, €29.90"), this is the substance ("the customer says their
    plan is €24.90"). Only the model can supply it, because only it has read
    what the customer said."""

    disambiguation_candidates: list[ContractCandidate] = Field(default_factory=list)

    told_to_customer: list[str] = Field(default_factory=list)
    """What the agent has already said, so the receiving human does not
    contradict it. §12.2 calls this "the field most often left out and the one
    that most often causes a second complaint"."""

    handover_reference: str | None = None
    """The most recently opened case — what the customer was last told."""

    handovers: dict[str, OpenedCase] = Field(default_factory=dict)
    """Every case this conversation opened, by record type.

    One per type, not one per conversation: a billing dispute followed by "I
    want to cancel" is two requests for two teams. Keyed so that a repeated call
    returns *that type's* case — returning the dispute's reference to a
    cancellation told the customer the retention team would call about a case
    that was sitting in billing operations."""

    sensitive_data_seen: list[str] = Field(default_factory=list)
    """Kinds, never values — ["IBAN"], not the IBAN (§9)."""

    def as_prompt_block(self) -> str:
        """Render for the volatile half of the prompt.

        Appended *after* the cached stable block, never interpolated into it —
        this changes every turn, and putting it earlier would invalidate the
        provider cache on every request.
        """
        lines: list[str] = []
        if self.contract_number:
            lines.append(f"- Contract number: {self.contract_number} (given by the customer)")
        if self.billing:
            b = self.billing
            lines.append(f"- Billing status: {b.status}")
            if b.amount is not None:
                lines.append(f"- Amount: €{b.amount}")
            if b.failure_reason:
                lines.append(f"- Reason for refusal: {b.failure_reason}")
            if b.next_retry_date:
                lines.append(f"- Next retry: {b.next_retry_date.isoformat()}")
            if b.payment_link:
                lines.append(f"- Payment link: {b.payment_link}")
            lines.append(f"- Lock-in: {b.lock_in_until.isoformat() if b.lock_in_until else 'none'}")
            if b.remaining_instalments:
                lines.append(f"- Router instalments remaining: {b.remaining_instalments}")
        if self.stated_reason:
            lines.append(f'- Reason the customer gave: "{self.stated_reason}"')
        if self.disambiguation_candidates:
            lines.append("- Contracts for the customer to choose between (by address):")
            lines += [
                f"    · {c.contract_number} — {c.service_address}"
                for c in self.disambiguation_candidates
            ]
        for record_type, case in self.handovers.items():
            label = record_type.replace("_", " ")
            lines.append(f"- Case already opened ({label}): no. {case.reference}")
        if self.sensitive_data_seen:
            lines.append(
                f"- The customer pasted sensitive data "
                f"({', '.join(self.sensitive_data_seen)}): never repeat it."
            )

        if not lines:
            return "## State already collected\n\nNothing yet."
        return "## State already collected\n\n" + "\n".join(lines)


class ConversationState(BaseModel):
    """Everything the turn loop and the trace need."""

    id: UUID = Field(default_factory=uuid4)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    current_domain: DomainKey = DomainKey.fallback
    turns: list[Turn] = Field(default_factory=list)
    collected: CollectedState = Field(default_factory=CollectedState)
    capability_calls: list[CapabilityCall] = Field(default_factory=list)

    status: ConversationStatus = ConversationStatus.active
    pending_confirmation: PendingConfirmation | None = None

    # Trigger inputs (§12.1). Deterministic counters, deliberately — the model
    # is not asked whether progress is being made.
    turn_count: int = 0
    successful_capability_calls: int = 0
    format_reasks: int = 0
    disambiguation_turns: int = 0
    no_progress_streak: int = 0

    terse: bool = False
    """§12.1: signs of distress shorten the agent's answers and bring forward
    the offer of a human."""

    offer_human_now: bool = False

    pending_trigger: str | None = None
    """The escalation trigger that fired on the last turn (§12.1).

    Carried so the handover payload can record *why* the conversation escalated
    (§12.2) and so §12.5 can watch the distribution of triggers — a rising
    serviceUnavailable rate is an integration problem wearing an agent
    problem's clothes."""

    # Attribution (§14). Which build produced this conversation.
    test_mode: str | None = None
    agent_model: str = ""
    agent_model_served: str | None = None
    prompt_sha: str = ""

    model_config = {"arbitrary_types_allowed": True}

    def customer_turns(self) -> list[str]:
        """Everything the customer actually typed.

        Passed to `billing_lookup` so §5.2's verbatim rule can be checked. It is
        a required argument there rather than optional context, because a rule
        that cannot be checked is a request.
        """
        return [t.text for t in self.turns if t.role is TurnRole.customer]

    def record_turn(self, role: TurnRole, text: str) -> None:
        self.turns.append(Turn(role=role, text=text, domain=self.current_domain))
        if role is TurnRole.customer:
            self.turn_count += 1

    def record_capability(self, call: CapabilityCall) -> None:
        self.capability_calls.append(call)
        if call.outcome in {"found", "created", "createdWithoutContractLink", "transferred"}:
            self.successful_capability_calls += 1

    def snapshot(self) -> dict[str, Any]:
        """For the trace and the API. Never includes raw customer text —
        transcripts are stored redacted and separately (§9)."""
        return {
            "conversation_id": str(self.id),
            "domain": self.current_domain.value,
            "status": self.status.value,
            "turn_count": self.turn_count,
            "contract_number": self.collected.contract_number,
            "handover_reference": self.collected.handover_reference,
            "capability_calls": [
                {"capability": c.capability, "outcome": c.outcome, "ms": c.duration_ms}
                for c in self.capability_calls
            ],
            "prompt_sha": self.prompt_sha,
            "agent_model_served": self.agent_model_served,
        }
