"""The reset is idempotent (§17.12).

"The reset is scripted and idempotent so that a demo can be repeated from a
clean state on demand." Repeatable means the second run is indistinguishable
from the first — including the surrogate keys and the next reference number,
because a demo that produces case 00001042 the first time and 00001051 the
fourth time is not a demo from a known state.
"""

from __future__ import annotations

import asyncpg
import pytest
from fibrapunto.scripts.reset import FIRST_REFERENCE, reset
from fibrapunto.settings import get_settings

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def owner_conn():
    conn = await asyncpg.connect(get_settings().dsn("owner"))
    try:
        yield conn
    finally:
        await conn.close()


async def _snapshot(conn: asyncpg.Connection) -> list[tuple]:
    """Everything a demo could notice, in a stable order."""
    customers = await conn.fetch("SELECT id, full_name, email, phone FROM crm.customer ORDER BY id")
    contracts = await conn.fetch(
        """
        SELECT contract_number, customer_id, plan_name, monthly_fee_eur,
               activation_date, lock_in_until, service_address, status
        FROM crm.contract ORDER BY contract_number
        """
    )
    next_reference = await conn.fetchval(
        "SELECT last_value, is_called FROM crm.handover_reference_seq"
    )
    return [tuple(r) for r in customers] + [tuple(r) for r in contracts] + [next_reference]


async def test_reset_twice_is_indistinguishable(owner_conn):
    await reset(owner_conn)
    first = await _snapshot(owner_conn)

    await reset(owner_conn)
    second = await _snapshot(owner_conn)

    assert first == second, "The second reset did not reproduce the first state"


async def test_reset_restores_the_worked_examples_reference(owner_conn):
    """§4.1's worked example gives the customer case 00001042.

    The transcript in the spec and the transcript on screen should agree, so
    the sequence restarts rather than continuing from wherever it got to.
    """
    await reset(owner_conn)
    last_value = await owner_conn.fetchval("SELECT last_value FROM crm.handover_reference_seq")
    assert last_value == FIRST_REFERENCE


async def test_reset_clears_records_created_since(owner_conn):
    """A demo that opened three cases leaves no trace after a reset."""
    await reset(owner_conn)
    await owner_conn.execute(
        """
        INSERT INTO crm.handover_record
            (record_type, priority, queue, contract_number, subject,
             collected_context, origin_session_id, contract_link_missing)
        VALUES ('billing_dispute', 'medium', 'billing_operations', 'FP-2023-048812',
                'Contestazione di prova', '{}'::jsonb, gen_random_uuid(), false)
        """
    )
    assert await owner_conn.fetchval("SELECT count(*) FROM crm.handover_record") == 1

    await reset(owner_conn)
    assert await owner_conn.fetchval("SELECT count(*) FROM crm.handover_record") == 0
