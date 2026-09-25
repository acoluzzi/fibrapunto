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
    NoAgentAvailable,
    TransferAccepted,
    service_window_text,
    within_service_hours,
)
from fibrapunto.escalation.triggers import Trigger, TriggerKind
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
        case HandoverCreated() if result.existing:
            # A case already open on this contract, from another conversation:
            # the same case, not a second one for the same team.
            return _with_availability(
                f"This is already registered as case no. {result.reference}. "
                f"{_sentence_case(result.queue_label)} will get back to you "
                f"{result.response_window}.",
                state,
            )

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
# A sentence ends at . ! or ? followed by space — but not after "no.", as in
# "case no. 00001042": split there, the number became a sentence of its own
# and survived the filter that removed the rest.
_SENTENCE = re.compile(r"(?<=[.!?])(?<![Nn]o\.)\s+")


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


# --- §12.1: what a fired trigger makes the code do ---------------------------

_OFFER = {
    TriggerKind.service_unavailable: (
        "I can open a case so a colleague picks this up once the billing system "
        "is back. Would you like me to do that?"
    ),
    TriggerKind.contract_not_found: (
        "I can open a case so a colleague can look into the contract with you. "
        "Would you like me to do that?"
    ),
    TriggerKind.disambiguation_unresolved: (
        "I can pass this to a colleague who can check your contract details with "
        "you. Would you like me to open a case?"
    ),
    TriggerKind.format_invalid_after_reask: (
        "I can pass this to a colleague who can find your contract with you. "
        "Would you like me to open a case?"
    ),
    TriggerKind.no_progress: (
        "I don't want to keep you going round in circles: I can pass this to a "
        "colleague. Would you like me to open a case?"
    ),
    TriggerKind.turn_budget_exhausted: (
        "I don't want to keep you going round in circles: I can pass this to a "
        "colleague. Would you like me to open a case?"
    ),
    TriggerKind.distress: (
        "If you'd prefer, I can pass this to a colleague straight away. Would you "
        "like me to open a case?"
    ),
}

_SUBJECT = {
    TriggerKind.service_unavailable: "Billing system unavailable — customer's question unanswered",
    TriggerKind.contract_not_found: "Contract not found in billing",
    TriggerKind.disambiguation_unresolved: "Contract could not be identified among several",
    TriggerKind.format_invalid_after_reask: "No valid contract number after one re-ask",
    TriggerKind.no_progress: "Conversation not progressing — passed to a colleague",
    TriggerKind.turn_budget_exhausted: "Turn budget exhausted without a successful lookup",
    TriggerKind.distress: "Customer upset — asked to be passed to a colleague",
    TriggerKind.human_requested: "Customer asked to speak to a person",
    TriggerKind.categorical_cancellation: "Cancellation request",
    TriggerKind.categorical_payment_change: "Change of payment method requested",
    TriggerKind.verification_failed: "Customer could not be verified in chat",
}

_REQUEST_PHRASE = {
    "dispute": "you're disputing",
    "refund": "you'd like a refund of",
    "credit": "you'd like a credit on",
}

# Sentences in which the model offers, or promises, a colleague or a case.
# Removed when the templated offer goes on, so the customer gets one offer —
# the one the code holds and will act on — rather than two differently worded.
_OFFER_SENTENCE = re.compile(
    r"\b(?:open(?:ing)?\s+(?:a|the)\s+case|colleague|hand(?:ing)?\s+(?:you|this|it)\s+over"
    r"|hand\s*over|put\s+you\s+through|speak\s+(?:to|with)\s+(?:a|someone)|a\s+person)\b",
    re.IGNORECASE,
)
# The question that trails a model's offer once the offer itself is gone:
# "I can open a case for you. Would you like me to do that?" loses its first
# sentence to _OFFER_SENTENCE and the second survives, so the customer read
# "Would you like me to do that? I can open a case … Would you like me to do
# that?" — the same question twice, the first pointing at nothing. Every
# template here ends with its own question; a model question of this shape
# is redundant by construction.
_OFFER_QUESTION = re.compile(
    r"^\W*(?:would|do|shall|should|can|could)\s+(?:you\s+(?:like|want)\s+(?:me|us)\s+to|I)\b.*\?\s*$",
    re.IGNORECASE,
)


