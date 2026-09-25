"""Conversations that survive a restart, owned by an anonymous browser (006).

Runs the real app — lifespan, both database pools, the real CRM gateway —
against the test database, with a scripted model and the in-process billing
stub. A "restart" closes the app (lifespan shutdown) and clears its in-memory
registries, then starts it again with the same browser cookie.
"""

from __future__ import annotations

import asyncio
import json
from contextlib import contextmanager
from typing import Any
from uuid import uuid4

import asyncpg
import pytest
from fastapi.testclient import TestClient
from fibrapunto.api import app as app_module
from fibrapunto.integrations.billing_client import BillingClient
from fibrapunto.persistence.sessions import SessionStore
from fibrapunto.privacy import retention
from fibrapunto.scripts.reset import reset
from fibrapunto.settings import get_settings
from langchain_core.messages import AIMessage
from test_conversation_wiring import (  # noqa: F401 — the autouse fixtures
    _billing_in_process,
    _fast_stub,
    routed_to,
    scripted,
    tool_call,
)

CONTRACT = "FP-2023-048812"
IBAN = "IT60X0542811101000000123456"


def sql(query: str, *args: Any) -> Any:
    async def run():
        conn = await asyncpg.connect(get_settings().dsn("owner"))
        try:
            return await conn.fetch(query, *args)
        finally:
            await conn.close()

    return asyncio.run(run())


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    async def run():
        conn = await asyncpg.connect(get_settings().dsn("owner"))
        try:
            await reset(conn)
        finally:
            await conn.close()

    asyncio.run(run())
    # The credential store is not what these tests are about.
    monkeypatch.setattr(
        "fibrapunto.conversation._default_billing_client",
        lambda: BillingClient(
            base_url="http://billing.test", token_provider=lambda: "billing-dev-token"
        ),
    )
    app_module._recorders.clear()
    yield
    app_module._recorders.clear()


@contextmanager
def backend(cookies=None):
    """One lifetime of the backend process. Leaving the block is a restart."""
    with TestClient(
        app_module.app, base_url="http://localhost", raise_server_exceptions=False
    ) as client:
        if cookies is not None:
            client.cookies = cookies
        yield client
    app_module._recorders.clear()


def browser(client: TestClient) -> TestClient:
    assert client.post("/api/customer/access").status_code == 200
    return client


def new_session(client: TestClient) -> str:
    response = client.post("/api/sessions", json={})
    assert response.status_code == 200, response.text
    return response.json()["conversation_id"]


def turn(client: TestClient, cid: str, text: str, client_turn_id: str | None = None):
    return client.post(
        f"/api/sessions/{cid}/turns",
        json={"client_turn_id": client_turn_id or str(uuid4()), "text": text},
    )


def looked_up(monkeypatch):
    routed_to(monkeypatch, "billing")
    return scripted(
        monkeypatch,
        [
            tool_call("billing_lookup", {"contract_number": CONTRACT}),
            AIMessage("Your €29.90 debit was refused; the next attempt is on 19 September."),
        ],
    )


# --- 1. restart --------------------------------------------------------------


def test_a_session_survives_a_backend_restart(monkeypatch):
    with backend() as client:
        browser(client)
        cid = new_session(client)
        looked_up(monkeypatch)
        assert turn(client, cid, f"payment refused, {CONTRACT}").status_code == 200
        cookies = client.cookies

    with backend(cookies) as client:
        model = scripted(monkeypatch, [AIMessage("Anything else?")])
        response = turn(client, cid, "thanks, and the retry date again?")

        assert response.status_code == 200, response.text
        # The collected facts came back from the checkpoint, not a new lookup:
        prompt = "\n".join(str(m.content) for m in model.calls[0])
        assert f"Contract number: {CONTRACT}" in prompt
        assert "Amount: €29.90" in prompt
        assert response.json()["contract_number"] == CONTRACT

        restored = client.get(f"/api/sessions/{cid}").json()
        assert [m["role"] for m in restored["messages"]] == ["customer", "agent"] * 2
        assert len(restored["replies"]) == 2


# --- 2 & 3. ownership --------------------------------------------------------


def test_another_browser_gets_404_everywhere(monkeypatch):
    with backend() as a:
        browser(a)
        cid = new_session(a)

    with backend() as b:
        browser(b)
        assert b.get(f"/api/sessions/{cid}").status_code == 404
        assert turn(b, cid, "hello").status_code == 404
        assert b.get(f"/api/sessions/{cid}/events").status_code == 404
        assert b.get(f"/api/sessions/{cid}/trace").status_code == 404


def test_no_cookie_no_sessions():
    with backend() as client:
        assert client.get("/api/sessions").status_code == 401
        assert client.post("/api/sessions", json={}).status_code == 401


