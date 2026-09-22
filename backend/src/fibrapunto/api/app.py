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

from contextlib import asynccontextmanager
from typing import Any
from uuid import UUID

import structlog
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from fibrapunto.conversation import AgentReply, Conversation
from fibrapunto.domains.registry import DOMAINS
from fibrapunto.observability.events import EventType
from fibrapunto.observability.trace import TraceStore, stream_events
from fibrapunto.perimeter.policy import PerimeterError, platform_admin, svc_agent
from fibrapunto.prompts.loader import reload_all

log = structlog.get_logger(__name__)

_sessions: dict[str, Conversation] = {}
_store: TraceStore | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _store
    try:
        _store = await TraceStore.connect()
    except Exception as exc:  # noqa: BLE001
        # A conversation must not fail because the trace store is unavailable.
        # Losing a trace is bad; refusing to talk to a customer because we
        # cannot log it is worse.
        log.warning("trace.store_unavailable", error=str(exc))
        _store = None
    yield
    if _store:
        await _store.close()


app = FastAPI(
    title="Fibrapunto — assistente",
    version="0.1.0",
    lifespan=lifespan,
)

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
            "timeout | duplicate | notfound | slow. Session-scoped, so the "
            "harness can run cases concurrently without them colliding (§7)."
        ),
    )


class SessionCreated(BaseModel):
    conversation_id: str
    domain: str


class SendTurn(BaseModel):
    text: str


@app.post("/api/sessions", response_model=SessionCreated)
async def create_session(request: CreateSession) -> SessionCreated:
    convo = Conversation(test_mode=request.test_mode)
    _sessions[str(convo.state.id)] = convo

    if _store:
        await _store.open_conversation(convo.state)

    return SessionCreated(conversation_id=str(convo.state.id), domain=convo.domain.value)


@app.post("/api/sessions/{conversation_id}/turns", response_model=AgentReply)
async def send_turn(conversation_id: str, request: SendTurn) -> AgentReply:
    convo = _require(conversation_id)
    reply = await convo.send(request.text)

    if _store:
        await _store.store_events(convo.trace.events)
        # NOTE: turn text is deliberately not stored yet. §9 requires
        # transcripts to be stored redacted, and the redaction pipeline lands
        # on day 10. Writing raw text now and cleaning it later would mean raw
        # customer text in the database for six days, which is the thing §9
        # exists to prevent.
        convo.trace.events.clear()

    return reply


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
            yield {"event": payload["name"], "data": payload}

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
    exists so the denial has a readable reason (friction log 1).
    """
    result = {}
    for name, principal in (("svc_agent", svc_agent()), ("platform_admin", platform_admin())):
        try:
            principal.check(entity, "read", (field,))
            result[name] = {"allowed": True}
        except PerimeterError as exc:
            result[name] = {"allowed": False, "reason": str(exc)}
    return {"entity": entity, "field": field, **result}


@app.post("/api/_admin/reload-prompts")
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


@app.get("/api/health")
async def health() -> dict[str, Any]:
    return {"status": "ok", "trace_store": _store is not None}


def _require(conversation_id: str) -> Conversation:
    convo = _sessions.get(conversation_id)
    if convo is None:
        raise HTTPException(404, "no such conversation")
    return convo


__all__ = ["app", "EventType"]