def subject_for(kind: TriggerKind | str) -> str:
    """The case subject when code, not the model, opens the case."""
    return _SUBJECT[TriggerKind(kind)]


def offer(reply: str, kind: TriggerKind) -> str:
    """The model's reply, then the offer the code will honour (§4.1 instr. 6)."""
    kept = [
        s
        for s in _SENTENCE.split(reply.strip())
        if s and not _OFFER_SENTENCE.search(s) and not _OFFER_QUESTION.search(s)
    ]
    body = " ".join(kept)
    return f"{body} {_OFFER[kind]}" if body else _OFFER[kind]


def honour_request(
    result: HandoverCreated | HandoverCreatedWithoutContractLink | HandoverFailed,
    state: ConversationState,
    transfer: TransferAccepted | NoAgentAvailable | None = None,
) -> str:
    """§12.1: an explicit request for a person, "honoured immediately and without
    resistance or a retention attempt". No model call, so no retention attempt
    is possible.

    The case is opened first so the colleague has everything (§12.2), then the
    transfer is attempted. Put through if someone is there; if not, the closing
    template states when the service reopens (§12.4) instead of "shortly".
    """
    if isinstance(result, HandoverFailed):
        return closing_message(result, state)
    if isinstance(transfer, TransferAccepted):
        state.status = ConversationStatus.handed_over
        return (
            f"Of course. I'm putting you through to a colleague now — the wait is "
            f"about {transfer.wait_estimate}. I've opened case no. {result.reference}, "
            f"so they'll have everything you've told me."
        )
    return f"Of course, I'll pass this to a colleague. {closing_message(result, state)}"


def dispute_summary(state: ConversationState) -> str:
    """The one-sentence summary for §4.1 instruction 5, built from typed state.

    Figures only from the lookup — never from the model — so the read-back
    cannot state an amount nobody verified (§1 "no invention")."""
    collected = state.collected
    what = _REQUEST_PHRASE.get(collected.financial_request or "", "you're disputing")
    billing = collected.billing
    if billing and billing.amount is not None:
        month = f" {billing.invoice_month}" if billing.invoice_month else ""
        charge = f"the{month} charge of €{billing.amount}"
    else:
        charge = "a charge"
    return f"On contract {collected.contract_number}, {what} {charge}."


def read_back(reply: str, summary: str) -> str:
    """The model's reply, then the read-back the code will act on.

    Any sentence of the model's offering a colleague or a case is dropped, as
    for an escalation offer: one question, the one the code holds."""
    question = (
        "I can't change amounts or issue refunds myself, but I can pass this to "
        f"a colleague in billing with everything collected. To confirm: {summary} "
        "Is that correct?"
    )
    kept = [s for s in _SENTENCE.split(reply.strip()) if s and not _OFFER_SENTENCE.search(s)]
    body = " ".join(kept)
    return f"{body} {question}" if body else question


# --- retention offers (recommendation-system/, §4.2 as amended) --------------


# "I will register your cancellation now" — true a moment later, false while
# the customer is still deciding on the offer that follows it.
_REGISTERING = re.compile(
    r"\b(?:register|record|process|submit|log|go\s+ahead|pass\s+(?:it|this|your))\w*",
    re.IGNORECASE,
)
# The model relaying the platform's mechanics to the customer — "Your case is
# being prepared. Please wait a moment — you may see an offer presented to
# you before the request is finalised." Seen verbatim in an eval run: the
# held-handover outcome told the model an offer would follow, and the model
# told the customer. Anything about the offer is the platform's sentence to
# say, once; anything about preparing or finalising is not a thing to say.
_OFFER_MENTION = re.compile(r"\b(?:offers?|discounts?|promotions?|deals?)\b", re.IGNORECASE)
_PLATFORM_NARRATION = re.compile(
    r"\b(?:being\s+prepared|wait\s+a\s+moment|one\s+moment|finali[sz]e|presented\s+to\s+you"
    r"|the\s+system\s+(?:will|may)|you\s+(?:may|will)\s+see)\b",
    re.IGNORECASE,
)


