"""The turn loop, driven by a scripted model (no network).

Everything structural about a turn — domain selection, which tools are bound,
whether a tool result lands in CollectedState, whether the domain switch of §15
preserves what was collected — is testable without a provider. Reserving the
real model for the things that genuinely need judgement keeps the feedback loop
free and the iteration fast, which is the difference between tuning prompts on
day 10 and rationing runs.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from fibrapunto.conversation import Conversation
from fibrapunto.domains.registry import DomainKey
from fibrapunto.integrations.billing_client import BillingClient
from fibrapunto.llm.registry import ModelRole, get_registry
from fibrapunto.router.classifier import RoutingDecision
from langchain_core.messages import AIMessage
from langchain_core.utils.function_calling import convert_to_openai_tool
from mock_billing.main import app as billing_app

# No module-level asyncio mark: this file mixes sync state checks with async
# conversation tests, and `asyncio_mode = "auto"` already handles the latter.

CONTRACT = "FP-2023-048812"


class ScriptedModel:
    """Returns pre-written AIMessages in order. Stands in for ChatOpenAI."""

    def __init__(self, script: list[AIMessage]) -> None:
        self.script = list(script)
        self.calls: list[list[Any]] = []

    def bind_tools(self, tools: list[Any]) -> ScriptedModel:
        # Convert the schemas exactly as langchain-openai would. The first
        # version of this fake only recorded the names, and a tool whose
        # args_schema was bare `BaseModel` sailed through every offline test
        # and then failed against the real provider with an AttributeError
        # from deep inside pydantic. A fake that accepts what the real thing
        # rejects is worse than no fake: it converts a cheap failure into an
        # expensive one.
        self.bound_tools = [t.name for t in tools]
        self.tool_schemas = [convert_to_openai_tool(t) for t in tools]
        return self

    async def ainvoke(self, messages: list[Any], **_: Any) -> AIMessage:
        self.calls.append(messages)
        response = self.script.pop(0) if self.script else AIMessage("(fine)")
        # Report the served model as the provider does. Every agent response is
        # now checked against the pin (§10); a test that wants a substitution
        # sets response_metadata itself.
        if not response.response_metadata:
            response.response_metadata = {"model_name": get_registry()[ModelRole.agent].id}
        return response


@pytest.fixture(autouse=True)
def _fast_stub(monkeypatch):
    from mock_billing.settings import get_settings

    get_settings.cache_clear()
    settings = get_settings()
    object.__setattr__(settings, "base_latency_ms", 0.0)
    object.__setattr__(settings, "latency_jitter_ms", 0.0)
    yield
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def _billing_in_process(monkeypatch):
    original = httpx.AsyncClient

    def factory(*args, **kwargs):
        kwargs.setdefault("transport", httpx.ASGITransport(app=billing_app))
        return original(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", factory)


def scripted(monkeypatch, script: list[AIMessage]) -> ScriptedModel:
    model = ScriptedModel(script)
    monkeypatch.setattr("fibrapunto.graph.build.build_chat_model", lambda *a, **k: model)
    return model


def routed_to(monkeypatch, domain: str, confidence: float = 0.95, **kwargs) -> None:
    async def fake_classify(*_a, **_k):
        return RoutingDecision(
            domain=domain,
            confidence=confidence,
            explicit_human_request=kwargs.get("human", False),
            sentiment=kwargs.get("sentiment", "neutral"),
            financial_request=kwargs.get("financial", "none"),
            rationale="test",
        )

    monkeypatch.setattr("fibrapunto.conversation.classify", fake_classify)


def conversation() -> Conversation:
    return Conversation(
        billing_client=BillingClient(
            base_url="http://billing.test", token_provider=lambda: "billing-dev-token"
        )
    )


def tool_call(name: str, args: dict[str, Any]) -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[{"name": name, "args": args, "id": "call_1", "type": "tool_call"}],
    )


# --- the enumerated capability list (§17.1) ----------------------------------


async def test_each_domain_binds_only_its_own_capabilities(monkeypatch):
    """§17.1. The fallback domain gets one tool; billing gets three.

    This is why §1's cancellation-discipline metric is a structural zero rather
    than a prompt outcome — the tools simply are not there.
    """
    routed_to(monkeypatch, "fallback")
    model = scripted(monkeypatch, [AIMessage("I cannot help with that here.")])

    await conversation().send("what is the capital of France?")
    assert model.bound_tools == ["transfer_to_human"]

    routed_to(monkeypatch, "billing")
    model = scripted(monkeypatch, [AIMessage("Could you give me the contract number?")])
    await conversation().send("my bill does not add up")
    assert set(model.bound_tools) == {
        "billing_lookup",
        "create_handover_record",
        "transfer_to_human",
    }


async def test_a_tool_outside_the_domain_is_refused_not_executed(monkeypatch):
    """Belt and braces on the binding: even if a call for an unbound capability
    arrives, the dispatcher refuses it rather than running it."""
    routed_to(monkeypatch, "fallback")
    scripted(
        monkeypatch,
        [
            tool_call("billing_lookup", {"contract_number": CONTRACT}),
            AIMessage("I cannot help with that."),
        ],
    )

    convo = conversation()
    await convo.send("tell me my invoice status")

    assert convo.state.collected.billing is None, (
        "a capability the fallback domain does not have was executed"
    )


# --- the lookup lands in typed state -----------------------------------------


async def test_a_successful_lookup_populates_collected_state(monkeypatch):
    routed_to(monkeypatch, "billing")
    scripted(
        monkeypatch,
        [
            tool_call("billing_lookup", {"contract_number": CONTRACT}),
            AIMessage("The September charge was refused."),
        ],
    )

    convo = conversation()
    reply = await convo.send(f"my contract is {CONTRACT}, what happened?")

    assert reply.domain == "billing"
    assert convo.state.collected.contract_number == CONTRACT
    assert convo.state.collected.billing is not None
    assert str(convo.state.collected.billing.amount) == "29.90"
    assert [c.outcome for c in reply.capability_calls] == ["found"]


async def test_the_reply_reports_the_outcome_as_a_fact(monkeypatch):
    """§14's deterministic assertions depend on this: which domain fired and
    which capabilities ran are fields, not things a judge has to infer."""
    routed_to(monkeypatch, "billing")
    scripted(
        monkeypatch,
        [
            tool_call("billing_lookup", {"contract_number": CONTRACT}),
            AIMessage("Here is the position."),
        ],
    )

    reply = await conversation().send(f"contract {CONTRACT}")
    assert reply.routing.decided_by in {"first_turn", "unchanged", "classifier"}
    assert reply.capability_calls[0].capability == "billing_lookup"
    assert reply.prompt_sha


# --- §15: the mid-conversation switch ----------------------------------------


async def test_switching_domain_keeps_what_was_collected(monkeypatch):
    """§15 row 4, the whole point of keeping facts out of the transcript.

    "…comunque voglio disdire" mid-billing must not cause the contract number
    to be asked for again — because the cancellation domain's first render
    already contains it.
    """
    routed_to(monkeypatch, "billing")
    scripted(
        monkeypatch,
        [
            tool_call("billing_lookup", {"contract_number": CONTRACT}),
            AIMessage("The charge was refused for insufficient funds."),
        ],
    )

    convo = conversation()
    await convo.send(f"payment refused, contract {CONTRACT}")
    assert convo.domain is DomainKey.billing

    routed_to(monkeypatch, "cancellation", confidence=0.92)
    model = scripted(monkeypatch, [AIMessage("Very well, I will proceed with the request.")])
    await convo.send("anyway I want to cancel, I have moved house")

    assert convo.domain is DomainKey.cancellation
    assert convo.state.collected.contract_number == CONTRACT

    # The proof: the contract number is in the prompt the cancellation domain
    # was rendered with, so there is nothing to re-ask.
    rendered = "\n".join(str(m.content) for m in model.calls[0])
    assert CONTRACT in rendered
    assert "29.90" in rendered or "29.9" in rendered


async def test_a_low_confidence_switch_is_suppressed(monkeypatch):
    """Hysteresis. One ambiguous turn must not flap the conversation."""
    routed_to(monkeypatch, "billing")
    scripted(monkeypatch, [AIMessage("Could you give me the contract number?")])
    convo = conversation()
    await convo.send("my invoice does not add up")

    routed_to(monkeypatch, "cancellation", confidence=0.4)
    scripted(monkeypatch, [AIMessage("Continuing with the bill.")])
    reply = await convo.send("dunno, not sure")

    assert convo.domain is DomainKey.billing
    assert reply.routing.decided_by == "below_switch_threshold"


async def test_an_acknowledgement_never_reroutes(monkeypatch):
    """The confirmation lock (§4.1 instruction 5).

    "Yes" classified on its own is not a billing message. Without the lock it
    routes to fallback and the handover never happens — losing the one step the
    spec singles out as making the record trustworthy.
    """
    from fibrapunto.state.models import PendingConfirmation

    routed_to(monkeypatch, "billing")
    scripted(monkeypatch, [AIMessage("To confirm: you dispute the €29.90 charge. Is that right?")])
    convo = conversation()
    await convo.send("€29.90 is not right, I want a refund")
    convo.state.pending_confirmation = PendingConfirmation(
        summary="September charge disputed, €29.90", asked_at_turn=1
    )

    # If the classifier were consulted it would say fallback — and be ignored.
    routed_to(monkeypatch, "fallback", confidence=0.99)
    scripted(monkeypatch, [AIMessage("I have opened the case.")])
    reply = await convo.send("Yes")

    assert convo.domain is DomainKey.billing
    assert reply.routing.decided_by == "confirmation_lock"


# --- §12.1 customer-initiated ------------------------------------------------


async def test_an_explicit_request_for_a_person_is_honoured_by_code(monkeypatch):
    """§12.1: honoured immediately, without a retention attempt. The model is
    not called, so it cannot make one. (No CRM here, so the honest outcome is
    the templated failure — the full path is in test_handover_wiring.py.)"""
    routed_to(monkeypatch, "billing", human=True)
    model = scripted(monkeypatch, [AIMessage("Before I do — can I help you myself?")])

    convo = conversation()
    reply = await convo.send("enough, put me through to a person")

    assert model.calls == []
    assert "can I help you myself" not in reply.text
    assert "wasn't able to register" in reply.text


async def test_anger_shortens_the_answers(monkeypatch):
    routed_to(monkeypatch, "billing", sentiment="angry")
    model = scripted(monkeypatch, [AIMessage("Capisco.")])

    convo = conversation()
    await convo.send("this is the third time I am writing!!")

    assert convo.state.terse is True
    rendered = "\n".join(str(m.content) for m in model.calls[0])
    assert "briefly and factually" in rendered


# --- the cache split ---------------------------------------------------------


async def test_the_stable_prompt_block_is_marked_cacheable(monkeypatch):
    """The stable block carries a cache breakpoint and the volatile block does
    not. Getting this backwards would invalidate the cache every turn and
    roughly triple the input cost (§16)."""
    routed_to(monkeypatch, "billing")
    model = scripted(monkeypatch, [AIMessage("Good morning.")])
    await conversation().send("ciao")

    system_messages = [m for m in model.calls[0] if m.type == "system"]
    assert len(system_messages) == 2, "expected a stable block and a volatile block"

    stable, volatile = system_messages
    assert stable.additional_kwargs.get("cache_control") == {"type": "ephemeral"}
    assert "cache_control" not in volatile.additional_kwargs
    assert "State already collected" in volatile.content


async def test_the_stable_block_does_not_vary_between_turns(monkeypatch):
    """If it varies, it is not cacheable — and a contract number leaking into
    it is the way that happens."""
    routed_to(monkeypatch, "billing")
    scripted(
        monkeypatch,
        [
            tool_call("billing_lookup", {"contract_number": CONTRACT}),
            AIMessage("Refused."),
        ],
    )
    convo = conversation()
    await convo.send(f"contract {CONTRACT}")

    model = scripted(monkeypatch, [AIMessage("Confirmed.")])
    await convo.send("and now?")

    stable = next(m for m in model.calls[0] if m.type == "system")
    assert CONTRACT not in stable.content, (
        "the contract number leaked into the cacheable block — the cache is dead"
    )


async def test_every_declared_tool_converts_to_a_provider_schema(monkeypatch):
    """Each capability's args_schema must survive conversion.

    The regression this pins: `transfer_to_human` takes no arguments (§5 lists
    its input as "—") and was declared with bare `BaseModel`, which is abstract.
    Offline tests passed; the first real call died with an AttributeError from
    inside pydantic that named nothing useful.
    """
    routed_to(monkeypatch, "billing")
    model = scripted(monkeypatch, [AIMessage("ok")])
    await conversation().send("my bill does not add up")

    assert len(model.tool_schemas) == 3
    for schema in model.tool_schemas:
        assert schema["type"] == "function"
        assert schema["function"]["description"], "§17.5: every capability needs a description"
        assert "parameters" in schema["function"]


# --- the contract number survives a failed lookup ----------------------------
#
# Found by re-running eval case 005 in a browser: billing was unavailable, the
# customer had given a valid contract number, and the handover was created
# without a contract link — so the agent asked them to repeat the number to a
# human. §13's whole premise is that those first minutes are gone.


def _state_after(outcome: str, number: str = "FP-2023-048812"):
    from fibrapunto.graph.tool_node import ToolDispatcher
    from fibrapunto.state.models import ConversationState

    state = ConversationState()
    result = SimpleNamespace(outcome=outcome, matches=[], reason=None)
    ToolDispatcher._absorb_billing(
        ToolDispatcher.__new__(ToolDispatcher),
        result,
        {"contract_number": number},
        state,
    )
    return state


def test_the_number_survives_a_billing_outage():
    """The defect, as an assertion. The customer said it; an external system
    being down does not un-say it."""
    state = _state_after("serviceUnavailable")
    assert state.collected.contract_number == "FP-2023-048812"
    assert state.collected.contract_number_source == "customer_verbatim"


def test_the_number_survives_a_contract_absent_from_billing():
    """§6.2's deliberate misalignment: present in the CRM, absent from billing.
    The handover must still link it."""
    state = _state_after("notFound", "FP-2020-004199")
    assert state.collected.contract_number == "FP-2020-004199"


def test_a_rejected_number_is_not_recorded():
    """`rejected` is precisely the outcome meaning the number was not
    acceptable — malformed, or not in the customer's own words (§5.2)."""
    assert _state_after("rejected").collected.contract_number is None


