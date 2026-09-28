"""The §1 table reaches the dashboard (§14).

`platform.eval_run` was created with the schema and, as the friction log
records, nothing wrote to it: the harness kept its runs on the host, the
backend runs in a container, and the dashboard's §1 section said "available
after make eval" after every make eval. These tests hold the loop closed:
the harness can record a run, the dashboard can read it back, and the
judge-vendor constraint (docs/DECISIONS.md #11) is enforced on what is
recorded — the first time it has had anything to enforce.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any

import asyncpg
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from fibrapunto.api import auth, evals
from fibrapunto.settings import get_settings
from pydantic import SecretStr

PASSWORD = "correct-horse"
AUTH = ("operator", PASSWORD)


def _run(run_id: str, **overrides: Any) -> dict[str, Any]:
    payload = {
        "run_id": run_id,
        "git_sha": "abc1234",
        "agent_model": "anthropic/claude-sonnet-5",
        "judge_model": "google/gemini-2.5-pro",
        "dataset_sha": "d" * 12,
        "prompt_sha": "p" * 12,
        "case_count": 30,
        "started_at": "2026-09-25T15:03:48+00:00",
        "metrics": [
            {"name": "Autonomous resolution", "value": 0.8, "target": "≥ 70%", "passed": True},
            {"name": "No invention", "value": 0, "target": "= 0", "passed": True},
            {"name": "Escalation precision", "value": None, "target": "≥ 90%", "passed": None},
        ],
    }
    payload.update(overrides)
    return payload


def _app(monkeypatch, with_database: bool) -> FastAPI:
    monkeypatch.setattr(
        auth, "get_settings", lambda: SimpleNamespace(operator_password=SecretStr(PASSWORD))
    )
    evals.set_pool(None)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # The pool is opened on the app's own loop, as at startup: an asyncpg
        # pool is bound to the loop that created it.
        if not with_database:
            yield
            return
        try:
            pool = await asyncpg.create_pool(get_settings().dsn("platform"), min_size=1, max_size=2)
        except (OSError, asyncpg.PostgresError) as exc:
            pytest.skip(f"database not available: {exc}")
        await pool.execute("TRUNCATE platform.eval_run")
        evals.set_pool(pool)
        try:
            yield
        finally:
            evals.set_pool(None)
            await pool.close()

    app = FastAPI(lifespan=lifespan)
    app.include_router(auth.router)
    app.include_router(evals.router)
    return app


@pytest.fixture
def client(monkeypatch):
    """Past the operator gate, against the test database (conftest)."""
    with TestClient(_app(monkeypatch, with_database=True)) as client:
        yield client


@pytest.fixture
def no_database(monkeypatch):
    """No pool: past the gate the answer is 503, which tells "authorised" from
    "refused" without a database (the same trick as test_operator_auth)."""
    with TestClient(_app(monkeypatch, with_database=False)) as client:
        yield client


def test_recording_a_run_is_an_operator_action(no_database):
    client = no_database
    assert client.post("/api/operator/eval-runs", json=_run("r1")).status_code == 401
    assert client.post("/api/operator/eval-runs", json=_run("r1"), auth=AUTH).status_code == 503


def test_reading_the_runs_is_open(no_database):
    client = no_database
    # Open like /api/domains: no credentials asked, only the database missing.
    assert client.get("/api/eval-runs").status_code == 503


def test_a_recorded_run_comes_back_newest_first_with_its_table(client):
    older = _run("2026-09-25T13-09-08Z-89ff5a1", started_at="2026-09-25T13:09:08+00:00")
    newer = _run("2026-09-25T15-03-48Z-bafb1b6")
    # Recorded out of order on purpose: a backfill publishes whatever order
    # the directory listing gives, and the dashboard must still lead with the
    # latest run.
    assert client.post("/api/operator/eval-runs", json=newer, auth=AUTH).status_code == 201
    assert client.post("/api/operator/eval-runs", json=older, auth=AUTH).status_code == 201

    runs = client.get("/api/eval-runs").json()
    assert [r["run_id"] for r in runs] == [newer["run_id"], older["run_id"]]
    latest = runs[0]
    assert latest["agent_model"] == "anthropic/claude-sonnet-5"
    assert latest["case_count"] == 30
    assert latest["metrics"] == newer["metrics"]
    # null stays null: "not measured" must not become 0 on the way through.
    assert latest["metrics"][2]["value"] is None
    assert latest["metrics"][2]["passed"] is None


def test_recording_the_same_run_twice_replaces_it(client):
    """`make eval-judge` re-grades a run and `make eval-publish RUN=all` is
    safe to repeat: by run id, the second write wins and nothing doubles."""
    first = _run("r1", judge_model=None)
    assert client.post("/api/operator/eval-runs", json=first, auth=AUTH).status_code == 201
    regraded = _run("r1", judge_model="google/gemini-2.5-pro")
    assert client.post("/api/operator/eval-runs", json=regraded, auth=AUTH).status_code == 201

    runs = client.get("/api/eval-runs").json()
    assert len(runs) == 1
    assert runs[0]["judge_model"] == "google/gemini-2.5-pro"


def test_a_run_without_a_judge_is_recorded_as_such(client):
    """`make eval-quick` has no judge. Recorded as null — not as a placeholder
    vendor that would pass the judge_is_not_subject CHECK by lying."""
    assert (
        client.post(
            "/api/operator/eval-runs", json=_run("q1", judge_model=None), auth=AUTH
        ).status_code
        == 201
    )
    assert client.get("/api/eval-runs").json()[0]["judge_model"] is None


def test_the_judge_vendor_constraint_finally_has_something_to_refuse(client):
    """docs/DECISIONS.md #11: the DB CHECK on platform.eval_run enforces judge ≠
    subject at vendor level. It has been there since the schema; this is the
    first write it has ever been asked to judge."""
    response = client.post(
        "/api/operator/eval-runs",
        json=_run("self", judge_model="anthropic/claude-opus-5-5"),
        auth=AUTH,
    )
    assert response.status_code == 422
    assert "shares a vendor" in response.json()["detail"]
    assert client.get("/api/eval-runs").json() == []
