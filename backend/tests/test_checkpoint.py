"""RuntimeCheckpointV1 round-trips (state/checkpoint.py)."""

from __future__ import annotations

import pytest
from fibrapunto.domains.registry import DomainKey
from fibrapunto.state import checkpoint
from fibrapunto.state.checkpoint import RuntimeCheckpointV1, UnsupportedCheckpoint
from fibrapunto.state.models import (
    ConversationState,
    OpenedCase,
    PendingConfirmation,
    PendingEscalation,
)


def _roundtrip(state: ConversationState, turns=()) -> ConversationState:
    stored = RuntimeCheckpointV1.from_state(state).model_dump(mode="json")
    return checkpoint.load(checkpoint.SCHEMA_VERSION, stored).to_state(list(turns))


def test_an_unconfirmed_read_back_stays_unconfirmed():
    """SCALING.md's warning: a botched round-trip turns a confirmation lock
    into a confirmed case. The lock and its answer must survive exactly."""
    state = ConversationState(current_domain=DomainKey.billing)
    state.pending_confirmation = PendingConfirmation(summary="Disputes €29.90.", asked_at_turn=2)
    restored = _roundtrip(state)
    assert restored.pending_confirmation is not None
    assert restored.pending_confirmation.satisfied is False


def test_the_decision_making_state_survives():
    state = ConversationState(current_domain=DomainKey.cancellation, turn_count=3)
    state.collected.contract_number = "FP-2023-048812"
    state.collected.lookup_outcomes = ["serviceUnavailable"]
    state.collected.handovers["billing_dispute"] = OpenedCase(
        reference="00001042", contract_linked=True
    )
    state.collected.transferred = True
    state.pending_escalation = PendingEscalation(
        trigger="conditional:service_unavailable", asked_at_turn=2
    )
    state.escalations_offered = ["conditional:service_unavailable"]
    state.format_reasks = 1

    restored = _roundtrip(state, [("customer", "hi"), ("agent", "hello")])

    assert restored.id == state.id
    assert restored.current_domain is DomainKey.cancellation
    assert restored.collected.lookup_outcomes == ["serviceUnavailable"]
    assert restored.collected.handovers["billing_dispute"].reference == "00001042"
    assert restored.collected.transferred is True
    assert restored.pending_escalation.trigger == "conditional:service_unavailable"
    assert restored.format_reasks == 1 and restored.turn_count == 3
    assert restored.collected.told_to_customer == ["hello"]


def test_an_unknown_schema_is_refused_not_guessed():
    stored = RuntimeCheckpointV1.from_state(ConversationState()).model_dump(mode="json")
    with pytest.raises(UnsupportedCheckpoint):
        checkpoint.load(99, stored)
    with pytest.raises(UnsupportedCheckpoint):
        checkpoint.load(checkpoint.SCHEMA_VERSION, {**stored, "schema_version": 2})


def test_an_offer_survives_so_it_is_never_made_twice():
    from decimal import Decimal

    from fibrapunto.state.models import OfferFacts, PendingOffer

    state = ConversationState(current_domain=DomainKey.cancellation)
    state.collected.offer = OfferFacts(
        offer_id="loyalty-5-off",
        title="€5 off every month for 12 months",
        description="…",
        monthly_discount_eur=Decimal("5.00"),
        duration_months=12,
        cost_eur=Decimal("60.00"),
    )
    state.collected.offer_status = "presented"
    state.pending_offer = PendingOffer(context="cancellation", asked_at_turn=3)
    state.threatened_to_leave = True

    restored = _roundtrip(state)
    assert restored.collected.offer.offer_id == "loyalty-5-off"
    assert restored.collected.offer_status == "presented"
    assert restored.pending_offer.context == "cancellation"
    assert restored.threatened_to_leave is True
