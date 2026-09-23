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


def test_routing_options_pin_the_provider_and_refuse_retention():
    options = get_registry().provider_options()["provider"]
    assert options["allow_fallbacks"] is False, (
        "Without this the provider may serve a different model under load, and "
        "a 'pinned' eval run is not pinned."
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
        await chat.ainvoke(Boom(), [])
    assert "No auth" in caught.value.reason
    assert "OpenRouter" in (caught.value.hint or "")

    class AlreadyTyped:
        async def ainvoke(self, _messages):
            raise chat.ReasoningLayerUnavailable("già tipizzato", hint="h")

    with pytest.raises(chat.ReasoningLayerUnavailable) as caught:
        await chat.ainvoke(AlreadyTyped(), [])
    assert caught.value.reason == "già tipizzato", "must not be re-interpreted"
