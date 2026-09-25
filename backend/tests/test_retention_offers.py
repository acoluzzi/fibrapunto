"""Retention offers from the recommendation system (§4.2 as amended).

The agent never composes an offer. Code asks the recommendation system, puts
at most one offer to the customer — before a cancellation is registered, or
when a customer unhappy with a dispute says they will leave — and acts on the
answer. In a cancellation the request is registered whatever the answer.
"""

from __future__ import annotations

from decimal import Decimal

from fibrapunto.conversation import Conversation
from fibrapunto.integrations.billing_client import BillingClient
from fibrapunto.integrations.recommendation_client import RetentionOffer
from langchain_core.messages import AIMessage
from test_conversation_wiring import (  # noqa: F401 — the autouse fixtures
    _billing_in_process,
    _fast_stub,
    routed_to,
    scripted,
    tool_call,
)
from test_handover_wiring import FakeCrm

CONTRACT = "FP-2021-017350"

OFFER = RetentionOffer(
    offer_id="loyalty-5-off",
    title="€5 off every month for 12 months",
    description="Your plan stays exactly as it is, at €5 less each month for the next 12 months.",
    monthly_discount_eur=Decimal("5.00"),
    duration_months=12,
    cost_eur=Decimal("60.00"),
)


class FakeOffers:
    def __init__(self, offer: RetentionOffer | None = OFFER, accept_ok: bool = True) -> None:
        self.offer, self.accept_ok = offer, accept_ok
        self.asked: list[str] = []
        self.accepted: list[str] = []

    async def best_offer(self, contract_number: str):
        self.asked.append(contract_number)
        return self.offer

    async def accept(self, offer, contract_number, conversation_id) -> bool:
        self.accepted.append(offer.offer_id)
        return self.accept_ok


def conversation(crm: FakeCrm, offers: FakeOffers) -> Conversation:
    return Conversation(
        billing_client=BillingClient(
            base_url="http://billing.test", token_provider=lambda: "billing-dev-token"
        ),
        gateway=crm,  # type: ignore[arg-type]
        recommendations=offers,  # type: ignore[arg-type]
    )


async def _cancellation_until_the_offer(monkeypatch, crm, offers) -> tuple[Conversation, object]:
    """The model never calls the tool; code reaches the moment to register."""
    routed_to(monkeypatch, "cancellation")
    convo = conversation(crm, offers)
    scripted(monkeypatch, [AIMessage("Of course. What is your contract number?")])
    await convo.send("i want to close my contract, i've moved house")
    scripted(
        monkeypatch,
        [
            tool_call("billing_lookup", {"contract_number": CONTRACT}),
            AIMessage("There is no lock-in on your contract. May I ask why you are leaving?"),
        ],
    )
    await convo.send(CONTRACT)
    scripted(monkeypatch, [AIMessage("Thank you. I will register your cancellation now.")])
    reply = await convo.send("I found a cheaper operator")
    return convo, reply


async def test_the_offer_comes_before_the_cancellation_is_registered(monkeypatch):
    crm, offers = FakeCrm(), FakeOffers()
    convo, reply = await _cancellation_until_the_offer(monkeypatch, crm, offers)

    assert crm.records == [], "nothing registered while the customer decides"
    assert "€5 off every month for 12 months" in reply.text
    assert reply.text.endswith("If not, I will go ahead with your request.")
    assert "register your cancellation now" not in reply.text, "no contradicting promise"
    assert convo.state.pending_offer.context == "cancellation"
    assert reply.status == "active"


