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

What counts as a figure lives in `figures.py`, and is deliberately generous:
amounts in digits or words with any currency marker, dates in any order with
or without a year, ISO dates read year-first, and the relative forms that still
assert a date ("the 19th", "end of the month"). A form the extractor misses is
a figure nobody checks, so the list errs long.

Verified facts belong to one contract. `BillingFacts` carries the number it
was fetched for, and the tool node drops them when a lookup concerns another
one — otherwise contract A's €29.90 would ground "contract B's charge of
€29.90" during an outage on B.

Two deliberate exemptions, both narrow:

  * Numbers that are part of ordinary prose ("once", "two working days")
    are not figures. The guard extracts *typed* values — currency, dates,
    contract numbers — not every digit.
  * The service window and response times come from templates, not from the
    model, so they are grounded by construction.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from fibrapunto.guardrails import figures
from fibrapunto.guardrails.figures import DateMention
from fibrapunto.state.models import ConversationState

_CONTRACT = re.compile(r"\bFP-\d{4}-\d{6}\b", re.IGNORECASE)
# What the customer may have typed for the same number: "fp 2023 048812",
# "FP2023048812". The reply's canonical form is grounded by any of them — an
# eval run showed the guard blocking "FP-2023-048812" as invented when the
# customer had written it with spaces, and substituting a reply that said a
# check had failed when none had been made.
_CONTRACT_TYPED = re.compile(r"\bFP[-\s]?(\d{4})[-\s]?(\d{6})\b", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class Ungrounded:
    kind: str
    value: str

    def __str__(self) -> str:
        return f"{self.kind} {self.value!r}"


def _without_contracts(text: str) -> str:
    # FP-2023-048812 must not be read as the month 2023-04 or anything else.
    return _CONTRACT.sub(lambda m: " " * len(m.group()), text)


def _grounded_amounts(state: ConversationState) -> set[Decimal]:
    grounded: set[Decimal] = set()
    billing = state.collected.billing
    if billing and billing.amount is not None:
        grounded.add(billing.amount)
    offer = state.collected.offer
    if offer is not None:
        # The recommendation system's figures, as presented: title and text.
        for text in (offer.title, offer.description):
            grounded.update(a.value for a in figures.amounts(text))
        grounded.update({offer.monthly_discount_eur, offer.cost_eur})
    # Anything the customer said themselves is grounded: repeating a figure
    # back to the person who supplied it is not invention.
    for turn in state.customer_turns():
        clean = figures.date_spans(_without_contracts(turn))
        grounded.update(a.value for a in figures.amounts(clean))
    return grounded


def _grounded_dates(state: ConversationState) -> list[DateMention]:
    """What the agent may state a date from: lookup results and customer words."""
    grounded: list[DateMention] = []
    billing = state.collected.billing
    if billing:
        for value in (billing.next_retry_date, billing.lock_in_until):
            if isinstance(value, date):
                grounded.append(DateMention(value.year, value.month, value.day))
        if billing.invoice_month:
            year, _, month = billing.invoice_month.partition("-")
            if year.isdigit() and month.isdigit():
                grounded.append(DateMention(int(year), int(month)))
    for turn in state.customer_turns():
        for readings in figures.dates(_without_contracts(turn)):
            grounded.extend(readings)
    return grounded


def _is_today(readings: list[DateMention]) -> bool:
    """The reply names today's date, in full.

    Today is a fact of the platform, not a figure the model could invent: the
    cancellation instructions tell the model to say the request is recorded as
    of today, and an eval run showed the guard blocking exactly that sentence.
    Only an explicit day counts — "the end of the month" is not made true by
    the calendar happening to be near it, so relative forms stay ungrounded.
    """
    today = date.today()
    fact = DateMention(today.year, today.month, today.day)
    return any(r.edge is None and r.day is not None and figures.supports(fact, r) for r in readings)


def _grounded_contracts(state: ConversationState) -> set[str]:
    grounded = set()
    if state.collected.contract_number:
        grounded.add(state.collected.contract_number.upper())
    for candidate in state.collected.disambiguation_candidates:
        grounded.add(candidate.contract_number.upper())
    for turn in state.customer_turns():
        grounded.update(f"FP-{year}-{number}" for year, number in _CONTRACT_TYPED.findall(turn))
    return grounded


def check(reply: str, state: ConversationState) -> list[Ungrounded]:
    """Figures in `reply` that nothing supports."""
    problems: list[Ungrounded] = []

    contracts = _grounded_contracts(state)
    for match in _CONTRACT.finditer(reply):
        if match.group().upper() not in contracts:
            problems.append(Ungrounded("contract number", match.group()))

    text = _without_contracts(reply)

    dates = _grounded_dates(state)
    for readings in figures.dates(text):
        if any(figures.supports(v, r) for r in readings for v in dates):
            continue
        if _is_today(readings):
            continue
        problems.append(Ungrounded("date", readings[0].text))

    amounts = _grounded_amounts(state)
    for amount in figures.amounts(figures.date_spans(text)):
        if amount.value not in amounts:
            problems.append(Ungrounded("amount", amount.text))

    return problems
