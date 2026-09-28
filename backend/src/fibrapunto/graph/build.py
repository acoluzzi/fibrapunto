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

import time
from typing import Any

import structlog
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from fibrapunto.capabilities.billing_lookup import DESCRIPTION, INPUT_DESCRIPTION
from fibrapunto.domains.registry import CapabilityName, Domain, DomainKey
from fibrapunto.escalation import engine
from fibrapunto.llm.chat import ainvoke, build_chat_model, served_model
from fibrapunto.llm.registry import ModelRole
from fibrapunto.observability.events import EventType
from fibrapunto.observability.trace import TraceRecorder
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
        # The security-question exchange is the platform's, not conversation
        # to continue — and the answer is never shown to the model at all.
        if engine.is_verification_exchange(turn.text):
            continue
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
    if state.pending_escalation:
        lines.append(
            "- You have offered to open a case for a colleague and are waiting "
            "for the customer's answer. Do not open it yourself, and do not "
            "offer again."
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
    verified = [
        number for number, status in state.collected.verification.items() if status == "verified"
    ]
    if verified and not state.collected.billing:
        # Seen in an eval run: verified, then straight to "let me confirm your
        # refund" without ever reading billing — a read-back with nothing
        # behind it. §4.1 instruction 2: the lookup comes first.
        lines.append(
            f"- The customer is verified for {', '.join(verified)} and billing has "
            "not been read yet. Call the billing lookup before summarising, "
            "confirming or opening anything."
        )
    if state.collected.billing and state.collected.contract_number:
        # Seen in an eval run: the facts were in the state block above and the
        # model looked the contract up again on the next turn — seconds for the
        # customer, a call on the bill, and nothing new (§16).
        lines.append(
            f"- The billing lookup for {state.collected.contract_number} has "
            "already returned in this conversation; its results are listed "
            "above. Do not call it again unless the customer gives a different "
            "contract number."
        )
    if not lines:
        return ""
    return "## Constraints for this turn\n\n" + "\n".join(lines)


async def run_domain_turn(
    domain: Domain,
    state: ConversationState,
    dispatcher,
    extra_instruction: str | None = None,
    trace: TraceRecorder | None = None,
) -> tuple[str, list]:
    """One customer turn: call the model, run any tools, call again, answer.

    Returns the reply text and the tool messages produced, so the caller can
    trace them. Bounded by MAX_TOOL_ROUNDS.

    Each model call is announced on `trace` before it is made (`llm.request`),
    so the live stream distinguishes "the model is composing" from "a
    capability is being called" — the two things a customer waits on.
    """
    model = build_chat_model(ModelRole.agent).bind_tools(_tool_stubs(domain))
    messages = build_messages(domain, state)
    if extra_instruction:
        # A guardrail repair (§11). Appended last so it is the most
        # recent thing the model reads, and outside the cached block.
        messages.append(SystemMessage(extra_instruction))
    tool_messages: list = []
    calls_made = 0

    async def call(model_to_use: Any, prompt: list[BaseMessage], *, tools: bool) -> AIMessage:
        """One model call, announced before and reported after (§17.10)."""
        nonlocal calls_made
        calls_made += 1
        if trace is not None:
            trace.emit(
                EventType.llm_request,
                turn_seq=state.turn_count,
                role=ModelRole.agent.value,
                # 1-based: "round 2" reads as the second call, not the third.
                round=calls_made,
                # False on the closing call: no capability can follow it, so
                # what comes next is the reply.
                tools_available=tools,
            )
        started = time.perf_counter()
        response: AIMessage = await ainvoke(model_to_use, prompt, role=ModelRole.agent)
        state.agent_model_served = served_model(response)
        if trace is not None:
            trace.emit(
                EventType.llm_response,
                turn_seq=state.turn_count,
                duration_ms=int((time.perf_counter() - started) * 1000),
                role=ModelRole.agent.value,
                round=calls_made,
                finish_reason=_finish_reason(response),
                tool_calls=[c["name"] for c in response.tool_calls],
                # The shape of what came back, never the text: enough to see
                # "cut off mid tool call" or "nothing at all" on the panel.
                text_chars=len(_text_of(response)),
                cut_off=_cut_off(response),
            )
        return response

    closing_instruction = "Answer the customer now, without any further checks."

    for round_index in range(MAX_TOOL_ROUNDS):
        response = await call(model, messages, tools=True)

        if _cut_off(response):
            # The output ceiling, mid tool call: the call never happened and
            # there may be no text either. Seen once as a blank reply. The
            # broken message is not appended — a half-written tool call sent
            # back to the provider is refused — and the closing call below
            # answers with what the turn already has.
            log.warning(
                "graph.round_cut_off",
                domain=domain.key.value,
                round=round_index + 1,
                finish_reason=_finish_reason(response),
                invalid_tool_calls=len(response.invalid_tool_calls),
            )
            closing_instruction = (
                "Your previous message was cut off before it was complete, so "
                "the customer did not receive it. Answer them now, in a few "
                "sentences, without any further checks."
            )
            break

        if not response.tool_calls:
            text = _text_of(response)
            if text:
                return text, tool_messages
            # Nothing to send and nothing to run. One more ask before the
            # guards decide (guardrails/pipeline.py, GuardName.empty).
            log.warning("graph.round_empty", domain=domain.key.value, round=round_index + 1)
            closing_instruction = (
                "Your previous message was empty, so the customer received "
                "nothing. Answer them now, in a few sentences, without any "
                "further checks."
            )
            break

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

    # Out of rounds, or a round that produced nothing usable. Ask once for an
    # answer with no tools available, rather than returning whatever the last
    # tool-calling message happened to contain. If this comes back empty too,
    # it is returned as such: the guards substitute, and the trace says so.
    final = await call(
        build_chat_model(ModelRole.agent),
        [*messages, SystemMessage(closing_instruction)],
        tools=False,
    )
    return _text_of(final), tool_messages


def _finish_reason(message: AIMessage) -> str | None:
    """Why the provider stopped, as it reported it (`stop`, `tool_calls`,
    `length`). None from a fake or a provider that does not say."""
    metadata = message.response_metadata or {}
    return metadata.get("finish_reason") or metadata.get("stop_reason")


def _cut_off(message: AIMessage) -> bool:
    """The output ceiling was reached before the message was complete.

    Two signs, either one enough: the provider says `length`, or LangChain
    could not parse a tool call (it lands in `invalid_tool_calls`, with the
    content empty — exactly the shape that once reached a customer as a
    blank reply).
    """
    return bool(message.invalid_tool_calls) or _finish_reason(message) == "length"


def _text_of(message: AIMessage) -> str:
    if isinstance(message.content, str):
        return message.content.strip()
    parts = [
        block.get("text", "")
        for block in message.content
        if isinstance(block, dict) and block.get("type") == "text"
    ]
    return "".join(parts).strip()
