"""When escalation fires (§12.1).

    "Escalation is a designed subsystem, not the behaviour that remains when
    nothing else applies."

Every trigger here is **deterministic code**. The model is not asked whether it
is making progress, whether the lookup failed, or whether a cancellation
warrants a human — §12.1 is explicit that the categorical ones "are not
judgement calls and the agent is not permitted to weigh them".

The two that look softest are the two the spec cares most about:

    "The last two exist because the characteristic failure of a conversational
    agent is not a wrong answer. It is a loop that feels like progress to the
    system and like being trapped to the customer."

So no-progress is measured against *state*, not against the model's opinion of
itself: a turn where no new fact was collected and no capability succeeded did
not advance the conversation, whatever it sounded like.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

import structlog

from fibrapunto.domains.registry import DomainKey
from fibrapunto.settings import get_settings
from fibrapunto.state.models import CollectedState, ConversationState

log = structlog.get_logger(__name__)


class TriggerKind(StrEnum):
    """Reported in the trace and recorded in the handover payload (§12.2).

    §12.5: "Escalation rate and the distribution of triggers are monitored, not
    just logged. A rising serviceUnavailable escalation rate is an integration
    problem wearing an agent problem's clothes."
    """

    # Categorical — always, regardless of how well the conversation is going.
    categorical_cancellation = "categorical:cancellation"
    categorical_amount_dispute = "categorical:amount_dispute"
    categorical_payment_change = "categorical:payment_method_change"

    # Conditional — driven by the state of the conversation.
    service_unavailable = "conditional:service_unavailable"
    contract_not_found = "conditional:contract_not_found"
    disambiguation_unresolved = "conditional:disambiguation_unresolved"
    format_invalid_after_reask = "conditional:format_invalid_after_reask"
    no_progress = "conditional:no_progress"
    turn_budget_exhausted = "conditional:turn_budget_exhausted"

    # Customer-initiated.
    human_requested = "customer:human_requested"
    distress = "customer:distress"


@dataclass(frozen=True, slots=True)
class Trigger:
    kind: TriggerKind
    reason: str
    """One line, for the handover record's context."""

    immediate: bool = False
    """True when the turn must stop and escalate rather than letting the domain
    answer first. Reserved for §12.1's customer-initiated case: an explicit
    request for a person is "honoured immediately and without resistance"."""


def evaluate(state: ConversationState) -> Trigger | None:
    """The first trigger that fires, or None.

    Order matters: customer-initiated first, because §12.1 says it is honoured
    immediately; then categorical, which cannot be weighed; then conditional.
    """
    settings = get_settings()

    # --- customer-initiated (§12.1) -----------------------------------------
    if state.human_requested and state.collected.handover_reference is None:
        return Trigger(
            TriggerKind.human_requested,
            "The customer asked to speak to a person.",
            immediate=True,
        )

    # --- categorical (§12.1) — not judgement calls --------------------------
    if state.current_domain is DomainKey.cancellation:
        return Trigger(
            TriggerKind.categorical_cancellation,
            "Cancellation request: always goes to a person.",
        )

    request = state.collected.financial_request
    if request == "payment_method_change":
        return Trigger(
            TriggerKind.categorical_payment_change,
            "Change of payment method: always goes to a person.",
        )
    if request in ("dispute", "refund", "credit"):
        return Trigger(
            TriggerKind.categorical_amount_dispute,
            f"{request.capitalize()} requested: always goes to a person.",
        )

    # --- conditional --------------------------------------------------------
    # From collected state, not the capability log: the log is rebuilt from the
    # best-effort trace after a restart, and a trigger must not stop firing
    # because a trace row was lost. `lookup_outcomes` is in the checkpoint.
    recent = state.collected.lookup_outcomes

    if "serviceUnavailable" in recent:
        return Trigger(
            TriggerKind.service_unavailable,
            "The billing system did not respond.",
        )

    if "notFound" in recent:
        # Covers both a mistyped number and §6.1's contract that exists in the
        # CRM and not in billing. The agent cannot tell them apart, which is
        # exactly why neither is resolved by guessing.
        return Trigger(
            TriggerKind.contract_not_found,
            "The contract does not exist in the billing system.",
        )

    if state.disambiguation_turns >= 1 and state.collected.contract_number is None:
        return Trigger(
            TriggerKind.disambiguation_unresolved,
            "Several contracts match and the customer could not tell them apart.",
        )

    if state.format_reasks >= 1 and state.collected.contract_number is None:
        return Trigger(
            TriggerKind.format_invalid_after_reask,
            "The contract number was asked for a second time without success.",
        )

    if state.no_progress_streak >= 2:
        return Trigger(
            TriggerKind.no_progress,
            "Two turns with no progress: the customer is going in circles.",
        )

    if (
        state.turn_count > settings.turn_budget_without_capability
        and state.successful_capability_calls == 0
    ):
        return Trigger(
            TriggerKind.turn_budget_exhausted,
            f"{state.turn_count} turns without a single successful lookup.",
        )

    # --- customer-initiated, softer (§12.1) ---------------------------------
    # Anger brings the offer forward; it does not open a case unasked.
    if state.offer_human_now and state.collected.handover_reference is None:
        return Trigger(TriggerKind.distress, "The customer is upset or angry.")

    return None


OFFERED: frozenset[TriggerKind] = frozenset(
    {
        TriggerKind.service_unavailable,
        TriggerKind.contract_not_found,
        TriggerKind.disambiguation_unresolved,
        TriggerKind.format_invalid_after_reask,
        TriggerKind.no_progress,
        TriggerKind.turn_budget_exhausted,
        TriggerKind.distress,
    }
)
"""Triggers the code answers with an offer of a colleague, made and held by
code (§4.1 instruction 6, §12.1). The categorical ones are enforced by
structure instead: the cancellation domain has no tool but the handover, and a
dispute cannot open without the read-back gate. `human_requested` is not
offered — it is honoured."""


def update_progress(state: ConversationState, before: CollectedState) -> None:
    """Did this turn advance the conversation? (§12.1)

    Measured against collected state rather than against the text, because a
    model can produce a fluent, apologetic, entirely stationary turn — and that
    is precisely the failure §12.1 names: "a loop that feels like progress to
    the system and like being trapped to the customer".

    A turn advances if it learned a new fact or completed a capability call.
    Nothing else counts, including sounding helpful.
    """
    after = state.collected

    advanced = any(
        (
            before.contract_number != after.contract_number,
            before.billing != after.billing,
            before.stated_reason != after.stated_reason,
            before.disputed_point != after.disputed_point,
            len(before.lookup_outcomes) != len(after.lookup_outcomes),
            before.handover_reference != after.handover_reference,
        )
    )

    if advanced:
        state.no_progress_streak = 0
    else:
        state.no_progress_streak += 1
        log.info("escalation.no_progress", streak=state.no_progress_streak)
