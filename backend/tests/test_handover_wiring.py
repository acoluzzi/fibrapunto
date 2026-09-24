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
        return REFERENCE

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
