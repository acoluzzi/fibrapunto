"""The HTTP surface (§17.8).

    "A programmatic conversation interface, so the agent can be driven by an
    external test harness rather than only by hand."

The eval harness drives exactly these routes, because §14.2 requires it to be
"an external program, outside the agent's platform". Importing `Conversation`
in-process would be easier and would quietly mean the interface this section
demands never gets exercised by the thing it exists for.

Sessions are in-memory. §3 says the agent has no memory across conversations, so
there is nothing to persist between them; what *is* persisted is the trace, into
the platform schema, for §14 and §17.10.
"""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID

import structlog
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from fibrapunto import faults
from fibrapunto.api import analyst, auth, customer_auth, evals, operator
from fibrapunto.api.auth import require_operator
from fibrapunto.capabilities.runtime import CapabilityCall
from fibrapunto.conversation import AgentReply, Conversation
from fibrapunto.domains.registry import DOMAINS
from fibrapunto.escalation import engine
from fibrapunto.handover import lifecycle
from fibrapunto.integrations.recommendation_client import RecommendationClient
from fibrapunto.llm.chat import ReasoningLayerUnavailable
from fibrapunto.observability.events import EventType, TraceEvent
from fibrapunto.observability.trace import TraceRecorder, TraceStore, stream_events
from fibrapunto.perimeter.gateway import CrmGateway
from fibrapunto.perimeter.policy import PerimeterError, platform_admin, svc_agent
from fibrapunto.persistence.sessions import (
    CheckpointConflict,
    ConversationBusy,
    ConversationNotFound,
    SessionStore,
    TurnConflict,
)
from fibrapunto.privacy import redaction, retention
from fibrapunto.prompts.loader import reload_all
from fibrapunto.state import checkpoint
from fibrapunto.state.checkpoint import UnsupportedCheckpoint
from fibrapunto.state.models import TurnRole

log = structlog.get_logger(__name__)

# Live trace recorders by conversation, so a browser watching the trace keeps
# receiving events across turns. A cache: after a restart it is re-seeded from
# platform.trace_event (best-effort), while the conversation itself comes back
# from its checkpoint (critical) — persistence/sessions.py.
_recorders: dict[str, TraceRecorder] = {}
_sessions_store: SessionStore | None = None
_store: TraceStore | None = None
_gateway: CrmGateway | None = None
_operator_pool = None
_sweep: asyncio.Task | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _store, _gateway, _operator_pool
    # One pool, opened under the agent's own database role (§8). Every
    # conversation shares it; none of them can widen it.
    _gateway = await CrmGateway.connect(svc_agent())
    # The operator view runs as platform_admin: it reads internal_comments and
    # the completeness view, both of which the agent is denied (§8). Two pools,
    # two identities, and the difference is visible in what each can see.
    import asyncpg

    from fibrapunto.settings import get_settings

    _operator_pool = await asyncpg.create_pool(
        get_settings().dsn("platform"), min_size=1, max_size=3
    )
    operator.set_pool(_operator_pool)
    operator.set_notifier(_notify_conversation)
    operator.set_conversation_reader(_conversation_state)
    analyst.set_pool(_operator_pool)
    evals.set_pool(_operator_pool)
    global _sessions_store
    _sessions_store = SessionStore(_operator_pool)

    try:
        _store = await TraceStore.connect()
    except Exception as exc:  # noqa: BLE001
        # A conversation must not fail because the trace store is unavailable.
        # Losing a trace is bad; refusing to talk to a customer because we
        # cannot log it is worse.
        log.warning("trace.store_unavailable", error=str(exc))
        _store = None

    # §9's retention period, applied without anyone remembering to apply it.
    global _sweep
    _sweep = asyncio.create_task(retention.sweep_forever(_operator_pool))

    yield

    _sweep.cancel()
    with suppress(asyncio.CancelledError):
        await _sweep
    if _store:
        await _store.close()
    if _gateway:
        await _gateway.close()
    if _operator_pool:
        await _operator_pool.close()


app = FastAPI(
    title="Fibrapunto — assistant",
    version="0.1.0",
    lifespan=lifespan,
)

for _role_router in auth.routers.values():
    app.include_router(_role_router)
