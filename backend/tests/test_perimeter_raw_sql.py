"""The perimeter proof (§8, §11 layer 1, §15 "Outside the perimeter").

Every test here connects to Postgres **as the agent role, with no Fibrapunto
code in the loop**. Nothing in `perimeter/policy.py` can influence the outcome;
if these pass, the boundary is real.

That distinction is the whole point. §8 says: "Test the boundary, do not trust
it. Ask the agent for the email address associated with a contract. It must fail
on *authorization*, not on an instruction. If an instruction is what stops it,
the prompt has been quietly covering a hole in the permissions."

A Python-level check can be bypassed, refactored around, or deleted by someone
who does not know why it is there. A missing column grant cannot.
"""

from __future__ import annotations

import asyncpg
import pytest
from fibrapunto.settings import get_settings

pytestmark = pytest.mark.asyncio

# SQLSTATE 42501 — insufficient_privilege. This is the error the perimeter
# produces, and the one §8.1 complains is too opaque to debug.
INSUFFICIENT_PRIVILEGE = "42501"


@pytest.fixture
async def agent_conn():
    """A connection under the agent's own service identity (§8)."""
    settings = get_settings()
    conn = await asyncpg.connect(settings.dsn("agent"))
    try:
        yield conn
    finally:
        await conn.close()


@pytest.fixture
async def platform_conn():
    """A connection under the platform identity, for the contrast."""
    settings = get_settings()
    conn = await asyncpg.connect(settings.dsn("platform"))
    try:
        yield conn
    finally:
        await conn.close()


# --- what the agent may do ---------------------------------------------------


async def test_agent_may_read_customer_name(agent_conn):
    """Name only. Needed to link a handover record; nothing more is needed."""
    await agent_conn.fetch("SELECT id, full_name FROM crm.customer LIMIT 1")


async def test_agent_may_read_contract(agent_conn):
    await agent_conn.fetch("SELECT * FROM crm.contract LIMIT 1")


# --- what the agent may not do -----------------------------------------------


@pytest.mark.parametrize("column", ["email", "phone"])
async def test_agent_cannot_read_customer_contact_details(agent_conn, column):
    """§9: what it cannot read it cannot repeat in a conversation or send to a
    model. Minimisation by authorization, not by instruction."""
    with pytest.raises(asyncpg.InsufficientPrivilegeError) as exc:
        await agent_conn.fetch(f"SELECT {column} FROM crm.customer LIMIT 1")
    assert exc.value.sqlstate == INSUFFICIENT_PRIVILEGE


async def test_agent_cannot_select_star_on_customer(agent_conn):
    """The reason the gateway must always project columns explicitly.

    With column grants in force `SELECT *` is denied outright — it asks for
    every column, including the two that are denied. This is a feature, and it
    is why a `SELECT *` anywhere in the data layer is a bug.
    """
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        await agent_conn.fetch("SELECT * FROM crm.customer LIMIT 1")


async def test_agent_cannot_read_internal_comments(agent_conn):
    """§8: field-level restriction on the handover record too."""
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        await agent_conn.fetch("SELECT internal_comments FROM crm.handover_record LIMIT 1")


async def test_agent_cannot_write_to_contract(agent_conn):
    """§8: contract state is a human's decision. No writes, ever."""
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        await agent_conn.execute("UPDATE crm.contract SET status = 'terminated'")


async def test_agent_cannot_delete_contract(agent_conn):
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        await agent_conn.execute("DELETE FROM crm.contract")


async def test_agent_cannot_modify_a_handover_record(agent_conn):
    """§8: "Does not edit or close it: once open, it belongs to people."""
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        await agent_conn.execute("UPDATE crm.handover_record SET status = 'closed'")


async def test_agent_cannot_delete_a_handover_record(agent_conn):
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        await agent_conn.execute("DELETE FROM crm.handover_record")


async def test_agent_cannot_read_its_own_transcripts(agent_conn):
    """The agent has no grant on schema platform at all.

    Same decision as the email restriction, for the same reason: it does not
    need them, and what it cannot read it cannot leak.
    """
    with pytest.raises((asyncpg.InsufficientPrivilegeError, asyncpg.InvalidSchemaNameError)):
        await agent_conn.fetch("SELECT * FROM platform.turn LIMIT 1")


async def test_agent_cannot_read_trace_events(agent_conn):
    with pytest.raises((asyncpg.InsufficientPrivilegeError, asyncpg.InvalidSchemaNameError)):
        await agent_conn.fetch("SELECT * FROM platform.trace_event LIMIT 1")


# --- the contrast: same query, other identity --------------------------------


async def test_platform_identity_may_read_the_email(platform_conn):
    """The denial is about *this identity*, not about the column being secret.

    This is the second column of `make perimeter-proof`, and the reason that
    output is convincing: the same query, two identities, two outcomes.
    """
    await platform_conn.fetch("SELECT email, phone FROM crm.customer LIMIT 1")


# --- the invariants the grants encode ----------------------------------------


async def test_agent_cannot_choose_a_handover_reference(agent_conn):
    """§12.4: the agent must not state a reference number it did not receive.

    It has USAGE on the sequence and no INSERT grant on the column, so the
    reference is assigned by the database. It cannot be forged or chosen.
    """
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        await agent_conn.execute(
            "INSERT INTO crm.handover_record "
            "(reference, record_type, priority, queue, subject, collected_context, "
            " origin_session_id, contract_link_missing) "
            "VALUES ('00009999', 'billing_dispute', 'medium', 'billing_operations', "
            "'x', '{}'::jsonb, gen_random_uuid(), true)"
        )


async def test_agent_cannot_claim_a_record_is_not_agent_handled(agent_conn):
    """§13: handled_by_agent is true for every record the agent opens.

    Omitted from the INSERT grant and defaulted to true, so this is an invariant
    rather than a field the model is trusted to fill in honestly.
    """
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        await agent_conn.execute(
            "INSERT INTO crm.handover_record "
            "(record_type, priority, queue, subject, collected_context, "
            " origin_session_id, contract_link_missing, handled_by_agent) "
            "VALUES ('billing_dispute', 'medium', 'billing_operations', "
            "'x', '{}'::jsonb, gen_random_uuid(), true, false)"
        )
