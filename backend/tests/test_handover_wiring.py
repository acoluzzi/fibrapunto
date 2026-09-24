"""The escalation subsystems, engaged by the live turn loop.

Their earlier tests set `pending_confirmation` or `pending_trigger` by hand and
asserted on what followed: they proved each mechanism works when engaged, and
nothing proved it was ever engaged. Every test here goes through
`Conversation.send`, with a scripted model and the in-process billing stub, and
sets no escalation state itself.
"""

from __future__ import annotations

import json
from datetime import date
from types import SimpleNamespace

from fibrapunto.conversation import Conversation
from fibrapunto.domains.registry import DomainKey
from fibrapunto.integrations.billing_client import BillingClient
from fibrapunto.state.models import ConversationStatus
from langchain_core.messages import AIMessage
from test_conversation_wiring import (  # noqa: F401 — the autouse fixtures
    _billing_in_process,
    _fast_stub,
    routed_to,
    scripted,
    tool_call,
)

CONTRACT = "FP-2023-048812"
REFERENCE = "000123"


class FakeCrm:
    """Records what would have been written. The contract always links."""

    def __init__(self) -> None:
        self.records: list[dict] = []

    async def get_contract(self, principal, number):
        return SimpleNamespace(customer_id=1, activation_date=date(2023, 3, 1))

    async def create_handover_record(self, principal, values):
        self.records.append(values)
        return f"{int(REFERENCE) + len(self.records) - 1:06d}"  # 000123, 000124, …

    def context(self) -> dict:
        return json.loads(self.records[-1]["collected_context"])


def conversation(crm: FakeCrm, test_mode: str | None = None) -> Conversation:
    return Conversation(
        billing_client=BillingClient(
            base_url="http://billing.test", token_provider=lambda: "billing-dev-token"
        ),
        test_mode=test_mode,
        gateway=crm,  # type: ignore[arg-type]
    )


async def _disputed(monkeypatch, crm: FakeCrm) -> Conversation:
    """Two turns in: the amount was looked up and the customer disputes it."""
    routed_to(monkeypatch, "billing")
    convo = conversation(crm)
    scripted(
        monkeypatch,
        [
            tool_call("billing_lookup", {"contract_number": CONTRACT}),
            AIMessage("Your €29.90 direct debit was refused."),
        ],
    )
    await convo.send(f"I got a text, payment refused. {CONTRACT}")

    scripted(
        monkeypatch,
        [
            tool_call(
                "create_handover_record",
                {
                    "summary": "Customer disputes the €29.90 charge; says the plan is €24.90.",
                    "disputed_point": "says their plan is €24.90, not €29.90",
                    "stated_reason": "€29.90 is not right, my plan is €24.90",
                },
            ),
            AIMessage(
                "To confirm: you dispute the €29.90 charge because your plan is "
                "€24.90. Is that right?"
            ),
        ],
    )
    await convo.send("€29.90 is not right, my plan is €24.90. I want the difference refunded.")
    return convo


# --- §4.1 instruction 5: the read-back gate ----------------------------------


async def test_a_dispute_is_not_opened_before_the_customer_confirms(monkeypatch):
    crm = FakeCrm()
    convo = await _disputed(monkeypatch, crm)

    assert crm.records == [], "no case before the customer agreed"
    assert convo.state.collected.handover_reference is None
    pending = convo.state.pending_confirmation
    assert pending is not None and not pending.satisfied
    assert "€24.90" in pending.summary
    outcomes = [c.outcome for c in convo.state.capability_calls]
    assert "awaitingConfirmation" in outcomes


async def test_the_customer_yes_opens_the_case_by_code(monkeypatch):
    crm = FakeCrm()
    convo = await _disputed(monkeypatch, crm)

    # Classified alone, "Yes, that's correct" might be fallback. It must not
    # matter, and the model must not be needed.
    routed_to(monkeypatch, "fallback", confidence=0.99)
    model = scripted(monkeypatch, [])
    reply = await convo.send("Yes, that's correct")

    assert convo.domain is DomainKey.billing
    assert reply.routing.decided_by == "confirmation_lock"
    assert model.calls == [], "the confirmation turn does not call the model"
    assert len(crm.records) == 1
    assert crm.records[0]["subject"].startswith("Customer disputes the €29.90 charge")
    assert crm.context()["disputed_point"] == "says their plan is €24.90, not €29.90"
    assert reply.handover_reference == REFERENCE
    assert f"case no. {REFERENCE}" in reply.text
    assert convo.state.pending_confirmation is None


