"""Judge separation and version pinning, enforced rather than promised (§10, §14.2)."""

from __future__ import annotations

from datetime import date

import pytest
from fibrapunto.llm import chat
from fibrapunto.llm.registry import (
    MAX_TOKENS,
    JudgeIsSubject,
    LLMRegistry,
    ModelPinViolation,
    ModelRole,
    ModelSpec,
    get_registry,
)

PINNED = date(2026, 9, 22)


def build(agent: str, judge: str) -> LLMRegistry:
    return LLMRegistry(
        {
            ModelRole.agent: ModelSpec(ModelRole.agent, agent, PINNED, MAX_TOKENS[ModelRole.agent]),
            ModelRole.router: ModelSpec(
                ModelRole.router, agent, PINNED, MAX_TOKENS[ModelRole.router]
            ),
            ModelRole.judge: ModelSpec(ModelRole.judge, judge, PINNED, MAX_TOKENS[ModelRole.judge]),
            ModelRole.persona: ModelSpec(
                ModelRole.persona, judge, PINNED, MAX_TOKENS[ModelRole.persona]
            ),
        }
    )


def test_a_different_vendor_is_accepted():
    registry = build("anthropic/claude-sonnet-5", "google/gemini-2.5-pro")
    assert registry[ModelRole.agent].vendor == "anthropic"
    assert registry[ModelRole.judge].vendor == "google"


def test_the_same_model_is_refused():
    """§10: "Grading one's own output is not measurement." """
    with pytest.raises(JudgeIsSubject, match="same model"):
        build("anthropic/claude-sonnet-5", "anthropic/claude-sonnet-5")


def test_the_same_vendor_is_refused_even_for_a_different_model():
    """The check is at vendor level on purpose.

    Two checkpoints of one family share training data and failure modes. A judge
    that hallucinates where the subject hallucinates will confirm the subject's
    answer, and the measurement will look excellent.
    """
    with pytest.raises(JudgeIsSubject, match="share a vendor"):
        build("anthropic/claude-sonnet-5", "anthropic/claude-opus-5")


def test_the_configured_registry_is_valid():
    """The real .env must satisfy the rule, not just the test fixtures."""
    registry = get_registry()
    assert registry[ModelRole.agent].vendor != registry[ModelRole.judge].vendor


# --- pinning (§10) -----------------------------------------------------------


def test_routing_options_allow_provider_fallback_and_refuse_retention():
    options = get_registry().provider_options()["provider"]
    assert options["allow_fallbacks"] is True, (
        "A fallback is to another provider of the same model, never to another "
        "model (that is the `models` list, which is not sent). With it off, one "
        "unhealthy endpoint takes the agent down while others serve the model. "
        "The pin is enforced by assert_served_model on every response."
    )
    assert options["data_collection"] == "deny", (
        "§9: restrict routing to providers that do not retain prompts."
    )


def test_a_substituted_model_is_caught():
    with pytest.raises(ModelPinViolation, match="was served"):
        LLMRegistry.assert_served_model("anthropic/claude-sonnet-5", "anthropic/claude-haiku-4-5")


def test_a_silent_provider_is_treated_as_a_failure():
    """If we cannot tell which model answered, the pin is unverifiable — and an
    unverifiable pin should not be quietly accepted as a verified one."""
    with pytest.raises(ModelPinViolation, match="did not report"):
        LLMRegistry.assert_served_model("anthropic/claude-sonnet-5", None)


def test_the_expected_model_passes():
    LLMRegistry.assert_served_model("anthropic/claude-sonnet-5", "anthropic/claude-sonnet-5")


def test_every_role_has_an_explicit_output_budget():
    """§16 makes cost per conversation a stated criterion, and OpenRouter
    reserves credit against max_tokens rather than actual usage — so omitting
    it both inflates the reservation and leaves the budget undefined.

    See docs/FRICTION-LOG.md entry 3: this was discovered as a 402, not as a
    cost surprise, which was the lucky version of the problem.
    """
    registry = get_registry()
    for role in ModelRole:
        budget = registry[role].max_tokens
        assert 0 < budget <= 4096, f"{role} has an implausible budget: {budget}"


# --- when the provider itself is the thing that failed -----------------------


def test_a_credit_error_is_interpreted_rather_than_re_raised():
    """A 402 used to surface as a 500 with a stack trace in a container log.

    The provider's own words are the most useful thing available and they arrive
    buried in a repr; pulling them out is the difference between "500" and
    knowing what to do about it.
    """
    raw = Exception(
        "Error code: 402 - {'error': {'message': \"This request requires more "
        "credits, or fewer max_tokens. You requested up to 1024 tokens, but can "
        "only afford 398\", 'code': 402}}"
    )
    interpreted = chat.interpret_provider_error(raw)

    assert isinstance(interpreted, chat.ReasoningLayerUnavailable)
    assert "1024 tokens" in interpreted.reason
    assert interpreted.hint and "max_tokens" in interpreted.hint


