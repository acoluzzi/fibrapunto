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
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from fibrapunto.api.auth import require_operator
from fibrapunto.handover import completeness
from fibrapunto.handover.routing import for_type

router = APIRouter(
    prefix="/api/operator",
    tags=["operator"],
    dependencies=[Depends(require_operator)],
)

_pool = None


def set_pool(pool: Any) -> None:
    """Injected at startup, under the platform identity."""
    global _pool
    _pool = pool


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
    """The work list. Newest first, high priority first."""
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
         ORDER BY r.priority DESC, r.created_at DESC
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
    missing = completeness.missing_fields(record["record_type"], context, record["subject"] or "")

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
            if k not in {"what_customer_was_told", "escalation_trigger", "lookups"}
        },
        # These two columns the agent cannot read at all (§8). Their presence
        # here is the operator identity doing something the agent could not.
        "internal_comments": record["internal_comments"],
        "customer_contact": {"email": record["email"], "phone": record["phone"]},
        "completeness": {"is_complete": record["is_complete"], "missing": missing},
    }