async def test_a_no_sends_the_summary_back_to_the_model(monkeypatch):
    crm = FakeCrm()
    convo = await _disputed(monkeypatch, crm)

    model = scripted(monkeypatch, [AIMessage("Sorry — what is it about, then?")])
    await convo.send("No, it's about the router fee")

    assert crm.records == []
    assert convo.state.pending_confirmation is None
    assert len(model.calls) == 1


async def test_a_yes_with_an_objection_is_not_agreement(monkeypatch):
    crm = FakeCrm()
    convo = await _disputed(monkeypatch, crm)

    scripted(monkeypatch, [AIMessage("Understood, let me correct that.")])
    await convo.send("yes but the amount is wrong")

    assert crm.records == []
    assert not convo.state.pending_confirmation.satisfied


# --- §12.3: the closing message is the template -------------------------------


async def test_the_model_cannot_write_its_own_promise_of_contact(monkeypatch):
    crm = FakeCrm()
    routed_to(monkeypatch, "billing")
    convo = conversation(crm, test_mode="timeout")
    scripted(monkeypatch, [AIMessage("I can check. What is your contract number?")])
    await convo.send("how much do I owe?")

    scripted(
        monkeypatch,
        [
            tool_call("billing_lookup", {"contract_number": CONTRACT}),
            tool_call("create_handover_record", {"summary": "Billing unavailable; amount due."}),
            # An invented case number would be the promises guard's to catch;
            # this is the part no guard sees — a real-sounding promise of
            # contact the company never made.
            AIMessage(
                "The billing system is not responding, so I can't give you an "
                "amount. Someone will call you back within the hour."
            ),
        ],
    )
    reply = await convo.send(CONTRACT)

    assert reply.handover_reference == REFERENCE
    assert "within the hour" not in reply.text
    assert "not responding" in reply.text, "the model's non-commitment content stays"
    assert f"case no. {REFERENCE}" in reply.text
    assert "within two working days" in reply.text
    assert reply.status == ConversationStatus.handed_over.value


async def test_a_failed_registration_says_so_and_changes_the_status(monkeypatch):
    class DownCrm(FakeCrm):
        async def create_handover_record(self, principal, values):
            raise ConnectionError("CRM unreachable")

    routed_to(monkeypatch, "billing")
    convo = conversation(DownCrm(), test_mode="timeout")
    scripted(monkeypatch, [AIMessage("What is your contract number?")])
    await convo.send("how much do I owe?")

    scripted(
        monkeypatch,
        [
            tool_call("billing_lookup", {"contract_number": CONTRACT}),
            tool_call("create_handover_record", {"summary": "Billing unavailable."}),
            AIMessage("I've opened case 123456 for you."),
        ],
    )
    reply = await convo.send(CONTRACT)

    assert reply.handover_reference is None
    assert "123456" not in reply.text
    assert "wasn't able to register" in reply.text
    assert reply.status == ConversationStatus.escalated_no_record.value


# --- §12.2 / §12.5: the trigger on the record ---------------------------------


async def test_a_case_opened_in_the_outage_turn_records_the_outage(monkeypatch):
    """Evaluated after the reply, this used to record amount_dispute."""
    crm = FakeCrm()
    routed_to(monkeypatch, "billing")
    convo = conversation(crm, test_mode="timeout")
    scripted(monkeypatch, [AIMessage("What is your contract number?")])
    await convo.send("how much do I owe?")

    scripted(
        monkeypatch,
        [
            tool_call("billing_lookup", {"contract_number": CONTRACT}),
            tool_call("create_handover_record", {"summary": "Billing unavailable."}),
            AIMessage("The billing system is not responding."),
        ],
    )
    await convo.send(CONTRACT)

    assert crm.context()["escalation_trigger"] == "conditional:service_unavailable"
    assert len(crm.records) == 1, "an outage case needs no read-back (§4.1 instruction 6)"


async def test_a_confirmed_dispute_records_the_dispute(monkeypatch):
    crm = FakeCrm()
    convo = await _disputed(monkeypatch, crm)
    routed_to(monkeypatch, "billing")
    scripted(monkeypatch, [])
    await convo.send("Yes")
    assert crm.context()["escalation_trigger"] == "categorical:amount_dispute"


# --- first-turn cases (§12.2) --------------------------------------------------