app.include_router(operator.router)
app.include_router(analyst.router)
app.include_router(evals.router)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# --- sessions ----------------------------------------------------------------


class CreateSession(BaseModel):
    test_mode: str | None = Field(
        default=None,
        description=(
            "Which failure mode the billing system is in for this session: "
            "timeout | slow. Session-scoped, so the harness can run cases "
            "concurrently without them colliding (§7). An absent or duplicated "
            "contract is not a mode: it is the number the customer types."
        ),
    )


class SessionCreated(BaseModel):
    conversation_id: str
    domain: str


class SendTurn(BaseModel):
    client_turn_id: UUID = Field(
        description=(
            "One per message the customer submits, reused when retrying it. A "
            "retry returns the stored reply instead of calling the model again."
        )
    )
    text: str


def _store_required() -> SessionStore:
    if _sessions_store is None:
        raise HTTPException(503, "conversation store unavailable")
    return _sessions_store


async def _principal(request: Request) -> UUID:
    return await customer_auth.require_principal(_store_required().pool, request)


async def _owned(conversation_id: str, request: Request) -> UUID:
    """Ownership, checked on every session route. Another browser's
    conversation is a 404 — never a 403 that would confirm it exists."""
    try:
        cid = UUID(conversation_id)
        await _store_required().require_owned(cid, await _principal(request))
    except (ValueError, ConversationNotFound):
        raise HTTPException(404, "no such conversation") from None
    return cid


@app.post("/api/customer/access")
async def customer_access(request: Request, response: Response) -> dict[str, bool]:
    """Create or refresh this browser's anonymous identity (api/customer_auth.py).

    Identifies a browser installation, not a person: it lets a browser find its
    own conversations again, and authenticates nothing about the customer."""
    await customer_auth.access(_store_required().pool, request, response)
    return {"ok": True}


@app.post("/api/sessions", response_model=SessionCreated)
async def create_session(request: CreateSession, http: Request) -> SessionCreated:
    principal = await _principal(http)
    convo = Conversation(test_mode=request.test_mode, gateway=_gateway)
    # Critical: the conversation and its first checkpoint, or nothing.
    await _store_required().create(convo.state, principal)
    _recorders[str(convo.state.id)] = convo.trace
    if await _persist("store_events", lambda store: store.store_events(convo.trace.pending)):
        convo.trace.mark_persisted()
    return SessionCreated(conversation_id=str(convo.state.id), domain=convo.domain.value)


@app.get("/api/sessions")
async def list_sessions(
    http: Request, status: str | None = None, cursor: str | None = None
) -> dict[str, Any]:
    """This browser's conversations — and only this browser's."""
    items, next_cursor = await _store_required().list(
        await _principal(http), status=status, cursor=cursor
    )
    return {"items": items, "next_cursor": next_cursor}


@app.exception_handler(ReasoningLayerUnavailable)
async def reasoning_layer_unavailable(request: Request, exc: ReasoningLayerUnavailable):
    """503, with the provider's own words (§17.10).

    The alternative is a 500 and a stack trace in a container log, which is what
    this used to be. A model provider that is unreachable — rate limited, out of
    credit, or simply down — is an operational fact the operator needs stated,
    and the demo where it happens is exactly the one where nobody has time to
    read logs.

    Not dressed up as a reply from the agent. §12.4's principle: report what
    happened, not what was supposed to.
    """
    log.error("reasoning_layer.unavailable", reason=exc.reason, hint=exc.hint)
    return JSONResponse(
        status_code=503,
        content={
            "error": "reasoning_layer_unavailable",
            "message": "The reasoning service is unreachable.",
            "provider_said": exc.reason,
            "hint": exc.hint,
        },
    )


