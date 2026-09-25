"""Verifying the customer before anything about their account is shared or
done (migration 008).

A contract number is printed on every invoice. Before billing figures, cases,
offers or case history, code asks a personal security question, checks the
answer in the database, and never records the answer or shows it to the model.
"""

from __future__ import annotations

from fibrapunto.conversation import Conversation
from fibrapunto.integrations.billing_client import BillingClient
from langchain_core.messages import AIMessage
from test_conversation_wiring import (  # noqa: F401 — the autouse fixtures
    _billing_in_process,
    _fast_stub,
    routed_to,
    scripted,
    tool_call,
)
from test_handover_wiring import FakeCrm

CONTRACT = "FP-2023-048812"
QUESTION = ("first_pet", "What was the name of your first pet?", "Birba")


class SecureCrm(FakeCrm):
    def __init__(self, **kwargs) -> None:
        super().__init__()
        self.security = {CONTRACT: QUESTION}
        self.cases = kwargs.get("cases", [])


def conversation(crm: FakeCrm) -> Conversation:
    return Conversation(
        billing_client=BillingClient(
            base_url="http://billing.test", token_provider=lambda: "billing-dev-token"
        ),
        gateway=crm,  # type: ignore[arg-type]
    )


async def _asked(monkeypatch, crm=None) -> tuple[Conversation, object, object]:
    routed_to(monkeypatch, "billing")
    convo = conversation(crm or SecureCrm())
    model = scripted(monkeypatch, [AIMessage("Your €29.90 debit was refused.")])
    reply = await convo.send(f"payment refused, {CONTRACT}")
    return convo, reply, model


async def test_the_question_comes_before_anything_about_the_account(monkeypatch):
    convo, reply, model = await _asked(monkeypatch)

    assert model.calls == [], "no model, no lookup, no figure before verification"
    assert reply.text == (
        f"Before I look into contract {CONTRACT}, I need to check that it's you. "
        "What was the name of your first pet?"
    )
    assert convo.state.capability_calls == []
    assert reply.status == "active"


async def test_the_right_answer_verifies_and_the_request_carries_on(monkeypatch):
    convo, _, _ = await _asked(monkeypatch)
    model = scripted(
        monkeypatch,
        [
            tool_call("billing_lookup", {"contract_number": CONTRACT}),
            AIMessage("Your €29.90 debit was refused; the next attempt is on 19 September."),
        ],
    )
    reply = await convo.send("  birba ")

    assert reply.text.startswith("Thank you, that's confirmed. Your €29.90 debit")
    assert convo.state.collected.verification[CONTRACT] == "verified"
    assert convo.state.collected.billing is not None, "the lookup is allowed now"
    prompt = "\n".join(str(m.content) for m in model.calls[0])
    assert "birba" not in prompt.lower(), "the model never sees the answer"
    assert all("birba" not in t.text.lower() for t in convo.state.turns), "never recorded"


async def test_three_wrong_answers_lock_the_contract_and_offer_a_colleague(monkeypatch):
    convo, _, _ = await _asked(monkeypatch)
    model = scripted(monkeypatch, [])
    first = await convo.send("Rex")
    assert first.text.startswith("That doesn't match our records. Please try again:")
    await convo.send("Fluffy")
    locked = await convo.send("Max")

    assert model.calls == []
    assert "couldn't confirm that it's you" in locked.text
    assert convo.state.collected.verification[CONTRACT] == "locked"
    assert convo.state.pending_escalation.trigger == "conditional:verification_failed"

    # A colleague, yes: the case opens even though the customer is unverified.
    reply = await convo.send("yes please")
    assert reply.handover_reference is not None


async def test_a_locked_contract_stays_locked_in_the_conversation(monkeypatch):
    convo, _, _ = await _asked(monkeypatch)
    scripted(monkeypatch, [])
    for wrong in ("a", "b", "c"):
        await convo.send(wrong)
    await convo.send("no thanks")
    reply = await convo.send(f"try again with {CONTRACT}")
    assert "couldn't confirm that it's you" in reply.text


