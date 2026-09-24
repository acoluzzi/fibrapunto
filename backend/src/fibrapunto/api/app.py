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
from typing import Any
from uuid import UUID

import structlog
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from fibrapunto import faults
from fibrapunto.api import auth, operator
from fibrapunto.api.auth import require_operator
from fibrapunto.conversation import AgentReply, Conversation
from fibrapunto.domains.registry import DOMAINS
from fibrapunto.llm.chat import ReasoningLayerUnavailable
from fibrapunto.observability.events import EventType
from fibrapunto.observability.trace import TraceStore, stream_events
from fibrapunto.perimeter.gateway import CrmGateway
from fibrapunto.perimeter.policy import PerimeterError, platform_admin, svc_agent
from fibrapunto.privacy import retention
from fibrapunto.prompts.loader import reload_all

log = structlog.get_logger(__name__)

_sessions: dict[str, Conversation] = {}
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

app.include_router(auth.router)
app.include_router(operator.router)

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
    text: str


@app.post("/api/sessions", response_model=SessionCreated)
async def create_session(request: CreateSession) -> SessionCreated:
    convo = Conversation(test_mode=request.test_mode, gateway=_gateway)
    _sessions[str(convo.state.id)] = convo

    await _persist("open_conversation", lambda store: store.open_conversation(convo.state))

    return SessionCreated(conversation_id=str(convo.state.id), domain=convo.domain.value)


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
async def send_turn(conversation_id: str, request: SendTurn) -> AgentReply:
    convo = _require(conversation_id)
    reply = await convo.send(request.text)

    stored = await _persist("store_events", lambda store: store.store_events(convo.trace.pending))
    # Redaction happens at the write boundary (§9), so what lands on disk is
    # redacted regardless of whether inbound redaction was on for the model.
    stored = stored and await _persist(
        "store_transcript", lambda store: store.store_transcript(convo.state)
    )
    if stored:
        # Clearing costs a late-joining browser its replay, so remember how far
        # we have persisted instead. The recorder is bounded by the turn budget
        # (§12.1), so it cannot grow without limit. Not marked when a write
        # failed, so the next turn retries what this one could not store.
        convo.trace.mark_persisted()

    return reply


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
async def get_session(conversation_id: str) -> dict[str, Any]:
    return _require(conversation_id).snapshot()


@app.get("/api/sessions/{conversation_id}/events")
async def session_events(conversation_id: str) -> EventSourceResponse:
    """The live trace (§17.10).

    Assistant replies and trace events share one channel deliberately: it
    preserves the interleaving between "the agent said X" and "the capability
    returned outcome Y", and that interleaving is what makes the trace panel
    convincing rather than decorative.
    """
    convo = _require(conversation_id)

    async def generator():
        async for payload in stream_events(convo.trace):
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
async def session_trace(conversation_id: str) -> list[dict[str, Any]]:
    """The persisted trace, for the harness and for a session that has ended."""
    if not _store:
        raise HTTPException(503, "trace store unavailable")
    return await _store.read_events(UUID(conversation_id))


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


def _require(conversation_id: str) -> Conversation:
    convo = _sessions.get(conversation_id)
    if convo is None:
        raise HTTPException(404, "no such conversation")
    return convo


__all__ = ["app", "EventType"]
