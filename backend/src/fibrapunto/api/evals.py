"""The §1 metrics, recorded by the harness and read by the dashboard.

    "Results are versioned with the code. The interesting artefact is not the
    last run; it is the progression across runs, sitting next to the changes
    that caused it." (§14)

The harness (evals/) is an external program: it drives the agent over HTTP and
writes each run to evals/runs/ on the host. The backend runs in a container
that cannot see that directory, so for a long time the dashboard's "§1
metrics" section said "available after `make eval`" and stayed that way after
`make eval`. `platform.eval_run` existed for exactly this and nothing wrote to
it (docs/FRICTION-LOG.md, 7d).

Two routes close the loop. The harness records a run here at the end of
`make eval` (and `make eval-publish` records saved runs after the fact); the
chat page reads the recorded runs back. Recording is an operator action —
it writes as platform_admin — so it sits behind the operator credentials like
the rest of the operator API. Reading is open, like `/api/domains` and
`/api/retention`: six aggregate numbers, a model id and a git sha, and never a
row about a customer.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from fibrapunto.api.auth import require_operator

router = APIRouter(tags=["evals"])

_pool = None


def set_pool(pool: Any) -> None:
    """Injected at startup, under the platform identity."""
    global _pool
    _pool = pool


def _require_pool():
    if _pool is None:
        raise HTTPException(503, "eval runs unavailable: no database connection")
    return _pool


class EvalMetric(BaseModel):
    """One row of the §1 table, as evals/metrics.py computes it."""

    name: str
    value: float | int | None = None
    target: str
    passed: bool | None = None


class EvalRun(BaseModel):
    run_id: str = Field(min_length=1, max_length=120)
    git_sha: str | None = None
    agent_model: str = Field(min_length=1)
    # None when the run had no judge (`make eval-quick`). Never a placeholder:
    # the judge_is_not_subject constraint compares vendors, and a made-up one
    # would pass it for the wrong reason.
    judge_model: str | None = None
    dataset_sha: str | None = None
    prompt_sha: str | None = None
    case_count: int | None = Field(default=None, ge=0)
    started_at: datetime | None = None
    metrics: list[EvalMetric]


@router.post(
    "/api/operator/eval-runs",
    status_code=201,
    dependencies=[Depends(require_operator)],
)
async def record_run(run: EvalRun) -> dict[str, Any]:
    """Record a run, or replace what was recorded for it.

    Idempotent on run_id: `make eval-judge` re-grades a saved run and changes
    its judge, and publishing every saved run twice must not double them.
    """
    try:
        await _require_pool().execute(
            """
            INSERT INTO platform.eval_run
                (run_id, git_sha, agent_model, judge_model, dataset_sha, prompt_sha,
                 case_count, started_at, metrics)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9::jsonb)
            ON CONFLICT (run_id) DO UPDATE
               SET git_sha     = EXCLUDED.git_sha,
                   agent_model = EXCLUDED.agent_model,
                   judge_model = EXCLUDED.judge_model,
                   dataset_sha = EXCLUDED.dataset_sha,
                   prompt_sha  = EXCLUDED.prompt_sha,
                   case_count  = EXCLUDED.case_count,
                   started_at  = EXCLUDED.started_at,
                   metrics     = EXCLUDED.metrics
            """,
            run.run_id,
            run.git_sha,
            run.agent_model,
            run.judge_model,
            run.dataset_sha,
            run.prompt_sha,
            run.case_count,
            run.started_at,
            json.dumps([m.model_dump() for m in run.metrics]),
        )
    except Exception as exc:  # noqa: BLE001
        # asyncpg.CheckViolationError, without importing asyncpg here: the
        # constraint is the point (docs/DECISIONS.md #11) and its message is
        # the one the harness should print.
        if type(exc).__name__ == "CheckViolationError":
            raise HTTPException(
                422,
                f"refused: judge {run.judge_model!r} shares a vendor with agent "
                f"{run.agent_model!r} — grading one's own output is not measurement (§10)",
            ) from exc
        raise
    return {"recorded": run.run_id}


@router.get("/api/eval-runs")
async def list_runs(limit: int = 20) -> list[dict[str, Any]]:
    """The recorded runs, newest first, each with its §1 table.

    Ordered by when the run was made, not when it was recorded, so a backfilled
    history reads in the order it happened.
    """
    rows = await _require_pool().fetch(
        """
        SELECT run_id, git_sha, agent_model, judge_model, dataset_sha, prompt_sha,
               case_count, started_at, created_at, metrics
          FROM platform.eval_run
         ORDER BY started_at DESC NULLS LAST, created_at DESC
         LIMIT $1
        """,
        max(1, min(limit, 100)),
    )
    out = []
    for row in rows:
        record = dict(row)
        metrics = record.pop("metrics", None)
        if isinstance(metrics, str):
            metrics = json.loads(metrics)
        record["metrics"] = metrics or []
        out.append(record)
    return out
