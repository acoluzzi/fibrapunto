"""Conversations that survive a restart (migration 006).

Checkpoint persistence is **critical**: a turn is not done until its transcript,
its checkpoint and its stored reply are committed together. Trace persistence
stays best-effort (api/app.py `_persist`) — losing a trace row is an
observability gap; losing the conversation's state would be losing the
conversation.

The lifecycle of one turn:

    acquire the lease       one turn at a time per conversation, across processes
    check client_turn_id    a retry returns the stored reply, no second model call
    load + rebuild          checkpoint + redacted transcript -> ConversationState
    process the turn
    commit, one transaction transcript rows, checkpoint (version = the one read,
                            lease = ours), stored reply, last_activity_at
    release the lease

This module is also **the only write path to platform.turn**. Redaction happens
here, on the way in, rather than being trusted to the caller (§9).
"""

from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import asyncpg
import structlog

from fibrapunto.privacy import redaction
from fibrapunto.settings import get_settings
from fibrapunto.state import checkpoint
from fibrapunto.state.checkpoint import RuntimeCheckpointV1
from fibrapunto.state.models import ConversationState

log = structlog.get_logger(__name__)

LEASE_SECONDS = 120
"""Longer than a turn can take (model rounds, a billing timeout, a repair). A
turn that overruns it loses the lease, and its commit is refused rather than
overwriting whatever the next holder did."""


class ConversationNotFound(Exception):
    """Absent, purged, or someone else's — deliberately indistinguishable."""


class ConversationBusy(Exception):
    """Another turn holds the lease."""


class TurnConflict(Exception):
    """The same client_turn_id was sent with different content."""


class CheckpointConflict(Exception):
    """The checkpoint moved on, or the lease was lost, while the turn ran."""


@dataclass(frozen=True, slots=True)
class Lease:
    conversation_id: UUID
    token: UUID
    version: int
    last_turn_seq: int
    schema_version: int
    state_json: dict[str, Any]


def request_hash(text: str) -> bytes:
    return hashlib.sha256(text.encode()).digest()


def _label(domain: str, created_at: datetime) -> str:
    when = created_at.astimezone(UTC)
    return f"{domain.capitalize()} · {when.day} {when:%B}, {when:%H:%M}"


def _encode_cursor(last_activity_at: datetime, conversation_id: UUID) -> str:
    raw = f"{last_activity_at.isoformat()}|{conversation_id}"
    return base64.urlsafe_b64encode(raw.encode()).decode()


def _decode_cursor(cursor: str) -> tuple[datetime, UUID]:
    raw = base64.urlsafe_b64decode(cursor.encode()).decode()
    when, _, conversation_id = raw.partition("|")
    return datetime.fromisoformat(when), UUID(conversation_id)


