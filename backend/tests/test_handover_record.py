"""Creating a handover record, against the real database (§12.4, §13, §1).

No model calls: the payload is built from typed state, so everything §13 asks
about a record — completeness, queue, priority, the agent-handled flag, the
reference the agent cannot choose — is testable without a provider. That is the
same property that makes §1's completeness target reachable in the first place.
"""

from __future__ import annotations

import asyncpg
import pytest
from fibrapunto.capabilities.create_handover_record import create_handover_record
from fibrapunto.capabilities.outcomes import (
    HandoverCreated,
    HandoverCreatedWithoutContractLink,
    HandoverFailed,
)
from fibrapunto.domains.registry import DomainKey
from fibrapunto.handover import completeness
from fibrapunto.perimeter.gateway import CrmGateway
from fibrapunto.perimeter.policy import svc_agent
from fibrapunto.scripts.reset import reset
from fibrapunto.settings import get_settings
from fibrapunto.state.models import BillingFacts, ConversationState

# No module-level asyncio mark: this file mixes sync completeness checks with
# async database tests, and `asyncio_mode = "auto"` already handles the latter.

LINKED = "FP-2024-092003"  # live lock-in, 2 instalments


@pytest.fixture
async def clean_db():
    conn = await asyncpg.connect(get_settings().dsn("owner"))
    try:
        await reset(conn)
        yield conn
    finally:
        await conn.close()


@pytest.fixture
async def gateway(clean_db):
    gw = await CrmGateway.connect(svc_agent())
    try:
        yield gw
    finally:
        await gw.close()


def state_for(domain: DomainKey, contract: str | None = LINKED) -> ConversationState:
    """A conversation that has already collected what §13 requires."""
    state = ConversationState(current_domain=domain)
    state.collected.contract_number = contract
    state.collected.stated_reason = "mi sono trasferito"
    state.collected.told_to_customer = ["Il vincolo termina il 10 maggio 2026 e restano 2 rate."]
    state.collected.billing = BillingFacts(status="paid", amount=None, remaining_instalments=2)
    return state


async def create(state, gateway, subject="Richiesta disdetta — trasloco"):
    return await create_handover_record(
        state=state,
        gateway=gateway,
        principal=svc_agent(),
        subject=subject,
        trigger="categorical:cancellation",
    )


# --- the happy path ----------------------------------------------------------


async def test_a_cancellation_record_is_created(gateway, clean_db):
    result = await create(state_for(DomainKey.cancellation), gateway)

    assert isinstance(result, HandoverCreated), getattr(result, "missing_fields", result)
    assert result.reference == "00001042", (
        "the first reference of a fresh demo should match §4.1's worked example"
    )
    assert "giorno lavorativo" in result.response_window


async def test_queue_and_priority_are_derived_not_chosen(gateway, clean_db):
    """§13's table, and §1's cancellation discipline.

    The model supplies the subject and the customer's words. Type, queue and
    priority come from the domain, and CHECK constraints make the wrong pairing
    impossible even if this code were bypassed.
    """
    await create(state_for(DomainKey.cancellation), gateway)

    row = await clean_db.fetchrow("SELECT record_type, priority, queue FROM crm.handover_record")
    assert row["record_type"] == "cancellation_request"
    assert row["priority"] == "high"
    assert row["queue"] == "retention"


async def test_the_agent_cannot_claim_the_record_is_not_agent_handled(gateway, clean_db):
    """§13. The column is absent from the agent's INSERT grant and defaults
    true, so this is an invariant rather than a field the model fills in."""
    await create(state_for(DomainKey.cancellation), gateway)
    assert await clean_db.fetchval("SELECT handled_by_agent FROM crm.handover_record")


async def test_the_reference_comes_from_the_database(gateway, clean_db):
    """§12.4: the agent must not state a reference it did not receive.

    It holds USAGE on the sequence and has no INSERT grant on the column, so
    the value is assigned by Postgres and returned — never constructed here.
    """
    first = await create(state_for(DomainKey.cancellation), gateway)
    second = await create(state_for(DomainKey.cancellation), gateway)
    assert first.reference != second.reference
    assert int(second.reference) == int(first.reference) + 1


async def test_the_record_is_complete_by_sql_not_only_by_python(gateway, clean_db):
    """§1 measures completeness with a SQL view that recomputes the check
    independently of the code path that wrote the row — so the metric is not
    measured by the thing it measures."""
    await create(state_for(DomainKey.cancellation), gateway)
    assert await clean_db.fetchval("SELECT is_complete FROM crm.v_handover_completeness")


async def test_the_request_date_is_recorded(gateway, clean_db):
    """§4.2: "notice periods run from it, and a customer who is told 'your
    request is registered as of today' has something the company can be held
    to"."""
    await create(state_for(DomainKey.cancellation), gateway)
    context = await clean_db.fetchval("SELECT collected_context FROM crm.handover_record")
    import json

    assert json.loads(context)["request_date"]


async def test_what_the_customer_was_told_is_carried(gateway, clean_db):
    """§12.2 calls this "the field most often left out and the one that most
    often causes a second complaint"."""
    import json

    await create(state_for(DomainKey.cancellation), gateway)
    context = json.loads(
        await clean_db.fetchval("SELECT collected_context FROM crm.handover_record")
    )
    assert context["what_customer_was_told"]
    assert "10 maggio 2026" in context["what_customer_was_told"][0]


# --- §13: the contract that cannot be linked ---------------------------------


