"""Deterministic policy wrapped around the classifier (§4, §12.1).

The classifier proposes; this decides. Every rule here is code because each one
is a place where a model's judgement would be worse than an `if`:

  * **Hysteresis.** Switching away from the active domain needs real
    confidence. Without it, one ambiguous turn flaps the conversation into
    another domain and back, and the customer is asked for the contract number
    twice for reasons invisible to them.

  * **The confirmation lock.** This is the highest-value rule in the file. §4.1
    instruction 5 has the agent read a summary back and wait. The customer then
    replies "Yes" — which, classified on its own, is not a billing message. Without
    the lock, that turn routes to fallback and the handover never happens: the
    one step the spec singles out as making the record trustworthy is lost to a
    classification artefact.

  * **An explicit request for a person** is honoured before the domain runs at
    all (§12.1), with no retention attempt.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import structlog

from fibrapunto.domains.registry import DomainKey
from fibrapunto.router.classifier import RoutingDecision
from fibrapunto.state.models import ConversationState

log = structlog.get_logger(__name__)

# Switching away from the active domain requires at least this much confidence.
# Staying put requires none: continuity is the default, change is the exception.
SWITCH_THRESHOLD = 0.60

# Short affirmations and negations. A turn that is only one of these carries no
# topic of its own and must not be classified as though it did.
_ACKNOWLEDGEMENT = re.compile(
    r"^\W*(?:"
    r"y(?:es|eah|ep|up)|no|nope|ok(?:ay)?|sure|correct|right|exactly|"
    r"confirmed|agreed|fine|(?:that(?:'s| is) )?right|go ahead|please do|"
    r"that(?:'s| is) correct|sounds good|perfect"
    r")\W*$",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class RoutingOutcome:
    domain: DomainKey
    switched: bool
    decided_by: str
    decision: RoutingDecision | None


# The answer to a read-back, as opposed to a bare acknowledgement. Looser at the
# end — "Yes, that's correct" and "yes please, go ahead" are both agreement —
# and refused outright if the reply also objects to something.
_AFFIRMATIVE = re.compile(
    r"^\W*(?:y(?:es|eah|ep|up)|correct|exactly|confirmed|agreed|sure|ok(?:ay)?|"
    r"(?:that(?:'s| is) )?(?:right|correct)|go ahead|please do|sounds good|perfect)\b",
    re.IGNORECASE,
)
_OBJECTION = re.compile(r"\b(?:but|not|no|wrong|except|actually|isn't|wasn't)\b", re.IGNORECASE)
_NEGATIVE = re.compile(r"^\W*(?:no|nope|not really|that(?:'s| is) (?:not|wrong)|wrong)\b", re.I)


def is_acknowledgement(text: str) -> bool:
    return bool(_ACKNOWLEDGEMENT.match(text.strip()))


def is_affirmative(text: str) -> bool:
    """Did the customer agree to the summary read back to them?

    Deliberately conservative: anything that also objects ("yes but the amount
    is wrong") is not agreement, and goes back to the model to revise.
    """
    text = text.strip()
    return bool(_AFFIRMATIVE.match(text)) and not _OBJECTION.search(text)


def is_negative(text: str) -> bool:
    return bool(_NEGATIVE.match(text.strip()))


def answers_read_back(text: str) -> bool:
    """A reply that answers the pending summary rather than raising a topic.

    Wider than `is_acknowledgement`: "Yes, that's correct" is not a bare
    acknowledgement, and classified on its own it can land in fallback — where
    the case would be opened under the wrong routing, or not at all.
    """
    return is_acknowledgement(text) or is_affirmative(text) or is_negative(text)


def apply(
    state: ConversationState, message: str, decision: RoutingDecision | None
) -> RoutingOutcome:
    """Resolve the domain for this turn."""
    current = state.current_domain

    # The confirmation lock, checked before anything else — including before the
    # classifier's opinion is consulted at all.
    if (state.pending_confirmation or state.pending_escalation) and answers_read_back(message):
        log.info("router.locked_by_pending_confirmation", domain=current.value)
        return RoutingOutcome(current, False, "confirmation_lock", decision)

    if decision is None:
        return RoutingOutcome(current, False, "no_decision", None)

    proposed = DomainKey(decision.domain)

    # First customer turn: nothing to be sticky about.
    if state.turn_count <= 1:
        return RoutingOutcome(proposed, proposed is not current, "first_turn", decision)

    if proposed is current:
        return RoutingOutcome(current, False, "unchanged", decision)

    if decision.confidence < SWITCH_THRESHOLD:
        log.info(
            "router.switch_suppressed",
            proposed=proposed.value,
            confidence=decision.confidence,
            threshold=SWITCH_THRESHOLD,
        )
        return RoutingOutcome(current, False, "below_switch_threshold", decision)

    log.info("router.switched", **{"from": current.value, "to": proposed.value})
    return RoutingOutcome(proposed, True, "classifier", decision)


def apply_request_kind(
    state: ConversationState, decision: RoutingDecision | None, message: str
) -> None:
    """§12.1's categorical financial requests: the model detects, code acts.

    The first one flagged is kept, with the customer's message as their own
    words (§12.2) — the text here is already redacted (§9). Nothing is weighed
    afterwards: "these are not judgement calls and the agent is not permitted
    to weigh them"."""
    if decision is None or decision.financial_request == "none":
        return
    collected = state.collected
    if collected.financial_request is None:
        collected.financial_request = decision.financial_request
        log.info("router.financial_request", kind=decision.financial_request)
    collected.stated_reason = collected.stated_reason or message
    # The substance of the request — what is disputed, or what they want
    # changed. §13 requires it on a billing record once billing was read.
    collected.disputed_point = collected.disputed_point or message


def apply_sentiment(state: ConversationState, decision: RoutingDecision | None) -> None:
    """§12.1's customer-initiated triggers.

    The model detects; code acts. Detection of distress is a language judgement.
    What happens next — shorter answers, the offer brought forward — is not, and
    is not left to the model to decide it has been sympathetic enough.
    """
    if decision is None:
        return

    if decision.explicit_human_request:
        state.human_requested = True
        state.offer_human_now = True
        state.terse = True
        log.info("router.human_requested")
        return

    if decision.sentiment == "angry":
        state.terse = True
        state.offer_human_now = True
    elif decision.sentiment == "frustrated":
        state.terse = True
