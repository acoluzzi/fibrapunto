"""What happens when a trigger fires (§12.2, §12.3, §12.4).

Once escalation is decided, the sentences that carry legal or contractual weight
stop being model output. Reference numbers, response-time commitments and the
registration of a request date are **hand-written templates with slot
filling**, because §16 is explicit about where model variance is acceptable:

    "The reasoning layer handles the input and introduces variance; the typed
    capability contracts and the authorization perimeter are what keep that
    variance from reaching anything that matters."

A response-time promise is something that matters. The model is allowed to read
"I got a text saying my payment was refused"; it is not allowed to invent
what the company has committed to.

§12.4's two failure cases are the reason this module exists at all — both are
failure modes that a design treating escalation as a fallback will not have
considered.
"""

from __future__ import annotations

import re

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

ALTERNATIVE_CHANNEL = "the freephone line 800 123 456 or the customer area on the website"


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
                f"I've opened case no. {result.reference}. "
                f"{_sentence_case(result.queue_label)} will get back to you "
                f"{result.response_window}."
            )
            if state.collected.stated_reason:
                body += " I've recorded the request with today's date."
            return _with_availability(body, state)

        case HandoverCreatedWithoutContractLink():
            # §13: the record exists, but the operator will need to check the
            # number. Saying so is the difference between an honest record and
            # a surprise on the phone tomorrow.
            return _with_availability(
                f"I've opened case no. {result.reference}. "
                f"I couldn't link it to your contract, so {result.queue_label} "
                f"will ask you to confirm the number when they get back to you "
                f"{result.response_window}.",
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
                "I wasn't able to register your request: there's a technical "
                "problem, and I don't want to tell you it's been picked up "
                f"when it hasn't. You can register it through "
                f"{ALTERNATIVE_CHANNEL}. I'm sorry for the trouble."
            )


def _sentence_case(text: str) -> str:
    """ "a colleague in billing operations" opens a sentence in the template."""
    return text[:1].upper() + text[1:]


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

    return f"{body} Please note we're closed at the moment: we're open {service_window_text()}."


_COMMITMENT = re.compile(
    r"\b(?:case|reference|ticket)\s+(?:no\.?|number|#)|\bget\s+back\s+to\s+you"
    r"|\b(?:contact|call|phone|email|reach)\s+you\b|\bbe\s+in\s+touch\b"
    r"|\breach\s+out\b|\bworking\s+days?\b",
    re.IGNORECASE,
)
_SENTENCE = re.compile(r"(?<=[.!?])\s+")


def without_commitments(
    reply: str,
    result: HandoverCreated | HandoverCreatedWithoutContractLink | HandoverFailed,
) -> str:
    """The model's reply, minus the sentences the closing template owns.

    The model is told not to write the case number or the response time; this
    is what makes that true rather than requested. Whole sentences go: one that
    names the reference, the queue, a response time or a promise of contact is
    a commitment, and the templated version is appended right after — keeping
    the model's version would mean two, possibly contradicting, promises.
    """
    markers = [
        getattr(result, "reference", None),
        getattr(result, "queue_label", None),
        getattr(result, "response_window", None),
    ]
    kept = [
        sentence
        for sentence in _SENTENCE.split(reply.strip())
        if sentence
        and not _COMMITMENT.search(sentence)
        and not any(m and m.lower() in sentence.lower() for m in markers)
    ]
    return " ".join(kept)


def close(
    reply: str,
    result: HandoverCreated | HandoverCreatedWithoutContractLink | HandoverFailed,
    state: ConversationState,
) -> str:
    """The reply the customer receives on the turn a case is opened or fails.

    Whatever the model said that is not a commitment, then the template. Also
    where the conversation's status leaves `active`: `closing_message` sets
    handed_over or escalated_no_record, so §1's outcome counts read a status
    the code set rather than one inferred from the text.
    """
    closing = closing_message(result, state)
    body = without_commitments(reply, result)
    return f"{body} {closing}" if body else closing


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