class SessionStore:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    @property
    def pool(self) -> asyncpg.Pool:
        """The platform-identity pool, shared with the browser-principal checks."""
        return self._pool

    # --- creation, ownership, listing -----------------------------------------

    async def create(self, state: ConversationState, principal_id: UUID | None) -> None:
        settings = get_settings()
        retention_until = datetime.now(UTC) + timedelta(days=settings.transcript_retention_days)
        snapshot = RuntimeCheckpointV1.from_state(state).model_dump(mode="json")
        async with self._pool.acquire() as conn, conn.transaction():
            await conn.execute(
                """
                INSERT INTO platform.conversation
                    (id, channel, test_mode, agent_model, agent_model_served,
                     router_model, prompt_sha, retention_until, browser_principal_id)
                VALUES ($1, 'web', $2, $3, $4, $5, $6, $7, $8)
                """,
                state.id,
                state.test_mode,
                state.agent_model,
                state.agent_model_served,
                settings.model_router,
                state.prompt_sha,
                retention_until,
                principal_id,
            )
            await conn.execute(
                """
                INSERT INTO platform.conversation_checkpoint
                    (conversation_id, state_schema_version, state_json, last_turn_seq)
                VALUES ($1, $2, $3::jsonb, -1)
                """,
                state.id,
                checkpoint.SCHEMA_VERSION,
                json.dumps(snapshot),
            )

    async def require_owned(self, conversation_id: UUID, principal_id: UUID) -> None:
        owned = await self._pool.fetchval(
            """
            SELECT 1 FROM platform.conversation c
              JOIN platform.conversation_checkpoint k ON k.conversation_id = c.id
             WHERE c.id = $1 AND c.browser_principal_id = $2 AND c.purged_at IS NULL
            """,
            conversation_id,
            principal_id,
        )
        if not owned:
            raise ConversationNotFound(str(conversation_id))

    async def list(
        self,
        principal_id: UUID,
        *,
        status: str | None = None,
        cursor: str | None = None,
        limit: int = 20,
    ) -> tuple[list[dict[str, Any]], str | None]:
        """This browser's conversations, most recent first. Summaries only: no
        transcript, no facts, nothing a label needs that it does not show."""
        after = _decode_cursor(cursor) if cursor else None
        rows = await self._pool.fetch(
            """
            SELECT c.id, c.created_at, c.last_activity_at,
                   k.state_json->>'current_domain' AS domain,
                   k.state_json->>'status'         AS status
              FROM platform.conversation c
              JOIN platform.conversation_checkpoint k ON k.conversation_id = c.id
             WHERE c.browser_principal_id = $1
               AND c.purged_at IS NULL
               AND ($2::text IS NULL OR k.state_json->>'status' = $2)
               AND ($3::timestamptz IS NULL OR (c.last_activity_at, c.id) < ($3, $4))
             ORDER BY c.last_activity_at DESC, c.id DESC
             LIMIT $5
            """,
            principal_id,
            status,
            after[0] if after else None,
            after[1] if after else None,
            limit + 1,
        )
        page, more = rows[:limit], len(rows) > limit
        items = [
            {
                "conversation_id": str(r["id"]),
                "created_at": r["created_at"],
                "last_activity_at": r["last_activity_at"],
                "domain": r["domain"],
                "status": r["status"],
                "label": _label(r["domain"], r["created_at"]),
            }
            for r in page
        ]
        next_cursor = _encode_cursor(page[-1]["last_activity_at"], page[-1]["id"]) if more else None
        return items, next_cursor

    async def transcript(self, conversation_id: UUID) -> list[tuple[str, str, str | None]]:
        """(role, redacted text, domain) per turn, in order."""
        rows = await self._pool.fetch(
            """
            SELECT role::text AS role, text_redacted, domain
              FROM platform.turn
             WHERE conversation_id = $1 AND purged_at IS NULL
             ORDER BY seq
            """,
            conversation_id,
        )
        return [(r["role"], r["text_redacted"] or "", r["domain"]) for r in rows]

    async def replies(self, conversation_id: UUID) -> list[dict[str, Any]]:
        rows = await self._pool.fetch(
            """
            SELECT response_json FROM platform.turn_request
             WHERE conversation_id = $1 AND status = 'done' AND response_json IS NOT NULL
             ORDER BY created_at
            """,
            conversation_id,
        )
        return [json.loads(r["response_json"]) for r in rows]

    # --- the turn lifecycle ---------------------------------------------------

    async def acquire(self, conversation_id: UUID) -> Lease:
        token = uuid4()
        row = await self._pool.fetchrow(
            """
            UPDATE platform.conversation_checkpoint
               SET lease_token = $2, lease_until = now() + make_interval(secs => $3)
             WHERE conversation_id = $1
               AND (lease_until IS NULL OR lease_until < now())
            RETURNING version, last_turn_seq, state_schema_version, state_json
            """,
            conversation_id,
            token,
            LEASE_SECONDS,
        )
        if row is None:
            exists = await self._pool.fetchval(
                "SELECT 1 FROM platform.conversation_checkpoint WHERE conversation_id = $1",
                conversation_id,
            )
            raise (
                ConversationBusy(str(conversation_id))
                if exists
                else ConversationNotFound(str(conversation_id))
            )
        return Lease(
            conversation_id,
            token,
            row["version"],
            row["last_turn_seq"],
            row["state_schema_version"],
            json.loads(row["state_json"]),
        )

    async def release(self, lease: Lease) -> None:
        await self._pool.execute(
            """
            UPDATE platform.conversation_checkpoint
               SET lease_token = NULL, lease_until = NULL
             WHERE conversation_id = $1 AND lease_token = $2
            """,
            lease.conversation_id,
            lease.token,
        )

    async def stored_reply(
        self, conversation_id: UUID, client_turn_id: UUID, text: str
    ) -> dict[str, Any] | None:
        """The reply already given to this client_turn_id, if any.

        A `processing` row seen while we hold the lease is an attempt that died
        before committing — nothing of it was saved — so it is retried.
        """
        row = await self._pool.fetchrow(
            """
            SELECT request_hash, status, response_json FROM platform.turn_request
             WHERE conversation_id = $1 AND client_turn_id = $2
            """,
            conversation_id,
            client_turn_id,
        )
        if row is None:
            return None
        if bytes(row["request_hash"]) != request_hash(text):
            raise TurnConflict(str(client_turn_id))
        if row["status"] == "done":
            return json.loads(row["response_json"])
        return None

    async def begin_turn(self, conversation_id: UUID, client_turn_id: UUID, text: str) -> None:
        await self._pool.execute(
            """
            INSERT INTO platform.turn_request
                (conversation_id, client_turn_id, request_hash, status)
            VALUES ($1, $2, $3, 'processing')
            ON CONFLICT (conversation_id, client_turn_id) DO NOTHING
            """,
            conversation_id,
            client_turn_id,
            request_hash(text),
        )

    async def commit(
        self,
        lease: Lease,
        state: ConversationState,
        client_turn_id: UUID | None,
        response: dict[str, Any] | None,
    ) -> None:
        """Transcript, checkpoint, stored reply and activity — all or nothing.

        `client_turn_id` None: a message to the customer with no request behind
        it — a colleague's decision posted into the conversation."""
        snapshot = RuntimeCheckpointV1.from_state(state).model_dump(mode="json")
        new_turns = [
            (
                state.id,
                seq,
                turn.role.value,
                *redaction.for_storage(turn.text),
                turn.domain.value if turn.domain else None,
            )
            for seq, turn in enumerate(state.turns)
            if seq > lease.last_turn_seq
        ]
        async with self._pool.acquire() as conn, conn.transaction():
            if new_turns:
                await conn.executemany(
                    """
                    INSERT INTO platform.turn
                        (conversation_id, seq, role, text_redacted, redaction_flags, domain)
                    VALUES ($1, $2, $3::platform.turn_role, $4, $5::jsonb, $6)
                    """,
                    [
                        (cid, seq, role, text, json.dumps(flags), domain)
                        for cid, seq, role, text, flags, domain in new_turns
                    ],
                )
            moved = await conn.execute(
                """
                UPDATE platform.conversation_checkpoint
                   SET version = version + 1,
                       state_schema_version = $3,
                       state_json = $4::jsonb,
                       last_turn_seq = $5,
                       lease_token = NULL,
                       lease_until = NULL,
                       updated_at = now()
                 WHERE conversation_id = $1 AND version = $2 AND lease_token = $6
                """,
                lease.conversation_id,
                lease.version,
                checkpoint.SCHEMA_VERSION,
                json.dumps(snapshot),
                len(state.turns) - 1,
                lease.token,
            )
            if moved != "UPDATE 1":
                # Raising inside the transaction rolls the transcript rows back
                # with it: nothing of this turn is saved.
                raise CheckpointConflict(str(lease.conversation_id))
            if client_turn_id is not None:
                await conn.execute(
                    """
                    UPDATE platform.turn_request
                       SET status = 'done', response_json = $3::jsonb
                     WHERE conversation_id = $1 AND client_turn_id = $2
                    """,
                    lease.conversation_id,
                    client_turn_id,
                    json.dumps(response, default=str),
                )
            await conn.execute(
                """
                UPDATE platform.conversation
                   SET last_activity_at = now(),
                       agent_model_served = $2,
                       prompt_sha = $3
                 WHERE id = $1
                """,
                lease.conversation_id,
                state.agent_model_served,
                state.prompt_sha,
            )