async def test_a_case_opened_on_the_first_message_is_written(monkeypatch):
    """Case 005's shape: one message, billing down, case opened. It used to
    fail validation because nothing had been said to the customer yet."""
    crm = FakeCrm()
    routed_to(monkeypatch, "billing")
    convo = conversation(crm, test_mode="timeout")
    scripted(
        monkeypatch,
        [
            tool_call("billing_lookup", {"contract_number": CONTRACT}),
            tool_call("create_handover_record", {"summary": "Billing unavailable."}),
            AIMessage("The billing system is not responding."),
        ],
    )
    reply = await convo.send(f"payment refused, contract {CONTRACT}, how much do I owe?")

    assert reply.handover_reference == REFERENCE
    told = crm.context()["what_customer_was_told"][0]
    assert "not responding" in told
    assert "within two working days" in told
    assert "within two working days" in reply.text, "the record says what the reply says"


# --- one case per request type (§13) -----------------------------------------


async def test_cancelling_after_a_dispute_opens_a_cancellation_case(monkeypatch):
    """The customer used to be told the retention team would call — about the
    dispute's case number, with no cancellation record anywhere."""
    crm = FakeCrm()
    convo = await _disputed(monkeypatch, crm)
    scripted(monkeypatch, [])
    await convo.send("Yes")
    assert convo.state.collected.handover_reference == REFERENCE

    routed_to(monkeypatch, "cancellation")
    scripted(
        monkeypatch,
        [
            tool_call("create_handover_record", {"summary": "Wants to cancel."}),
            AIMessage("There is no lock-in on your contract."),
        ],
    )
    reply = await convo.send("You know what, forget it. I want to cancel.")

    assert [r["record_type"] for r in crm.records] == ["billing_dispute", "cancellation_request"]
    assert reply.handover_reference == "000124"
    assert "case no. 000124" in reply.text
    assert "retention" in reply.text.lower()


# --- §12.1: triggers enforced by code -----------------------------------------


async def _outage_without_an_offer(monkeypatch, crm: FakeCrm) -> tuple[Conversation, object]:
    """Billing down, and the model neither offers nor opens a case."""
    routed_to(monkeypatch, "billing")
    convo = conversation(crm, test_mode="timeout")
    scripted(monkeypatch, [AIMessage("What is your contract number?")])
    await convo.send("how much do I owe?")
    scripted(
        monkeypatch,
        [
            tool_call("billing_lookup", {"contract_number": CONTRACT}),
            AIMessage("The billing system is not responding right now. Please try again later."),
        ],
    )
    reply = await convo.send(CONTRACT)
    return convo, reply


async def test_an_outage_always_ends_with_an_offer_of_a_colleague(monkeypatch):
    """§4.1 instruction 6. It used to be the model's choice whether to offer."""
    convo, reply = await _outage_without_an_offer(monkeypatch, FakeCrm())

    assert "open a case" in reply.text
    assert convo.state.pending_escalation is not None
    assert convo.state.pending_escalation.trigger == "conditional:service_unavailable"


async def test_yes_to_the_offer_opens_the_case_by_code(monkeypatch):
    crm = FakeCrm()
    convo, _ = await _outage_without_an_offer(monkeypatch, crm)

    routed_to(monkeypatch, "fallback", confidence=0.99)
    model = scripted(monkeypatch, [])
    reply = await convo.send("yes please")

    assert model.calls == [], "the answer to the offer does not need the model"
    assert reply.handover_reference == REFERENCE
    assert crm.context()["escalation_trigger"] == "conditional:service_unavailable"
    assert crm.records[0]["subject"].startswith("Billing system unavailable")
    assert f"case no. {REFERENCE}" in reply.text


async def test_no_to_the_offer_is_respected_and_not_repeated(monkeypatch):
    crm = FakeCrm()
    convo, _ = await _outage_without_an_offer(monkeypatch, crm)

    scripted(monkeypatch, [AIMessage("Understood.")])
    await convo.send("no thanks")
    assert convo.state.pending_escalation is None

    scripted(monkeypatch, [AIMessage("It is still not responding, I'm afraid.")])
    reply = await convo.send("is it working now?")

    assert crm.records == []
    assert "open a case" not in reply.text, "a declined offer is not pressed again"


async def test_the_model_offer_is_replaced_by_the_held_one(monkeypatch):
    """One offer, the one the code will act on — not two differently worded."""
    routed_to(monkeypatch, "billing")
    convo = conversation(FakeCrm(), test_mode="timeout")
    scripted(monkeypatch, [AIMessage("What is your contract number?")])
    await convo.send("how much do I owe?")
    scripted(
        monkeypatch,
        [
            tool_call("billing_lookup", {"contract_number": CONTRACT}),
            AIMessage(
                "The billing system is not responding. Shall I hand this over to "
                "a colleague who will sort it out today?"
            ),
        ],
    )
    reply = await convo.send(CONTRACT)

    assert "sort it out today" not in reply.text
    assert reply.text.count("?") == 1


