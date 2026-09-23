"""The graph (§4).

    "It is a router plus a small number of bounded domains, each with its own
    description, its own reasoning instructions and its own explicitly
    enumerated set of permitted capabilities."

Which is this, drawn:

        route ──┬──▶ billing ──────┐
                ├──▶ cancellation ─┼──▶ tools ──▶ (back to domain)
                └──▶ fallback ─────┘

Each domain node binds **only its own capability tuple**, so §17.1 is a property
of the graph rather than a instruction. The cancellation node cannot call a
capability that executes a cancellation because it was never handed one.

The tool node is ours (see graph/tool_node.py); LangGraph's prebuilt one does
not satisfy §5.1 in either of its branches.
"""

from __future__ import annotations

import structlog
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from fibrapunto.capabilities.billing_lookup import DESCRIPTION, INPUT_DESCRIPTION
from fibrapunto.domains.registry import CapabilityName, Domain, DomainKey
from fibrapunto.llm.chat import ainvoke, build_chat_model, served_model
from fibrapunto.llm.registry import ModelRole
from fibrapunto.state.models import ConversationState

log = structlog.get_logger(__name__)

MAX_TOOL_ROUNDS = 3
"""How many times a single turn may call tools before answering.

A bound, not a target. §12.1's "conversation exceeds a turn budget without a
capability call succeeding" is about the conversation; this is about one turn,
and it stops a loop inside a turn from looking like progress.
"""


# --- tool schemas the model sees (§17.5) -------------------------------------
#
# The descriptions are §5.2's, verbatim. "A vague description is not a
# documentation problem; it is a defect that surfaces at runtime as a
# hallucination."


class BillingLookupArgs(BaseModel):
    contract_number: str = Field(description=INPUT_DESCRIPTION)


class NoArgs(BaseModel):
    """§5 gives `transfer to human` an input of "—".

    A concrete empty model, not bare `BaseModel`: the latter is abstract and
    LangChain's schema conversion fails on it — with an `AttributeError` deep
    inside pydantic that names nothing useful.
    """


class HandoverArgs(BaseModel):
    summary: str = Field(
        description=(
            "One sentence describing what the customer is asking for. "
            "The customer must already have confirmed it is correct."
        )
    )
    stated_reason: str | None = Field(
        default=None,
        description="The reason in the customer's own words, if they gave one.",
    )
    disputed_point: str | None = Field(
        default=None,
        description=(
            "Disputes only: exactly what the customer is disputing, in one "
            "sentence. For example: 'says their plan is €24.90, not €29.90'."
        ),
    )


def _handover_description(domain: Domain) -> str:
    """The capability description varies by domain, because the spec does.

    §4.1 instruction 5 requires a read-back before opening a billing dispute:
    "summarise the issue in one sentence, confirm that summary with the
    customer, then create a handover record." The customer's own agreement is
    what makes the record trustworthy to the operator who receives it.

    §4.2 requires no such step — its worked example opens the record in the same
    turn as stating the constraints, and a customer who has already said they
    are leaving should not be asked to confirm that they meant it.

    A single description for both domains imposes the billing rule on
    cancellations and costs that customer a turn. Since the tool is bound per
    domain (§17.1), the description can be too.
    """
    if domain.key is DomainKey.cancellation:
        return (
            "Record the cancellation request for the retention team, with what "
            "you have already collected. Use it as soon as you have the "
            "contract number and have stated the constraints: do not ask the "
            "customer to confirm that they really want to cancel."
        )
    return (
        "Open a case for a human colleague, with what you have already "
        "collected. Use it when the customer disputes an amount, asks for a "
        "refund or asks for a credit. Before using it you must have summarised "
        "the request in one sentence and had the customer confirm it."
    )


def _tool_stubs(domain: Domain) -> list[StructuredTool]:
    """Declarations only.

    Nothing here executes: the graph intercepts `tool_calls` and dispatches
    through `graph/tool_node.py`, so the capability contract and the typed
    outcomes stay in one place instead of being split between a LangChain tool
    body and a wrapper.
    """
    catalogue = {
        CapabilityName.billing_lookup: StructuredTool(
            name="billing_lookup",
            description=DESCRIPTION,
            args_schema=BillingLookupArgs,
            func=lambda **_: None,
        ),
        CapabilityName.create_handover_record: StructuredTool(
            name="create_handover_record",
            description=_handover_description(domain),
            args_schema=HandoverArgs,
            func=lambda **_: None,
        ),
        CapabilityName.transfer_to_human: StructuredTool(
            name="transfer_to_human",
            description=(
                "Hand the conversation to a human colleague. Use it when the "
                "customer explicitly asks for one, or when the request is "
                "outside what you can help with."
            ),
            args_schema=NoArgs,
            func=lambda **_: None,
        ),
    }
    return [catalogue[c] for c in domain.capabilities]


