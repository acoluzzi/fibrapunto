"""Commitments the agent is not allowed to make (§11 layer 3, §12.1).

    "No refunds, no payment-method changes, no cancellation execution, no
    offers. Irreversible actions with financial effect stay human."

The capability list already makes these impossible to *do* — there is no tool
that executes a cancellation or issues a refund. This guard covers what remains:
*saying* one has happened, which costs the company just as much and is the only
route left open to a model that wants to be helpful.

§1 measures this as "cancellation discipline: 0 — did it execute **or promise**
a cancellation?" The second half is this file.
"""

from __future__ import annotations

import re

from fibrapunto.state.models import ConversationState

# Each pattern is a claim the company would have to honour.
#
# English needs a wider net than Italian did. Italian marks a completed action
# with a small set of verb forms; English says "I have cancelled", "I've
# cancelled", "your contract has been cancelled", "that's been cancelled" and
# "I went ahead and cancelled". So each entry matches the *aspect* — something
# completed, by us, to their account — rather than one phrasing.
#
# Both spellings of cancelled/canceled, because the model will produce either.
_FORBIDDEN = [
    (
        "cancellation claimed as executed",
        re.compile(
            r"\b(?:i|we)(?:'ve|'ll| have| will)?\s+(?:already\s+|now\s+|just\s+)?"
            r"(?:cancelled|canceled|closed|terminated|deactivated)\b"
            r"|\b(?:i|we)\s+went\s+ahead\s+and\s+(?:cancelled|canceled|closed)\b"
            r"|\b(?:your\s+|the\s+)?(?:contract|service|line|account)\s+(?:has|have)\s+been\s+"
            r"(?:cancelled|canceled|closed|terminated|deactivated)\b"
            r"|\b(?:the\s+|your\s+)?cancellation\s+(?:has\s+been|is|was)\s+"
            r"(?:completed|processed|done|confirmed|effective|registered)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "refund promised",
        re.compile(
            r"\b(?:i|we)(?:'ve| have)?\s+(?:issued|authorised|authorized|arranged|approved)\s+"
            r"(?:a|the|your)?\s*refund\b"
            r"|\b(?:i|we)(?:'ll| will)\s+refund\b"
            r"|\b(?:a|the|your)\s+refund\s+(?:will\s+be|has\s+been|is\s+being)\s+"
            r"(?:issued|processed|credited|paid)\b"
            r"|\byou(?:'ll| will)\s+(?:be\s+refunded|get\s+(?:a|your)\s+refund)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "credit promised",
        re.compile(
            r"\b(?:i|we)(?:'ve| have)?\s+(?:credited|reversed|waived)\b"
            r"|\b(?:the\s+|your\s+)?(?:credit|charge)\s+(?:has\s+been|was)\s+"
            r"(?:applied|reversed|removed|waived)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "payment method changed",
        re.compile(
            r"\b(?:i|we)(?:'ve| have)?\s+(?:changed|updated|switched|amended)\s+"
            r"(?:your\s+)?(?:payment\s+method|payment\s+details|bank\s+details|card|iban)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "retention offer",
        re.compile(
            r"\b(?:i\s+can\s+)?(?:offer|give)\s+you\s+(?:a\s+)?"
            r"(?:discount|promotion|better\s+(?:deal|price|rate))\b"
            r"|\bif\s+you\s+(?:stay|remain)\b.{0,40}\b(?:discount|off|free)\b",
            re.IGNORECASE,
        ),
    ),
]

_REFERENCE = re.compile(r"\b(?:case|reference|ref)\D{0,20}(\d{6,8})\b", re.IGNORECASE)


def check(reply: str, state: ConversationState) -> list[str]:
    """Forbidden commitments, plus any reference number that does not exist."""
    problems = [label for label, pattern in _FORBIDDEN if pattern.search(reply)]

    # §12.4: the agent must not state a reference number it did not receive.
    # The guard is the backstop for the structural rule — the reference comes
    # from the database, so any *other* number presented as one is invented.
    actual = state.collected.handover_reference
    for match in _REFERENCE.finditer(reply):
        if match.group(1) != actual:
            problems.append(f"case number that does not exist: {match.group(1)}")

    return problems