async def test_a_request_for_a_person_opens_the_case_immediately(monkeypatch):
    crm = FakeCrm()
    routed_to(monkeypatch, "billing", human=True)
    model = scripted(monkeypatch, [AIMessage("Before that, let me try to help.")])
    convo = conversation(crm)

    reply = await convo.send("I want to talk to a person, now")

    assert model.calls == [], "no model call, so no retention attempt"
    assert reply.handover_reference == REFERENCE
    assert crm.context()["escalation_trigger"] == "customer:human_requested"
    assert reply.text.startswith("Of course")


async def test_anger_brings_the_offer_forward_but_opens_nothing(monkeypatch):
    crm = FakeCrm()
    routed_to(monkeypatch, "billing", sentiment="angry")
    scripted(monkeypatch, [AIMessage("I understand. What is your contract number?")])
    convo = conversation(crm)

    reply = await convo.send("this is the third time I'm writing!!")

    assert crm.records == []
    assert convo.state.pending_escalation.trigger == "customer:distress"
    assert "open a case" in reply.text


async def test_an_unanswered_offer_lapses_and_cannot_hijack_a_later_yes(monkeypatch):
    """Demo beats 2 → 3: the outage offer is ignored, the customer asks to
    cancel, and their "yes" to the cancellation must not open the outage case."""
    crm = FakeCrm()
    convo, _ = await _outage_without_an_offer(monkeypatch, crm)

    routed_to(monkeypatch, "cancellation")
    scripted(monkeypatch, [AIMessage("I can help with that. Can you confirm you want to cancel?")])
    await convo.send("You know what, forget it. I want to cancel.")
    assert convo.state.pending_escalation is None

    scripted(
        monkeypatch,
        [
            tool_call("create_handover_record", {"summary": "Wants to cancel."}),
            AIMessage("Done."),
        ],
    )
    await convo.send("Yes, that's correct")

    assert [r["record_type"] for r in crm.records] == ["cancellation_request"]
    assert crm.context()["escalation_trigger"] == "categorical:cancellation"


# --- §1 outcome 1: `resolved` is a state the code checks ----------------------


async def test_a_successful_lookup_with_nothing_pending_is_resolved(monkeypatch):
    routed_to(monkeypatch, "billing")
    convo = conversation(FakeCrm())
    scripted(monkeypatch, [AIMessage("What is your contract number?")])
    first = await convo.send("my payment was refused")
    assert first.status == ConversationStatus.active.value, "asking is not resolving"

    scripted(
        monkeypatch,
        [
            tool_call("billing_lookup", {"contract_number": CONTRACT}),
            AIMessage("Your €29.90 debit was refused; the next attempt is on 19 September."),
        ],
    )
    reply = await convo.send(CONTRACT)
    assert reply.status == ConversationStatus.resolved.value


async def test_waiting_on_an_address_is_not_resolved(monkeypatch):
    routed_to(monkeypatch, "billing")
    convo = conversation(FakeCrm(), test_mode="duplicate")
    scripted(
        monkeypatch,
        [
            tool_call("billing_lookup", {"contract_number": CONTRACT}),
            AIMessage("Which address is it for?"),
        ],
    )
    reply = await convo.send(f"check my bill, {CONTRACT}")
    assert reply.status == ConversationStatus.active.value


async def test_an_outage_with_an_offer_pending_is_not_resolved(monkeypatch):
    _, reply = await _outage_without_an_offer(monkeypatch, FakeCrm())
    assert reply.status == ConversationStatus.active.value


async def test_a_handover_is_final(monkeypatch):
    crm = FakeCrm()
    convo, _ = await _outage_without_an_offer(monkeypatch, crm)
    scripted(monkeypatch, [])
    reply = await convo.send("yes")
    assert reply.status == ConversationStatus.handed_over.value


# --- §12.1 categorical triggers, executed by code -----------------------------
#
# In every test below the model never calls create_handover_record. The case,
# or the read-back that gates it, happens anyway.


def _open_hours(monkeypatch, is_open: bool) -> None:
    monkeypatch.setattr(
        "fibrapunto.capabilities.transfer_to_human.within_service_hours", lambda *_: is_open
    )
    monkeypatch.setattr("fibrapunto.escalation.engine.within_service_hours", lambda *_: is_open)


