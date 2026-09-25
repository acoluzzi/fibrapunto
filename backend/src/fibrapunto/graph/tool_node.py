"""Our tool node, not LangGraph's (§5.1, §17.4).

Verified against langgraph 1.2.12, the prebuilt `ToolNode` defaults to:

    def _default_handle_tool_errors(e: Exception) -> str:
        if isinstance(e, ToolInvocationError):
            return e.message
        raise e

Two branches, and §5.1 accepts neither. A malformed tool call comes back to the
model as free text — an untyped error in front of the reasoning layer, exactly
the failure the spec calls the most important rule in the document. Anything
else re-raises and takes the conversation down, which is not a guardrail but an
outage. The spec wants a third thing: **a named outcome in both cases.**

So this node does three things the prebuilt one does not:

  1. Dispatches through `@typed_capability`, so nothing can raise.
  2. Refuses a call to a capability this domain was not granted (§17.1) —
     returning a typed refusal rather than executing it.
  3. Records every call into ConversationState, so the trace, the escalation
     triggers and the §13 handover payload all read from one place.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import structlog
from langchain_core.messages import AIMessage, ToolMessage

from fibrapunto.capabilities.billing_lookup import billing_lookup
from fibrapunto.capabilities.create_handover_record import create_handover_record
from fibrapunto.capabilities.outcomes import (
    BillingRejected,
    HandoverAwaitingConfirmation,
    HandoverCreated,
    HandoverCreatedWithoutContractLink,
    HandoverFailed,
    RetentionOfferFirst,
)
from fibrapunto.capabilities.runtime import CapabilityCall
from fibrapunto.capabilities.transfer_to_human import (
    TransferAccepted,
    TransferResult,
    transfer_to_human,
)
from fibrapunto.domains.registry import Domain, DomainKey
from fibrapunto.escalation import engine, triggers
from fibrapunto.handover import routing
from fibrapunto.integrations.billing_client import BillingClient
from fibrapunto.integrations.recommendation_client import RecommendationClient
from fibrapunto.perimeter.gateway import CrmGateway
from fibrapunto.perimeter.policy import Principal, svc_agent
from fibrapunto.state.models import (
    BillingFacts,
    ContractCandidate,
    ConversationState,
    OfferFacts,
    PendingConfirmation,
    PendingOffer,
)

log = structlog.get_logger(__name__)


class ToolDispatcher:
    """Executes the tool calls in an assistant message."""

    def __init__(
        self,
        billing_client: BillingClient,
        gateway: CrmGateway | None = None,
        principal: Principal | None = None,
        offers: RecommendationClient | None = None,
    ) -> None:
        self._billing = billing_client
        self.offers = offers or RecommendationClient()
        # The agent's own service identity (§8). Passed explicitly rather than
        # defaulted at the call site, so that every CRM access names who is
        # acting — there is no ambient identity anywhere in this codebase.
        self._gateway = gateway
        self._principal = principal or svc_agent()
        self.last_handover: (
            HandoverCreated | HandoverCreatedWithoutContractLink | HandoverFailed | None
        ) = None
        """The handover outcome of the current turn, if any. Read by the turn
        loop to append the templated closing message; reset each turn."""
        self.last_trigger: str | None = None
        """The trigger recorded on the last case opened, for the trace."""

    async def run(
        self, message: AIMessage, domain: Domain, state: ConversationState
    ) -> list[ToolMessage]:
        results: list[ToolMessage] = []

        for call in message.tool_calls or []:
            name = call["name"]

            if not domain.may_call(name):
                # §17.1: each domain has an explicitly enumerated capability
                # list. Reaching here means the model asked for something its
                # node was not given — which should be impossible, since the
                # tools were bound per domain. Refuse anyway and say so loudly:
                # a silent execution here would undo the enumeration.
                log.error(
                    "tool.not_permitted_in_domain",
                    capability=name,
                    domain=domain.key.value,
                )
                results.append(
                    self._message(
                        call,
                        {
                            "outcome": "rejected",
                            "guidance": (
                                "This operation is not available in this "
                                "context. Offer to hand over to a colleague."
                            ),
                        },
                    )
                )
                continue

            outcome = await self._execute(name, call.get("args", {}), state)
            results.append(self._message(call, outcome))

        return results

    async def _execute(
        self, name: str, args: dict[str, Any], state: ConversationState
    ) -> dict[str, Any]:
        recorder = _StateRecorder(state)

        match name:
            case "billing_lookup":
                result = await billing_lookup(
                    args.get("contract_number", ""),
                    client=self._billing,
                    customer_turns=state.customer_turns(),
                    test_mode=state.test_mode,
                    recorder=recorder,
                )
                self._absorb_billing(result, args, state)

            case "create_handover_record":
                trigger = _trigger_for(state)
                holding = (
                    state.pending_offer is not None
                    and state.pending_offer.context == "cancellation"
                )
                offer = holding or await self.offer_first(state, "cancellation")
                if offer:
                    # §4.2 as amended: the customer hears the offer before the
                    # cancellation is registered. Code presents it after this
                    # reply; the case opens on their answer, whatever it is.
                    # Held for as long as the offer is pending — the model
                    # calling again in its next tool round must not slip past.
                    return RetentionOfferFirst().model_dump(mode="json")
                if _needs_read_back(state, trigger):
                    result = self._await_confirmation(state, args, recorder)
                else:
                    result = await self._open_case(
                        state,
                        recorder,
                        subject=args.get("summary", ""),
                        trigger=trigger,
                        stated_reason=args.get("stated_reason"),
                        disputed_point=args.get("disputed_point"),
                    )

            case "transfer_to_human":
                result = await self.transfer(state)

            case _:
                log.error("tool.unknown_capability", capability=name)
                result = BillingRejected(reason="format_invalid")

        return result.model_dump(mode="json")

    async def open_confirmed_case(
        self, state: ConversationState
    ) -> HandoverCreated | HandoverCreatedWithoutContractLink | HandoverFailed:
        """Open the case the customer has just agreed to (§4.1 instruction 5).

        Called by the turn loop, not by the model: the customer's "yes" is the
        event, and the summary they agreed to is the one stored when the model
        proposed it — not whatever the model might write on this turn.
        """
        pending = state.pending_confirmation
        assert pending is not None and pending.satisfied  # guarded by the caller
        return await self._open_case(
            state,
            _StateRecorder(state),
            subject=pending.summary,
            trigger=_trigger_for(state),
            stated_reason=pending.stated_reason,
            disputed_point=pending.disputed_point,
        )

    async def transfer(self, state: ConversationState) -> TransferResult:
        """Execute §5's transfer to human, recorded like any call.

        Idempotent per conversation: once the customer has been put through,
        a repeat — the model asking again, or a turn replayed after a crash —
        reports the same transfer rather than making a second one. A
        `noAgentAvailable` is not remembered: later, someone may be there.
        """
        if state.collected.transferred:
            return TransferAccepted(wait_estimate="a few minutes")
        result = await transfer_to_human(recorder=_StateRecorder(state))
        # §12.4: outside service hours the transfer does not happen. Recorded
        # so the closing message states the real window, not "shortly".
        state.collected.lookup_outcomes.append(result.outcome)
        if isinstance(result, TransferAccepted):
            state.collected.transferred = True
        return result

    async def offer_first(self, state: ConversationState, context: str) -> OfferFacts | None:
        """Fetch and hold one offer, if this is the moment for one.

        At most one offer per conversation, never to a customer who asked for
        a person or is upset, never once the case is open, and only with a
        contract to make it on. None when the recommendation system has
        nothing within budget, or cannot be reached — offers are never a
        dependency.
        """
        collected = state.collected
        if context == "cancellation" and state.current_domain is not DomainKey.cancellation:
            return None
        if (
            collected.offer_status is not None
            or state.pending_offer is not None
            or state.human_requested
            or state.offer_human_now
            or not collected.contract_number
            or "cancellation_request" in collected.handovers
        ):
            return None
        found = await self.offers.best_offer(collected.contract_number)
        if found is None:
            return None
        collected.offer = OfferFacts(**found.model_dump())
        collected.offer_status = "presented"
        state.pending_offer = PendingOffer(context=context, asked_at_turn=state.turn_count)
        return collected.offer

    async def open_retention_case(
        self,
        state: ConversationState,
        trigger: str,
        subject: str,
        stated_reason: str | None = None,
    ) -> HandoverCreated | HandoverCreatedWithoutContractLink | HandoverFailed:
        """The retention team's case, opened by code: for an accepted offer
        (to apply it) or a cancellation after a declined one."""
        self.last_trigger = trigger
        result = (
            await create_handover_record(
                state=state,
                gateway=self._gateway,
                principal=self._principal,
                subject=subject,
                trigger=trigger,
                stated_reason=stated_reason or state.collected.stated_reason,
                record_type="cancellation_request",
                recorder=_StateRecorder(state),
            )
            if self._gateway is not None
            else HandoverFailed(incident_id="no-gateway")
        )
        self.last_handover = result
        return result

    async def open_escalation_case(
        self,
        state: ConversationState,
        trigger: str,
        stated_reason: str | None = None,
    ) -> HandoverCreated | HandoverCreatedWithoutContractLink | HandoverFailed:
        """Open the case a fired trigger calls for (§12.1), by code.

        For an explicit request for a person, and for a "yes" to an offer the
        code made. The subject is templated per trigger and the trigger is the
        one that fired — not re-derived, and not the model's choice.
        """
        return await self._open_case(
            state,
            _StateRecorder(state),
            subject=engine.subject_for(trigger),
            trigger=trigger,
            stated_reason=stated_reason,
            disputed_point=None,
        )

    async def _open_case(
        self,
        state: ConversationState,
        recorder: _StateRecorder,
        *,
        subject: str,
        trigger: str,
        stated_reason: str | None,
        disputed_point: str | None,
    ) -> HandoverCreated | HandoverCreatedWithoutContractLink | HandoverFailed:
        self.last_trigger = trigger
        if self._gateway is None:
            # No CRM connection. §12.4: say it could not be registered rather
            # than claiming it was.
            log.error("handover.no_gateway")
            result: Any = HandoverFailed(incident_id="no-gateway")
        else:
            result = await create_handover_record(
                state=state,
                gateway=self._gateway,
                principal=self._principal,
                subject=subject,
                trigger=trigger,
                stated_reason=stated_reason,
                disputed_point=disputed_point,
                recorder=recorder,
            )
        if not isinstance(result, HandoverFailed):
            state.pending_confirmation = None
        self.last_handover = result
        return result

    def _await_confirmation(
        self, state: ConversationState, args: dict[str, Any], recorder: _StateRecorder
    ) -> HandoverAwaitingConfirmation:
        """Hold the dispute until the customer agrees to its summary.

        Replaces any earlier unconfirmed summary: if the customer said "no, it
        is about X", the model's next proposal is the one to read back.
        """
        summary = (args.get("summary") or "").strip()
        state.pending_confirmation = PendingConfirmation(
            summary=summary,
            asked_at_turn=state.turn_count,
            stated_reason=args.get("stated_reason"),
            disputed_point=args.get("disputed_point"),
        )
        recorder.record(
            CapabilityCall(
                capability="create_handover_record",
                outcome="awaitingConfirmation",
                started_at=datetime.now(UTC),
                duration_ms=0,
            )
        )
        log.info("handover.awaiting_confirmation", turn=state.turn_count)
        return HandoverAwaitingConfirmation(summary=summary)

    def _absorb_billing(self, result: Any, args: dict[str, Any], state: ConversationState) -> None:
        """Move what the lookup learned into CollectedState.

        This is the step that makes §15's domain switch free: the facts are now
        in typed state, so the next domain renders them without a second lookup
        and without re-asking.
        """
        state.collected.lookup_outcomes.append(result.outcome)
        requested = args.get("contract_number", "").strip().upper()

        # Verified figures belong to the contract they were fetched for. A
        # lookup for any other number — found, down, absent, ambiguous or
        # refused — means the conversation has moved to that contract, and the
        # old figures must stop grounding anything. Otherwise "contract B's
        # charge is €29.90" passes the guard on contract A's amount during an
        # outage on B, and B's handover record carries A's figure.
        billing = state.collected.billing
        if billing is not None and billing.contract_number != requested:
            state.collected.billing = None

        # The contract number is a fact about the **conversation**, not a result
        # of the lookup: the customer said it, and `came_from_customer()` has
        # already validated that it appeared in their own words (§5.2). So it is
        # recorded whenever it was acceptable, independently of whether the
        # external system could be reached.
        #
        # Recording it only on `found` was a real defect. With billing down, the
        # customer supplied a valid number and it was discarded — so the handover
        # could not link the contract and the agent had to ask them to repeat it
        # to a human, which is precisely the first minute §13 exists to delete.
        # It also broke §15 on the failure path: a switch to cancellation after
        # a failed lookup would re-ask for the number the customer had given.
        #
        # The two exclusions are deliberate. `rejected` *is* the outcome meaning
        # the number was not acceptable. `multipleMatches` means it resolved to
        # several contracts, so there is not yet a single one to record — that is
        # what the disambiguation turn is for.
        if result.outcome in ("found", "serviceUnavailable", "notFound"):
            if requested:
                state.collected.contract_number = requested
                state.collected.contract_number_source = "customer_verbatim"

        match result.outcome:
            case "found":
                state.collected.billing = BillingFacts(
                    contract_number=requested,
                    status=result.status,
                    failure_reason=result.failure_reason,
                    amount=result.amount,
                    invoice_month=result.invoice_month,
                    next_retry_date=result.next_retry_date,
                    payment_link=result.payment_link,
                    lock_in_until=result.lock_in_until,
                    remaining_instalments=result.remaining_instalments,
                )
                state.collected.disambiguation_candidates = []

            case "multipleMatches":
                state.collected.disambiguation_candidates = [
                    ContractCandidate(
                        contract_number=m.contract_number,
                        service_address=m.service_address,
                    )
                    for m in result.matches
                ]
                # §12.1: unresolved after one disambiguation turn escalates.
                state.disambiguation_turns += 1

            case "rejected" if result.reason == "format_invalid":
                # §4.1 instruction 2: ask once more, then offer a human. The
                # counter is what makes "once more" enforceable.
                state.format_reasks += 1

    @staticmethod
    def _message(call: dict[str, Any], payload: dict[str, Any]) -> ToolMessage:
        return ToolMessage(
            content=json.dumps(payload, ensure_ascii=False, default=str),
            tool_call_id=call["id"],
            name=call["name"],
        )


class _StateRecorder:
    """Adapts ConversationState to the CapabilityRecorder protocol."""

    def __init__(self, state: ConversationState) -> None:
        self._state = state

    def record(self, call: CapabilityCall) -> None:
        self._state.record_capability(call)


def _trigger_for(state: ConversationState) -> str:
    """Why this handover is being opened (§12.2), evaluated **now**.

    Computed at the moment the case is opened, from the state as it stands —
    including the lookup that failed earlier in this same turn. Reading the
    trigger the turn loop stored after the *previous* reply labelled a case
    opened in the same turn as an outage "amount_dispute", which is exactly
    the misreading §12.5 warns about: an integration problem recorded as an
    agent problem, invisible in the distribution it exists to feed.
    """
    trigger = triggers.evaluate(state)
    if trigger:
        return trigger.kind.value
    if state.current_domain is DomainKey.cancellation:
        return triggers.TriggerKind.categorical_cancellation.value
    return triggers.TriggerKind.categorical_amount_dispute.value


def _needs_read_back(state: ConversationState, trigger: str) -> bool:
    """§4.1 instruction 5 applies to disputes, refunds and credits — not to
    every billing handover.

    A case opened because the billing system is down, the contract is absent,
    the customer asked for a person or the conversation stopped progressing is
    instruction 6 or §12.1: offered and opened, with no read-back. Only a case
    whose reason is the dispute itself waits for the customer's agreement.
    """
    if "billing_dispute" in state.collected.handovers:
        return False  # already open; the capability returns the existing one
    if routing.for_domain(state.current_domain).record_type != "billing_dispute":
        return False
    if trigger != triggers.TriggerKind.categorical_amount_dispute.value:
        return False
    pending = state.pending_confirmation
    return not (pending and pending.satisfied)
