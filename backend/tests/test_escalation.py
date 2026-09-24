"""Escalation as a designed subsystem (§12).

    "Escalation is a designed subsystem, not the behaviour that remains when
    nothing else applies. It has triggers, a payload, a receiving experience,
    and a defined behaviour for when it cannot complete."

All of it is deterministic, so all of it is testable without a model — which is
the point: §12.1's categorical triggers "are not judgement calls and the agent
is not permitted to weigh them", and a rule the model cannot weigh is a rule a
test can pin.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from fibrapunto.capabilities.outcomes import (
    HandoverCreated,
    HandoverCreatedWithoutContractLink,
    HandoverFailed,
)
from fibrapunto.capabilities.transfer_to_human import (
    NoAgentAvailable,
    TransferAccepted,
    transfer_to_human,
    within_service_hours,
)
from fibrapunto.domains.registry import DomainKey
from fibrapunto.escalation import engine, triggers
from fibrapunto.escalation.triggers import TriggerKind
from fibrapunto.state.models import (
    BillingFacts,
    CollectedState,
    ConversationState,
    ConversationStatus,
)

ROME = ZoneInfo("Europe/Rome")


def state(domain: DomainKey = DomainKey.billing, **kwargs) -> ConversationState:
    s = ConversationState(current_domain=domain)
    for key, value in kwargs.items():
        setattr(s, key, value)
    return s


def with_outcome(outcome: str, domain: DomainKey = DomainKey.billing) -> ConversationState:
    from datetime import UTC

    from fibrapunto.capabilities.runtime import CapabilityCall

    s = state(domain)
    s.capability_calls.append(
        CapabilityCall(
            capability="billing_lookup",
            outcome=outcome,
            started_at=datetime.now(UTC),
            duration_ms=10,
        )
    )
    return s


# --- categorical: not judgement calls (§12.1) --------------------------------


def test_every_cancellation_escalates():
    """§12.1: "Cancellation. [...] These are not judgement calls and the agent
    is not permitted to weigh them."

    Not "usually", not "when the customer seems serious". Every one.
    """
    trigger = triggers.evaluate(state(DomainKey.cancellation))
    assert trigger and trigger.kind is TriggerKind.categorical_cancellation


def test_a_calm_polite_cancellation_still_escalates():
    """The trigger does not consult tone. A customer who is pleasant about
    leaving is still leaving."""
    s = state(DomainKey.cancellation, terse=False, turn_count=1)
    s.collected.billing = BillingFacts(status="paid")
    assert triggers.evaluate(s).kind is TriggerKind.categorical_cancellation


# --- conditional (§12.1) -----------------------------------------------------


def test_service_unavailable_escalates():
    """§15 row 1. The figure the customer wanted cannot be produced, so the
    conversation must not continue as though it could."""
    assert triggers.evaluate(with_outcome("serviceUnavailable")).kind is (
        TriggerKind.service_unavailable
    )


def test_a_contract_not_found_escalates():
    """Covers both a typo and §6.1's CRM-only contract. The agent cannot tell
    them apart, which is why neither is resolved by guessing."""
    assert triggers.evaluate(with_outcome("notFound")).kind is TriggerKind.contract_not_found


def test_an_unresolved_disambiguation_escalates_after_one_turn():
    """§12.1: "a multiple match unresolved after one disambiguation turn"."""
    s = state(disambiguation_turns=1)
    assert triggers.evaluate(s).kind is TriggerKind.disambiguation_unresolved


def test_a_resolved_disambiguation_does_not_escalate():
    s = state(disambiguation_turns=1)
    s.collected.contract_number = "FP-2023-048812"
    assert triggers.evaluate(s) is None


def test_a_still_invalid_format_escalates_after_one_reask():
    """§4.1 instruction 2: "ask once more, then offer a human"."""
    assert triggers.evaluate(state(format_reasks=1)).kind is (
        TriggerKind.format_invalid_after_reask
    )


def test_the_turn_budget_escalates_when_nothing_has_succeeded():
    """§12.1's last conditional trigger, and §16's cost argument: "the expensive
    shape is the long conversation that ends in a handover anyway"."""
    s = state(turn_count=7, successful_capability_calls=0)
    assert triggers.evaluate(s).kind is TriggerKind.turn_budget_exhausted


def test_the_turn_budget_does_not_fire_while_work_is_getting_done():
    s = state(turn_count=9, successful_capability_calls=2)
    assert triggers.evaluate(s) is None


# --- no progress: the trigger the spec cares most about ----------------------


def test_no_progress_is_measured_against_state_not_against_the_text():
    """§12.1: "the characteristic failure of a conversational agent is not a
    wrong answer. It is a loop that feels like progress to the system and like
    being trapped to the customer."

    So a turn that learned nothing does not count as progress, however fluent
    and apologetic it was.
    """
    s = state()
    before = s.collected.model_copy(deep=True)

    triggers.update_progress(s, before)
    assert s.no_progress_streak == 1
    assert triggers.evaluate(s) is None, "one stationary turn is not yet a loop"

    triggers.update_progress(s, s.collected.model_copy(deep=True))
    assert s.no_progress_streak == 2
    assert triggers.evaluate(s).kind is TriggerKind.no_progress


