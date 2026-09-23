"""Collect and persist trace events (§17.10, §9).

The recorder is in-memory first and persisted second, on purpose: a conversation
must not fail because the trace store is unavailable. Losing a trace is bad;
dropping a customer's turn because we could not log it is worse.

Persistence runs as the **platform** identity, into the `platform` schema. The
agent has no grant there at all — it cannot read its own transcripts, which is
the same decision as the email restriction and made for the same reason (§8).

Transcript text is stored redacted (§9), and the redaction happens on this side
of the boundary — `store_turn` redacts whatever it is given rather than trusting
its caller to have done so.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import asyncpg
import structlog

from fibrapunto.observability.events import EventType, TraceEvent
from fibrapunto.privacy import redaction
from fibrapunto.settings import get_settings

log = structlog.get_logger(__name__)


class TraceRecorder:
    """Per-conversation event collector.

    Also the live feed for the UI: subscribers receive events as they happen,
    which is what preserves the interleaving between "the agent said X" and
    "the capability returned outcome Y" in the trace panel.
    """

    def __init__(self, conversation_id: UUID) -> None:
        self.conversation_id = conversation_id
        self.events: list[TraceEvent] = []
        self._subscribers: list[Callable[[TraceEvent], None]] = []

    def emit(
        self,
        name: EventType,
        turn_seq: int | None = None,
        duration_ms: int | None = None,
        **attributes: Any,
    ) -> TraceEvent:
        event = TraceEvent(
            name=name,
            conversation_id=self.conversation_id,
            turn_seq=turn_seq,
            duration_ms=duration_ms,
            attributes=attributes,
        )
        self.events.append(event)
        for notify in self._subscribers:
            try:
                notify(event)
            except Exception:  # noqa: BLE001
                # A broken subscriber (a disconnected browser) must not take
                # the conversation with it.
                log.warning("trace.subscriber_failed", event=name.value)
        return event

    def subscribe(self, callback: Callable[[TraceEvent], None]) -> Callable[[], None]:
        self._subscribers.append(callback)
        return lambda: self._subscribers.remove(callback)

    def by_name(self, name: EventType) -> list[TraceEvent]:
        return [e for e in self.events if e.name is name]


class TraceStore:
    """Writes conversations, turns and events to the platform schema."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    @classmethod
    async def connect(cls) -> TraceStore:
        settings = get_settings()
        pool = await asyncpg.create_pool(settings.dsn("platform"), min_size=1, max_size=5)
        return cls(pool)

    async def close(self) -> None:
        await self._pool.close()

    async def open_conversation(self, state: Any) -> None:
        settings = get_settings()
        retention_until = datetime.now(UTC) + timedelta(days=settings.transcript_retention_days)
        await self._pool.execute(
            """
            INSERT INTO platform.conversation
                (id, channel, test_mode, agent_model, agent_model_served,
                 router_model, prompt_sha, retention_until)
            VALUES ($1, 'web', $2, $3, $4, $5, $6, $7)
            ON CONFLICT (id) DO NOTHING
            """,
            state.id,
            state.test_mode,
            state.agent_model,
            state.agent_model_served,
            get_settings().model_router,
            state.prompt_sha,
            retention_until,
        )

    async def store_transcript(self, state: Any) -> None:
        """Persist every message recorded so far, redacted (§9).

        **The only write path to `platform.turn`**, deliberately. Redaction
        happens here rather than being required of the caller: the column used
        to be fed by a `store_turn(text_redacted=...)` in the hope that the
        parameter name would make callers careful, which is the same class of
        control as an instruction in a prompt — §8.1's argument, applied to
        ourselves. A second write path would be the hole.

        Writes the whole transcript rather than the latest pair, and relies on
        `ON CONFLICT DO NOTHING` to skip what is already there. `seq` is the
        index into the conversation's own message list, so the ordering stored
        is the ordering the agent reasoned over — there is no second numbering
        scheme to drift out of step with the first.
        """
        rows = [
            (state.id, seq, turn.role.value, *redaction.for_storage(turn.text))
            for seq, turn in enumerate(state.turns)
        ]
        if not rows:
            return
        await self._pool.executemany(
            """
            INSERT INTO platform.turn
                (conversation_id, seq, role, text_redacted, redaction_flags)
            VALUES ($1, $2, $3::platform.turn_role, $4, $5::jsonb)
            ON CONFLICT (conversation_id, seq) DO NOTHING
            """,
            [(cid, seq, role, text, _json(flags)) for cid, seq, role, text, flags in rows],
        )

    async def store_events(self, events: list[TraceEvent]) -> None:
        if not events:
            return
        await self._pool.executemany(
            """
            INSERT INTO platform.trace_event
                (conversation_id, turn_seq, name, start_ts, duration_ms, attributes)
            VALUES ($1, $2, $3, $4, $5, $6::jsonb)
            """,
            [
                (
                    e.conversation_id,
                    e.turn_seq,
                    e.name.value,
                    e.start,
                    e.duration_ms,
                    _json(e.attributes),
                )
                for e in events
            ],
        )

    async def close_conversation(self, conversation_id: UUID, status: str) -> None:
        await self._pool.execute(
            """
            UPDATE platform.conversation
               SET ended_at = now(), final_status = $2
             WHERE id = $1
            """,
            conversation_id,
            status,
        )

    async def read_events(self, conversation_id: UUID) -> list[dict[str, Any]]:
        rows = await self._pool.fetch(
            """
            SELECT name, turn_seq, start_ts, duration_ms, attributes
              FROM platform.trace_event
             WHERE conversation_id = $1
             ORDER BY id
            """,
            conversation_id,
        )
        return [dict(r) for r in rows]


def _json(value: dict[str, Any]) -> str:
    import json

    return json.dumps(value, default=str, ensure_ascii=False)


async def stream_events(recorder: TraceRecorder) -> AsyncIterator[dict[str, Any]]:
    """Yield trace events as they happen, for the SSE endpoint."""
    import asyncio

    queue: asyncio.Queue[TraceEvent] = asyncio.Queue()
    unsubscribe = recorder.subscribe(queue.put_nowait)

    # Replay what already happened, so a browser that connects late still sees
    # the whole conversation rather than joining mid-story.
    for event in list(recorder.events):
        yield event.for_stream()

    try:
        while True:
            event = await queue.get()
            yield event.for_stream()
            if event.name is EventType.session_ended:
                return
    finally:
        unsubscribe()