def test_the_selector_lists_only_this_browsers_sessions():
    with backend() as a:
        browser(a)
        mine = {new_session(a), new_session(a)}
        cookies_a = a.cookies
    with backend() as b:
        browser(b)
        theirs = new_session(b)
        listed_b = {i["conversation_id"] for i in b.get("/api/sessions").json()["items"]}
    with backend(cookies_a) as a:
        listed_a = {i["conversation_id"] for i in a.get("/api/sessions").json()["items"]}
        item = a.get("/api/sessions").json()["items"][0]

    assert listed_a == mine
    assert listed_b == {theirs}
    assert set(item) == {
        "conversation_id",
        "created_at",
        "last_activity_at",
        "domain",
        "status",
        "label",
    }, "summaries only: no transcript, no facts"


# --- 4 & 5. idempotency and concurrency ---------------------------------------


def test_a_retried_client_turn_id_does_not_call_the_model_twice(monkeypatch):
    with backend() as client:
        browser(client)
        cid = new_session(client)
        model = looked_up(monkeypatch)
        client_turn_id = str(uuid4())

        first = turn(client, cid, f"payment refused, {CONTRACT}", client_turn_id)
        calls = len(model.calls)
        again = turn(client, cid, f"payment refused, {CONTRACT}", client_turn_id)

        assert again.status_code == 200
        assert len(model.calls) == calls, "the stored reply, not a second model call"
        assert again.json()["text"] == first.json()["text"]
        assert len(client.get(f"/api/sessions/{cid}").json()["messages"]) == 2

        different = turn(client, cid, "something else entirely", client_turn_id)
        assert different.status_code == 409


def test_a_held_lease_refuses_a_second_turn(monkeypatch):
    with backend() as client:
        browser(client)
        cid = new_session(client)
        sql(
            """UPDATE platform.conversation_checkpoint
                  SET lease_token = gen_random_uuid(), lease_until = now() + interval '60 seconds'
                WHERE conversation_id = $1::uuid""",
            cid,
        )
        model = scripted(monkeypatch, [AIMessage("hello")])
        routed_to(monkeypatch, "billing")

        assert turn(client, cid, "hello").status_code == 409
        assert model.calls == []
        version = sql(
            "SELECT version FROM platform.conversation_checkpoint WHERE conversation_id = $1::uuid",
            cid,
        )[0]["version"]
        assert version == 1, "nothing advanced"


def test_a_stale_version_cannot_be_committed():
    """Optimistic concurrency, directly: a commit naming a version that has
    moved on writes nothing — transcript rows included."""
    from fibrapunto.state.models import ConversationState, TurnRole

    async def run():
        pool = await asyncpg.create_pool(get_settings().dsn("platform"), min_size=1, max_size=2)
        store = SessionStore(pool)
        state = ConversationState()
        await store.create(state, None)
        lease = await store.acquire(state.id)
        await pool.execute(
            "UPDATE platform.conversation_checkpoint SET version = 7 WHERE conversation_id = $1",
            state.id,
        )
        state.record_turn(TurnRole.customer, "hello")
        await store.begin_turn(state.id, uuid4(), "hello")
        from fibrapunto.persistence.sessions import CheckpointConflict

        with pytest.raises(CheckpointConflict):
            await store.commit(lease, state, uuid4(), {"text": "x"})
        turns = await pool.fetchval(
            "SELECT count(*) FROM platform.turn WHERE conversation_id = $1", state.id
        )
        await pool.close()
        return turns

    assert asyncio.run(run()) == 0


# --- 6. side effects -----------------------------------------------------------


def test_a_crash_after_the_case_is_opened_does_not_open_a_second(monkeypatch):
    """The case is written, then the process dies before the checkpoint. The
    retry replays the turn from the old checkpoint — and the unique index
    (migration 005) hands back the case that already exists."""
    real_commit = SessionStore.commit
    crashed = {"done": False}

    async def crash_once(self, *args, **kwargs):
        if not crashed["done"]:
            crashed["done"] = True
            raise RuntimeError("process died")
        return await real_commit(self, *args, **kwargs)

    monkeypatch.setattr(SessionStore, "commit", crash_once)

    with backend() as client:
        browser(client)
        cid = new_session(client)
        routed_to(monkeypatch, "billing", human=True)  # the case opens by code
        scripted(monkeypatch, [])
        client_turn_id = str(uuid4())

        assert turn(client, cid, "put me through to a person", client_turn_id).status_code == 500
        retried = turn(client, cid, "put me through to a person", client_turn_id)

        assert retried.status_code == 200, retried.text
        rows = sql("SELECT reference FROM crm.handover_record")
        assert len(rows) == 1, "one case, whatever the crash"
        assert retried.json()["handover_reference"] == rows[0]["reference"]


# --- 7. what a checkpoint holds ------------------------------------------------


def test_a_checkpoint_holds_no_transcript_and_no_sensitive_value(monkeypatch):
    monkeypatch.setattr(get_settings(), "redact_inbound", False)  # the worst case
    with backend() as client:
        browser(client)
        cid = new_session(client)
        routed_to(monkeypatch, "billing", financial="dispute")
        scripted(
            monkeypatch,
            [
                tool_call("billing_lookup", {"contract_number": CONTRACT}),
                AIMessage("I see the refused charge."),
            ],
        )
        message = f"the €29.90 charge is wrong, use my IBAN {IBAN} instead, {CONTRACT}"
        assert turn(client, cid, message).status_code == 200

    row = sql(
        "SELECT state_json::text AS j FROM platform.conversation_checkpoint"
        " WHERE conversation_id = $1::uuid",
        cid,
    )[0]
    stored = json.loads(row["j"])
    assert "turns" not in stored and "told_to_customer" not in stored["facts"]
    assert "capability_calls" not in stored
    assert IBAN not in row["j"] and "0542811101" not in row["j"]
    assert "I see the refused charge" not in row["j"], "no agent text in the checkpoint"
    assert stored["facts"]["contract_number"] == CONTRACT