async def test_taking_the_offer_opens_a_retention_case_carrying_it(monkeypatch):
    crm, offers = FakeCrm(), FakeOffers()
    convo, _ = await _cancellation_until_the_offer(monkeypatch, crm, offers)

    routed_to(monkeypatch, "fallback", confidence=0.99)
    model = scripted(monkeypatch, [])
    reply = await convo.send("yes please")

    assert model.calls == [], "the answer is acted on by code"
    assert offers.accepted == ["loyalty-5-off"], "the budget is spent on acceptance"
    assert len(crm.records) == 1
    assert crm.records[0]["subject"] == "Retention offer accepted: €5 off every month for 12 months"
    assert crm.records[0]["record_type"] == "cancellation_request"
    assert crm.context()["retention_offer"] == {
        "offer_id": "loyalty-5-off",
        "title": "€5 off every month for 12 months",
        "status": "accepted",
    }
    assert "A colleague will confirm it and apply it" in reply.text
    # The SQL completeness view requires the customer's own words on a
    # cancellation record; the case was written without them, "Complete: no".
    assert "moved house" in crm.records[0]["stated_reason"]
    assert f"case no. {reply.handover_reference}" in reply.text


async def test_declining_registers_the_cancellation_at_once(monkeypatch):
    crm, offers = FakeCrm(), FakeOffers()
    convo, _ = await _cancellation_until_the_offer(monkeypatch, crm, offers)

    scripted(monkeypatch, [])
    reply = await convo.send("no, just cancel it")

    assert offers.accepted == []
    assert crm.records[0]["subject"] == "Cancellation request"
    assert crm.context()["retention_offer"]["status"] == "declined"
    assert crm.context()["escalation_trigger"] == "categorical:cancellation"
    assert reply.text.startswith("Understood — I have gone ahead with your request.")
    assert "moved house" in crm.records[0]["stated_reason"]


async def test_an_offer_gone_by_acceptance_still_registers_the_request(monkeypatch):
    """The budget ran out between showing and accepting."""
    crm, offers = FakeCrm(), FakeOffers(accept_ok=False)
    convo, _ = await _cancellation_until_the_offer(monkeypatch, crm, offers)

    scripted(monkeypatch, [])
    reply = await convo.send("yes")

    assert crm.records[0]["subject"] == "Cancellation request"
    assert crm.context()["retention_offer"]["status"] == "unavailable"
    assert "no longer available, so I have gone ahead" in reply.text


async def test_the_model_registering_first_is_held_for_the_offer(monkeypatch):
    """The model calls the tool on the turn it has the constraints: the gate
    holds the case, and the offer goes out in that same reply."""
    crm, offers = FakeCrm(), FakeOffers()
    routed_to(monkeypatch, "cancellation")
    convo = conversation(crm, offers)
    scripted(
        monkeypatch,
        [
            tool_call("billing_lookup", {"contract_number": CONTRACT}),
            tool_call("create_handover_record", {"summary": "Wants to cancel."}),
            AIMessage("There is no lock-in on your contract."),
        ],
    )
    reply = await convo.send(f"cancel my contract {CONTRACT}, I'm moving")

    assert crm.records == []
    assert "€5 off every month" in reply.text
    assert convo.state.pending_offer is not None


async def test_no_offer_available_means_no_delay(monkeypatch):
    """Nothing within budget, or the system down: the request opens as before."""
    crm, offers = FakeCrm(), FakeOffers(offer=None)
    convo, reply = await _cancellation_until_the_offer(monkeypatch, crm, offers)

    assert offers.asked == [CONTRACT]
    assert len(crm.records) == 1 and crm.records[0]["record_type"] == "cancellation_request"
    assert convo.state.pending_offer is None


async def test_no_offer_to_a_customer_who_asked_for_a_person(monkeypatch):
    crm, offers = FakeCrm(), FakeOffers()
    routed_to(monkeypatch, "cancellation", human=True)
    convo = conversation(crm, offers)
    scripted(monkeypatch, [])
    await convo.send(f"cancel {CONTRACT}, put me through to a person")
    assert offers.asked == []
    assert convo.state.pending_offer is None


