"""Create a handover record — the second capability (§5, §13).

The exit path, and §12.4 governs it:

    "The agent must not state a reference number it did not receive. It says
    plainly that it could not register the request, gives the alternative
    channel, and does not claim that anything has been recorded. This is the
    same principle as §5.1 applied to the exit path: the agent reports what
    happened, not what was supposed to happen."

So the reference in `HandoverCreated` is the one the **database** assigned,
returned by `INSERT ... RETURNING`. It is never constructed here, and there is
no `reference` field on `HandoverFailed` at all.
"""

from __future__ import annotations

import json

import structlog

from fibrapunto.capabilities.outcomes import (
    HandoverCreated,
    HandoverCreatedWithoutContractLink,
    HandoverFailed,
    HandoverResult,
)
from fibrapunto.capabilities.runtime import typed_capability
from fibrapunto.handover import completeness, payload, routing
from fibrapunto.perimeter.gateway import CrmGateway
from fibrapunto.perimeter.policy import Principal
from fibrapunto.settings import get_settings
from fibrapunto.state.models import ConversationState, OpenedCase

log = structlog.get_logger(__name__)

CAPABILITY_NAME = "create_handover_record"

DESCRIPTION = (
    "Open a case for a human colleague, with everything you have already "
    "collected. Use it when the customer disputes an amount, asks for a refund "
    "or a credit, or wants to cancel. Before using it you must have summarised "
    "the request in one sentence and had the customer confirm it."
)


@typed_capability(
    CAPABILITY_NAME,
    on_unexpected=lambda incident_id: HandoverFailed(incident_id=incident_id),
    timeout_s=get_settings().capability_timeout_s,
)
async def create_handover_record(
    *,
    state: ConversationState,
    gateway: CrmGateway,
    principal: Principal,
    subject: str,
    trigger: str,
    stated_reason: str | None = None,
    disputed_point: str | None = None,
    record_type: str | None = None,
) -> HandoverResult:
    """Build the payload from typed state, validate it, insert it.

    The route follows the domain, unless `record_type` names one: an offer
    accepted during a dispute is retention's work, not billing's."""
    route = (
        routing.for_type(record_type) if record_type else routing.for_domain(state.current_domain)
    )

    # Idempotent per conversation **and record type**. One customer request is
    # one case, and this capability is the only thing that can open one — so
    # the guarantee belongs here rather than in a prompt asking the model to
    # call it once. Per type because a dispute and a cancellation are two
    # requests: keyed per conversation alone, the cancellation got the
    # dispute's reference back and no cancellation record was ever written.
    # The database enforces the same key (migration 005) for the concurrent
    # case this in-memory check cannot see.
    #
    # Two ways to reach a second call, and both were observed on the first live
    # run of the dispute case: the model emitted **two tool calls in the same
    # assistant message**, each with its own summary, and both executed. It can
    # equally call again on a later turn after the customer adds something.
    #
    # Returning the existing reference is the honest answer under §12.4 — the
    # record is real, that is its number, and it is the number the customer was
    # already given. Opening a second one would leave the receiving team two
    # cases for one problem, which §13's adoption metric ("the rate at which
    # these records are reassigned or reopened") is precisely designed to catch.
    opened = state.collected.handovers.get(route.record_type)
    if opened:
        log.warning(
            "handover.duplicate_suppressed",
            reference=opened.reference,
            record_type=route.record_type,
            attempted_subject=subject,
        )
        return _existing(opened, route)

    if stated_reason:
        state.collected.stated_reason = stated_reason
    if disputed_point:
        state.collected.disputed_point = disputed_point

    context = payload.build_context(state, trigger)
    contract_linked = False
    customer_id = None
    activation = None

    if state.collected.contract_number:
        # The contract must exist in the CRM to be linked. §6.1's case — present
        # in the CRM, absent from billing — links fine; the reverse does not.
        contract = await gateway.get_contract(principal, state.collected.contract_number)
        if contract:
            contract_linked = True
            customer_id = contract.customer_id
            activation = contract.activation_date

    if route.record_type == "cancellation_request":
        context = payload.add_cancellation_fields(context, state, activation)

    # Validate before inserting. §1 wants completeness at 100%, which is only
    # reachable if a thin record cannot be written in the first place.
    missing = completeness.missing_fields(route.record_type, context, subject)
    if missing:
        log.warning("handover.incomplete", missing=missing, record_type=route.record_type)
        return HandoverFailed(missing_fields=missing)

    values = payload.build_values(state, route, subject, context, customer_id, contract_linked)
    # Serialise the context build_values already scrubbed — never `context`
    # itself. Re-dumping the raw dict here silently undid the redaction, so any
    # value inbound redaction missed or was switched off for reached the CRM.
    values["collected_context"] = json.dumps(
        values["collected_context"], default=str, ensure_ascii=False
    )

    reference = await gateway.create_handover_record(principal, values)
    state.collected.handover_reference = reference
    state.collected.handovers[route.record_type] = OpenedCase(
        reference=reference, contract_linked=contract_linked
    )

    log.info(
        "handover.created",
        reference=reference,
        record_type=route.record_type,
        queue=route.queue,
        contract_linked=contract_linked,
    )

    if contract_linked:
        return HandoverCreated(
            reference=reference,
            queue_label=route.queue_label,
            response_window=route.response_window,
        )

    # §13: "An incomplete record beats no record: the customer has still asked
    # for something, and the company has still been told."
    return HandoverCreatedWithoutContractLink(
        reference=reference,
        queue_label=route.queue_label,
        response_window=route.response_window,
    )


def _existing(opened: OpenedCase, route: routing.Routing) -> HandoverResult:
    """The outcome for a case of this type the conversation already opened.

    The route is this record type's, so the queue label the customer is told
    matches the queue the record is actually in.
    """
    reference = opened.reference
    if opened.contract_linked:
        return HandoverCreated(
            reference=reference,
            queue_label=route.queue_label,
            response_window=route.response_window,
        )
    return HandoverCreatedWithoutContractLink(
        reference=reference,
        queue_label=route.queue_label,
        response_window=route.response_window,
    )