@app.post("/api/sessions/{conversation_id}/turns", response_model=AgentReply)
async def send_turn(conversation_id: str, request: SendTurn, http: Request) -> Any:
    """One turn, resumable and idempotent (persistence/sessions.py).

    Ownership, then the lease, then the stored reply for this client_turn_id if
    there is one. Otherwise the conversation is rebuilt from its checkpoint and
    its redacted transcript, the turn runs, and transcript + checkpoint +
    reply are committed together. The trace is written afterwards, best-effort.
    """
    cid = await _owned(conversation_id, http)
    store = _store_required()
    try:
        lease = await store.acquire(cid)
    except ConversationBusy:
        raise HTTPException(409, "another turn of this conversation is in progress") from None
    except ConversationNotFound:
        raise HTTPException(404, "no such conversation") from None

    committed = False
    try:
        stored = await store.stored_reply(cid, request.client_turn_id, request.text)
        if stored is not None:
            return stored  # a retry: the reply already given, no model call
        try:
            saved = checkpoint.load(lease.schema_version, lease.state_json)
        except UnsupportedCheckpoint as exc:
            log.error("session.unsupported_checkpoint", conversation_id=str(cid), error=str(exc))
            raise HTTPException(
                409, "this conversation cannot be resumed by this version of the service"
            ) from None
        await store.begin_turn(cid, request.client_turn_id, request.text)

        state = saved.to_state(await store.transcript(cid))
        trace = await _recorder_for(cid)
        state.capability_calls = _calls_from(trace)
        convo = Conversation.resume(state, trace, gateway=_gateway)

        reply = await convo.send(request.text)
        # The commit releases the lease itself, in the same transaction.
        await store.commit(
            lease, convo.state, request.client_turn_id, reply.model_dump(mode="json")
        )
        committed = True
    except TurnConflict:
        raise HTTPException(409, "client_turn_id reused for a different message") from None
    except CheckpointConflict:
        raise HTTPException(409, "the conversation moved on while this turn ran; retry") from None
    finally:
        if not committed:
            await store.release(lease)

    if await _persist("store_events", lambda s: s.store_events(trace.pending)):
        # Not marked when the write failed, so the next turn retries it.
        trace.mark_persisted()
    return reply


async def _recorder_for(cid: UUID) -> TraceRecorder:
    """The conversation's live recorder, re-seeded from the stored trace after
    a restart so the panel replays the whole conversation, not just its tail."""
    key = str(cid)
    if key in _recorders:
        return _recorders[key]
    recorder = TraceRecorder(cid)
    if _store:
        try:
            faults.fail_if_down("trace_store")
            for row in await _store.read_events(cid):
                recorder.events.append(
                    TraceEvent(
                        name=EventType(row["name"]),
                        conversation_id=cid,
                        turn_seq=row["turn_seq"],
                        start=row["start_ts"],
                        duration_ms=row["duration_ms"],
                        attributes=_attributes(row["attributes"]),
                    )
                )
        except Exception as exc:  # noqa: BLE001 — best-effort, like every trace read
            log.warning("trace.reseed_failed", conversation_id=key, error=str(exc))
        recorder.mark_persisted()
    _recorders[key] = recorder
    return recorder


def _attributes(value: Any) -> dict[str, Any]:
    return json.loads(value) if isinstance(value, str) else dict(value or {})


def _calls_from(trace: TraceRecorder) -> list[CapabilityCall]:
    """The capability log, rebuilt from the trace for the handover payload's
    history (§12.2). Best-effort by design: nothing that decides reads it."""
    return [
        CapabilityCall(
            capability=e.attributes.get("capability", ""),
            outcome=e.attributes.get("outcome", ""),
            started_at=e.start,
            duration_ms=e.duration_ms or e.attributes.get("duration_ms") or 0,
            incident_id=e.attributes.get("incident_id"),
        )
        for e in trace.events
        if e.name is EventType.capability_result
    ]


async def _persist(what: str, write) -> bool:
    """One write to the trace store, never allowed to fail the turn.

    The customer's reply is already decided when this runs. Losing a trace row
    is an observability gap to log and alert on; losing the reply because the
    observability database is down would be the tail wagging the dog.
    """
    if not _store:
        return False
    try:
        faults.fail_if_down("trace_store")
        await write(_store)
    except Exception as exc:  # noqa: BLE001 — any store failure is non-fatal
        log.error("trace_store.write_failed", what=what, error=str(exc))
        return False
    return True


# --- a colleague's decision on an accepted offer ------------------------------


class OfferDecision(BaseModel):
    decision: Literal["confirm", "decline"]
    reason: str | None = Field(
        default=None,
        description="Required to decline: shown to the customer, in their conversation.",
    )