async def test_no_offer_to_an_angry_customer(monkeypatch):
    crm, offers = FakeCrm(), FakeOffers()
    convo = conversation(crm, offers)
    routed_to(monkeypatch, "cancellation", sentiment="angry")
    scripted(monkeypatch, [AIMessage("What is your contract number?")])
    await convo.send("this is ridiculous, I'm cancelling")
    scripted(
        monkeypatch,
        [tool_call("billing_lookup", {"contract_number": CONTRACT}), AIMessage("No lock-in.")],
    )
    await convo.send(CONTRACT)
    scripted(monkeypatch, [AIMessage("Noted.")])
    await convo.send("just do it")
    assert offers.asked == []


# --- the dispute path ------------------------------------------------------------


async def _dispute_with_a_threat(monkeypatch, crm, offers):
    routed_to(monkeypatch, "billing", financial="dispute", leaving=True)
    convo = conversation(crm, offers)
    scripted(
        monkeypatch,
        [
            tool_call("billing_lookup", {"contract_number": CONTRACT}),
            AIMessage("I see a charge of €24.90."),
        ],
    )
    reply = await convo.send(f"€24.90 is wrong, fix it or I'm leaving. {CONTRACT}")
    return convo, reply


async def test_a_threat_to_leave_during_a_dispute_gets_one_offer(monkeypatch):
    crm, offers = FakeCrm(), FakeOffers()
    convo, reply = await _dispute_with_a_threat(monkeypatch, crm, offers)

    assert "I would rather not lose you as a customer" in reply.text
    assert convo.state.pending_offer.context == "dispute"
    assert convo.state.pending_confirmation is None, "one question at a time"


async def test_taking_it_during_a_dispute_goes_to_retention(monkeypatch):
    crm, offers = FakeCrm(), FakeOffers()
    convo, _ = await _dispute_with_a_threat(monkeypatch, crm, offers)

    routed_to(monkeypatch, "billing")
    scripted(monkeypatch, [])
    await convo.send("ok yes")

    assert crm.records[0]["record_type"] == "cancellation_request"
    assert crm.context()["escalation_trigger"] == "customer:threatened_to_leave"


async def test_declining_it_lets_the_dispute_carry_on_and_is_not_asked_again(monkeypatch):
    crm, offers = FakeCrm(), FakeOffers()
    convo, _ = await _dispute_with_a_threat(monkeypatch, crm, offers)

    routed_to(monkeypatch, "billing", financial="dispute", leaving=True)
    scripted(monkeypatch, [AIMessage("Understood.")])
    reply = await convo.send("no, I just want the charge fixed or I'm off")

    assert crm.records == []
    assert convo.state.collected.offer_status == "declined"
    assert offers.asked == [CONTRACT], "one offer per conversation"
    assert reply.text.endswith("Is that correct?"), "the dispute read-back follows"


async def test_a_second_attempt_to_register_in_the_same_turn_is_held_too(monkeypatch):
    """Found live: the gate held the first call, the model called again in its
    next tool round, and the second call registered the cancellation before
    the offer was answered."""
    crm, offers = FakeCrm(), FakeOffers()
    routed_to(monkeypatch, "cancellation")
    convo = conversation(crm, offers)
    scripted(
        monkeypatch,
        [
            tool_call("billing_lookup", {"contract_number": CONTRACT}),
            tool_call("create_handover_record", {"summary": "Wants to cancel."}),
            tool_call("create_handover_record", {"summary": "Wants to cancel, again."}),
            AIMessage("There is no lock-in on your contract."),
        ],
    )
    reply = await convo.send(f"cancel my contract {CONTRACT}, I'm moving")

    assert crm.records == [], "nothing registered while the offer is pending"
    assert reply.handover_reference is None
    assert "€5 off every month" in reply.text


def test_a_case_number_is_not_split_into_a_sentence_of_its_own():
    from fibrapunto.capabilities.outcomes import HandoverCreated
    from fibrapunto.escalation import engine

    result = HandoverCreated(
        reference="00001042", queue_label="a colleague", response_window="within a day"
    )
    kept = engine.without_commitments(
        "There is no lock-in. I've opened case no. 00001042. Anything else?", result
    )
    assert "00001042" not in kept
    assert kept == "There is no lock-in. Anything else?"
