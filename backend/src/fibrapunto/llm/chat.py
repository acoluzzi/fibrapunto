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
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

from langchain_openai import ChatOpenAI

from fibrapunto import faults
from fibrapunto.llm.registry import LLMRegistry, ModelPinViolation, ModelRole, get_registry
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
# OpenRouter relays an upstream failure as the wrapper "Provider returned
# error" and keeps the provider's own words in `metadata.raw`, a JSON string,
# next to `provider_name`. Those two are the useful part.
_UPSTREAM_MESSAGE = re.compile(r"'raw':\s*'[^']*?\\?\"message\\?\":\s*\\?\"([^\"\\]{1,400})")
_UPSTREAM_PROVIDER = re.compile(r"'provider_name':\s*'([^']{1,80})'")
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
    relayed = "Provider returned error" in raw
    if relayed and (upstream := _UPSTREAM_MESSAGE.search(raw)):
        provider = _UPSTREAM_PROVIDER.search(raw)
        detail = upstream.group(1)
        if provider:
            detail = f"{provider.group(1)}: {detail}"

    status = getattr(exc, "status_code", None)
    if status is None and (found := _STATUS_CODE.search(raw)):
        status = int(found.group(1))

    hint = None
    if relayed and status in (404, 500, 502, 503):
        hint = (
            "The provider OpenRouter routed to failed upstream. Another provider "
            "may serve the same model: check the model's endpoint list on "
            "OpenRouter and the routing block in llm/registry.py."
        )
    elif status == 402 or "requires more credits" in raw:
        hint = (
            "OpenRouter credit exhausted. The provider reserves credit equal to "
            "max_tokens for every in-flight request, so the balance must cover "
            "the requested ceiling and not just the tokens used."
        )
    elif status == 401:
        hint = "OpenRouter key missing or invalid in .env."
    elif status == 429:
        hint = "Provider rate limit. Retry in a few seconds."
    elif isinstance(exc, TimeoutError) or "timeout" in raw.lower():
        hint = "The provider did not respond within the timeout."

    return ReasoningLayerUnavailable(detail, hint=hint)


# --- what a turn cost (§16) ---------------------------------------------------
#
# "Conversational agents are priced per conversation, one way or another."
# The eval harness measures a whole run from the provider's balance
# (evals/spend.py); this is the per-conversation half. Every call through
# `ainvoke` records what the provider said it cost, and the conversation sums
# the calls of one turn onto its trace events. The figure is the provider's
# own — `usage.include` in the routing options — never a tariff table kept
# here that would drift the day a price changes.


@dataclass(frozen=True, slots=True)
class ModelCall:
    role: ModelRole
    cost_usd: float | None
    input_tokens: int | None
    output_tokens: int | None
    model_served: str | None = None
    """Which model answered, as the provider reported it. Recorded per call so
    the trace can name the router's model as well as the agent's: with only the
    agent's pin on the panel, the router looked as if it ran on the same model."""


_ledger: ContextVar[list[ModelCall] | None] = ContextVar("fibrapunto_model_calls", default=None)