def test_learning_a_fact_resets_the_streak():
    s = state(no_progress_streak=1)
    before = s.collected.model_copy(deep=True)
    s.collected.contract_number = "FP-2023-048812"

    triggers.update_progress(s, before)
    assert s.no_progress_streak == 0


def test_a_new_lookup_counts_as_progress_even_when_it_failed():
    """A failed lookup is information: it moves the conversation toward a
    handover. Counting it as stagnation would escalate for the wrong reason and
    hide the real one from §12.5's trigger distribution."""
    s = state()
    before = s.collected.model_copy(deep=True)
    s.collected.lookup_outcomes.append("serviceUnavailable")

    triggers.update_progress(s, before)
    assert s.no_progress_streak == 0


# --- customer-initiated is honoured first (§12.1) ----------------------------


def test_an_explicit_request_for_a_person_outranks_everything():
    """§12.1: "honoured immediately and without resistance or a retention
    attempt." It must not queue behind a categorical trigger."""
    s = state(DomainKey.cancellation, offer_human_now=True)
    trigger = triggers.evaluate(s)
    assert trigger.kind is TriggerKind.human_requested
    assert trigger.immediate is True


def test_the_request_stops_being_pending_once_a_record_exists():
    s = state(offer_human_now=True)
    s.collected.handover_reference = "00001042"
    assert triggers.evaluate(s) is None


# --- §12.4, case 1: no human available ---------------------------------------


@pytest.mark.asyncio
async def test_outside_service_hours_no_agent_is_available():
    """§12.4: "A promise of 'someone will call you shortly' made at 23:40 on a
    Saturday is a worse outcome than an honest 'nobody is available until
    Monday morning'."
    """
    saturday_night = datetime(2026, 9, 19, 23, 40, tzinfo=ROME)
    result = await transfer_to_human(now=saturday_night)

    assert isinstance(result, NoAgentAvailable)
    assert "Monday" in result.service_window or "Friday" in result.service_window
    assert "do not promise" in result.guidance.lower()


@pytest.mark.asyncio
async def test_during_service_hours_the_transfer_happens():
    tuesday_morning = datetime(2026, 9, 22, 10, 0, tzinfo=ROME)
    assert isinstance(await transfer_to_human(now=tuesday_morning), TransferAccepted)


def test_the_service_window_is_a_fact_not_a_sentence():
    """A model asked to be reassuring at 23:40 will be reassuring. The window
    is returned by the capability so the closing message cannot soften it."""
    assert within_service_hours(datetime(2026, 9, 22, 10, 0, tzinfo=ROME))
    assert not within_service_hours(datetime(2026, 9, 19, 23, 40, tzinfo=ROME))
    assert not within_service_hours(datetime(2026, 9, 20, 12, 0, tzinfo=ROME))  # Sunday


# --- §12.4, case 2: the record itself fails ----------------------------------


def test_a_failed_record_produces_no_reference_and_no_claim():
    """§12.4: "It says plainly that it could not register the request, gives the
    alternative channel, and does not claim that anything has been recorded."
    """
    s = state()
    message = engine.closing_message(HandoverFailed(), s)

    assert "wasn't able to register" in message.lower()
    assert "freephone" in message or "customer area" in message
    assert "pratica n." not in message, "a reference was stated that does not exist"
    assert s.status is ConversationStatus.escalated_no_record, (
        "a failed handover must not be counted as handed_over — that would make "
        "a failure look like a success in §1's metrics"
    )


def test_a_created_record_states_the_real_reference():
    s = state()
    message = engine.closing_message(
        HandoverCreated(
            reference="00001042",
            queue_label="a colleague in billing operations",
            response_window="within two working days",
        ),
        s,
    )
    assert "00001042" in message
    assert s.status is ConversationStatus.handed_over


def test_an_unlinked_record_says_so_rather_than_glossing_over_it():
    """§13. The operator will have to ask about the number; the customer should
    hear it from the agent first, not as a surprise on the phone."""
    s = state()
    message = engine.closing_message(
        HandoverCreatedWithoutContractLink(
            reference="00001043",
            queue_label="a colleague",
            response_window="within two working days",
        ),
        s,
    )
    assert "00001043" in message
    assert "confirm the number" in message


# --- the trigger reaches the record (§12.2, §12.5) ---------------------------


def test_the_computed_trigger_is_what_lands_in_the_record():
    """§12.5 reads the trigger distribution to tell an integration problem from
    an agent problem. A field that always says the same thing tells it nothing.
    """
    from fibrapunto.graph.tool_node import _trigger_for

    s = with_outcome("serviceUnavailable")
    # Evaluated from the state at the moment of opening — no value carried
    # over from the previous turn is involved.
    assert s.pending_trigger is None
    assert _trigger_for(s) == "conditional:service_unavailable"


def test_collected_state_copies_cleanly_for_progress_comparison():
    """update_progress compares snapshots; if the copy aliases, every turn looks
    like progress and the no-progress trigger silently never fires."""
    s = state()
    s.collected.contract_number = "FP-2023-048812"
    before = s.collected.model_copy(deep=True)
    s.collected.contract_number = "FP-2021-017350"

    assert before.contract_number == "FP-2023-048812"
    assert isinstance(before, CollectedState)
