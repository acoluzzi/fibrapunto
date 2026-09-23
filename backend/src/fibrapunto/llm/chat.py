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

import re
from typing import Any

from langchain_openai import ChatOpenAI

from fibrapunto.llm.registry import LLMRegistry, ModelRole, get_registry
from fibrapunto.settings import get_settings


class ReasoningLayerUnavailable(RuntimeError):
    """The model provider could not be reached, or refused the request.

    Deliberately **not** a capability outcome. §5.1's typed outcomes exist so a
    failing capability can be reasoned about *by the model*; if the model itself
    is the thing that failed there is nobody left to hand an outcome to, and
    dressing this up as one would mean inventing a reply the reasoning layer
    never produced — which is §12.4's failure exactly.

    So it stays an exception, and the boundary that catches it is the API: the
    honest response to "the agent cannot think right now" is to say so, not to
    improvise on its behalf.
    """

    def __init__(self, reason: str, *, hint: str | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.hint = hint


# The provider's own words are the most useful thing we have, but they arrive
# buried in a repr of a dict inside an exception message. Worth the regex: the
# difference between "500" and "you requested 1024 tokens but can afford 398"
# is the difference between opening docker logs and knowing what to do.
_PROVIDER_MESSAGE = re.compile(r"'message':\s*[\"']([^\"']{5,400})")
# The status is not reliably an attribute. LangChain re-raises some provider
# failures as plain exceptions carrying only the formatted string, so read it
# from there too — a test caught this by asserting on a 401 that had lost its
# `status_code` on the way up.
_STATUS_CODE = re.compile(r"[Ee]rror code:\s*(\d{3})")


def interpret_provider_error(exc: Exception) -> ReasoningLayerUnavailable:
    """Turn a provider exception into something a human can act on."""
    raw = str(exc)
    match = _PROVIDER_MESSAGE.search(raw)
    detail = match.group(1) if match else raw[:300]

    status = getattr(exc, "status_code", None)
    if status is None and (found := _STATUS_CODE.search(raw)):
        status = int(found.group(1))

    hint = None
    if status == 402 or "requires more credits" in raw:
        hint = (
            "Credito OpenRouter esaurito. Il provider trattiene credito pari a "
            "max_tokens per ogni richiesta in volo, quindi il saldo deve coprire "
            "il tetto richiesto e non solo i token usati."
        )
    elif status == 401:
        hint = "Chiave OpenRouter non valida o assente in .env."
    elif status == 429:
        hint = "Limite di frequenza del provider. Riprovare fra qualche secondo."
    elif isinstance(exc, TimeoutError) or "timeout" in raw.lower():
        hint = "Il provider non ha risposto entro il timeout."

    return ReasoningLayerUnavailable(detail, hint=hint)


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


async def ainvoke(model: Any, messages: Any) -> Any:
    """Call a model, translating provider failures at the point of the call.

    Every `ainvoke` in the codebase goes through here. The alternative — one
    `try` at the top of the conversation — would catch the same exceptions but
    lose which call failed, and "the router could not be reached" and "the agent
    could not be reached" are different operational facts.
    """
    try:
        return await model.ainvoke(messages)
    except ReasoningLayerUnavailable:
        raise
    except Exception as exc:
        raise interpret_provider_error(exc) from exc


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
