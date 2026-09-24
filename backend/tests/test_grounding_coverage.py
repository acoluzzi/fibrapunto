"""The grounding guard's blind spots, closed one by one.

Each test here was a way through the guard: a figure it did not recognise, a
date it read the wrong way round, a year it never looked at, or a verified
figure that outlived the contract it belonged to. A figure the guard does not
see is not blocked and not counted, so these are the failures that would never
have shown up in a report.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from fibrapunto.capabilities.outcomes import BillingFound, BillingServiceUnavailable
from fibrapunto.domains.registry import DomainKey
from fibrapunto.graph.tool_node import ToolDispatcher
from fibrapunto.guardrails import output_grounding
from fibrapunto.handover.payload import build_context
from fibrapunto.state.models import BillingFacts, ConversationState, Turn, TurnRole

CONTRACT_A = "FP-2023-048812"
CONTRACT_B = "FP-2021-017350"


def verified() -> ConversationState:
    """€29.90, retry on 19 September 2026, invoice for September 2026."""
    state = ConversationState(current_domain=DomainKey.billing)
    state.collected.contract_number = CONTRACT_A
    state.collected.billing = BillingFacts(
        contract_number=CONTRACT_A,
        status="failed",
        amount=Decimal("29.90"),
        invoice_month="2026-09",
        next_retry_date=date(2026, 9, 19),
    )
    state.turns.append(Turn(role=TurnRole.customer, text=f"contract {CONTRACT_A}, payment refused"))
    return state


def nothing_verified() -> ConversationState:
    state = ConversationState(current_domain=DomainKey.billing)
    state.turns.append(Turn(role=TurnRole.customer, text="how much do I owe?"))
    return state


def problems(reply: str, state: ConversationState) -> list[str]:
    return [str(p) for p in output_grounding.check(reply, state)]


# --- amounts in every spelling -----------------------------------------------


@pytest.mark.parametrize(
    "reply",
    [
        "You owe 30 euros.",
        "You owe €30.",
        "You owe 30€.",
        "You owe EUR 30.",
        "You owe thirty euros.",
        "You owe twenty-nine euros and ninety cents.",
        "You owe 29 euros and 90 cents.",
        "The outstanding amount is 29.90.",
        "The charge is €1,234.56.",
        "There is a fee of ninety cents.",
    ],
)
def test_an_amount_in_any_spelling_is_a_figure(reply):
    assert problems(reply, nothing_verified()), reply


@pytest.mark.parametrize(
    "reply",
    [
        "The charge of €29.90 was refused.",
        "The charge of 29,90 € was refused.",
        "The charge of twenty-nine euros ninety was refused.",
        "The charge of 29 euros and 90 cents was refused.",
    ],
)
def test_the_verified_amount_passes_in_any_spelling(reply):
    assert problems(reply, verified()) == []


def test_a_rounded_amount_is_not_the_verified_one():
    """€30 is not €29.90. "About thirty" is a figure the customer will quote."""
    assert problems("You owe about €30.", verified())


# --- dates -------------------------------------------------------------------


@pytest.mark.parametrize(
    "reply",
    [
        "The next attempt is on Sep 19.",
        "The next attempt is on Sept. 19th.",
        "The next attempt is on the 19th.",
        "The next attempt is at the end of the month.",
        "The next attempt is in mid-September.",
        "The next attempt is on 2026-09-19.",
        "The next attempt is on 19/09/2026.",
    ],
)
def test_a_date_in_any_form_is_a_figure(reply):
    assert problems(reply, nothing_verified()), reply


@pytest.mark.parametrize(
    "reply",
    [
        "The next attempt is on 19 September.",
        "The next attempt is on September 19, 2026.",
        "The next attempt is on Sep 19.",
        "The next attempt is on the 19th.",
        "The next attempt is in mid-September.",
        "This is your September 2026 invoice.",
        "Invoice month: 2026-09.",
    ],
)
def test_the_verified_date_passes_in_any_form(reply):
    assert problems(reply, verified()) == []


def test_iso_dates_are_read_year_first():
    """The tool result says 2026-09-19. Echoing it is correct, and used to be
    blocked: the old pattern read "09-19" out of it as day 9 of month 19."""
    assert problems("Next attempt: 2026-09-19.", verified()) == []


def test_the_year_is_checked():
    assert problems("The next attempt is on 19 September 2025.", verified())
    assert problems("The next attempt is on 19 September 2026.", verified()) == []


def test_a_date_more_specific_than_its_source_is_invented():
    """The customer said "the 19th". Adding a month they never named is the
    agent supplying a fact."""
    state = nothing_verified()
    state.turns.append(Turn(role=TurnRole.customer, text="they said they'd retry on the 19th"))
    assert problems("Yes, on the 19th.", state) == []
    assert problems("Yes, on 19 September.", state)


def test_a_month_edge_must_match_the_verified_day():
    """19 September is the middle of the month, not its start or its end."""
    assert problems("The next attempt is at the start of next month.", verified())
    assert problems("The next attempt is at the end of the month.", verified())


def test_prose_is_still_not_a_figure():
    reply = (
        "A colleague will get back to you within two working days, Monday to "
        "Friday, 9:00 to 18:00. You have 2 instalments left. It is the first "
        "time this has happened, and you may pay by card."
    )
    assert problems(reply, verified()) == []


# --- stale facts across contracts --------------------------------------------


def _found(amount: str) -> BillingFound:
    return BillingFound(
        status="failed",
        failure_reason="insufficient funds",
        amount=Decimal(amount),
        invoice_month="2026-09",
        next_retry_date=date(2026, 9, 19),
        payment_link=None,
        lock_in_until=None,
        remaining_instalments=0,
        source_system="billing",
        fetched_at="2026-09-01T00:00:00Z",
    )


def _absorb(state: ConversationState, result, contract: str) -> None:
    ToolDispatcher(billing_client=None)._absorb_billing(  # type: ignore[arg-type]  # noqa: SLF001
        result, {"contract_number": contract}, state
    )


def test_contract_a_figures_do_not_ground_a_reply_about_contract_b():
    """A looked up (€29.90), then B during an outage. "B's charge of €29.90"
    used to pass: A's amount was never cleared."""
    state = nothing_verified()
    state.turns.append(Turn(role=TurnRole.customer, text=f"{CONTRACT_A} and also {CONTRACT_B}"))
    _absorb(state, _found("29.90"), CONTRACT_A)
    assert problems("The charge of €29.90 was refused.", state) == []

    _absorb(state, BillingServiceUnavailable(incident_id="x"), CONTRACT_B)

    assert state.collected.billing is None
    assert state.collected.contract_number == CONTRACT_B
    assert problems(f"The charge on {CONTRACT_B} is €29.90.", state)


def test_contract_a_amount_does_not_reach_contract_b_handover_record():
    state = nothing_verified()
    _absorb(state, _found("29.90"), CONTRACT_A)
    _absorb(state, BillingServiceUnavailable(incident_id="x"), CONTRACT_B)
    assert "amount_eur" not in build_context(state, trigger="billing_unavailable")


def test_a_failed_retry_on_the_same_contract_keeps_its_figures():
    """The facts about A are still verified facts about A."""
    state = nothing_verified()
    _absorb(state, _found("29.90"), CONTRACT_A)
    _absorb(state, BillingServiceUnavailable(incident_id="x"), CONTRACT_A)
    assert state.collected.billing is not None
    assert state.collected.billing.amount == Decimal("29.90")


def test_a_lookup_for_b_replaces_a_figures():
    state = nothing_verified()
    _absorb(state, _found("29.90"), CONTRACT_A)
    _absorb(state, _found("44.90"), CONTRACT_B)
    assert state.collected.billing.contract_number == CONTRACT_B
    assert state.collected.billing.amount == Decimal("44.90")
