"""Build the handover payload from typed state (§12.2, §13).

    "The receiving human must never have to ask the customer something the
    customer has already said."

The payload is assembled from `CollectedState`, not from a model call. The model
contributes two things — the one-sentence Italian subject and the customer's
stated reason in their own words — and nothing else. Everything factual comes
from what the capabilities actually returned.

That is the whole reason §1's completeness target is reachable: a missing field
is a build-time condition, checked before the insert, rather than a thin record
the receiving team discovers tomorrow.

`what_customer_was_told` gets its own key because §12.2 names it: "the field
most often left out and the one that most often causes a second complaint."
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

from fibrapunto.handover import routing
from fibrapunto.handover.routing import Routing
from fibrapunto.privacy import redaction
from fibrapunto.state.models import ConversationState


def build_context(state: ConversationState, trigger: str) -> dict[str, Any]:
    """The `collected_context` JSONB column.

    Named keys rather than prose: §1 measures "records carrying every required
    field", and with free text "complete" degrades to "long enough", which makes
    the metric unfalsifiable.
    """
    collected = state.collected
    billing = collected.billing

    context: dict[str, Any] = {
        # §12.2: which lookups succeeded and which failed. Outcome codes, not
        # the customer's billing data copied into a case record.
        "lookups": [
            {"capability": c.capability, "outcome": c.outcome, "ms": c.duration_ms}
            for c in state.capability_calls
        ],
        "escalation_trigger": trigger,
        # §12.2: "what the agent already told the customer, so the human does
        # not contradict it."
        "what_customer_was_told": collected.told_to_customer or _told_this_turn(state, trigger),
        "conversation_id": str(state.id),
        "turn_count": state.turn_count,
        "opened_at": datetime.now(UTC).isoformat(),
    }

    if billing:
        context["billing_status"] = billing.status
        if billing.amount is not None:
            context["amount_eur"] = str(billing.amount)
        if billing.invoice_month:
            context["invoice_month"] = billing.invoice_month
        if billing.failure_reason:
            context["rejection_reason"] = billing.failure_reason
        context["constraints"] = {
            "lock_in_until": billing.lock_in_until.isoformat() if billing.lock_in_until else None,
            "remaining_instalments": billing.remaining_instalments or 0,
        }
    else:
        # The lookup did not succeed. Say so in the record rather than leaving
        # a gap the receiving operator has to interpret — and §5.1's principle
        # applied to the exit path: report what happened, not what was
        # supposed to.
        context["billing_status"] = "not available"
        context["constraints"] = None

    if collected.disputed_point:
        context["disputed_point"] = collected.disputed_point

    if collected.sensitive_data_seen:
        # §9: never write the value into a record. The *kind* is recorded so
        # the operator knows to tell the customer not to send it again.
        context["sensitive_data_seen"] = collected.sensitive_data_seen

    return context


# Why the case was opened, in the words of what the customer hears. Keyed by
# trigger value, as recorded in `escalation_trigger`.
_TOLD_BECAUSE = {
    "conditional:service_unavailable": (
        "The billing system was not responding, so no amount or date could be given."
    ),
    "conditional:contract_not_found": "No contract with that number was found in billing.",
    "customer:human_requested": "They asked for a person, and are being passed to a colleague.",
    "customer:distress": "They are being passed to a colleague.",
    "categorical:cancellation": "Their cancellation request is being passed on.",
}


def _told_this_turn(state: ConversationState, trigger: str) -> list[str]:
    """What the customer is told on a turn that opens a case before the agent
    has said anything else — the customer's first turn.

    `told_to_customer` is filled after each reply, so on the first turn it is
    empty, and a required-but-empty field made every first-turn case fail
    validation: no record, the company never told (§12.4's worst branch).

    Not a guess at the model's wording. The closing sentence is a template the
    turn loop always sends when a case opens (escalation/engine.py), so what it
    says is known before it is sent; the reason comes from the trigger the case
    records. Marked as system-recorded so the operator can tell it from a
    transcript line.
    """
    route = routing.for_domain(state.current_domain)
    lines = ["[recorded by the system] Case opened on the customer's first message."]
    if trigger in _TOLD_BECAUSE:
        lines.append(_TOLD_BECAUSE[trigger])
    lines.append(
        f"Given the case number, and told {route.queue_label} will get back to them "
        f"{route.response_window}."
    )
    return [" ".join(lines)]


def add_cancellation_fields(
    context: dict[str, Any], state: ConversationState, activation: date | None
) -> dict[str, Any]:
    """§4.2 and §13's cancellation columns.

    "Recording the request date is not a courtesy: notice periods run from it,
    and a customer who is told 'your request is registered as of today' has
    something the company can be held to."
    """
    context["request_date"] = date.today().isoformat()
    context["tenure"] = (
        f"customer since {activation.isoformat()}" if activation else "not available"
    )
    if state.collected.billing is None:
        # §4.2 instruction 3 requires stating the constraints. If they could not
        # be read, the record must say so — silence here reads to the operator
        # as "no constraints", which is a different and wrong statement.
        context["constraints"] = "not verifiable: the system did not respond"
    return context


def build_values(
    state: ConversationState,
    routing: Routing,
    subject: str,
    context: dict[str, Any],
    customer_id: int | None,
    contract_linked: bool,
) -> dict[str, Any]:
    """The INSERT column map.

    `reference` and `handled_by_agent` are absent on purpose: the agent has no
    INSERT grant on either. The reference comes from a sequence it may only
    USAGE (§12.4) and the agent-handled flag defaults true (§13). Both are the
    database's to decide, not ours.

    Every free-text field is scrubbed here rather than at each call site (§9:
    "never write it into a handover record or any other stored field"). This is
    the single chokepoint for the INSERT, so it is the only place where the
    guarantee holds regardless of which path assembled the values.
    """
    return {
        "record_type": routing.record_type,
        "priority": routing.priority,
        "queue": routing.queue,
        "contract_number": state.collected.contract_number if contract_linked else None,
        "claimed_contract_number": state.collected.contract_number,
        "contract_link_missing": not contract_linked,
        "customer_id": customer_id if contract_linked else None,
        "subject": redaction.for_handover(subject),
        "collected_context": redaction.for_handover(context),
        "stated_reason": redaction.for_handover(state.collected.stated_reason),
        "origin_session_id": state.id,
    }