async def test_a_flagged_dispute_gets_its_read_back_without_the_model(monkeypatch):
    crm = FakeCrm()
    routed_to(monkeypatch, "billing", financial="dispute")
    convo = conversation(crm)
    scripted(
        monkeypatch,
        [
            tool_call("billing_lookup", {"contract_number": CONTRACT}),
            AIMessage("I see a refused charge on your account."),
        ],
    )
    reply = await convo.send(f"€29.90 is wrong, my plan is €24.90. {CONTRACT}")

    assert crm.records == []
    pending = convo.state.pending_confirmation
    assert pending is not None and CONTRACT in pending.summary and "€29.90" in pending.summary
    assert reply.text.endswith("Is that correct?")

    routed_to(monkeypatch, "billing")
    scripted(monkeypatch, [])
    reply = await convo.send("yes")

    assert reply.handover_reference == REFERENCE
    assert crm.context()["escalation_trigger"] == "categorical:amount_dispute"
    assert "€24.90" in crm.context()["disputed_point"], "the customer's own words"


async def test_a_refund_waits_for_the_contract_number_first(monkeypatch):
    """§4.1 instruction 1 still comes first: no read-back about an unknown contract."""
    routed_to(monkeypatch, "billing", financial="refund")
    convo = conversation(FakeCrm())
    scripted(monkeypatch, [AIMessage("I can look into that. What is your contract number?")])
    reply = await convo.send("I want my money back")
    assert convo.state.pending_confirmation is None
    assert "Is that correct?" not in reply.text

    routed_to(monkeypatch, "billing")
    scripted(
        monkeypatch,
        [tool_call("billing_lookup", {"contract_number": CONTRACT}), AIMessage("Thank you.")],
    )
    reply = await convo.send(CONTRACT)
    assert "you'd like a refund of" in reply.text
    assert convo.state.pending_confirmation is not None


async def test_a_payment_method_change_opens_its_case_by_code(monkeypatch):
    crm = FakeCrm()
    routed_to(monkeypatch, "billing", financial="payment_method_change")
    convo = conversation(crm)
    scripted(
        monkeypatch,
        [
            tool_call("billing_lookup", {"contract_number": CONTRACT}),
            AIMessage("I can't change payment details in chat."),
        ],
    )
    reply = await convo.send(f"I want to pay by a different bank account, {CONTRACT}")

    assert reply.handover_reference == REFERENCE
    assert crm.context()["escalation_trigger"] == "categorical:payment_method_change"
    assert crm.records[0]["subject"] == "Change of payment method requested"


async def test_a_cancellation_opens_by_code_if_the_model_never_does(monkeypatch):
    crm = FakeCrm()
    routed_to(monkeypatch, "cancellation")
    convo = conversation(crm)
    scripted(monkeypatch, [AIMessage("Of course. What is your contract number?")])
    await convo.send("i want to close my contract, i've moved house")

    scripted(
        monkeypatch,
        [
            tool_call("billing_lookup", {"contract_number": CONTRACT}),
            AIMessage("There is no lock-in on your contract. May I ask why you're leaving?"),
        ],
    )
    await convo.send(CONTRACT)
    assert crm.records == [], "the agent gets its turn with the contract in hand"

    scripted(monkeypatch, [AIMessage("Thank you for telling me.")])
    reply = await convo.send("I told you, I've moved to another city")

    assert reply.handover_reference == REFERENCE
    assert crm.context()["escalation_trigger"] == "categorical:cancellation"
    assert "moved house" in crm.records[0]["stated_reason"]
    assert "retention" in reply.text.lower()


async def test_a_request_for_a_person_is_transferred_when_someone_is_there(monkeypatch):
    _open_hours(monkeypatch, True)
    routed_to(monkeypatch, "billing", human=True)
    scripted(monkeypatch, [])
    convo = conversation(FakeCrm())

    reply = await convo.send("put me through to a human")

    calls = [(c.capability, c.outcome) for c in convo.state.capability_calls]
    assert ("transfer_to_human", "transferred") in calls, "executed, not offered"
    assert "putting you through" in reply.text
    assert f"case no. {REFERENCE}" in reply.text


async def test_out_of_hours_the_request_states_when_service_reopens(monkeypatch):
    _open_hours(monkeypatch, False)
    routed_to(monkeypatch, "billing", human=True)
    scripted(monkeypatch, [])
    convo = conversation(FakeCrm())

    reply = await convo.send("put me through to a human")

    calls = [(c.capability, c.outcome) for c in convo.state.capability_calls]
    assert ("transfer_to_human", "noAgentAvailable") in calls
    assert "closed at the moment" in reply.text
    assert "putting you through" not in reply.text
