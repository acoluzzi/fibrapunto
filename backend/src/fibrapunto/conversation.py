"""The programmatic conversation interface (§17.8).

    "A programmatic conversation interface, so the agent can be driven by an
    external test harness rather than only by hand."

One implementation, three consumers: the FastAPI routes, the eval harness (over
HTTP, because §14.2 requires the harness to be external), and the unit tests.

`AgentReply` returns **which domain fired and which capabilities ran with which
outcomes as facts**, not as things to be inferred from a transcript. That is
what lets §14's deterministic assertions compute routing accuracy and the
no-invention metric without asking a judge model — and it is why the judge can
be reserved for the questions only a judge can answer.
"""

from __future__ import annotations

import time

import structlog
from pydantic import BaseModel

from fibrapunto.credentials.store import BILLING_API_TOKEN, FileSecretStore
from fibrapunto.domains.registry import DomainKey, get
from fibrapunto.graph.build import run_domain_turn
from fibrapunto.graph.tool_node import ToolDispatcher
from fibrapunto.integrations.billing_client import BillingClient
from fibrapunto.llm.registry import ModelRole, get_registry
from fibrapunto.observability.events import EventType
from fibrapunto.observability.trace import TraceRecorder
from fibrapunto.router import policy
from fibrapunto.router.classifier import classify
from fibrapunto.state.models import ConversationState, ConversationStatus, TurnRole

log = structlog.get_logger(__name__)


def _default_billing_client() -> BillingClient:
    """Wire the credential store into the client (§17.7).

    A *provider*, not a value: the client resolves the handle at request time,
    so a rotation takes effect on the next call with no restart and no client
    carries a secret in its repr.
    """
    store = FileSecretStore()
    return BillingClient(token_provider=store.provider(BILLING_API_TOKEN))


class CapabilityCallView(BaseModel):
    capability: str
    outcome: str
    duration_ms: int


class RoutingView(BaseModel):
    domain: str
    switched: bool
    decided_by: str
    confidence: float | None = None
    sentiment: str | None = None


class AgentReply(BaseModel):
    """Everything the UI's trace panel and the harness need, per turn."""

    text: str
    conversation_id: str
    turn_index: int
    domain: str
    routing: RoutingView
    capability_calls: list[CapabilityCallView]
    status: str
    handover_reference: str | None = None
    contract_number: str | None = None
    model_served: str | None = None
    prompt_sha: str


class Conversation:
    """One customer conversation.

    No memory across conversations, by design (§3): "The agent has no memory
    across conversations. It cannot identify a customer except through a
    contract number the customer supplies in that conversation." That is
    load-bearing rather than a gap — cross-session memory needs authentication
    and a data policy this design does not have (§18, §19).
    """

    def __init__(
        self,
        billing_client: BillingClient | None = None,
        test_mode: str | None = None,
    ) -> None:
        self.state = ConversationState(test_mode=test_mode)
        self.state.agent_model = get_registry()[ModelRole.agent].id
        self._dispatcher = ToolDispatcher(billing_client or _default_billing_client())
        self.trace = TraceRecorder(self.state.id)
        self.trace.emit(
            EventType.session_started,
            agent_model=self.state.agent_model,
            test_mode=test_mode,
        )

    async def send(self, text: str) -> AgentReply:
        state = self.state
        started = time.perf_counter()
        previous_domain = state.current_domain
        calls_before = len(state.capability_calls)

        state.record_turn(TurnRole.customer, text)
        self.trace.emit(EventType.turn_received, turn_seq=state.turn_count)

        # 1. Classify. The model proposes a domain; policy decides.
        decision = None
        if not (state.pending_confirmation and policy.is_acknowledgement(text)):
            # The confirmation lock short-circuits before the call — a "Sì"
            # needs no classification, and skipping it saves a round trip on
            # the turn where latency is most visible.
            decision = await classify(text, [t.text for t in state.turns], state.current_domain)

        outcome = policy.apply(state, text, decision)
        policy.apply_sentiment(state, decision)
        state.current_domain = outcome.domain

        self.trace.emit(
            EventType.routing_decision,
            turn_seq=state.turn_count,
            chosen=outcome.domain.value,
            previous=previous_domain.value,
            switched=outcome.switched,
            decided_by=outcome.decided_by,
            confidence=decision.confidence if decision else None,
            sentiment=decision.sentiment if decision else None,
            # Internal only — the rationale is for the engineer reading the
            # trace, and must never be surfaced to the customer (§5.2).
            rationale=decision.rationale if decision else None,
        )
        if outcome.switched:
            self.trace.emit(
                EventType.domain_switched,
                turn_seq=state.turn_count,
                **{"from": previous_domain.value, "to": outcome.domain.value},
                # §15: the switch must not cost what was already collected.
                contract_number_retained=state.collected.contract_number,
            )

        domain = get(outcome.domain)
        state.prompt_sha = domain.prompt.sha
        self.trace.emit(
            EventType.domain_entered,
            turn_seq=state.turn_count,
            domain=domain.key.value,
            capabilities=[c.value for c in domain.capabilities],
            prompt_sha=domain.prompt.short_sha,
        )

        # 2. Run the domain. Only this domain's capabilities are bound (§17.1).
        reply_text, _ = await run_domain_turn(domain, state, self._dispatcher)

        # Capability calls are recorded into state by the dispatcher; mirror
        # the new ones into the trace so the panel can interleave them with the
        # reply rather than showing them as a separate list.
        for call in state.capability_calls[calls_before:]:
            self.trace.emit(
                EventType.capability_result,
                turn_seq=state.turn_count,
                duration_ms=call.duration_ms,
                capability=call.capability,
                outcome=call.outcome,
                incident_id=call.incident_id,
            )

        state.record_turn(TurnRole.agent, reply_text)
        self.trace.emit(
            EventType.agent_reply,
            turn_seq=state.turn_count,
            duration_ms=int((time.perf_counter() - started) * 1000),
            model_served=state.agent_model_served,
        )
        # §12.2: what the agent already told the customer, so the receiving
        # human does not contradict it.
        state.collected.told_to_customer.append(reply_text)

        # Resolution, escalation and the guardrail pipeline land on days 6-7.
        # Until then the status stays honest rather than optimistic.

        log.info(
            "conversation.turn",
            conversation_id=str(state.id),
            domain=outcome.domain.value,
            switched=outcome.switched,
            calls=[c.outcome for c in state.capability_calls],
        )

        return AgentReply(
            text=reply_text,
            conversation_id=str(state.id),
            turn_index=state.turn_count,
            domain=outcome.domain.value,
            routing=RoutingView(
                domain=outcome.domain.value,
                switched=outcome.switched,
                decided_by=outcome.decided_by,
                confidence=decision.confidence if decision else None,
                sentiment=decision.sentiment if decision else None,
            ),
            capability_calls=[
                CapabilityCallView(
                    capability=c.capability, outcome=c.outcome, duration_ms=c.duration_ms
                )
                for c in state.capability_calls
            ],
            status=state.status.value,
            handover_reference=state.collected.handover_reference,
            contract_number=state.collected.contract_number,
            model_served=state.agent_model_served,
            prompt_sha=state.prompt_sha,
        )

    def snapshot(self) -> dict:
        return self.state.snapshot()

    @property
    def status(self) -> ConversationStatus:
        return self.state.status

    @property
    def domain(self) -> DomainKey:
        return self.state.current_domain
