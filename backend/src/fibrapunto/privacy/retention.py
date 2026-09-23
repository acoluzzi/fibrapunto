"""Retention and purge (§9).

    "The mitigation therefore sits downstream: redaction on stored transcripts,
    a retention period short enough to be defensible, and access control on the
    transcript store. Any production deployment must answer those three before
    going live."

Three things, and this module is the second. The first is `detectors.redact`
plus the storage boundary in `privacy/redaction.py`; the third is that the whole
`platform` schema is ungranted to the agent role (§8).

**The purge nulls the words and keeps the skeleton.** A conversation that has
aged out loses `text_redacted` — the only column that could still carry meaning
about a person — while the row, its timings, its outcome, its capability calls
and its trace event *names* survive. That is deliberate and it is the difference
between a retention policy and data loss:

- §1's metrics are computed over conversations, and a policy that deleted rows
  would make every historical number silently drift as the window moved. A
  measurement that changes because time passed is not a measurement.
- §14 wants "the progression across runs". A run from six weeks ago must still
  be comparable to today's, and it cannot be if its denominator evaporated.
- A data subject asking "what do you hold about me" gets a truthful and
  complete answer: a row that says a conversation happened, when, and what the
  outcome was, with no content. That is a better answer than a missing row,
  which is indistinguishable from never having existed.

Trace event attributes are purged too. They are the likelier leak of the two —
`text_redacted` is obviously content and gets handled carefully, while an
attributes blob accumulates whatever a call site passed it. Rather than trusting
every present and future `emit()` call to be careful, the sweep replaces the
whole JSONB object on aged rows and keeps only the event name and its timing.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime

import asyncpg
import structlog

from fibrapunto.settings import get_settings

log = structlog.get_logger(__name__)

# How often the sweep wakes. Retention is measured in days, so the exact hour a
# row is purged does not matter; that it happens without anyone remembering to
# run it does.
SWEEP_INTERVAL_S = 3600


@dataclass(frozen=True, slots=True)
class PurgeResult:
    conversations: int
    turns: int
    events: int

    @property
    def empty(self) -> bool:
        return not (self.conversations or self.turns or self.events)


async def purge_expired(pool: asyncpg.Pool, *, now: datetime | None = None) -> PurgeResult:
    """Purge everything past its retention date. Idempotent.

    Runs in one transaction: a half-purged conversation — words gone from the
    turns but still present in the trace attributes — is the worst of both
    outcomes, and is exactly what an interrupted sweep would leave behind.
    """
    cutoff = now or datetime.now(UTC)

    async with pool.acquire() as connection, connection.transaction():
        expired = [
            row["id"]
            for row in await connection.fetch(
                """
                SELECT id FROM platform.conversation
                 WHERE retention_until <= $1 AND purged_at IS NULL
                """,
                cutoff,
            )
        ]
        if not expired:
            return PurgeResult(0, 0, 0)

        turns = await connection.execute(
            """
            UPDATE platform.turn
               SET text_redacted = NULL,
                   redaction_flags = '[]'::jsonb,
                   purged_at = $2
             WHERE conversation_id = ANY($1::uuid[]) AND purged_at IS NULL
            """,
            expired,
            cutoff,
        )
        # The name and the timing survive; whatever the attributes held does not.
        events = await connection.execute(
            """
            UPDATE platform.trace_event
               SET attributes = '{"purged": true}'::jsonb
             WHERE conversation_id = ANY($1::uuid[])
               AND attributes <> '{"purged": true}'::jsonb
            """,
            expired,
        )
        await connection.execute(
            "UPDATE platform.conversation SET purged_at = $2 WHERE id = ANY($1::uuid[])",
            expired,
            cutoff,
        )

    result = PurgeResult(len(expired), _rowcount(turns), _rowcount(events))
    log.info(
        "retention.purged",
        conversations=result.conversations,
        turns=result.turns,
        events=result.events,
    )
    return result


def _rowcount(status: str) -> int:
    """asyncpg returns the command tag, e.g. "UPDATE 12"."""
    try:
        return int(status.rsplit(" ", 1)[-1])
    except ValueError:
        return 0


async def sweep_forever(pool: asyncpg.Pool, interval_s: int = SWEEP_INTERVAL_S) -> None:
    """The background sweep, started at application startup.

    Never dies. A retention sweep that stopped after one bad night — a lock
    timeout, a restart of the database — would leave data past its date with
    nothing to say so, and the failure is silent by nature: nobody notices a
    purge that did not happen.
    """
    while True:
        try:
            result = await purge_expired(pool)
            if not result.empty:
                log.info("retention.sweep_completed", conversations=result.conversations)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            log.warning("retention.sweep_failed", error=str(exc))
        await asyncio.sleep(interval_s)


async def retention_status(pool: asyncpg.Pool) -> dict[str, int | str]:
    """What the operator needs to answer §9's third question.

    Surfaced through the operator API rather than only logged, because "what is
    your retention period and can you show me it is being applied" is a question
    with an auditor behind it.
    """
    row = await pool.fetchrow(
        """
        SELECT count(*) FILTER (WHERE purged_at IS NULL)                        AS live,
               count(*) FILTER (WHERE purged_at IS NOT NULL)                    AS purged,
               count(*) FILTER (WHERE purged_at IS NULL
                                  AND retention_until <= now())                 AS overdue,
               min(retention_until) FILTER (WHERE purged_at IS NULL)            AS next_due
          FROM platform.conversation
        """
    )
    return {
        "retention_days": get_settings().transcript_retention_days,
        "live": row["live"],
        "purged": row["purged"],
        # Non-zero means the sweep is not keeping up, which is the number an
        # auditor actually wants. Zero is the only acceptable value.
        "overdue": row["overdue"],
        "next_due": row["next_due"].isoformat() if row["next_due"] else None,
    }
