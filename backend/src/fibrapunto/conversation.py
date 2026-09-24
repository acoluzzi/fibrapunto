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

import re
import time

import structlog
from pydantic import BaseModel

from fibrapunto.capabilities.outcomes import HandoverFailed
from fibrapunto.credentials.store import BILLING_API_TOKEN, FileSecretStore
from fibrapunto.domains.registry import DomainKey, get
from fibrapunto.escalation import engine, triggers
from fibrapunto.graph.build import run_domain_turn
from fibrapunto.graph.tool_node import ToolDispatcher
from fibrapunto.guardrails import pipeline
from fibrapunto.integrations.billing_client import BillingClient
from fibrapunto.llm.registry import ModelRole, get_registry
from fibrapunto.observability.events import EventType
from fibrapunto.observability.trace import TraceRecorder
from fibrapunto.perimeter.gateway import CrmGateway
from fibrapunto.perimeter.policy import svc_agent
from fibrapunto.privacy import redaction
from fibrapunto.router import policy
from fibrapunto.router.classifier import classify
from fibrapunto.settings import get_settings
from fibrapunto.state.models import (
    ConversationState,
    ConversationStatus,
    PendingConfirmation,
    PendingEscalation,
    TurnRole,
)

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
        gateway: CrmGateway | None = None,
    ) -> None:
        self.state = ConversationState(test_mode=test_mode)
        self.state.agent_model = get_registry()[ModelRole.agent].id
        # svc_agent(), always. The conversation never runs under any other
        # identity, and the gateway refuses to be called without one (§8).
        self._dispatcher = ToolDispatcher(
            billing_client or _default_billing_client(),
            gateway=gateway,
            principal=svc_agent(),
        )
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

        collected_before = state.collected.model_copy(deep=True)

        # §9 + docs/DECISIONS.md #16: pre-redact before the turn is recorded or
        # sent to the provider. The model still learns that an IBAN was pasted,
        # so it can tell the customer not to send one — but the digits never
        # leave this process.
        #
        # `for_model` is the only optional enforcement point; the other three
        # (storage, handover, outbound) apply regardless, so the toggle changes
        # what the *model* sees and nothing else. See privacy/redaction.py.
        text, kinds = redaction.for_model(text)
        if kinds:
            for kind in kinds:
                if kind.value not in state.collected.sensitive_data_seen:
                    state.collected.sensitive_data_seen.append(kind.value)
            self.trace.emit(
                EventType.redaction_applied,
                turn_seq=state.turn_count + 1,
                # False means the toggle is off and the model did receive the
                # value — the trace should say so rather than implying a
                # redaction that did not happen (§9's honest limitation).
                redacted=get_settings().redact_inbound,
                # The KIND, never the value (§9).
                kinds=[k.value for k in kinds],
            )

        state.record_turn(TurnRole.customer, text)
        self.trace.emit(EventType.turn_received, turn_seq=state.turn_count)

        # 1. Classify. The model proposes a domain; policy decides.
        decision = None
        pending_answer = state.pending_confirmation or state.pending_escalation
        if not (pending_answer and policy.answers_read_back(text)):
            # The confirmation lock short-circuits before the call — a "Yes"
            # needs no classification, and skipping it saves a round trip on
            # the turn where latency is most visible.
            decision = await classify(text, [t.text for t in state.turns], state.current_domain)

        outcome = policy.apply(state, text, decision)
        policy.apply_sentiment(state, decision)
        policy.apply_request_kind(state, decision, text)
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

        self._dispatcher.last_handover = None
        reference_before = state.collected.handover_reference

        # 2a. §4.1 instruction 5: the customer's answer to a read-back. An
        # agreement opens the case — by code, from the summary they agreed to —
        # and the reply is the closing template. No model call: there is
        # nothing left to reason about, and the sentence the customer is left
        # with is a commitment (§12.3).
        reply_text: str | None = None
        extra_instruction: str | None = None

        # 2a. §12.1: the answer to an escalation offer the code made. "Yes"
        # opens the case by code; "no" is respected, and that trigger is not
        # offered again.
        escalation = state.pending_escalation
        if escalation:
            if policy.is_affirmative(text):
                state.pending_escalation = None
                result = await self._dispatcher.open_escalation_case(state, escalation.trigger)
                reply_text = self._templated(result, engine.closing_message)
            else:
                # "No", or not an answer at all — the customer moved on. Either
                # way the offer lapses: left pending, a later "yes" to something
                # else (a cancellation read-back) would open this case instead.
                state.pending_escalation = None

        # 2b. §12.1: an explicit request for a person, "honoured immediately
        # and without resistance or a retention attempt". The case opens by
        # code on this turn; the model is not called, so it cannot try to keep
        # the customer.
        if reply_text is None and state.human_requested:
            state.human_requested = False  # one attempt; a failure is stated, not retried
            if state.collected.handover_reference is None:
                result = await self._dispatcher.open_escalation_case(
                    state, triggers.TriggerKind.human_requested.value
                )
                if isinstance(result, HandoverFailed) and result.missing_fields:
                    self._dispatcher.last_handover = None
                else:
                    # The case first, so the colleague has everything; then
                    # §5's transfer, executed by code rather than offered.
                    transfer = (
                        None
                        if isinstance(result, HandoverFailed)
                        else await self._dispatcher.transfer(state)
                    )
                    reply_text = engine.honour_request(result, state, transfer)

        pending = state.pending_confirmation
        if reply_text is None and pending and not pending.satisfied:
            if policy.is_affirmative(text):
                pending.satisfied = True
                self.trace.emit(
                    EventType.handover_confirmation_received,
                    turn_seq=state.turn_count,
                    asked_at_turn=pending.asked_at_turn,
                )
                result = await self._dispatcher.open_confirmed_case(state)
                if isinstance(result, HandoverFailed) and result.missing_fields:
                    # Agreed, but the record is not yet complete (§13). Back to
                    # the model to collect what is missing; the summary stays
                    # agreed, so the next attempt is not gated again.
                    extra_instruction = (
                        "The customer confirmed the summary, but the case could "
                        "not be opened yet: these details are missing: "
                        f"{', '.join(result.missing_fields)}. Ask for them."
                    )
                    self._dispatcher.last_handover = None
                else:
                    reply_text = engine.closing_message(result, state)
            elif policy.is_negative(text):
                # The summary was wrong. The model revises it, and its next
                # proposal is read back afresh.
                state.pending_confirmation = None

        # 2c. Run the domain. Only this domain's capabilities are bound (§17.1).
        templated = reply_text is not None
        categorical_action: str | None = None
        if reply_text is None:
            reply_text = await self._model_turn(domain, extra_instruction)
            reply_text = self._closing(reply_text, reference_before)
            # 2d. §12.1's categorical triggers, executed by code whatever the
            # model did: they "are not judgement calls and the agent is not
            # permitted to weigh them".
            reply_text, categorical_action = await self._categorical(
                reply_text, reference_before, collected_before.contract_number is not None
            )

        # 3. §12.1: a fired trigger makes the code act, not only log. Evaluated
        # here, before the reply leaves, so the offer is part of what the
        # customer receives. Progress first: the no-progress trigger reads it.
        triggers.update_progress(state, collected_before)
        trigger = triggers.evaluate(state)
        action = "none"
        if trigger:
            reply_text, action = self._enforce(trigger, reply_text, templated, reference_before)
            if categorical_action:
                action = categorical_action

        # §9's fourth enforcement point: "never repeat such a value back in the
        # conversation." Not a guard — a guard inspects and may block, this
        # unconditionally removes — so it sits after the pipeline rather than
        # inside it. Unreachable while inbound redaction is on, because the
        # model never held the digits; it is the only thing left when the
        # demo toggle is off, which is exactly when it would be missed.
        reply_text, echoed = redaction.for_customer(reply_text)
        if echoed:
            self.trace.emit(
                EventType.outbound_filter_applied,
                turn_seq=state.turn_count,
                kinds=[k.value for k in echoed],
            )

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

        if state.collected.handover_reference != reference_before:
            self.trace.emit(
                EventType.handover_created,
                turn_seq=state.turn_count,
                reference=state.collected.handover_reference,
                trigger=self._dispatcher.last_trigger,
                confirmed=bool(pending and pending.satisfied),
            )

        if trigger:
            engine.note_trigger(state, trigger)
            self.trace.emit(
                EventType.escalation_triggered,
                turn_seq=state.turn_count,
                kind=trigger.kind.value,
                reason=trigger.reason,
                immediate=trigger.immediate,
                decided_by="code",
                # What the code did about it — so the trace shows enforcement,
                # not just detection.
                action=action,
            )
            state.pending_trigger = trigger.kind.value

        self._settle_status(trigger)

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

    async def _model_turn(self, domain, extra_instruction: str | None) -> str:
        """The domain's model turn, through the output guards (§11)."""
        state = self.state
        reply_text, _ = await run_domain_turn(
            domain, state, self._dispatcher, extra_instruction=extra_instruction
        )

        # §11: the output-boundary guards. One repair attempt, then substitute.
        guarded = pipeline.resolve(reply_text, state)
        if guarded.violations:
            repaired, _ = await run_domain_turn(
                domain,
                state,
                self._dispatcher,
                extra_instruction=pipeline.repair_instruction(guarded.violations),
            )
            guarded = pipeline.resolve(reply_text, state, repaired_reply=repaired)

        for violation in guarded.violations:
            self.trace.emit(
                EventType.guardrail_triggered,
                turn_seq=state.turn_count,
                guard=violation.guard.value,
                problems=violation.problems,
                blocking=violation.blocking,
                substituted=guarded.substituted,
            )
        return guarded.text

    def _settle_status(self, trigger: triggers.Trigger | None) -> None:
        """Where the conversation stands after this turn (§1, outcome 1).

        `resolved` is a state the code can check, not a feeling: a lookup
        returned this customer's figures, no case is open, nothing is waiting
        on the customer (a read-back or an escalation offer), and no trigger
        fired this turn. Anything short of that is `active` — asking for the
        contract number, or waiting for the customer to pick an address, is
        correct behaviour and is not a resolution. The two handover statuses
        are set by the escalation engine and are final.
        """
        state = self.state
        if state.status in (ConversationStatus.handed_over, ConversationStatus.escalated_no_record):
            return
        read_back_pending = bool(
            state.pending_confirmation and not state.pending_confirmation.satisfied
        )
        resolved = (
            state.collected.billing is not None
            and state.collected.handover_reference is None
            and state.pending_escalation is None
            and not read_back_pending
            and trigger is None
        )
        state.status = ConversationStatus.resolved if resolved else ConversationStatus.active

    def _templated(self, result, render) -> str | None:
        """A code-opened case's reply, or None to let the model collect
        missing details (§13) — a failure for missing fields is not the end."""
        if isinstance(result, HandoverFailed) and result.missing_fields:
            self._dispatcher.last_handover = None
            return None
        return render(result, self.state)

    async def _categorical(
        self, reply_text: str, reference_before: str | None, contract_known_before: bool
    ) -> tuple[str, str | None]:
        """Execute the categorical triggers (§12.1). Returns the reply and the
        action taken, or None if there was nothing to do yet.

        * Dispute, refund, credit: once the contract is known, the read-back
          (§4.1 instruction 5) is appended from a template built on typed state
          and held as `PendingConfirmation`; the customer's "yes" opens the case.
        * Change of payment method: the case opens as soon as the contract is
          known. Instruction 5's read-back is for disputes, refunds and credits.
        * Cancellation: the case opens on the turn after the agent has had the
          contract in hand — the turn §4.2 gives it to state the constraints and
          ask the reason, once — unless the model already opened it.

        None of this waits for the model to call a tool. The model can still
        open the case itself, with better wording; this is what happens when it
        does not.
        """
        state = self.state
        collected = state.collected
        if collected.handover_reference != reference_before or not collected.contract_number:
            return reply_text, None
        kind = collected.financial_request

        if kind in ("dispute", "refund", "credit") and "billing_dispute" not in collected.handovers:
            if state.pending_confirmation:
                return reply_text, None  # a read-back is already out
            summary = engine.dispute_summary(state)
            state.pending_confirmation = PendingConfirmation(
                summary=summary,
                asked_at_turn=state.turn_count,
                stated_reason=collected.stated_reason,
                disputed_point=collected.disputed_point,
            )
            return engine.read_back(reply_text, summary), "read_back_requested"

        if kind == "payment_method_change" and "billing_dispute" not in collected.handovers:
            trigger = triggers.TriggerKind.categorical_payment_change.value
            return await self._open_by_code(reply_text, trigger, collected.stated_reason)

        if (
            state.current_domain is DomainKey.cancellation
            and "cancellation_request" not in collected.handovers
            and contract_known_before
            and self._agent_turns_in(DomainKey.cancellation) >= 1
        ):
            trigger = triggers.TriggerKind.categorical_cancellation.value
            reason = collected.stated_reason or self._words_since_entering(DomainKey.cancellation)
            return await self._open_by_code(reply_text, trigger, reason)

        return reply_text, None

    async def _open_by_code(
        self, reply_text: str, trigger: str, stated_reason: str | None
    ) -> tuple[str, str | None]:
        result = await self._dispatcher.open_escalation_case(
            self.state, trigger, stated_reason=stated_reason
        )
        if isinstance(result, HandoverFailed) and result.missing_fields:
            self._dispatcher.last_handover = None
            return reply_text, "case_incomplete"
        return engine.close(reply_text, result, self.state), "case_opened_by_code"

    def _agent_turns_in(self, domain: DomainKey) -> int:
        return sum(1 for t in self.state.turns if t.role is TurnRole.agent and t.domain is domain)

    def _words_since_entering(self, domain: DomainKey) -> str | None:
        """The customer's own words since the conversation entered `domain`
        (§12.2: the stated reason "in their own words"), minus turns that were
        only a contract number. Verbatim, and already redacted (§9)."""
        turns = self.state.turns
        first = next(
            (i for i, t in enumerate(turns) if t.role is TurnRole.agent and t.domain is domain),
            None,
        )
        if first is None:
            return None
        words = [
            t.text
            for t in turns[max(first - 1, 0) :]
            if t.role is TurnRole.customer
            and not re.fullmatch(r"\W*FP-\d{4}-\d{6}\W*", t.text.strip(), re.I)
        ]
        return " / ".join(words) or None

    def _enforce(
        self,
        trigger: triggers.Trigger,
        reply_text: str,
        templated: bool,
        reference_before: str | None,
    ) -> tuple[str, str]:
        """Act on a fired trigger. Returns the reply and what was done.

        Conditional and distress triggers get an offer of a colleague, worded
        by a template and held in state, so the customer's "yes" opens the case
        by code. Offered once per trigger: a customer who said no is not asked
        again every turn. Categorical triggers are enforced by structure — the
        cancellation domain's only write is the handover, and a dispute cannot
        open without the read-back gate — so there is nothing to add here.
        """
        state = self.state
        if trigger.kind not in triggers.OFFERED:
            return reply_text, "structural"
        if state.collected.handover_reference is not None:
            return reply_text, "case_open"
        if state.collected.handover_reference != reference_before or templated:
            return reply_text, "handled_this_turn"
        if state.pending_confirmation and not state.pending_confirmation.satisfied:
            return reply_text, "read_back_pending"  # one question at a time
        if state.pending_escalation:
            return reply_text, "offer_pending"
        if trigger.kind.value in state.escalations_offered:
            return reply_text, "already_offered"

        state.pending_escalation = PendingEscalation(
            trigger=trigger.kind.value, asked_at_turn=state.turn_count
        )
        state.escalations_offered.append(trigger.kind.value)
        return engine.offer(reply_text, trigger.kind), "offered"

    def _closing(self, reply_text: str, reference_before: str | None) -> str:
        """Append the templated closing when this turn opened a case (§12.3).

        Only for a case opened *this turn*, or a real failure to open one. A
        repeated call that returned the existing case is not news, and a
        failure for missing fields is the model's cue to collect them, not the
        end of the conversation.
        """
        result = self._dispatcher.last_handover
        if result is None:
            return reply_text
        if isinstance(result, HandoverFailed):
            if result.missing_fields:
                return reply_text
        elif self.state.collected.handover_reference == reference_before:
            return reply_text
        return engine.close(reply_text, result, self.state)

    def snapshot(self) -> dict:
        return self.state.snapshot()

    @property
    def status(self) -> ConversationStatus:
        return self.state.status

    @property
    def domain(self) -> DomainKey:
        return self.state.current_domain
