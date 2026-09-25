"""Creating a handover record, against the real database (§12.4, §13, §1).

No model calls: the payload is built from typed state, so everything §13 asks
about a record — completeness, queue, priority, the agent-handled flag, the
reference the agent cannot choose — is testable without a provider. That is the
same property that makes §1's completeness target reachable in the first place.
"""

from __future__ import annotations

import json

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
    state.collected.stated_reason = "I have moved house"
    state.collected.told_to_customer = ["The lock-in ends on 10 May 2026 and 2 instalments remain."]
    state.collected.billing = BillingFacts(status="paid", amount=None, remaining_instalments=2)
    return state


async def create(state, gateway, subject="Cancellation request — moving house"):
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
    assert "working day" in result.response_window


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
    assert "10 May 2026" in context["what_customer_was_told"][0]


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

    result = await create(state, gateway, subject="")  # a required field, missing

    assert isinstance(result, HandoverFailed)
    assert "subject" in result.missing_fields
    assert await clean_db.fetchval("SELECT count(*) FROM crm.handover_record") == 0


async def test_a_failed_handover_carries_no_reference_at_all(gateway, clean_db):
    """§12.4: "The agent must not state a reference number it did not receive."

    Not an empty string, not None — the field does not exist on the model, so
    there is nothing for the reasoning layer to mistake for one.
    """
    state = state_for(DomainKey.cancellation)

    result = await create(state, gateway, subject="")
    assert not hasattr(result, "reference")
    assert "give no case number" in result.guidance.lower()


async def test_a_billing_dispute_routes_to_the_other_queue(gateway, clean_db):
    state = state_for(DomainKey.billing)
    state.collected.billing = BillingFacts(status="failed", failure_reason="insufficient funds")
    state.collected.told_to_customer = ["The charge was refused."]
    # §13 requires a billing dispute to record what is actually disputed —
    # supplied by the model, since only it knows what the customer objected to.
    state.collected.disputed_point = "the customer says their plan is €24.90"

    result = await create_handover_record(
        state=state,
        gateway=gateway,
        principal=svc_agent(),
        subject="September charge disputed",
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
        "what_customer_was_told": ["The system is not responding."],
        "billing_status": "non disponibile",
    }
    base.update(overrides)
    return base


def test_a_dispute_is_not_required_when_no_figure_was_ever_read():
    """The bug from case 005, as an assertion."""
    missing = completeness.missing_fields(
        "billing_dispute",
        _context(lookups=[{"capability": "billing_lookup", "outcome": "serviceUnavailable"}]),
        subject="Direct debit failed, lookup unsuccessful",
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
        subject="Amount disputed",
    )
    assert missing == ["disputed_point"]


def test_the_dispute_requirement_is_satisfied_once_collected():
    missing = completeness.missing_fields(
        "billing_dispute",
        _context(
            lookups=[{"capability": "billing_lookup", "outcome": "found"}],
            billing_status="failed",
            disputed_point="The plan is €24.90, not €29.90",
        ),
        subject="Amount disputed",
    )
    assert missing == []


def test_what_the_customer_was_told_is_required_either_way():
    """The relaxation is narrow. Fields the agent always has are still enforced,
    because §12.2's "what the agent already told the customer" is what stops the
    receiving human contradicting it."""
    missing = completeness.missing_fields(
        "billing_dispute",
        _context(what_customer_was_told=[]),
        subject="Something",
    )
    assert "what_customer_was_told" in missing


async def test_a_first_turn_case_records_what_the_customer_is_told(gateway, clean_db):
    """On the customer's first message the agent has said nothing yet, so the
    field §12.2 requires was empty and the record could never be written.
    It now carries what the closing template tells them, and why."""
    state = state_for(DomainKey.cancellation)
    state.collected.told_to_customer = []

    result = await create(state, gateway)

    assert isinstance(result, HandoverCreated), getattr(result, "missing_fields", result)
    told = json.loads(
        await clean_db.fetchval("SELECT collected_context::text FROM crm.handover_record")
    )["what_customer_was_told"]
    assert told[0].startswith("[recorded by the system]")
    assert "cancellation request is being passed on" in told[0]
    assert "will get back to them" in told[0]


# --- one conversation, one case ----------------------------------------------
#
# Found on the first live run of the dispute case: the model emitted TWO
# create_handover_record calls in a single assistant message, each with its own
# summary, and both executed. The operator queue showed 00001042 and 00001043
# for one customer with one problem.


async def test_a_second_call_returns_the_first_reference(gateway, clean_db):
    """§12.4 says report what happened. What happened is that a case exists and
    this is its number — so the second call is answered truthfully rather than
    refused, and the customer is told the same reference either way."""
    state = state_for(DomainKey.cancellation)

    first = await create(state, gateway)
    second = await create(state, gateway, subject="A different summary entirely")

    assert isinstance(first, HandoverCreated)
    assert isinstance(second, HandoverCreated)
    assert second.reference == first.reference
    assert await clean_db.fetchval("SELECT count(*) FROM crm.handover_record") == 1


async def test_two_calls_in_one_turn_open_one_case(gateway, clean_db):
    """The shape that actually happened: both tool calls dispatched from the
    same assistant message, so nothing in between could have noticed."""
    import asyncio

    state = state_for(DomainKey.cancellation)
    await create(state, gateway)
    await asyncio.gather(
        create(state, gateway, subject="Summary A"),
        create(state, gateway, subject="Summary B"),
    )

    assert await clean_db.fetchval("SELECT count(*) FROM crm.handover_record") == 1


