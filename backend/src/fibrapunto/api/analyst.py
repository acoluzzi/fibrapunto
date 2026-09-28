"""What the analyst sees: the numbers, never a conversation.

The operator view is about one case at a time; the developer console about
one conversation's trace. This is the third reading of the same platform
data — aggregates over every conversation and every case, the campaigns and
their budget — for someone who decides *policy*: how much the assistant may
give away in offers, and whether the §1 targets are holding.

Runs as **platform_admin** like the operator view, because the tables it
counts over are the platform's (§8), but it never returns a row: every number
here is a count, a sum or a median. The budget is the recommendation system's
own; setting it is the one write on this surface, and it is the analyst's
decision by design (recommendation-system/main.py).

Gated by the analyst password (api/auth.py). Without it, the aggregate
metrics and the budget would be one URL away from the chat.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from fibrapunto.api.auth import require_analyst
from fibrapunto.integrations.recommendation_client import RecommendationClient

router = APIRouter(
    prefix="/api/analyst",
    tags=["analyst"],
    dependencies=[Depends(require_analyst)],
)

_pool = None


def set_pool(pool: Any) -> None:
    """Injected at startup, under the platform identity."""
    global _pool
    _pool = pool


def _require_pool():
    if _pool is None:
        raise HTTPException(503, "analyst view unavailable: no database connection")
    return _pool


@router.get("/overview")
async def overview() -> dict[str, Any]:
    """Every conversation and every case, counted.

    Conversations are read with their checkpoint, which holds the domain and
    the status the assistant last recorded; purged conversations (§9) are
    gone from here as from everywhere else. The trace tallies come from
    platform.trace_event: how often a guardrail fired, why cases were opened,
    what the capabilities returned — the same events the developer console
    shows one at a time.
    """
    pool = _require_pool()

    conversations = await pool.fetchrow(
        """
        SELECT count(*)                                   AS total,
               count(*) FILTER (WHERE c.run_id IS NOT NULL) AS from_eval_runs
          FROM platform.conversation c
         WHERE c.purged_at IS NULL
        """
    )
    by_domain = await pool.fetch(
        """
        SELECT coalesce(k.state_json->>'current_domain', 'unknown') AS domain, count(*) AS n
          FROM platform.conversation c
          JOIN platform.conversation_checkpoint k ON k.conversation_id = c.id
         WHERE c.purged_at IS NULL
         GROUP BY 1 ORDER BY n DESC, 1
        """
    )
    by_status = await pool.fetch(
        """
        SELECT coalesce(k.state_json->>'status', 'unknown') AS status, count(*) AS n
          FROM platform.conversation c
          JOIN platform.conversation_checkpoint k ON k.conversation_id = c.id
         WHERE c.purged_at IS NULL
         GROUP BY 1 ORDER BY n DESC, 1
        """
    )
    turns = await pool.fetchrow(
        """
        SELECT count(*) FILTER (WHERE t.role = 'customer') AS customer_turns,
               count(*) FILTER (WHERE t.role = 'agent')    AS agent_turns
          FROM platform.turn t
          JOIN platform.conversation c ON c.id = t.conversation_id
         WHERE c.purged_at IS NULL
        """
    )
    # Latency and cost come from the agent.reply events, the same source the
    # developer console reads for one session (§16): the turn row records
    # neither. A reply the provider did not price is counted, not priced.
    replies = await pool.fetchrow(
        """
        SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY e.duration_ms)  AS latency_p50_ms,
               percentile_cont(0.95) WITHIN GROUP (ORDER BY e.duration_ms) AS latency_p95_ms,
               sum((e.attributes->>'turn_cost_usd')::numeric)               AS cost_usd,
               count(e.attributes->>'turn_cost_usd')                        AS costed_turns
          FROM platform.trace_event e
          JOIN platform.conversation c ON c.id = e.conversation_id
         WHERE c.purged_at IS NULL AND e.name = 'agent.reply'
        """
    )
    events = await pool.fetch(
        """
        SELECT e.name, count(*) AS n
          FROM platform.trace_event e
          JOIN platform.conversation c ON c.id = e.conversation_id
         WHERE c.purged_at IS NULL
           AND e.name IN ('guardrail.triggered', 'escalation.triggered',
                          'capability.result', 'domain.switched', 'offer.lookup')
         GROUP BY e.name
        """
    )
    outcomes = await pool.fetch(
        """
        SELECT e.attributes->>'outcome' AS outcome, count(*) AS n
          FROM platform.trace_event e
          JOIN platform.conversation c ON c.id = e.conversation_id
         WHERE c.purged_at IS NULL AND e.name = 'capability.result'
         GROUP BY 1 ORDER BY n DESC, 1
        """
    )
    escalations = await pool.fetch(
        """
        SELECT e.attributes->>'kind' AS kind, count(*) AS n
          FROM platform.trace_event e
          JOIN platform.conversation c ON c.id = e.conversation_id
         WHERE c.purged_at IS NULL AND e.name = 'escalation.triggered'
         GROUP BY 1 ORDER BY n DESC, 1
        """
    )
    cases = await pool.fetch(
        """
        SELECT r.queue::text AS queue, r.status::text AS status, count(*) AS n,
               count(*) FILTER (WHERE v.is_complete) AS complete
          FROM crm.handover_record r
          JOIN crm.v_handover_completeness v ON v.id = r.id
         GROUP BY 1, 2 ORDER BY 1, 2
        """
    )
    offers = await pool.fetch(
        """
        SELECT coalesce(r.collected_context->'retention_offer'->>'status', 'none') AS status,
               count(*) AS n
          FROM crm.handover_record r
         WHERE r.collected_context ? 'retention_offer'
         GROUP BY 1 ORDER BY n DESC, 1
        """
    )

    return {
        "conversations": {
            "total": conversations["total"],
            "from_eval_runs": conversations["from_eval_runs"],
            "by_domain": [dict(r) for r in by_domain],
            "by_status": [dict(r) for r in by_status],
        },
        "turns": {
            "customer": turns["customer_turns"],
            "agent": turns["agent_turns"],
            "latency_p50_ms": replies["latency_p50_ms"],
            "latency_p95_ms": replies["latency_p95_ms"],
            # Never 0 for unknown: None when no reply carried a cost (§16).
            "cost_usd": str(replies["cost_usd"]) if replies["cost_usd"] is not None else None,
            "costed_turns": replies["costed_turns"],
        },
        "events": {r["name"]: r["n"] for r in events},
        "capability_outcomes": [dict(r) for r in outcomes],
        "escalations": [dict(r) for r in escalations],
        "cases": [dict(r) for r in cases],
        "offers_on_cases": [dict(r) for r in offers],
    }


# --- the offers budget and the campaigns -------------------------------------


@router.get("/budget")
async def budget() -> dict[str, Any]:
    result = await RecommendationClient().budget()
    if result is None:
        raise HTTPException(503, "the recommendation system is not reachable")
    return result


class BudgetChange(BaseModel):
    total_eur: str


@router.put("/budget")
async def set_budget(request: BudgetChange) -> dict[str, Any]:
    """The analyst sets what the operator is prepared to give away. Applies to
    the next offer shown; what is already held or spent stays counted."""
    try:
        total = Decimal(request.total_eur)
    except InvalidOperation:
        raise HTTPException(422, "the budget must be a number of euros") from None
    if total < 0:
        raise HTTPException(422, "the budget cannot be negative")
    result = await RecommendationClient().set_budget(total)
    if result is None:
        raise HTTPException(503, "the recommendation system did not accept the change")
    return result


@router.get("/campaigns")
async def campaigns() -> list[dict[str, Any]]:
    result = await RecommendationClient().campaigns()
    if result is None:
        raise HTTPException(503, "the recommendation system is not reachable")
    return result