def build_messages(domain: Domain, state: ConversationState) -> list[BaseMessage]:
    """Assemble the prompt, stable part first.

    The ordering is load-bearing for cost, not style. The stable block —
    identity plus domain instructions — is byte-identical across every turn of
    every conversation, so the provider can cache it and read it back at ~0.1x.
    Anything that changes per turn goes *after* it. A contract number
    interpolated into the stable block would invalidate the cache on every
    request and roughly triple the input cost (§16).
    """
    stable = domain.prompt

    messages: list[BaseMessage] = [
        SystemMessage(
            content=stable.body,
            # Read by langchain-openai and passed through to the provider as
            # an Anthropic cache breakpoint.
            additional_kwargs={"cache_control": {"type": "ephemeral"}},
        )
    ]

    volatile = [state.collected.as_prompt_block()]
    constraints = _turn_constraints(state)
    if constraints:
        volatile.append(constraints)
    messages.append(SystemMessage(content="\n\n".join(volatile)))

    for turn in state.turns:
        messages.append(
            HumanMessage(turn.text) if turn.role.value == "customer" else AIMessage(turn.text)
        )

    return messages


def _turn_constraints(state: ConversationState) -> str:
    lines: list[str] = []
    if state.terse:
        lines.append(
            "- The customer is upset or frustrated: reply briefly and factually, "
            "and do not apologise more than once."
        )
    if state.offer_human_now:
        lines.append(
            "- Offer to hand over to a colleague **now**, without trying to keep "
            "them and without asking why."
        )
    if state.pending_confirmation and not state.pending_confirmation.satisfied:
        lines.append(
            f"- You have asked the customer to confirm this summary and are "
            f'waiting for their answer: "{state.pending_confirmation.summary}". '
            f"Do not open the case until they confirm."
        )
    if state.format_reasks >= 1:
        lines.append(
            "- You have already asked for the contract number once. If it is "
            "still wrong, do not insist: offer a colleague."
        )
    if not lines:
        return ""
    return "## Constraints for this turn\n\n" + "\n".join(lines)


async def run_domain_turn(
    domain: Domain,
    state: ConversationState,
    dispatcher,
    extra_instruction: str | None = None,
) -> tuple[str, list]:
    """One customer turn: call the model, run any tools, call again, answer.

    Returns the reply text and the tool messages produced, so the caller can
    trace them. Bounded by MAX_TOOL_ROUNDS.
    """
    model = build_chat_model(ModelRole.agent).bind_tools(_tool_stubs(domain))
    messages = build_messages(domain, state)
    if extra_instruction:
        # A guardrail repair (§11). Appended last so it is the most
        # recent thing the model reads, and outside the cached block.
        messages.append(SystemMessage(extra_instruction))
    tool_messages: list = []

    for round_index in range(MAX_TOOL_ROUNDS):
        response: AIMessage = await ainvoke(model, messages)
        state.agent_model_served = served_model(response)

        if not response.tool_calls:
            return _text_of(response), tool_messages

        results = await dispatcher.run(response, domain, state)
        tool_messages.extend(results)
        messages.append(response)
        messages.extend(results)

        log.info(
            "graph.tool_round",
            domain=domain.key.value,
            round=round_index + 1,
            calls=[c["name"] for c in response.tool_calls],
        )

    # Out of rounds. Ask once for an answer with no tools available, rather than
    # returning whatever the last tool-calling message happened to contain.
    final: AIMessage = await ainvoke(
        build_chat_model(ModelRole.agent),
        [*messages, SystemMessage("Answer the customer now, without any further checks.")],
    )
    return _text_of(final), tool_messages


def _text_of(message: AIMessage) -> str:
    if isinstance(message.content, str):
        return message.content.strip()
    parts = [
        block.get("text", "")
        for block in message.content
        if isinstance(block, dict) and block.get("type") == "text"
    ]
    return "".join(parts).strip()