def test_an_upstream_error_relayed_by_openrouter_names_the_provider_and_its_words():
    """OpenRouter wraps a provider's failure as "Provider returned error" and
    puts the provider's own message in `metadata.raw`. The wrapper is what the
    first regex found, and the log said nothing a person could act on."""
    raw = Exception(
        "Error code: 404 - {'error': {'message': 'Provider returned error', "
        "'code': 404, 'metadata': {'raw': '{\"type\":\"error\",\"error\":"
        '{"type":"not_found_error","message":"model: claude-sonnet-5"},'
        "\"request_id\":\"req_011\"}', 'provider_name': 'Claude Platform on AWS', "
        "'is_byok': False}}, 'user_id': 'user_1'}"
    )
    interpreted = chat.interpret_provider_error(raw)

    assert "Claude Platform on AWS" in interpreted.reason
    assert "model: claude-sonnet-5" in interpreted.reason
    assert "Provider returned error" not in interpreted.reason
    assert interpreted.hint and "provider" in interpreted.hint.lower()


def test_an_unrecognised_failure_still_becomes_typed():
    """No hint is honest when there is nothing to hint. What must not happen is
    the exception escaping as a 500."""
    interpreted = chat.interpret_provider_error(RuntimeError("socket closed"))
    assert isinstance(interpreted, chat.ReasoningLayerUnavailable)
    assert "socket closed" in interpreted.reason
    assert interpreted.hint is None


def test_a_timeout_is_named_as_one():
    interpreted = chat.interpret_provider_error(TimeoutError("read timed out"))
    assert "timeout" in (interpreted.hint or "").lower()


async def test_the_wrapper_translates_and_does_not_double_wrap():
    class Boom:
        async def ainvoke(self, _messages):
            raise RuntimeError("Error code: 401 - {'error': {'message': 'No auth'}}")

    with pytest.raises(chat.ReasoningLayerUnavailable) as caught:
        await chat.ainvoke(Boom(), [], role=ModelRole.agent)
    assert "No auth" in caught.value.reason
    assert "OpenRouter" in (caught.value.hint or "")

    class AlreadyTyped:
        async def ainvoke(self, _messages):
            raise chat.ReasoningLayerUnavailable("already typed", hint="h")

    with pytest.raises(chat.ReasoningLayerUnavailable) as caught:
        await chat.ainvoke(AlreadyTyped(), [], role=ModelRole.agent)
    assert caught.value.reason == "already typed", "must not be re-interpreted"


# --- the pin is verified on every call, at the shared boundary (§10) ---------
#
# `verify_pin` existed and was tested, and nothing on the agent path called it:
# a substituted model would have served whole conversations unnoticed. These
# go through `chat.ainvoke` and the real call sites.


def _served(model_id: str | None, text: str = "ok"):
    from langchain_core.messages import AIMessage

    return AIMessage(text, response_metadata={"model_name": model_id} if model_id else {})


class _Returns:
    def __init__(self, response):
        self.response = response

    async def ainvoke(self, _messages):
        return self.response


async def test_the_boundary_rejects_a_substituted_model():
    with pytest.raises(chat.ReasoningLayerUnavailable, match="was served") as caught:
        await chat.ainvoke(_Returns(_served("vendor/other-model")), [], role=ModelRole.agent)
    assert "pinned" in (caught.value.hint or "")


async def test_the_boundary_rejects_a_silent_provider():
    with pytest.raises(chat.ReasoningLayerUnavailable, match="did not report"):
        await chat.ainvoke(_Returns(_served(None)), [], role=ModelRole.router)


async def test_the_boundary_passes_the_pinned_model_through():
    pinned = get_registry()[ModelRole.agent].id
    response = await chat.ainvoke(_Returns(_served(pinned)), [], role=ModelRole.agent)
    assert response.content == "ok"


async def test_structured_output_is_verified_and_unwrapped():
    pinned = get_registry()[ModelRole.router].id
    parsed = object()
    ok = {"raw": _served(pinned), "parsed": parsed, "parsing_error": None}
    assert await chat.ainvoke(_Returns(ok), [], role=ModelRole.router) is parsed

    wrong = {"raw": _served("vendor/other-model"), "parsed": parsed, "parsing_error": None}
    with pytest.raises(chat.ReasoningLayerUnavailable, match="was served"):
        await chat.ainvoke(_Returns(wrong), [], role=ModelRole.router)

    unparsed = {"raw": _served(pinned), "parsed": None, "parsing_error": ValueError("bad")}
    with pytest.raises(chat.ReasoningLayerUnavailable, match="could not be parsed"):
        await chat.ainvoke(_Returns(unparsed), [], role=ModelRole.router)