def test_an_ambiguous_number_is_not_recorded_as_the_contract():
    """§4.1: several matches means there is not yet a single contract to record.
    That is what the disambiguation turn resolves."""
    assert _state_after("multipleMatches").collected.contract_number is None


# --- §10: every agent response is checked against the pin --------------------


async def test_a_substituted_agent_model_stops_the_turn(monkeypatch):
    from fibrapunto.llm.chat import ReasoningLayerUnavailable

    routed_to(monkeypatch, "billing")
    substituted = AIMessage("Hello!", response_metadata={"model_name": "vendor/other-model"})
    scripted(monkeypatch, [substituted])

    with pytest.raises(ReasoningLayerUnavailable, match="was served"):
        await conversation().send("my bill")


async def test_the_final_no_tools_answer_is_checked_too(monkeypatch):
    """After MAX_TOOL_ROUNDS the graph asks once more with no tools — a
    separate call, and one that used to be unchecked."""
    from fibrapunto.graph.build import MAX_TOOL_ROUNDS
    from fibrapunto.llm.chat import ReasoningLayerUnavailable

    routed_to(monkeypatch, "billing")
    rounds = [tool_call("transfer_to_human", {}) for _ in range(MAX_TOOL_ROUNDS)]
    final = AIMessage("Done.", response_metadata={"model_name": "vendor/other-model"})
    scripted(monkeypatch, [*rounds, final])

    with pytest.raises(ReasoningLayerUnavailable, match="was served"):
        await conversation().send("my bill")


