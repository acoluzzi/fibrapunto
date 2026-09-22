"""The one place a chat model is constructed (§10).

Code asks for a *role*; this resolves it to a pinned model with an explicit
output budget and the provider options that make §10 and §9 true. Nothing else
in the codebase should instantiate a client, because three things must hold on
every single request and a second construction site is where one of them
quietly stops holding:

  * `allow_fallbacks: false` — otherwise the provider may serve a different
    model under load and a "pinned" run is not pinned.
  * `data_collection: "deny"` — routes only to providers that do not retain
    prompts (§9).
  * an explicit `max_tokens` — OpenRouter reserves credit against the ceiling
    you request, not against what you use, so omitting it both inflates the
    reservation and leaves cost per conversation undefined (§16).

LangChain is the only LangGraph-facing dependency here, and `ChatOpenAI` is
pointed at OpenRouter by base URL. No vendor-specific client appears anywhere,
which is what keeps the model choice reversible.
"""

from __future__ import annotations

from typing import Any

from langchain_openai import ChatOpenAI

from fibrapunto.llm.registry import LLMRegistry, ModelRole, get_registry
from fibrapunto.settings import get_settings


def build_chat_model(role: ModelRole, **overrides: Any) -> ChatOpenAI:
    """A chat model for `role`, pinned and budgeted."""
    settings = get_settings()
    registry = get_registry()
    spec = registry[role]

    return ChatOpenAI(
        model=spec.id,
        base_url=settings.openrouter_base_url,
        api_key=settings.openrouter_api_key,
        max_tokens=spec.max_tokens,
        timeout=60,
        max_retries=1,
        extra_body=registry.provider_options(),
        **overrides,
    )


def served_model(response: Any) -> str | None:
    """Which model actually answered, as opposed to which was asked for.

    Checked against the request on every call (§10). The provider gives no
    pinning guarantee and no notice of change, so both are manufactured here.
    """
    metadata = getattr(response, "response_metadata", {}) or {}
    return metadata.get("model_name") or metadata.get("model")


def verify_pin(role: ModelRole, response: Any) -> None:
    """Raise if the response came from a model other than the pinned one."""
    LLMRegistry.assert_served_model(get_registry()[role].id, served_model(response))
