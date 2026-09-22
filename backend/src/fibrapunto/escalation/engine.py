"""What happens when a trigger fires (§12.2, §12.3, §12.4).

Once escalation is decided, the sentences that carry legal or contractual weight
stop being model output. Reference numbers, response-time commitments and the
registration of a request date are **hand-written Italian templates with slot
filling**, because §16 is explicit about where model variance is acceptable:

    "The reasoning layer handles the input and introduces variance; the typed
    capability contracts and the authorization perimeter are what keep that
    variance from reaching anything that matters."

A response-time promise is something that matters. The model is allowed to read
"mi è arrivato un SMS che dice pagamento respinto"; it is not allowed to invent
what the company has committed to.

§12.4's two failure cases are the reason this module exists at all — both are
failure modes that a design treating escalation as a fallback will not have
considered.
"""

from __future__ import annotations

import structlog

from fibrapunto.capabilities.outcomes import (
    HandoverCreated,
    HandoverCreatedWithoutContractLink,
    HandoverFailed,
)
from fibrapunto.capabilities.transfer_to_human import (
    service_window_text,
    within_service_hours,
)
from fibrapunto.escalation.triggers import Trigger
from fibrapunto.state.models import ConversationState, ConversationStatus

log = structlog.get_logger(__name__)

ALTERNATIVE_CHANNEL = "il numero verde 800 123 456 o l'area clienti sul sito"


def closing_message(
    result: HandoverCreated | HandoverCreatedWithoutContractLink | HandoverFailed,
    state: ConversationState,
) -> str:
    """The sentence the customer is left with.

    Templated rather than generated. Every branch here is a commitment the
    company either can or cannot keep, and §12.4 turns on saying exactly which.
    """
    match result:
        case HandoverCreated():
            body = (
                f"Ho aperto la pratica n. {result.reference}. "
                f"La ricontatterà {result.queue_label} {result.response_window}."
            )
            if state.collected.stated_reason:
                body += " Ho registrato la richiesta con la data di oggi."
            return _with_availability(body, state)

        case HandoverCreatedWithoutContractLink():
            # §13: the record exists, but the operator will need to check the
            # number. Saying so is the difference between an honest record and
            # a surprise on the phone tomorrow.
            return _with_availability(
                f"Ho aperto la pratica n. {result.reference}. "
                f"Non sono riuscito a collegarla al contratto, quindi "
                f"{result.queue_label} le chiederà di verificare il numero "
                f"quando la ricontatterà {result.response_window}.",
                state,
            )

        case HandoverFailed():
            # §12.4: "The agent must not state a reference number it did not
            # receive. It says plainly that it could not register the request,
            # gives the alternative channel, and does not claim that anything
            # has been recorded."
            #
            # No reference is available and none is invented. The status is
            # escalated_no_record, not handed_over, so a failure is never
            # counted as a success in §1's metrics.
            state.status = ConversationStatus.escalated_no_record
            return (
                "Non sono riuscito a registrare la sua richiesta: c'è un "
                "problema tecnico e non voglio dirle che è stata presa in "
                f"carico quando non lo è. Può farla registrare tramite "
                f"{ALTERNATIVE_CHANNEL}. Mi dispiace per il disagio."
            )


def _with_availability(body: str, state: ConversationState) -> str:
    """§12.4's other case: the record exists, but nobody is there.

    "A promise of 'someone will call you shortly' made at 23:40 on a Saturday
    is a worse outcome than an honest 'nobody is available until Monday
    morning'."

    The record is real, so the reference stands. What changes is the promise.
    """
    state.status = ConversationStatus.handed_over

    if within_service_hours():
        return body

    return (
        f"{body} Le segnalo che in questo momento il servizio è chiuso: "
        f"siamo operativi {service_window_text()}."
    )


def note_trigger(state: ConversationState, trigger: Trigger) -> None:
    """Record why this conversation escalated (§12.2, §12.5).

    §12.5 wants the *distribution* of triggers monitored, not just the rate:
    "A rising serviceUnavailable escalation rate is an integration problem
    wearing an agent problem's clothes, and reading it as the latter leads to
    tuning prompts to fix a network timeout."
    """
    log.info(
        "escalation.triggered",
        kind=trigger.kind.value,
        immediate=trigger.immediate,
        domain=state.current_domain.value,
        turn=state.turn_count,
    )