# --- 8 & 9. expiry and retention -----------------------------------------------


def test_an_expired_principal_cannot_resume():
    with backend() as client:
        browser(client)
        cid = new_session(client)
        sql("UPDATE platform.browser_principal SET expires_at = now() - interval '1 second'")

        assert client.get(f"/api/sessions/{cid}").status_code == 401
        # A fresh identity is a different browser as far as ownership goes.
        browser(client)
        assert client.get(f"/api/sessions/{cid}").status_code == 404


def test_retention_purges_transcript_and_checkpoint_together(monkeypatch):
    with backend() as client:
        browser(client)
        cid = new_session(client)
        looked_up(monkeypatch)
        assert turn(client, cid, f"payment refused, {CONTRACT}").status_code == 200

        sql(
            "UPDATE platform.conversation SET retention_until = now() - interval '1 second'"
            " WHERE id = $1::uuid",
            cid,
        )

        async def purge():
            pool = await asyncpg.create_pool(get_settings().dsn("platform"), min_size=1, max_size=1)
            try:
                await retention.purge_expired(pool)
            finally:
                await pool.close()

        asyncio.run(purge())

        assert not sql(
            "SELECT 1 FROM platform.conversation_checkpoint WHERE conversation_id = $1::uuid", cid
        )
        assert all(
            r["text_redacted"] is None
            for r in sql(
                "SELECT text_redacted FROM platform.turn WHERE conversation_id = $1::uuid", cid
            )
        )
        assert all(
            r["response_json"] is None
            for r in sql(
                "SELECT response_json FROM platform.turn_request WHERE conversation_id = $1::uuid",
                cid,
            )
        )
        assert client.get(f"/api/sessions/{cid}").status_code == 404
        assert cid not in {
            i["conversation_id"] for i in client.get("/api/sessions").json()["items"]
        }


# --- 10. an unreadable checkpoint ----------------------------------------------


def test_an_unsupported_checkpoint_schema_fails_safely(monkeypatch):
    with backend() as client:
        browser(client)
        cid = new_session(client)
        sql(
            "UPDATE platform.conversation_checkpoint SET state_schema_version = 99"
            " WHERE conversation_id = $1::uuid",
            cid,
        )
        model = scripted(monkeypatch, [AIMessage("hello")])
        routed_to(monkeypatch, "billing")

        first = turn(client, cid, "hello")
        second = turn(client, cid, "hello")

        assert first.status_code == 409 and "cannot be resumed" in first.json()["detail"]
        assert second.status_code == 409, "refused again, not left locked"
        assert model.calls == []
        assert not sql("SELECT 1 FROM platform.turn WHERE conversation_id = $1::uuid", cid)


# --- rebuilt turns keep their domain ---------------------------------------------


def test_a_cancellation_opens_by_code_across_rebuilt_turns(monkeypatch):
    """Found live: every turn is rebuilt from the checkpoint and the stored
    transcript, and the transcript did not keep which domain a turn was in.
    The cancellation rules count agent turns *in the cancellation domain* and
    take the stated reason from the customer's words since entering it — both
    saw nothing, the case never opened by code, and the retention case after
    an accepted offer failed for want of a reason."""
    with backend() as client:
        browser(client)
        cid = new_session(client)
        routed_to(monkeypatch, "cancellation")

        scripted(monkeypatch, [AIMessage("Of course. What is your contract number?")])
        assert turn(client, cid, "i want to close my contract, i've moved house").status_code == 200
        scripted(
            monkeypatch,
            [
                tool_call("billing_lookup", {"contract_number": "FP-2021-017350"}),
                AIMessage("There is no lock-in on your contract. May I ask why?"),
            ],
        )
        assert turn(client, cid, "FP-2021-017350").status_code == 200
        scripted(monkeypatch, [AIMessage("Thank you for telling me.")])
        third = turn(client, cid, "as I said, I'm moving to another city")

        assert third.status_code == 200, third.text
        rows = sql("SELECT record_type::text AS t, stated_reason FROM crm.handover_record")
        assert len(rows) == 1 and rows[0]["t"] == "cancellation_request"
        assert "moved house" in rows[0]["stated_reason"]
        # A customer turn is recorded before routing, so the opening message
        # carries the starting domain; every agent turn was in cancellation.
        agent_domains = sql(
            "SELECT domain FROM platform.turn"
            " WHERE conversation_id = $1::uuid AND role = 'agent' ORDER BY seq",
            cid,
        )
        assert [r["domain"] for r in agent_domains] == ["cancellation"] * 3