@app.post("/api/operator/records/{reference}/offer", dependencies=[Depends(require_operator)])
async def decide_offer(reference: str, request: OfferDecision) -> dict[str, Any]:
    """Confirm or decline the offer a customer accepted, and tell them.

    The customer hears it in their own conversation — a message written from a
    template at this decision, never by the model — and by email, queued in the
    outbox (not sent: demo). A declined offer's cost goes back to the budget.
    The customer's conversation is updated under its lease, like any turn, so
    the agent knows the outcome if they reply.
    """
    if _operator_pool is None:
        raise HTTPException(503, "database unavailable")
    row = await _operator_pool.fetchrow(
        """
        SELECT r.id, r.reference, r.origin_session_id, r.collected_context, c.email
          FROM crm.handover_record r
          LEFT JOIN crm.customer c ON c.id = r.customer_id
         WHERE r.reference = $1
        """,
        reference,
    )
    if row is None:
        raise HTTPException(404, f"no record with reference {reference}")
    context = _attributes(row["collected_context"])
    offer = context.get("retention_offer") or {}
    if offer.get("status") != "accepted":
        raise HTTPException(409, "no accepted offer awaiting a decision on this case")
    reason = (request.reason or "").strip()
    if request.decision == "decline" and not reason:
        raise HTTPException(422, "a reason is required to decline: the customer is told it")

    # The operator's words reach the customer: scrubbed like any outbound text.
    reason = redaction.for_customer(reason)[0] if reason else ""
    confirmed = request.decision == "confirm"
    text = (
        engine.offer_confirmed(offer["title"], reference)
        if confirmed
        else engine.offer_declined(offer["title"], reference, reason)
    )
    cid = row["origin_session_id"]

    # 1. Tell the customer in their conversation. Refused (409) if a turn is in
    #    progress, before anything else changes, so the colleague can retry.
    notified = await _notify_conversation(
        cid, text, "confirmed" if confirmed else "declined_by_team"
    )

    # 2. The budget follows the decision: a confirmed offer's hold becomes
    #    spend, a declined offer's hold goes back. Either may fail to reach the
    #    system; the decision stands and the operator is told.
    released = None
    settled = None
    if confirmed:
        settled = await RecommendationClient().confirm(offer["offer_id"], cid)
    else:
        released = await RecommendationClient().release(offer["offer_id"], cid)

    # 3. The case and the outbox, together.
    offer.update(
        status="confirmed" if confirmed else "declined_by_team",
        decision_reason=reason or None,
        decided_at=datetime.now(UTC).isoformat(),
        customer_notified_in_chat=notified,
    )
    context["retention_offer"] = offer
    async with _operator_pool.acquire() as conn, conn.transaction():
        await conn.execute(
            """
            UPDATE crm.handover_record
               SET collected_context = $2::jsonb, status = $3::crm.record_status
             WHERE id = $1
            """,
            row["id"],
            json.dumps(context, default=str, ensure_ascii=False),
            # The case's status is owned by handover/lifecycle.py: the
            # operator's own take/close action and this decision must agree
            # on what a confirmed offer does to the case.
            lifecycle.after_offer_decision(confirmed),
        )
        if row["email"]:
            await conn.execute(
                """
                INSERT INTO platform.notification_outbox
                    (reference, conversation_id, channel, recipient, subject, body)
                VALUES ($1, $2, 'email', $3, $4, $5)
                """,
                reference,
                cid,
                row["email"],
                f"Your Fibrapunto offer — case {reference}",
                text,
            )
    return {
        "reference": reference,
        "status": offer["status"],
        "customer_notified_in_chat": notified,
        "email_queued": bool(row["email"]),
        "budget_released": released,
        "budget_settled": settled,
    }