async def test_the_repeat_keeps_the_unlinked_shape(gateway, clean_db):
    """A repeat must not upgrade an unlinked record into a linked one. The
    customer was told a colleague would check the number; saying otherwise on
    the next turn contradicts what they were already told (§12.2)."""
    state = state_for(DomainKey.cancellation, contract="FP-2099-999999")

    first = await create(state, gateway)
    second = await create(state, gateway)

    assert isinstance(first, HandoverCreatedWithoutContractLink)
    assert isinstance(second, HandoverCreatedWithoutContractLink)
    assert second.reference == first.reference


# --- one case per type, enforced by the database (migration 005) -------------


async def test_a_cancellation_after_a_dispute_opens_its_own_case(gateway, clean_db):
    """Keyed per conversation alone, the cancellation got the dispute's
    reference back — no cancellation record was ever written, and the customer
    was told the retention team would call about a case sitting in billing."""
    state = state_for(DomainKey.billing)
    state.collected.disputed_point = "says their plan is €24.90"
    dispute = await create_handover_record(
        state=state,
        gateway=gateway,
        principal=svc_agent(),
        subject="Amount disputed",
        trigger="categorical:amount_dispute",
    )

    state.current_domain = DomainKey.cancellation
    cancellation = await create(state, gateway)

    assert isinstance(dispute, HandoverCreated)
    assert isinstance(cancellation, HandoverCreated)
    assert cancellation.reference != dispute.reference
    assert cancellation.queue_label != dispute.queue_label
    rows = await clean_db.fetch(
        "SELECT reference, record_type::text AS record_type FROM crm.handover_record"
    )
    assert {r["record_type"]: r["reference"] for r in rows} == {
        "billing_dispute": dispute.reference,
        "cancellation_request": cancellation.reference,
    }


async def test_concurrent_first_calls_open_one_case(gateway, clean_db):
    """Two requests for one conversation, neither aware of the other — two
    workers, or two copies of the state. The in-memory check passes in both;
    the unique index is what holds, and both are told the same reference."""
    import asyncio

    first = state_for(DomainKey.cancellation)
    second = first.model_copy(deep=True)

    a, b = await asyncio.gather(
        create(first, gateway, subject="Summary A"),
        create(second, gateway, subject="Summary B"),
    )

    assert await clean_db.fetchval("SELECT count(*) FROM crm.handover_record") == 1
    assert a.reference == b.reference


async def test_the_database_refuses_a_second_case_of_the_same_type(clean_db):
    """The index itself, with no Fibrapunto code in the loop."""
    state = state_for(DomainKey.cancellation)
    row = (
        "INSERT INTO crm.handover_record (record_type, priority, queue, subject, "
        "collected_context, origin_session_id, contract_link_missing) VALUES "
        "('cancellation_request', 'high', 'retention', 's', '{}', $1, true)"
    )
    await clean_db.execute(row, state.id)
    with pytest.raises(asyncpg.UniqueViolationError):
        await clean_db.execute(row, state.id)


# --- §9 point 3: the record defends itself -----------------------------------


async def test_a_raw_iban_in_state_never_reaches_the_record(gateway, clean_db):
    """Inbound redaction off, or a form the detector missed on the way in: the
    value sits in collected state. The handover boundary must scrub it anyway.

    It did not. `build_values` redacted the context, and the next line
    re-serialised the raw one over it."""
    iban = "IT60X0542811101000000123456"
    state = state_for(DomainKey.cancellation)
    state.collected.told_to_customer = [f"Please don't send your IBAN {iban} in chat."]
    state.collected.stated_reason = f"moving house, my IBAN is {iban}"

    result = await create(state, gateway)

    assert isinstance(result, HandoverCreated), getattr(result, "missing_fields", result)
    row = await clean_db.fetchrow(
        "SELECT collected_context::text AS context, stated_reason FROM crm.handover_record"
    )
    assert iban not in row["context"]
    assert iban not in row["stated_reason"]
    assert "[[REDACTED:IBAN]]" in row["context"]


def test_python_and_sql_agree_a_cancellation_needs_the_stated_reason():
    """`crm.v_handover_completeness` requires it; the pre-insert check did not,
    so a record could be written that the §1 metric then counted incomplete."""
    context = {
        "lookups": [],
        "escalation_trigger": "categorical:cancellation",
        "what_customer_was_told": ["x"],
        "request_date": "2026-09-25",
        "tenure": "3 years",
        "constraints": {},
    }
    assert "stated_reason" in completeness.missing_fields(
        "cancellation_request", context, "Cancellation request", None
    )
    assert (
        completeness.missing_fields(
            "cancellation_request", context, "Cancellation request", "I've moved house"
        )
        == []
    )


async def test_a_record_the_sql_view_calls_incomplete_is_never_written(gateway, clean_db):
    state = state_for(DomainKey.cancellation)
    state.collected.stated_reason = None

    result = await create(state, gateway)

    assert isinstance(result, HandoverFailed)
    assert "stated_reason" in result.missing_fields
    assert await clean_db.fetchval("SELECT count(*) FROM crm.handover_record") == 0