class CostLedger:
    """The model calls of one turn, in the order they were made.

    A context manager because the calls happen several frames below the
    conversation — the router, the graph, the guard repair — and none of them
    should have to carry an accumulator argument to make cost observable.
    Nothing active means nothing is recorded, which is what the unit tests
    that drive `ainvoke` directly get.
    """

    def __init__(self) -> None:
        self.calls: list[ModelCall] = []
        self._token = None

    def __enter__(self) -> CostLedger:
        self._token = _ledger.set(self.calls)
        return self

    def __exit__(self, *_exc: object) -> None:
        if self._token is not None:
            _ledger.reset(self._token)
            self._token = None

    def cost(self, role: ModelRole | None = None) -> float | None:
        """The cost of the calls made so far, or None if no call reported one.

        None rather than 0.0: a provider that did not report is not a free
        conversation, and a tile reading "$0.00" would say the wrong thing.
        """
        reported = [
            c.cost_usd
            for c in self.calls
            if c.cost_usd is not None and (role is None or c.role is role)
        ]
        return round(sum(reported), 6) if reported else None

    def tokens(self, role: ModelRole | None = None) -> tuple[int, int]:
        calls = [c for c in self.calls if role is None or c.role is role]
        return (
            sum(c.input_tokens or 0 for c in calls),
            sum(c.output_tokens or 0 for c in calls),
        )

    def count(self, role: ModelRole | None = None) -> int:
        return sum(1 for c in self.calls if role is None or c.role is role)

    def served(self, role: ModelRole) -> str | None:
        """The model that answered the most recent call in `role`, or None if
        no call in that role was made this turn."""
        for call in reversed(self.calls):
            if call.role is role:
                return call.model_served
        return None


def usage_of(role: ModelRole, response: Any) -> ModelCall:
    """What one response cost, as the provider reported it.

    The cost sits in the provider's `usage` block, which LangChain carries
    through as `response_metadata["token_usage"]`. Token counts prefer the
    standard `usage_metadata`, with the raw block as the fallback.
    """
    metadata = getattr(response, "response_metadata", {}) or {}
    raw = metadata.get("token_usage") or {}
    standard = getattr(response, "usage_metadata", None) or {}
    cost = raw.get("cost")
    return ModelCall(
        role=role,
        cost_usd=float(cost) if isinstance(cost, int | float) else None,
        input_tokens=standard.get("input_tokens") or raw.get("prompt_tokens"),
        output_tokens=standard.get("output_tokens") or raw.get("completion_tokens"),
        model_served=served_model(response),
    )


def _record(role: ModelRole, response: Any) -> None:
    calls = _ledger.get()
    if calls is not None:
        calls.append(usage_of(role, response))


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


async def ainvoke(model: Any, messages: Any, *, role: ModelRole) -> Any:
    """Call a model: translate provider failures, and verify the pin (§10).

    Every model call on the agent path goes through here — the router, every
    agent round, and the final no-tools answer — so this is the one place the
    served model can be checked on every response. `role` is required and
    keyword-only for that reason: a call site cannot forget to say what it
    asked for. Verifying only in a helper nothing called meant a substituted
    model would have served a whole conversation unnoticed.

    Structured output must be requested with `include_raw=True`: the parsed
    object carries no metadata, so without the raw message there is nothing to
    verify. The parsed object is what is returned.

    A pin violation is raised as `ReasoningLayerUnavailable` — the pinned
    reasoning layer is, in the only sense that matters, not available — so the
    API reports it as a 503 with the reason, not a 500.
    """
    if faults.current().model == "down":
        raise ReasoningLayerUnavailable(
            "The model provider is unreachable.",
            hint="Switched off from the demo control strip (Model provider: down).",
        )
    try:
        response = await model.ainvoke(messages)
    except ReasoningLayerUnavailable:
        raise
    except Exception as exc:
        raise interpret_provider_error(exc) from exc

    structured = isinstance(response, dict) and "raw" in response
    raw = response["raw"] if structured else response
    try:
        verify_pin(role, raw)
    except ModelPinViolation as exc:
        raise ReasoningLayerUnavailable(
            str(exc),
            hint=(
                f"The {role.value} model is pinned (§10) and the provider served "
                "something else. Check the pin in .env against the provider's catalogue."
            ),
        ) from exc

    # Recorded after the pin check: a call the pinned model did not answer is
    # a failure of the reasoning layer, not a cost of the conversation.
    _record(role, raw)

    if structured:
        if response.get("parsing_error") is not None or response.get("parsed") is None:
            raise ReasoningLayerUnavailable(
                f"The {role.value} model's structured output could not be parsed: "
                f"{response.get('parsing_error')}"
            )
        return response["parsed"]
    return response


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
