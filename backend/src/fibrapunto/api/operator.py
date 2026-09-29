"""What the receiving operator sees (§12.3).

    "One record, pre-populated, in the right queue, flagged as agent-handled so
    it can be found and reviewed. A short summary in the customer's language sits
    at the top; the structured fields sit beneath it. The operator's first action
    should be to continue the conversation, not to reconstruct it."

This is the half of the design that is easy to assert and hard to show. The
§1 metrics say a handover record is complete; this endpoint is what makes that
claim inspectable — you open the queue and either the first minutes of the call
are already gone, or they are not.

Runs as **platform_admin**, not as the agent. The operator view reads
`internal_comments` and the completeness view, both of which the agent is denied
(§8) — so this module is also a working demonstration that the two identities
see different things.

Because it reads what the agent may not, every route here requires operator
credentials (api/auth.py). Without that, the §8 perimeter would hold for the
agent and leak through this module to anyone who knows the chat's URL.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from fibrapunto.api.auth import require_operator
from fibrapunto.escalation import engine
from fibrapunto.handover import completeness, lifecycle
from fibrapunto.handover.routing import for_type

router = APIRouter(
    prefix="/api/operator",
    tags=["operator"],
    dependencies=[Depends(require_operator)],
)

_pool = None
_notifier: Callable[[UUID, str], Awaitable[bool]] | None = None
_conversation_reader: Callable[[UUID], Awaitable[dict[str, Any] | None]] | None = None


def set_pool(pool: Any) -> None:
    """Injected at startup, under the platform identity."""
    global _pool
    _pool = pool


def set_notifier(notify: Callable[[UUID, str], Awaitable[bool]]) -> None:
    """How a message reaches the customer's conversation: the same path the
    retention-offer decision uses, owned by app.py because it needs the
    session store and the trace. Injected so this module stays testable
    without either."""
    global _notifier
    _notifier = notify


def set_conversation_reader(reader: Callable[[UUID], Awaitable[dict[str, Any] | None]]) -> None:
    """Where the customer's conversation stands — still there, transferred,
    waiting. Owned by app.py because it needs the session store; injected
    for the same reason as the notifier."""
    global _conversation_reader
    _conversation_reader = reader


def _require_pool():
    if _pool is None:
        raise HTTPException(503, "operator view unavailable: no database connection")
    return _pool


@router.get("/queues")
async def queues() -> list[dict[str, Any]]:
    """The queues, with what is waiting in each.

    §13's adoption metric watches "the rate at which these records are
    reassigned or reopened" — this is where that would be read from, and the
    counts are shaped for it.
    """
    rows = await _require_pool().fetch(
        """
        SELECT r.queue::text        AS queue,
               r.priority::text     AS priority,
               count(*)             AS total,
               count(*) FILTER (WHERE r.status = 'open')     AS open,
               count(*) FILTER (WHERE r.status = 'in_progress') AS in_progress,
               count(*) FILTER (WHERE v.is_complete)         AS complete,
               count(*) FILTER (WHERE r.contract_link_missing) AS unlinked
          FROM crm.handover_record r
          JOIN crm.v_handover_completeness v ON v.id = r.id
         GROUP BY r.queue, r.priority
         ORDER BY r.priority DESC, r.queue
        """
    )
    return [dict(r) for r in rows]


@router.get("/records")
async def records(queue: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
    """The work list. What still needs a person first: open and in-progress
    cases before closed ones, then high priority, then newest."""
    query = """
        SELECT r.id, r.reference, r.record_type::text AS record_type,
               r.priority::text AS priority, r.queue::text AS queue,
               r.status::text AS status,
               r.subject, r.contract_number, r.claimed_contract_number,
               r.contract_link_missing, r.handled_by_agent, r.created_at,
               c.full_name AS customer_name,
               v.is_complete
          FROM crm.handover_record r
          JOIN crm.v_handover_completeness v ON v.id = r.id
          LEFT JOIN crm.customer c ON c.id = r.customer_id
         WHERE ($1::text IS NULL OR r.queue::text = $1)
         ORDER BY (r.status = 'closed'), r.priority DESC, r.created_at DESC
         LIMIT $2
    """
    rows = await _require_pool().fetch(query, queue, limit)
    return [dict(r) for r in rows]


@router.get("/records/{reference}")
async def record_detail(reference: str) -> dict[str, Any]:
    """One record, as the operator opens it.

    The summary sits at the top and the structured fields beneath, because §12.3
    is specific about that ordering: the operator should be able to continue the
    conversation after reading one sentence.
    """
    row = await _require_pool().fetchrow(
        """
        SELECT r.*, c.full_name AS customer_name, c.email, c.phone,
               v.is_complete
          FROM crm.handover_record r
          JOIN crm.v_handover_completeness v ON v.id = r.id
          LEFT JOIN crm.customer c ON c.id = r.customer_id
         WHERE r.reference = $1
        """,
        reference,
    )
    if row is None:
        raise HTTPException(404, f"no record with reference {reference}")

    record = dict(row)
    context = record.pop("collected_context", None)
    if isinstance(context, str):
        context = json.loads(context)
    context = context or {}

    routing = for_type(record["record_type"])
    missing = completeness.missing_fields(
        record["record_type"], context, record["subject"] or "", record["stated_reason"]
    )

    return {
        # The top of the page: everything needed to pick up the phone.
        "summary": {
            "reference": record["reference"],
            "subject": record["subject"],
            "customer": record["customer_name"],
            "contract": record["contract_number"] or record["claimed_contract_number"],
            "contract_linked": not record["contract_link_missing"],
            "priority": record["priority"],
            "queue_label": routing.queue_label,
            "promised": routing.response_window,
            "handled_by_agent": record["handled_by_agent"],
            "opened": record["created_at"],
            # Whether a person has dealt with it. Not the same as complete.
            "status": record["status"],
        },
        # §12.2: the customer's own words, never paraphrased.
        "stated_reason": record["stated_reason"],
        # §12.2's most-often-omitted field, surfaced deliberately: the operator
        # must not contradict what the agent already said.
        "already_told_the_customer": context.get("what_customer_was_told", []),
        "why_it_escalated": context.get("escalation_trigger"),
        "lookups": context.get("lookups", []),
        "context": {
            k: v
            for k, v in context.items()
            if k
            not in {"what_customer_was_told", "escalation_trigger", "lookups", "retention_offer"}
        },
        # These two columns the agent cannot read at all (§8). Their presence
        # here is the operator identity doing something the agent could not.
        # An offer the customer accepted awaits a colleague's decision here;
        # once decided, the decision and its reason.
        "retention_offer": context.get("retention_offer"),
        # Messages queued to the customer by email — never sent by the demo.
        "outbox": [
            {
                "channel": o["channel"],
                "recipient": o["recipient"],
                "subject": o["subject"],
                "body": o["body"],
                "created_at": o["created_at"],
                "sent": o["sent_at"] is not None,
            }
            for o in await _require_pool().fetch(
                """
                SELECT channel, recipient, subject, body, created_at, sent_at
                  FROM platform.notification_outbox
                 WHERE reference = $1
                 ORDER BY created_at
                """,
                record["reference"],
            )
        ],
        "internal_comments": record["internal_comments"],
        "customer_contact": {"email": record["email"], "phone": record["phone"]},
        # The other way to reach them: the conversation the case came from.
        # A customer who asked for a person and gave no contract number has
        # no email or phone on the record — but they are sitting in that
        # chat, waiting for the colleague they were promised. Without this
        # the page said "not available" about a customer who was right there.
        "conversation": await _conversation_state(record["origin_session_id"]),
        "completeness": {"is_complete": record["is_complete"], "missing": missing},
    }


async def _conversation_state(cid: UUID | None) -> dict[str, Any]:
    """Whether the customer can still be reached in their conversation.

    `waiting`: they were put through to a colleague (§5's transfer) and
    nobody has taken the case yet — the customer was told the wait is a few
    minutes, and that promise is now the operator's to keep.
    """
    absent = {"exists": False, "status": None, "transferred": False, "waiting": False}
    if cid is None or _conversation_reader is None:
        return absent
    found = await _conversation_reader(cid)
    return found or absent


# --- the operator acts on the case -------------------------------------------


class ChatMessage(BaseModel):
    text: str = Field(min_length=1, max_length=2000, description="The colleague's own words.")


@router.post("/records/{reference}/message")
async def message_customer(reference: str, request: ChatMessage) -> dict[str, Any]:
    """Write to the customer in their conversation, as the colleague on the case.

    The one channel that always exists for a case opened from a chat: the
    chat. The text is the operator's own — posted under the case number so
    the customer knows who is speaking — and is kept in the internal notes
    so the next colleague can see what was said. Not a status change: taking
    the case is a separate act, with its own template.
    """
    pool = _require_pool()
    row = await pool.fetchrow(
        """
        SELECT id, internal_comments, origin_session_id
          FROM crm.handover_record
         WHERE reference = $1
        """,
        reference,
    )
    if row is None:
        raise HTTPException(404, f"no record with reference {reference}")
    if _notifier is None:
        raise HTTPException(503, "the conversation cannot be reached: no session store")

    text = request.text.strip()
    if not text:
        raise HTTPException(422, "an empty message says nothing")

    delivered = await _notifier(row["origin_session_id"], engine.colleague_message(reference, text))
    if not delivered:
        raise HTTPException(
            410, "the conversation no longer exists: reach the customer by email or phone"
        )

    stamp = datetime.now(UTC).strftime("%Y-%m-%d %H:%M")
    line = f"[{stamp}] to the customer, in the chat: {text}"
    comments = f"{row['internal_comments']}\n{line}" if row["internal_comments"] else line
    await pool.execute(
        "UPDATE crm.handover_record SET internal_comments = $2 WHERE id = $1",
        row["id"],
        comments,
    )
    return {"reference": reference, "delivered": True}


class StatusChange(BaseModel):
    status: Literal["open", "in_progress", "closed"]
    note: str | None = Field(
        default=None,
        max_length=2000,
        description="Optional. Kept in internal_comments, which the assistant cannot read.",
    )


@router.patch("/records/{reference}/status")
async def change_status(reference: str, request: StatusChange) -> dict[str, Any]:
    """Take, release, close or reopen a case.

    This is the step that makes a case *done*. A record is created complete
    (§13) but it is created because a person is needed (§12.1), and until that
    person has acted it stays open. The rules live in handover/lifecycle.py;
    the retention-offer decision writes the same column through the same
    module, so the two cannot disagree about what closed means.

    The customer is told, in their own conversation, when the move is one
    they can see: taken, closed, reopened. A release back to the queue is the
    team's business and says nothing. The message is a template written at
    the operator's action, never the model's; the note stays internal.
    """
    pool = _require_pool()
    row = await pool.fetchrow(
        """
        SELECT id, status::text AS status, collected_context, internal_comments,
               origin_session_id
          FROM crm.handover_record
         WHERE reference = $1
        """,
        reference,
    )
    if row is None:
        raise HTTPException(404, f"no record with reference {reference}")

    context = row["collected_context"]
    if isinstance(context, str):
        context = json.loads(context)
    try:
        changed = lifecycle.check(row["status"], request.status, context or {})
    except lifecycle.TransitionRefused as refused:
        raise HTTPException(409, str(refused)) from refused

    note = (request.note or "").strip()
    if not changed and not note:
        return {
            "reference": reference,
            "status": row["status"],
            "changed": False,
            "customer_notified_in_chat": False,
        }

    # Tell the customer first: refused (409) if they are mid-turn, before the
    # case changes, so the colleague can simply retry. False when the
    # conversation no longer exists — the case still moves.
    notified = False
    text = _customer_message(row["status"], request.status, reference) if changed else None
    if text and _notifier is not None:
        notified = await _notifier(row["origin_session_id"], text)

    stamp = datetime.now(UTC).strftime("%Y-%m-%d %H:%M")
    line = f"[{stamp}] {row['status']} → {request.status}" if changed else f"[{stamp}]"
    if note:
        line = f"{line}: {note}"
    comments = f"{row['internal_comments']}\n{line}" if row["internal_comments"] else line

    await pool.execute(
        """
        UPDATE crm.handover_record
           SET status = $2::crm.record_status, internal_comments = $3
         WHERE id = $1
        """,
        row["id"],
        request.status,
        comments,
    )
    return {
        "reference": reference,
        "status": request.status,
        "changed": changed,
        "customer_notified_in_chat": notified,
    }


def _customer_message(current: str, target: str, reference: str) -> str | None:
    """What the customer is told about the move, or None for a move that is
    the team's business only (a release back to the queue)."""
    if target == "closed":
        return engine.case_closed(reference)
    if target == "in_progress":
        return (
            engine.case_reopened(reference) if current == "closed" else engine.case_taken(reference)
        )
    return None
