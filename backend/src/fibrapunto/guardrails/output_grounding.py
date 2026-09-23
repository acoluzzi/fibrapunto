"""Every figure in a reply must be traceable (§1, §15).

This is the guard that converts §1's "no invention: 0" from a measurement into a
structure.

    "Answers containing billing figures while the billing system is unavailable
    or the contract does not exist — target 0."

A metric with a target of zero cannot be reached by prompting. It can be reached
by making the failure impossible: extract every amount, date and contract number
from the reply, and require each one to appear either in a lookup result stored
this conversation, or in something the customer themselves typed. Anything else
is invented, by definition, and is blocked rather than counted.

With the billing stub in `timeout` mode there is no grounded figure at all — so
any figure in the reply fails, which is exactly §15 row 1:

    "States the service cannot be reached, invents no figures or dates, offers a
    human. [...] If the capability leaks an exception instead of a typed outcome,
    the model fills the gap."

Two deliberate exemptions, both narrow:

  * Numbers that are part of ordinary Italian prose ("once", "due giorni")
    are not figures. The guard extracts *typed* values — currency, dates,
    contract numbers — not every digit.
  * The service window and response times come from templates, not from the
    model, so they are grounded by construction.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

from fibrapunto.state.models import ConversationState

# Currency: €29.90 / 29.90 EUR. The decimal comma is still accepted, because a
# customer types whatever their keyboard and habits produce and the *customer's*
# text is one of the two grounding sources — a figure we failed to recognise in
# their message would be reported as invented when they see it repeated back.
_AMOUNT = re.compile(
    r"(?:€\s*)?(\d{1,3}(?:[,\s]\d{3})*[.,]\d{2})(?:\s*(?:€|eur|euros?))?",
    re.IGNORECASE,
)

_CONTRACT = re.compile(r"\bFP-\d{4}-\d{6}\b", re.IGNORECASE)

_MONTHS = {
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
}
_MONTH_NAMES = "|".join(_MONTHS)
# English writes a date both ways round, and an agent will use both: "19
# September" and "September 19". Matching only one would leave the other
# ungrounded-but-unchecked, which is the silent half of a guard failing.
_DATE_DMY = re.compile(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+({_MONTH_NAMES})\b", re.IGNORECASE)
_DATE_MDY = re.compile(rf"\b({_MONTH_NAMES})\s+(\d{{1,2}})(?:st|nd|rd|th)?\b", re.IGNORECASE)
_DATE_NUMERIC = re.compile(r"\b(\d{1,2})[/-](\d{1,2})(?:[/-](\d{2,4}))?\b")


def _text_dates(text: str) -> set[tuple[int, int]]:
    """Every (day, month) written in words, either ordering."""
    found = {(int(day), _MONTHS[name.lower()]) for day, name in _DATE_DMY.findall(text)}
    found |= {(int(day), _MONTHS[name.lower()]) for name, day in _DATE_MDY.findall(text)}
    return found


@dataclass(frozen=True, slots=True)
class Ungrounded:
    kind: str
    value: str

    def __str__(self) -> str:
        return f"{self.kind} {self.value!r}"


def _normalise_amount(raw: str) -> str:
    """29.90 / 29,90 / 1,234.56 / 1.234,56 → a comparable decimal string.

    Both conventions are accepted because both appear: the agent writes
    €29.90, and a customer may type either. The last separator before exactly
    two digits is the decimal one; everything before it is a grouping mark.
    """
    cleaned = raw.replace(" ", "")
    last_comma, last_dot = cleaned.rfind(","), cleaned.rfind(".")
    if last_comma > last_dot:
        cleaned = cleaned.replace(".", "").replace(",", ".")
    else:
        cleaned = cleaned.replace(",", "")
    return cleaned.rstrip("0").rstrip(".") if "." in cleaned else cleaned


def _grounded_amounts(state: ConversationState) -> set[str]:
    grounded: set[str] = set()
    billing = state.collected.billing
    if billing and billing.amount is not None:
        grounded.add(_normalise_amount(str(billing.amount)))
    # Anything the customer said themselves is grounded: repeating a figure
    # back to the person who supplied it is not invention.
    for turn in state.customer_turns():
        for match in _AMOUNT.finditer(turn):
            grounded.add(_normalise_amount(match.group(1)))
    return grounded


def _grounded_dates(state: ConversationState) -> set[tuple[int, int]]:
    """(day, month) pairs the agent may state."""
    grounded: set[tuple[int, int]] = set()
    billing = state.collected.billing
    for value in (
        billing.next_retry_date if billing else None,
        billing.lock_in_until if billing else None,
    ):
        if isinstance(value, date):
            grounded.add((value.day, value.month))
    for turn in state.customer_turns():
        grounded |= _text_dates(turn)
        for day, month, _ in _DATE_NUMERIC.findall(turn):
            grounded.add((int(day), int(month)))
    return grounded


def _grounded_contracts(state: ConversationState) -> set[str]:
    grounded = set()
    if state.collected.contract_number:
        grounded.add(state.collected.contract_number.upper())
    for candidate in state.collected.disambiguation_candidates:
        grounded.add(candidate.contract_number.upper())
    for turn in state.customer_turns():
        grounded.update(m.upper() for m in _CONTRACT.findall(turn))
    return grounded


def check(reply: str, state: ConversationState) -> list[Ungrounded]:
    """Figures in `reply` that nothing supports."""
    problems: list[Ungrounded] = []

    amounts = _grounded_amounts(state)
    for match in _AMOUNT.finditer(reply):
        if _normalise_amount(match.group(1)) not in amounts:
            problems.append(Ungrounded("amount", match.group(1)))

    contracts = _grounded_contracts(state)
    for match in _CONTRACT.finditer(reply):
        if match.group().upper() not in contracts:
            problems.append(Ungrounded("contract number", match.group()))

    dates = _grounded_dates(state)
    for day, month in _text_dates(reply):
        if (day, month) not in dates:
            problems.append(Ungrounded("date", f"{day}/{month}"))
    for day, month, _ in _DATE_NUMERIC.findall(reply):
        if (int(day), int(month)) not in dates:
            problems.append(Ungrounded("date", f"{day}/{month}"))

    return problems