async def test_an_unlinkable_contract_still_produces_a_record(gateway, clean_db):
    """§13: "An incomplete record beats no record: the customer has still asked
    for something, and the company has still been told."
    """
    state = state_for(DomainKey.cancellation, contract="FP-2099-999999")
    result = await create(state, gateway)

    assert isinstance(result, HandoverCreatedWithoutContractLink)
    assert result.reference

    row = await clean_db.fetchrow(
        "SELECT contract_number, claimed_contract_number, contract_link_missing "
        "FROM crm.handover_record"
    )
    assert row["contract_number"] is None
    assert row["claimed_contract_number"] == "FP-2099-999999"
    assert row["contract_link_missing"] is True


# --- §12.4: when it cannot complete ------------------------------------------


async def test_an_incomplete_record_is_never_written(gateway, clean_db):
    """§1 wants completeness at 100%, which is only reachable if a thin record
    cannot be written at all."""
    state = state_for(DomainKey.cancellation)
    state.collected.told_to_customer = []  # §12.2's required field, missing

    result = await create(state, gateway)

    assert isinstance(result, HandoverFailed)
    assert "what_customer_was_told" in result.missing_fields
    assert await clean_db.fetchval("SELECT count(*) FROM crm.handover_record") == 0


async def test_a_failed_handover_carries_no_reference_at_all(gateway, clean_db):
    """§12.4: "The agent must not state a reference number it did not receive."

    Not an empty string, not None — the field does not exist on the model, so
    there is nothing for the reasoning layer to mistake for one.
    """
    state = state_for(DomainKey.cancellation)
    state.collected.told_to_customer = []

    result = await create(state, gateway)
    assert not hasattr(result, "reference")
    assert "non dare nessun numero di pratica" in result.guidance.lower()


async def test_a_billing_dispute_routes_to_the_other_queue(gateway, clean_db):
    state = state_for(DomainKey.billing)
    state.collected.billing = BillingFacts(status="failed", failure_reason="fondi")
    state.collected.told_to_customer = ["L'addebito è stato respinto."]
    # §13 requires a billing dispute to record what is actually disputed —
    # supplied by the model, since only it knows what the customer objected to.
    state.collected.disputed_point = "il cliente dichiara un piano da 24,90 €"

    result = await create_handover_record(
        state=state,
        gateway=gateway,
        principal=svc_agent(),
        subject="Contestazione addebito settembre",
        trigger="categorical:amount_dispute",
    )

    assert isinstance(result, HandoverCreated), getattr(result, "missing_fields", result)
    row = await clean_db.fetchrow("SELECT record_type, priority, queue FROM crm.handover_record")
    assert (row["record_type"], row["priority"], row["queue"]) == (
        "billing_dispute",
        "medium",
        "billing_operations",
    )


# --- the completeness matrix itself ------------------------------------------


async def test_cancellation_requires_more_than_a_billing_dispute():
    """§13's table differs by type, and the check should too."""
    cancellation = set(completeness.required_for("cancellation_request"))
    dispute = set(completeness.required_for("billing_dispute"))

    assert "request_date" in cancellation and "request_date" not in dispute
    assert "disputed_point" in dispute
    assert {"what_customer_was_told", "escalation_trigger"} <= cancellation & dispute


# --- a billing escalation that is not a dispute -------------------------------
#
# Found by running eval case 005 against a live model: the billing system was
# down, the customer accepted a handover, and the record failed to validate
# because `disputed_point` was missing. There was nothing to dispute — no figure
# had ever been read. The company was therefore never told, which is the outcome
# §13 exists to prevent.


def _context(**overrides):
    base = {
        "lookups": [],
        "escalation_trigger": "billing_unavailable",
        "what_customer_was_told": ["Il sistema non risponde."],
        "billing_status": "non disponibile",
    }
    base.update(overrides)
    return base


def test_a_dispute_is_not_required_when_no_figure_was_ever_read():
    """The bug from case 005, as an assertion."""
    missing = completeness.missing_fields(
        "billing_dispute",
        _context(lookups=[{"capability": "billing_lookup", "outcome": "serviceUnavailable"}]),
        subject="Addebito diretto fallito, verifica non riuscita",
    )
    assert missing == [], f"the record must be creatable; missing={missing}"


def test_a_dispute_is_still_required_when_the_amount_was_read():
    """The valuable half of the check is preserved: if the agent read a figure
    and the customer objected to it, a record that does not say what they
    objected to is a thin record, and asking again is right."""
    missing = completeness.missing_fields(
        "billing_dispute",
        _context(
            lookups=[{"capability": "billing_lookup", "outcome": "found"}],
            billing_status="failed",
            amount_eur="29.90",
        ),
        subject="Contestazione importo",
    )
    assert missing == ["disputed_point"]


def test_the_dispute_requirement_is_satisfied_once_collected():
    missing = completeness.missing_fields(
        "billing_dispute",
        _context(
            lookups=[{"capability": "billing_lookup", "outcome": "found"}],
            billing_status="failed",
            disputed_point="Il piano è 24,90, non 29,90",
        ),
        subject="Contestazione importo",
    )
    assert missing == []


def test_what_the_customer_was_told_is_required_either_way():
    """The relaxation is narrow. Fields the agent always has are still enforced,
    because §12.2's "what the agent already told the customer" is what stops the
    receiving human contradicting it."""
    missing = completeness.missing_fields(
        "billing_dispute",
        _context(what_customer_was_told=[]),
        subject="Qualcosa",
    )
    assert "what_customer_was_told" in missing
