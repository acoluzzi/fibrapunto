"""Outcome codes never reach the customer (§5.2, §17.6).

    "Outputs are also marked for whether they may be shown to the customer.
    Technical outcome codes are for the reasoning layer and must never be
    surfaced in the conversation."

The check cannot work by withholding values at the tool boundary — the model
*must* see `outcome: "serviceUnavailable"`, that is the entire point of §5.1. So
it is enforced at the output boundary instead.

The blocklist is **derived from the outcome models at import time** rather than
hand-maintained, so a new outcome is covered the moment it is declared instead
of when somebody remembers to update a list.
"""

from __future__ import annotations

import re

from fibrapunto.capabilities import outcomes as outcome_models
from fibrapunto.capabilities.base import internal_fields
from fibrapunto.capabilities.transfer_to_human import TRANSFER_OUTCOMES
from fibrapunto.state.models import ConversationState


def _all_outcome_literals() -> frozenset[str]:
    return outcome_models.BILLING_OUTCOMES | outcome_models.HANDOVER_OUTCOMES | TRANSFER_OUTCOMES


def _all_internal_field_names() -> frozenset[str]:
    names: set[str] = set()
    for attribute in vars(outcome_models).values():
        if isinstance(attribute, type) and hasattr(attribute, "model_fields"):
            names |= internal_fields(attribute)
    return frozenset(names)


OUTCOME_LITERALS = _all_outcome_literals()
INTERNAL_FIELDS = _all_internal_field_names()

# Queue names and record types are internal vocabulary too: §13 gives the
# customer "un collega dell'amministrazione", not "billing_operations".
INTERNAL_VOCABULARY = frozenset(
    {
        "billing_operations",
        "retention",
        "technical_support",
        "billing_dispute",
        "cancellation_request",
        "svc_agent",
        "platform_admin",
    }
)


def check(reply: str, state: ConversationState) -> list[str]:
    """Internal tokens that leaked into customer-facing text."""
    found: list[str] = []
    lowered = reply.lower()

    for literal in OUTCOME_LITERALS:
        # Word-boundary match: "found" must not fire on "profondo".
        if re.search(rf"\b{re.escape(literal.lower())}\b", lowered):
            found.append(literal)

    for name in INTERNAL_FIELDS | INTERNAL_VOCABULARY:
        if re.search(rf"\b{re.escape(name.lower())}\b", lowered):
            found.append(name)

    # An incident id is for the log, never for the customer.
    for call in state.capability_calls:
        if call.incident_id and call.incident_id in reply:
            found.append("incident_id")

    return sorted(set(found))
