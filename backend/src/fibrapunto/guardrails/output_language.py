"""The reply is in Italian, and stays there (§10 criterion 3).

    "Italian quality. Not general fluency — specifically: handling of typos and
    dialect, correct register with an older customer, and **not drifting into
    English technical vocabulary**."

The prompts are written in Italian rather than in English with "reply in Italian"
appended, precisely to avoid this drift. This guard is what tells us whether that
worked, rather than assuming it did.
"""

from __future__ import annotations

import re

# English technical vocabulary an Italian customer-service reply should not use.
# Words Italians genuinely say (email, link, online, app, chat, account) are
# deliberately absent: the target is drift, not loanwords.
_ANGLICISMS = frozenset(
    {
        "payment",
        "invoice",
        "billing",
        "refund",
        "chargeback",
        "direct debit",
        "timeout",
        "failed",
        "pending",
        "retry",
        "contract",
        "customer",
        "please",
        "sorry",
        "unfortunately",
        "however",
        "request",
        "issue",
        "support ticket",
        "escalate",
        "provider",
        "outage",
    }
)

_LEI_VIOLATIONS = re.compile(
    r"\b(?:sei|tuo|tua|tuoi|tue|ti\s+(?:posso|mando|invio)|puoi|hai\s+bisogno)\b",
    re.IGNORECASE,
)


def check(reply: str) -> list[str]:
    problems: list[str] = []
    lowered = reply.lower()

    for word in _ANGLICISMS:
        if re.search(rf"\b{re.escape(word)}\b", lowered):
            problems.append(f"anglicismo: {word}")

    # §4 and the base identity require the formal register throughout: an
    # older customer addressed as "tu" is a different kind of error from a
    # wrong figure, but it is still one the operator would notice.
    if _LEI_VIOLATIONS.search(reply):
        problems.append("registro informale: dare del lei")

    return problems