def test_a_dated_snapshot_of_the_pinned_model_is_the_pinned_model():
    LLMRegistry.assert_served_model(
        "anthropic/claude-sonnet-5", "anthropic/claude-sonnet-5-20260801"
    )
    LLMRegistry.assert_served_model(
        "anthropic/claude-sonnet-5", "anthropic/claude-sonnet-5-2026-08-01"
    )
    with pytest.raises(ModelPinViolation):
        LLMRegistry.assert_served_model(
            "anthropic/claude-sonnet-5", "anthropic/claude-sonnet-5-lite"
        )
    with pytest.raises(ModelPinViolation):
        LLMRegistry.assert_served_model("anthropic/claude-sonnet-5", "anthropic/claude-sonnet-50")


async def test_the_router_call_is_verified(monkeypatch):
    from fibrapunto.domains.registry import DomainKey
    from fibrapunto.router import classifier

    class FakeRouterModel:
        def with_structured_output(self, schema, include_raw=False):
            assert include_raw, "without the raw message the pin cannot be checked"
            return _Returns(
                {"raw": _served("vendor/other-model"), "parsed": None, "parsing_error": None}
            )

    monkeypatch.setattr(classifier, "build_chat_model", lambda *_a, **_k: FakeRouterModel())
    with pytest.raises(chat.ReasoningLayerUnavailable, match="was served"):
        await classifier.classify("hello", [], DomainKey.fallback)


# --- cost per conversation (§16) ---------------------------------------------
#
# "Conversational agents are priced per conversation, one way or another."
# The provider is asked to report what each call cost, and the wrapper records
# it onto whichever ledger is open for the turn. No ledger, nothing recorded.


def test_routing_options_ask_the_provider_to_report_cost():
    options = get_registry().provider_options()
    assert options["usage"] == {"include": True}, (
        "§16 makes cost per conversation a criterion; without usage accounting "
        "on the response there is no number behind it"
    )


def _costed(model_id: str, cost: float | None, prompt: int = 120, completion: int = 30):
    from langchain_core.messages import AIMessage

    usage = {"prompt_tokens": prompt, "completion_tokens": completion}
    if cost is not None:
        usage["cost"] = cost
    return AIMessage("ok", response_metadata={"model_name": model_id, "token_usage": usage})


async def test_the_ledger_records_every_call_of_the_turn_with_its_cost():
    agent = get_registry()[ModelRole.agent].id
    router = get_registry()[ModelRole.router].id

    with chat.CostLedger() as ledger:
        await chat.ainvoke(_Returns(_costed(router, 0.0004, 300, 40)), [], role=ModelRole.router)
        await chat.ainvoke(_Returns(_costed(agent, 0.0121)), [], role=ModelRole.agent)
        await chat.ainvoke(_Returns(_costed(agent, 0.0079)), [], role=ModelRole.agent)

    assert ledger.count() == 3
    assert ledger.count(ModelRole.agent) == 2
    assert ledger.cost(ModelRole.router) == 0.0004
    assert ledger.cost(ModelRole.agent) == 0.02
    assert ledger.cost() == 0.0204
    assert ledger.tokens() == (540, 100)


async def test_a_provider_that_does_not_report_cost_yields_none_not_zero():
    agent = get_registry()[ModelRole.agent].id
    with chat.CostLedger() as ledger:
        await chat.ainvoke(_Returns(_costed(agent, None)), [], role=ModelRole.agent)
    assert ledger.count() == 1
    assert ledger.cost() is None, "an unreported cost is unknown, not free"
    assert ledger.tokens() == (120, 30), "token counts still arrive without a cost"


async def test_a_call_the_pinned_model_did_not_answer_is_not_a_cost_of_the_turn():
    with chat.CostLedger() as ledger:
        with pytest.raises(chat.ReasoningLayerUnavailable):
            await chat.ainvoke(
                _Returns(_costed("vendor/other-model", 0.5)), [], role=ModelRole.agent
            )
    assert ledger.count() == 0


async def test_structured_output_is_costed_from_the_raw_message():
    router = get_registry()[ModelRole.router].id
    parsed = object()
    with chat.CostLedger() as ledger:
        ok = {"raw": _costed(router, 0.0003), "parsed": parsed, "parsing_error": None}
        assert await chat.ainvoke(_Returns(ok), [], role=ModelRole.router) is parsed
    assert ledger.cost(ModelRole.router) == 0.0003


async def test_without_an_open_ledger_nothing_is_recorded_and_nothing_breaks():
    agent = get_registry()[ModelRole.agent].id
    response = await chat.ainvoke(_Returns(_costed(agent, 0.01)), [], role=ModelRole.agent)
    assert response.content == "ok"


async def test_the_ledger_closes_after_the_turn():
    agent = get_registry()[ModelRole.agent].id
    with chat.CostLedger() as first:
        await chat.ainvoke(_Returns(_costed(agent, 0.01)), [], role=ModelRole.agent)
    # A call after the turn must not land on the previous turn's ledger.
    await chat.ainvoke(_Returns(_costed(agent, 0.02)), [], role=ModelRole.agent)
    assert first.count() == 1
