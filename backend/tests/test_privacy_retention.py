"""Redaction boundaries and the retention sweep (§9).

§9's honest limitation names three mitigations and assigns all three to the
implementation: "redaction on stored transcripts, a retention period short
enough to be defensible, and access control on the transcript store."

The third is already proved elsewhere — `test_perimeter_raw_sql.py` shows the
agent role has no grant on the `platform` schema at all. This file is the other
two, and the load-bearing test is `test_storage_redaction_holds_with_inbound_
redaction_off`: if that one fails, the demo toggle is a data-protection switch
rather than a model-exposure switch, and §9's limitation is wider than the
document admits.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import asyncpg
import pytest
from fibrapunto.privacy import detectors, redaction, retention
from fibrapunto.settings import get_settings

# No module-level asyncio mark: this file mixes sync boundary tests with async
# database tests, and `asyncio_mode = "auto"` already handles the latter.

IBAN = "IT60X0542811101000000123456"
CARD = "4539578763621486"  # passes Luhn


@pytest.fixture
async def pool():
    settings = get_settings()
    pool = await asyncpg.create_pool(settings.dsn("platform"), min_size=1, max_size=2)
    try:
        yield pool
    finally:
        await pool.close()


@pytest.fixture
async def conversation(pool):
    """A conversation already past its retention date."""
    conversation_id = uuid4()
    await pool.execute(
        """
        INSERT INTO platform.conversation
            (id, agent_model, router_model, prompt_sha, retention_until)
        VALUES ($1, 'test/agent', 'test/router', 'deadbeef', $2)
        """,
        conversation_id,
        datetime.now(UTC) - timedelta(days=1),
    )
    yield conversation_id
    await pool.execute("DELETE FROM platform.conversation WHERE id = $1", conversation_id)


# --- the four enforcement points --------------------------------------------


def test_storage_redaction_holds_with_inbound_redaction_off(monkeypatch):
    """The one that matters.

    `REDACT_INBOUND` exists so both behaviours can be demonstrated (§9's honest
    limitation, DECISIONS #16). If storage redaction were conditional on it,
    flipping the toggle during a demo would start writing raw IBANs to disk —
    the toggle would silently be a data-protection switch. §11's layer argument,
    applied to §9: each boundary defends itself.
    """
    monkeypatch.setattr(get_settings(), "redact_inbound", False)

    to_model, kinds = redaction.for_model(f"My IBAN is {IBAN}")
    assert IBAN in to_model, "with the toggle off, the model does receive it — that is the point"
    assert kinds, "and the conversation still records that an IBAN was seen"

    stored, flags = redaction.for_storage(f"My IBAN is {IBAN}")
    assert IBAN not in stored
    assert flags == ["IBAN"]


def test_inbound_redaction_on_keeps_the_digits_in_this_process(monkeypatch):
    monkeypatch.setattr(get_settings(), "redact_inbound", True)
    to_model, kinds = redaction.for_model(f"My IBAN is {IBAN}")
    assert IBAN not in to_model
    assert [k.value for k in kinds] == ["IBAN"]


def test_the_handover_record_is_scrubbed_at_every_depth():
    """§9: "never write it into a handover record or any other stored field."
    Nested because the context block is assembled from typed state and will grow
    fields — a top-level-only version would be correct today and quietly wrong
    after the next addition."""
    scrubbed = redaction.for_handover(
        {
            "summary": f"They sent the card {CARD}",
            "nested": {"note": f"IBAN {IBAN}"},
            "list": [f"card {CARD}"],
            "untouched": 42,
        }
    )
    assert CARD not in str(scrubbed)
    assert IBAN not in str(scrubbed)
    assert scrubbed["untouched"] == 42


def test_the_reply_to_the_customer_is_scrubbed():
    """Structurally unreachable while inbound redaction is on — the model never
    held the digits. It is the only guard left when the toggle is off, which is
    exactly when a missing one would be missed."""
    reply, kinds = redaction.for_customer(f"Confirming the IBAN {IBAN}, thank you.")
    assert IBAN not in reply
    assert kinds


def test_a_contract_number_survives_every_boundary():
    """FP-2023-048812 is not sensitive and is needed in all four places. A
    redactor that ate it would break §4.1 while looking like caution."""
    contract = "FP-2023-048812"
    assert contract in redaction.for_storage(f"Contract {contract}")[0]
    assert contract in redaction.for_handover({"n": contract})["n"]
    assert contract in redaction.for_customer(f"Contract {contract}")[0]


# --- the sweep ---------------------------------------------------------------


async def test_the_sweep_nulls_the_words_and_keeps_the_skeleton(pool, conversation):
    """The difference between a retention policy and data loss. §1's metrics are
    computed over these rows; deleting them would make every historical number
    drift as the window moved, and a measurement that changes because time
    passed is not a measurement."""
    await pool.execute(
        """
        INSERT INTO platform.turn (conversation_id, seq, role, text_redacted)
        VALUES ($1, 0, 'customer', 'I would like to cancel')
        """,
        conversation,
    )
    await pool.execute(
        """
        INSERT INTO platform.trace_event (conversation_id, name, attributes)
        VALUES ($1, 'turn.received', '{"text": "something"}'::jsonb)
        """,
        conversation,
    )

    result = await retention.purge_expired(pool)
    assert result.conversations >= 1

    turn = await pool.fetchrow(
        "SELECT text_redacted, purged_at FROM platform.turn WHERE conversation_id = $1",
        conversation,
    )
    assert turn is not None, "the row must survive — only the words go"
    assert turn["text_redacted"] is None
    assert turn["purged_at"] is not None

    event = await pool.fetchrow(
        "SELECT name, attributes FROM platform.trace_event WHERE conversation_id = $1",
        conversation,
    )
    assert event["name"] == "turn.received", "the event name and timing survive"
    assert "something" not in event["attributes"]


async def test_the_sweep_is_idempotent(pool, conversation):
    """`make reset` and a restarting sweep both re-run it. A second pass must
    find nothing rather than double-counting."""
    await pool.execute(
        """
        INSERT INTO platform.turn (conversation_id, seq, role, text_redacted)
        VALUES ($1, 0, 'customer', 'text')
        """,
        conversation,
    )
    first = await retention.purge_expired(pool)
    second = await retention.purge_expired(pool)

    assert first.conversations >= 1
    assert second.conversations == 0
    assert second.empty


async def test_a_conversation_inside_its_retention_window_is_untouched(pool):
    conversation_id = uuid4()
    await pool.execute(
        """
        INSERT INTO platform.conversation
            (id, agent_model, router_model, prompt_sha, retention_until)
        VALUES ($1, 'test/agent', 'test/router', 'deadbeef', $2)
        """,
        conversation_id,
        datetime.now(UTC) + timedelta(days=30),
    )
    await pool.execute(
        """
        INSERT INTO platform.turn (conversation_id, seq, role, text_redacted)
        VALUES ($1, 0, 'customer', 'still inside the window')
        """,
        conversation_id,
    )
    try:
        await retention.purge_expired(pool)
        row = await pool.fetchrow(
            "SELECT text_redacted FROM platform.turn WHERE conversation_id = $1",
            conversation_id,
        )
        assert row["text_redacted"] == "still inside the window"
    finally:
        await pool.execute("DELETE FROM platform.conversation WHERE id = $1", conversation_id)


async def test_overdue_is_zero_after_a_sweep(pool, conversation):
    """The number an auditor asks for. Anything but zero means the sweep is not
    keeping up with the policy the operator published."""
    await retention.purge_expired(pool)
    status = await retention.retention_status(pool)
    assert status["overdue"] == 0
    assert status["retention_days"] == get_settings().transcript_retention_days


# --- the invariant -----------------------------------------------------------


async def test_no_stored_transcript_contains_a_detectable_value(pool):
    """The standing invariant (§9), re-run over whatever is in the table.

    Deliberately not a test of a function: it asks whether the *database* holds
    anything the detectors would flag, which is the only form of the question
    that survives someone adding a new write path.
    """
    rows = await pool.fetch(
        "SELECT conversation_id, seq, text_redacted FROM platform.turn "
        "WHERE text_redacted IS NOT NULL"
    )
    offenders = [
        (
            row["conversation_id"],
            row["seq"],
            [k.value for k in detectors.find_kinds(row["text_redacted"])],
        )
        for row in rows
        if detectors.contains_sensitive(row["text_redacted"])
    ]
    assert not offenders, f"sensitive values found in stored transcripts: {offenders}"
