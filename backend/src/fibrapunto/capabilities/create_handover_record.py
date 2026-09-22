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
from fibrapunto.state.models import ConversationState

log = structlog.get_logger(__name__)

CAPABILITY_NAME = "create_handover_record"

DESCRIPTION = (
    "Apre una pratica per un collega umano, con tutto quello che hai già "
    "raccolto. Usala quando il cliente contesta un importo, chiede un rimborso "
    "o uno storno, oppure quando vuole disdire. Prima di usarla devi aver "
    "riassunto la richiesta in una frase e aver ottenuto conferma dal cliente."
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
) -> HandoverResult:
    """Build the payload from typed state, validate it, insert it."""
    if stated_reason:
        state.collected.stated_reason = stated_reason
    if disputed_point:
        state.collected.disputed_point = disputed_point

    route = routing.for_domain(state.current_domain)

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
    values["collected_context"] = json.dumps(context, default=str, ensure_ascii=False)

    reference = await gateway.create_handover_record(principal, values)
    state.collected.handover_reference = reference

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
