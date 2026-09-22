"""Deterministic policy wrapped around the classifier (§4, §12.1).

The classifier proposes; this decides. Every rule here is code because each one
is a place where a model's judgement would be worse than an `if`:

  * **Hysteresis.** Switching away from the active domain needs real
    confidence. Without it, one ambiguous turn flaps the conversation into
    another domain and back, and the customer is asked for the contract number
    twice for reasons invisible to them.

  * **The confirmation lock.** This is the highest-value rule in the file. §4.1
    instruction 5 has the agent read a summary back and wait. The customer then
    replies "Sì" — which, classified on its own, is not a billing message. Without
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
    r"^\W*(s[iì]+|no|ok(ay)?|va bene|esatto|esattamente|confermo|certo|"
    r"giusto|corretto|perfetto|d'accordo|procedi|prego)\W*$",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class RoutingOutcome:
    domain: DomainKey
    switched: bool
    decided_by: str
    decision: RoutingDecision | None


def is_acknowledgement(text: str) -> bool:
    return bool(_ACKNOWLEDGEMENT.match(text.strip()))


def apply(
    state: ConversationState, message: str, decision: RoutingDecision | None
) -> RoutingOutcome:
    """Resolve the domain for this turn."""
    current = state.current_domain

    # The confirmation lock, checked before anything else — including before the
    # classifier's opinion is consulted at all.
    if state.pending_confirmation and is_acknowledgement(message):
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


def apply_sentiment(state: ConversationState, decision: RoutingDecision | None) -> None:
    """§12.1's customer-initiated triggers.

    The model detects; code acts. Detection of distress is a language judgement.
    What happens next — shorter answers, the offer brought forward — is not, and
    is not left to the model to decide it has been sympathetic enough.
    """
    if decision is None:
        return

    if decision.explicit_human_request:
        state.offer_human_now = True
        state.terse = True
        log.info("router.human_requested")
        return

    if decision.sentiment == "angry":
        state.terse = True
        state.offer_human_now = True
    elif decision.sentiment == "frustrated":
        state.terse = True
