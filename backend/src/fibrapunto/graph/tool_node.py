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
from typing import Any

import structlog
from langchain_core.messages import AIMessage, ToolMessage

from fibrapunto.capabilities.billing_lookup import billing_lookup
from fibrapunto.capabilities.create_handover_record import create_handover_record
from fibrapunto.capabilities.outcomes import BillingRejected, HandoverFailed
from fibrapunto.capabilities.runtime import CapabilityCall
from fibrapunto.capabilities.transfer_to_human import transfer_to_human
from fibrapunto.domains.registry import Domain
from fibrapunto.integrations.billing_client import BillingClient
from fibrapunto.perimeter.gateway import CrmGateway
from fibrapunto.perimeter.policy import Principal, svc_agent
from fibrapunto.state.models import (
    BillingFacts,
    ContractCandidate,
    ConversationState,
)

log = structlog.get_logger(__name__)


class ToolDispatcher:
    """Executes the tool calls in an assistant message."""

    def __init__(
        self,
        billing_client: BillingClient,
        gateway: CrmGateway | None = None,
        principal: Principal | None = None,
    ) -> None:
        self._billing = billing_client
        # The agent's own service identity (§8). Passed explicitly rather than
        # defaulted at the call site, so that every CRM access names who is
        # acting — there is no ambient identity anywhere in this codebase.
        self._gateway = gateway
        self._principal = principal or svc_agent()

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
                                "Questa operazione non è disponibile in questo "
                                "contesto. Offri il passaggio a un operatore."
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
                    args.get("numero_contratto", ""),
                    client=self._billing,
                    customer_turns=state.customer_turns(),
                    test_mode=state.test_mode,
                    recorder=recorder,
                )
                self._absorb_billing(result, args, state)

            case "create_handover_record":
                if self._gateway is None:
                    # No CRM connection. §12.4: say it could not be registered
                    # rather than claiming it was.
                    log.error("handover.no_gateway")
                    result = HandoverFailed(incident_id="no-gateway")
                else:
                    result = await create_handover_record(
                        state=state,
                        gateway=self._gateway,
                        principal=self._principal,
                        subject=args.get("riassunto", ""),
                        trigger=_trigger_for(state),
                        stated_reason=args.get("motivo_dichiarato"),
                        disputed_point=args.get("punto_contestato"),
                        recorder=recorder,
                    )

            case "transfer_to_human":
                result = await transfer_to_human(recorder=recorder)
                # §12.4: outside service hours the transfer does not happen.
                # Recorded so the closing message states the real window
                # rather than a comfortable "a breve".
                state.collected.lookup_outcomes.append(result.outcome)

            case _:
                log.error("tool.unknown_capability", capability=name)
                result = BillingRejected(reason="format_invalid")

        return result.model_dump(mode="json")

    def _absorb_billing(self, result: Any, args: dict[str, Any], state: ConversationState) -> None:
        """Move what the lookup learned into CollectedState.

        This is the step that makes §15's domain switch free: the facts are now
        in typed state, so the next domain renders them without a second lookup
        and without re-asking.
        """
        state.collected.lookup_outcomes.append(result.outcome)

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
            number = args.get("numero_contratto", "").strip().upper()
            if number:
                state.collected.contract_number = number
                state.collected.contract_number_source = "customer_verbatim"

        match result.outcome:
            case "found":
                state.collected.billing = BillingFacts(
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
    """Why this handover is being opened (§12.2).

    Prefers the trigger the engine actually computed, so the record says
    "conditional:service_unavailable" when that is what happened rather than a
    category inferred from the domain. §12.5 reads this distribution to tell an
    integration problem from an agent problem, and a field that always says the
    same thing tells it nothing.
    """
    if state.pending_trigger:
        return state.pending_trigger

    from fibrapunto.domains.registry import DomainKey

    if state.current_domain is DomainKey.cancellation:
        return "categorical:cancellation"
    return "categorical:amount_dispute"
