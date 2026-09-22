"""Judge separation and version pinning, enforced rather than promised (§10, §14.2)."""

from __future__ import annotations

from datetime import date

import pytest
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