async def _conversation_state(cid: UUID) -> dict[str, Any] | None:
    """Where the customer's conversation stands, for the operator view. Read
    without the lease, so it answers while the customer is mid-turn. None
    when the conversation no longer exists (purged)."""
    store = _store_required()
    found = await store.peek(cid)
    if found is None:
        return None
    version, payload = found
    try:
        saved = checkpoint.load(version, payload)
    except UnsupportedCheckpoint:
        return None
    status = saved.status.value
    open_case = saved.facts.handover_reference is not None
    return {
        "exists": True,
        "status": status,
        "transferred": saved.facts.transferred,
        # Put through to a colleague, and the conversation still open on
        # their side: the customer is in the chat, expecting someone.
        "waiting": saved.facts.transferred and status == "handed_over" and open_case,
    }


async def _notify_conversation(cid: UUID, text: str, offer_status: str | None = None) -> bool:
    """Append a message to the customer's conversation, as the platform.

    Same lease as a customer turn, so it cannot interleave with one; retried
    briefly, then refused. False when the conversation no longer exists
    (purged) — the email still goes to the outbox.
    """
    store = _store_required()
    for _ in range(10):
        try:
            lease = await store.acquire(cid)
            break
        except ConversationBusy:
            await asyncio.sleep(0.3)
        except ConversationNotFound:
            return False
    else:
        raise HTTPException(409, "the customer is mid-turn; try again in a moment")

    committed = False
    try:
        state = checkpoint.load(lease.schema_version, lease.state_json).to_state(
            await store.transcript(cid)
        )
        state.record_turn(TurnRole.agent, text)
        state.collected.told_to_customer.append(text)
        if offer_status is not None:
            state.collected.offer_status = offer_status
        await store.commit(lease, state, None, None)
        committed = True
    except UnsupportedCheckpoint:
        return False
    finally:
        if not committed:
            await store.release(lease)

    trace = await _recorder_for(cid)
    trace.emit(EventType.customer_notified, text=text, source="operator")
    if await _persist("store_events", lambda s: s.store_events(trace.pending)):
        trace.mark_persisted()
    return True


# --- demo failure switches (faults.py) ----------------------------------------


class FaultSwitches(BaseModel):
    crm: faults.UpDown | None = None
    colleagues: str | None = None
    model: faults.UpDown | None = None
    credential_store: faults.UpDown | None = None
    trace_store: faults.UpDown | None = None


def _require_fault_injection() -> None:
    if not faults.enabled():
        raise HTTPException(404, "fault injection is off (FAULT_INJECTION=false)")


@app.get("/api/faults", dependencies=[Depends(_require_fault_injection)])
async def get_faults() -> dict[str, str]:
    """The live state of every switchable module (§7). Billing is the stub's."""
    return faults.current().model_dump()


@app.post("/api/faults", dependencies=[Depends(_require_fault_injection)])
async def set_faults(request: FaultSwitches) -> dict[str, str]:
    """Flip one or more modules. Sticky until flipped back: a switch the
    control strip shows is a switch that decides, not one that expires."""
    changes = {k: v for k, v in request.model_dump().items() if v is not None}
    try:
        return faults.update(**changes).model_dump()
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.get("/api/sessions/{conversation_id}")
async def get_session(conversation_id: str, http: Request) -> dict[str, Any]:
    """One of this browser's conversations: its stored (redacted) transcript
    and the replies it received, so a reopened conversation shows what
    happened and the metrics panel can be rebuilt."""
    cid = await _owned(conversation_id, http)
    store = _store_required()
    return {
        "conversation_id": str(cid),
        "messages": [
            {"role": role, "text": text} for role, text, _domain in await store.transcript(cid)
        ],
        "replies": await store.replies(cid),
    }


@app.get("/api/sessions/{conversation_id}/events")
async def session_events(conversation_id: str, http: Request) -> EventSourceResponse:
    """The live trace (§17.10).

    Assistant replies and trace events share one channel deliberately: it
    preserves the interleaving between "the agent said X" and "the capability
    returned outcome Y", and that interleaving is what makes the trace panel
    convincing rather than decorative. The HttpOnly cookie comes with the
    EventSource request, so ownership is checked here like everywhere else.
    """
    trace = await _recorder_for(await _owned(conversation_id, http))

    async def generator():
        async for payload in stream_events(trace):
            # Two things this line has to get right, and it got both wrong:
            #
            # 1. **Unnamed.** SSE's `event:` field routes to
            #    addEventListener(name), never to onmessage. Naming each event
            #    meant a browser had to enumerate all seventeen types to receive
            #    any of them. The name is in the payload, which is where every
            #    consumer already reads it from.
            # 2. **Serialised here.** Handing sse-starlette a dict yields
            #    `str(dict)` — Python repr, with single quotes and None — which
            #    is not JSON and which `JSON.parse` rejects.
            #
            # Both failed silently: the stream connected, frames arrived, and
            # the panel stayed empty.
            yield {"data": json.dumps(payload, default=str, ensure_ascii=False)}

    return EventSourceResponse(generator())


