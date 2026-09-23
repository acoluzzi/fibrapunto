"""Domain classification (§4).

A separate structured call rather than a framework handoff, for one reason that
decides it: §1 requires **routing accuracy ≥ 90%, including mid-conversation
switches**, to be measurable. With a classifier, "which domain fired" is a field
in the response — a fact the harness compares against expectation. With handoffs
it would have to be inferred from a transcript by a judge model, which means
measuring the measuring instrument.

§4 also asks for exactly this shape: a mid-conversation switch should be "a
routing decision made by the system, not an `if` buried in a prompt".

The classifier sees the domains' **verbatim scope statements** — §4 says the
scope statement is what classification uses, so it is used unmodified rather
than paraphrased into something more classifier-friendly.
"""

from __future__ import annotations

from typing import Literal

import structlog
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from fibrapunto.domains.registry import DomainKey, classifiable_domains
from fibrapunto.llm.chat import ainvoke, build_chat_model
from fibrapunto.llm.registry import ModelRole

log = structlog.get_logger(__name__)


class RoutingDecision(BaseModel):
    """What the classifier returns. Every field is consumed by code, not prose."""

    domain: Literal["billing", "cancellation", "fallback"] = Field(
        description=(
            "The domain the customer's latest message belongs to. Use "
            "'fallback' only if it clearly belongs to neither of the others."
        )
    )
    confidence: float = Field(description="How sure you are, from 0.0 to 1.0.", ge=0.0, le=1.0)
    explicit_human_request: bool = Field(
        description=(
            "True if the customer explicitly asked to speak to a person "
            "('put me through to someone', 'I want to talk to a human')."
        )
    )
    sentiment: Literal["neutral", "frustrated", "angry"] = Field(
        description="The customer's tone in the latest message."
    )
    rationale: str = Field(
        description="One sentence on why. Internal use: never shown to the customer."
    )


SYSTEM = """\
You are a classifier. Read a customer's latest message and decide which domain \
it belongs to.

You do not reply to the customer and you do not give advice: you produce only \
the classification.

The available domains are these, with their official descriptions:

{scopes}

Rules:
- Classify the LATEST message, using earlier ones only as context.
- If the customer changes subject mid-conversation, follow the change: that is \
precisely the case you exist to catch.
- 'fallback' is for what belongs to no other domain, not for messages you find \
ambiguous. If it is ambiguous but plausibly one domain, choose that one with a \
low confidence.
"""


def _scopes_block() -> str:
    """Every scope statement the classifier may choose between, verbatim.

    Fallback is included and comes from the registry like every other domain.
    It used to carry a second, hand-written description here; two descriptions
    of one domain is a drift waiting to happen, and §4 is explicit that "a
    domain has a scope statement used for classification" — singular.

    What keeps fallback from becoming a shrug is the rule in SYSTEM, not its
    absence from this list: an ambiguous message goes to its likeliest domain
    with low confidence, and only a message that fits nowhere goes here. §4.4's
    refusal has to be a considered outcome.
    """
    return "\n\n".join(
        f"### {key.value}\n{domain.scope_statement}"
        for key, domain in classifiable_domains().items()
    )


async def classify(message: str, history: list[str], current_domain: DomainKey) -> RoutingDecision:
    """Classify one turn.

    `history` is the last few turns as plain text — enough for the classifier to
    notice a topic change, not so much that the call stops being cheap.
    """
    model = build_chat_model(ModelRole.router).with_structured_output(RoutingDecision)

    context = "\n".join(f"- {t}" for t in history[-4:]) or "(none)"
    prompt = (
        f"Current domain of the conversation: {current_domain.value}\n\n"
        f"Earlier messages:\n{context}\n\n"
        f"Customer's latest message:\n{message}"
    )

    decision: RoutingDecision = await ainvoke(
        model, [SystemMessage(SYSTEM.format(scopes=_scopes_block())), HumanMessage(prompt)]
    )
    log.info(
        "router.classified",
        domain=decision.domain,
        confidence=decision.confidence,
        sentiment=decision.sentiment,
    )
    return decision
