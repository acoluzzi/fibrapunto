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
_FORBIDDEN = [
    (
        "disdetta eseguita",
        re.compile(
            r"\b(?:ho|abbiamo)\s+(?:disdett|chius|cessat|annullat|disattivat)\w*"
            r"|\bla disdetta (?:è|e')\s+stat\w+\s+(?:effettuat|complet|attiv|registrat)\w*"
            r"|\bcontratto (?:è|e')\s+stato\s+(?:chiuso|cessato|disdetto)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "rimborso promesso",
        re.compile(
            r"\b(?:le|il|un)\s+rimbors\w+\s+(?:sarà|verrà|è stato)\b"
            r"|\bho\s+(?:disposto|autorizzato|emesso)\s+\w*\s*rimbors\w*"
            r"|\ble rimborser\w+\b|\bprovvedo al rimborso\b",
            re.IGNORECASE,
        ),
    ),
    (
        "storno promesso",
        re.compile(
            r"\bho\s+(?:stornat|accreditat)\w*|\b(?:storno|accredito)\s+"
            r"(?:è|e')\s+stat\w+\s+(?:effettuat|applicat)\w*",
            re.IGNORECASE,
        ),
    ),
    (
        "metodo di pagamento modificato",
        re.compile(
            r"\bho\s+(?:modificat|cambiat|aggiornat)\w*\s+(?:il|la|i)\s*"
            r"(?:metodo|modalità|coordinate|iban|carta)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "offerta di retention",
        re.compile(
            r"\b(?:le\s+)?(?:posso\s+)?(?:offrir|propor)\w*\s+"
            r"(?:uno\s+)?(?:sconto|promozione|offerta)\b"
            r"|\bse (?:resta|rimane)\s+.{0,30}\bsconto\b",
            re.IGNORECASE,
        ),
    ),
]

_REFERENCE = re.compile(r"\b(?:pratica|protocollo)\D{0,20}(\d{6,8})\b", re.IGNORECASE)


def check(reply: str, state: ConversationState) -> list[str]:
    """Forbidden commitments, plus any reference number that does not exist."""
    problems = [label for label, pattern in _FORBIDDEN if pattern.search(reply)]

    # §12.4: the agent must not state a reference number it did not receive.
    # The guard is the backstop for the structural rule — the reference comes
    # from the database, so any *other* number presented as one is invented.
    actual = state.collected.handover_reference
    for match in _REFERENCE.finditer(reply):
        if match.group(1) != actual:
            problems.append(f"numero di pratica inesistente: {match.group(1)}")

    return problems