@app.get("/api/sessions/{conversation_id}/trace")
async def session_trace(conversation_id: str, http: Request) -> list[dict[str, Any]]:
    """The persisted trace, for the harness and for a session that has ended."""
    cid = await _owned(conversation_id, http)
    if not _store:
        raise HTTPException(503, "trace store unavailable")
    return await _store.read_events(cid)


# --- what the system is (§17.1, §8) ------------------------------------------


@app.get("/api/domains")
async def domains() -> list[dict[str, Any]]:
    """The domains and their enumerated capability lists.

    Rendered by the UI so the panel shows what the agent *can* do in this
    conversation, not only what it did.
    """
    return [
        {
            "key": key.value,
            "enabled": domain.enabled,
            "scope_statement": domain.scope_statement,
            "capabilities": [c.value for c in domain.capabilities],
            "always_escalates": domain.always_escalates,
            "prompt_sha": domain.prompt.short_sha if domain.enabled else None,
        }
        for key, domain in DOMAINS.items()
    ]


@app.post("/api/_admin/perimeter-probe")
async def perimeter_probe(entity: str = "customer", field: str = "email") -> dict[str, Any]:
    """§8: "Test the boundary, do not trust it."

    The same check under both identities, so the UI can render the diff. The
    database is the authority — this endpoint reports the policy layer, which
    exists so the denial has a readable reason (friction log 1). It evaluates
    contracts/perimeter-policy.yaml in Python and never touches the database.

    Left open, unlike the other admin routes: the chat page calls it on load,
    and it returns a verdict about the policy, never a row.
    """
    result = {}
    for name, principal in (("svc_agent", svc_agent()), ("platform_admin", platform_admin())):
        try:
            principal.check(entity, "read", (field,))
            result[name] = {"allowed": True}
        except PerimeterError as exc:
            result[name] = {"allowed": False, "reason": str(exc)}
    return {"entity": entity, "field": field, **result}


@app.post("/api/_admin/reload-prompts", dependencies=[Depends(require_operator)])
async def reload_prompts() -> dict[str, Any]:
    """Pick up edited instruction files without a restart.

    This is the operator-editable half of docs/DECISIONS.md #12 tier 1: change a
    procedure, reload, and the next conversation follows it. No engineer, no
    redeploy.
    """
    reload_all()
    return {
        "reloaded": True,
        "prompts": {
            key.value: domain.prompt.short_sha for key, domain in DOMAINS.items() if domain.enabled
        },
    }


@app.get("/api/retention")
async def retention_status() -> dict[str, Any]:
    """§9: "a retention period short enough to be defensible".

    Surfaced rather than only logged, because "what is your retention period and
    can you show me it is being applied" is a question with an auditor behind
    it. `overdue` is the number that matters: anything but zero means the sweep
    is not keeping up.
    """
    if _operator_pool is None:
        raise HTTPException(503, "database unavailable")
    return await retention.retention_status(_operator_pool)


@app.post("/api/_admin/purge-now", dependencies=[Depends(require_operator)])
async def purge_now() -> dict[str, Any]:
    """Run the sweep immediately, for the demo and for tests.

    The hourly sweep is the mechanism; this is how you show it working without
    waiting an hour or falsifying a clock.
    """
    if _operator_pool is None:
        raise HTTPException(503, "database unavailable")
    result = await retention.purge_expired(_operator_pool)
    return {
        "conversations": result.conversations,
        "turns": result.turns,
        "events": result.events,
    }


@app.get("/api/health")
async def health() -> dict[str, Any]:
    return {"status": "ok", "trace_store": _store is not None}


__all__ = ["app", "EventType"]