def offer_message(reply: str, offer, context: str) -> str:
    """The model's reply, then the offer — worded here, from the catalogue.

    Sentences of the model's that offer something, or promise the case, are
    dropped: the customer hears one offer, the recommendation system's, and
    the cancellation is not described as done while they are still deciding.
    """
    if context == "cancellation":
        lead = "Before I register your cancellation, there is an offer available on your contract"
        close = "Would you like to take it? If not, I will go ahead with your request."
    else:
        lead = (
            "I would rather not lose you as a customer. "
            "There is an offer available on your contract"
        )
        close = "Would you like to take it?"
    text = f"{lead}: {offer.title}. {offer.description} {close}"
    kept = [
        s
        for s in _SENTENCE.split(reply.strip())
        if s
        and not _OFFER_SENTENCE.search(s)
        and not _OFFER_QUESTION.search(s)
        and not _OFFER_MENTION.search(s)
        and not _PLATFORM_NARRATION.search(s)
        and not _COMMITMENT.search(s)
        and not (context == "cancellation" and _REGISTERING.search(s))
    ]
    body = " ".join(kept)
    return f"{body} {text}" if body else text


def offer_accepted(offer, result, state: ConversationState) -> str:
    """The customer took the offer. A colleague applies it — the agent never
    does — and the case that carries it is the templated closing."""
    closing = closing_message(result, state)
    if isinstance(result, HandoverFailed):
        return closing
    return (
        f"Thank you — I have noted that you would like to take the offer: {offer.title}. "
        f"A colleague will confirm it and apply it; nothing changes on your bill until "
        f"they do. {closing} You will see their answer here, in this conversation."
    )


def cancellation_after_offer(result, state: ConversationState, unavailable: bool) -> str:
    """The offer was declined, or could no longer be honoured: the request is
    registered, exactly as it would have been without an offer."""
    if isinstance(result, HandoverFailed):
        return closing_message(result, state)  # says plainly nothing was registered
    lead = (
        "I am sorry — that offer is no longer available, so I have gone ahead with your request."
        if unavailable
        else "Understood — I have gone ahead with your request."
    )
    return f"{lead} {closing_message(result, state)}"


OFFER_UNAVAILABLE = "I am sorry — that offer is no longer available."


# --- identity verification (migration 008) -------------------------------------
#
# Written here, not by the model: the model never sees the question's answer,
# and never decides whether the customer is who they say.

VERIFICATION_PLACEHOLDER = "[answer to the security question — not recorded]"
VERIFIED = "Thank you, that's confirmed."


def verification_question(contract: str, question: str) -> str:
    return f"Before I look into contract {contract}, I need to check that it's you. {question}"


def verification_retry(question: str) -> str:
    return f"That doesn't match our records. Please try again: {question}"


def is_verification_exchange(text: str) -> bool:
    """A turn of the security-question exchange: the question, a retry, or
    the placeholder standing for the answer.

    Not conversation the model should continue. Shown the exchange, the model
    copied it — after a wrong answer and then the right one, it asked the
    question again. Recognised by the templates above, so it holds for turns
    rebuilt from storage too."""
    return (
        text == VERIFICATION_PLACEHOLDER
        or text.startswith("That doesn't match our records. Please try again:")
        or (text.startswith("Before I look into contract ") and "check that it's you" in text)
    )


VERIFICATION_LOCKED = (
    "I'm sorry — I couldn't confirm that it's you, so I can't share account details or "
    "register requests for this contract in this chat. A colleague can check your "
    "identity another way. Would you like me to open a case?"
)

VERIFICATION_UNAVAILABLE = (
    "I can't check your identity right now, so I can't share account details or register "
    "requests in this chat. A colleague can help instead. Would you like me to open a case?"
)


def offer_confirmed(title: str, reference: str) -> str:
    """Posted into the customer's conversation when a colleague confirms the
    offer (operator view). Written here, at the colleague's decision — never by
    the model — so it says exactly what was confirmed and nothing more."""
    return (
        f"An update on case no. {reference}: our retention team has confirmed your offer, "
        f"{title}. It applies from your next bill. There is nothing more you need to do."
    )


def offer_declined(title: str, reference: str, reason: str) -> str:
    """Posted when a colleague declines it, with the reason they gave. The
    customer's original request stands, dated as it was made."""
    return (
        f"An update on case no. {reference}: our retention team could not apply the offer "
        f"{title}. Their reason: {reason.strip()} Your original request is still registered "
        f"with the date you made it, and a colleague from the retention team will contact "
        f"you about it."
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