async def test_the_crm_being_down_fails_closed(monkeypatch):
    class DownCrm(SecureCrm):
        async def security_question(self, principal, contract):
            raise ConnectionError("CRM unreachable")

    routed_to(monkeypatch, "billing")
    convo = conversation(DownCrm())
    model = scripted(monkeypatch, [AIMessage("Your €29.90 debit was refused.")])
    reply = await convo.send(f"payment refused, {CONTRACT}")

    assert model.calls == []
    assert "can't check your identity right now" in reply.text


async def test_a_contract_the_crm_does_not_know_is_not_challenged(monkeypatch):
    """Nobody's account to protect: the lookup proceeds and finds nothing."""
    routed_to(monkeypatch, "billing")
    convo = conversation(SecureCrm())
    scripted(
        monkeypatch,
        [
            tool_call("billing_lookup", {"contract_number": "FP-2099-999999"}),
            AIMessage("I can't find that contract."),
        ],
    )
    reply = await convo.send("my bill, contract FP-2099-999999")
    assert "need to check that it's you" not in reply.text
    assert convo.state.collected.verification["FP-2099-999999"] == "unknown"


async def test_a_request_for_a_person_is_still_honoured_at_once(monkeypatch):
    """§12.1: honoured immediately. The colleague verifies the customer."""
    routed_to(monkeypatch, "billing", human=True)
    convo = conversation(SecureCrm())
    scripted(monkeypatch, [])
    reply = await convo.send(f"put me through to a person, {CONTRACT}")
    assert reply.handover_reference is not None
    assert "need to check that it's you" not in reply.text


async def test_once_verified_the_agent_knows_the_existing_cases(monkeypatch):
    from datetime import datetime

    crm = SecureCrm(
        cases=[
            {
                "reference": "00001042",
                "record_type": "cancellation_request",
                "status": "in_progress",
                "created_at": datetime(2026, 9, 25),
                "offer": {
                    "title": "€5 off every month for 12 months",
                    "status": "declined_by_team",
                    "decision_reason": "Only for customers of 2+ years.",
                },
            }
        ]
    )
    convo, _, _ = await _asked(monkeypatch, crm)
    model = scripted(monkeypatch, [AIMessage("I can see your earlier case.")])
    await convo.send("Birba")

    prompt = "\n".join(str(m.content) for m in model.calls[0])
    assert "no. 00001042" in prompt and "declined by the team" in prompt
    assert "Only for customers of 2+ years." in prompt


async def test_a_new_request_reuses_the_open_case_on_the_contract(monkeypatch):
    from datetime import datetime

    crm = SecureCrm(
        cases=[
            {
                "reference": "00001042",
                "record_type": "cancellation_request",
                "status": "open",
                "created_at": datetime(2026, 9, 25),
                "offer": None,
            }
        ]
    )
    routed_to(monkeypatch, "cancellation")
    convo = conversation(crm)
    scripted(monkeypatch, [])
    await convo.send(f"cancel my contract {CONTRACT}, I'm moving")  # the question
    scripted(
        monkeypatch,
        [
            tool_call("billing_lookup", {"contract_number": CONTRACT}),
            tool_call("create_handover_record", {"summary": "Wants to cancel."}),
            AIMessage("No lock-in."),
        ],
    )
    reply = await convo.send("Birba")

    assert crm.records == [], "no duplicate case"
    assert reply.handover_reference == "00001042"
    assert "already registered as case no. 00001042" in reply.text


async def test_after_a_wrong_answer_the_model_does_not_see_the_exchange(monkeypatch):
    """Found live: after a wrong answer and then the right one, the model saw
    the retry in the transcript and asked the question again itself."""
    convo, _, _ = await _asked(monkeypatch)
    scripted(monkeypatch, [])
    await convo.send("Rex")
    model = scripted(monkeypatch, [AIMessage("Your €29.90 debit was refused.")])
    await convo.send("Birba")

    prompt = "\n".join(str(m.content) for m in model.calls[0])
    assert "doesn't match our records" not in prompt
    assert "need to check that it's you" not in prompt
    assert "not recorded" not in prompt
    assert "payment refused" in prompt, "the customer's actual request is still there"