# --- the reply is always English -----------------------------------------------


async def test_a_customer_writing_italian_never_gets_an_italian_reply(monkeypatch):
    """The screenshot: Italian from the model, English from the templates. The
    model is repaired once; if it answers in Italian again, the English
    fallback goes out instead."""
    routed_to(monkeypatch, "fallback")
    italian = AIMessage(
        "Posso aiutarla con la fatturazione e con le disdette. Per qualsiasi "
        "altra richiesta, posso metterla in contatto con un collega."
    )
    model = scripted(monkeypatch, [italian, italian])

    reply = await conversation().send("cosa sai fare?")

    assert len(model.calls) == 2, "one repair attempt"
    assert "Always reply in English" in str(model.calls[1][-1].content)
    assert reply.text.startswith("I'm sorry — I can only help in English")


async def test_a_repaired_english_reply_is_what_goes_out(monkeypatch):
    routed_to(monkeypatch, "fallback")
    scripted(
        monkeypatch,
        [
            AIMessage("Posso aiutarla con la fatturazione e con le disdette del contratto."),
            AIMessage(
                "I can only help in English. I can help with billing and "
                "cancellations, or put you through to a colleague."
            ),
        ],
    )
    reply = await conversation().send("cosa sai fare?")
    assert reply.text.startswith("I can only help in English")
