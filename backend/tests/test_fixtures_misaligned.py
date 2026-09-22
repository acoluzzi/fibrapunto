"""The CRM and billing disagree, on purpose (§6.1, §6.2, §15).

    "This split is not an artefact of the demo, it is the situation a real
    operator is in, and it produces the most instructive failure in the whole
    design: a contract that exists in the CRM and not in billing. The two
    systems disagree."

The misalignment is load-bearing test data. Without this file, a future edit
that "fixes" the inconsistency would silently delete the §15 row-3 scenario and
every eval case that depends on it, and nothing would fail until someone
noticed the demo had got less interesting.
"""

from __future__ import annotations

import yaml
from fibrapunto.settings import REPO_ROOT

CRM_FIXTURES = REPO_ROOT / "db" / "fixtures" / "seed.yaml"
BILLING_FIXTURES = REPO_ROOT / "mock-billing" / "src" / "mock_billing" / "fixtures" / "billing.yaml"

# Named rather than merely counted: if this contract ever gains a billing
# record, the failure message should say which scenario just disappeared.
CRM_ONLY_CONTRACT = "FP-2020-004199"


def _crm_contracts() -> set[str]:
    data = yaml.safe_load(CRM_FIXTURES.read_text(encoding="utf-8"))
    return {c["contract_number"] for c in data["contracts"]}


def _billing_contracts() -> set[str]:
    data = yaml.safe_load(BILLING_FIXTURES.read_text(encoding="utf-8"))
    return set(data["contracts"])


def test_exactly_one_contract_is_in_the_crm_but_not_in_billing():
    crm_only = _crm_contracts() - _billing_contracts()
    assert crm_only == {CRM_ONLY_CONTRACT}, (
        f"Expected exactly {{{CRM_ONLY_CONTRACT}}} to exist in the CRM and not "
        f"in billing — that is §6.1's two-systems-disagree scenario and §15's "
        f"third row. Found {crm_only or 'none'}.\n"
        f"If this was deliberate, the eval cases that depend on it need updating "
        f"too. If it was an accident, restore the asymmetry."
    )


def test_billing_holds_no_contract_the_crm_has_never_heard_of():
    """The inconsistency runs one way only.

    A contract in billing but not in the CRM would be a different scenario
    entirely — one the spec does not describe and the agent has no instructions
    for — so it should not appear by accident.
    """
    orphans = _billing_contracts() - _crm_contracts()
    assert not orphans, f"Billing knows contracts the CRM does not: {orphans}"


def test_the_worked_examples_contract_is_in_a_failed_state():
    """§4.1's transcript depends on FP-2023-048812 having a rejected direct
    debit at 29,90 € with a retry date. If the fixture drifts, the spec and the
    demo stop being the same conversation."""
    billing = yaml.safe_load(BILLING_FIXTURES.read_text(encoding="utf-8"))
    record = billing["contracts"]["FP-2023-048812"]
    assert record["status"] == "failed"
    assert record["amount"] == "29.90", "quoted in YAML so Decimal stays exact"
    assert record["next_retry_date"], "the customer is told when the retry happens"
    assert record["payment_link"], "the alternative to waiting must exist"


def test_a_contract_with_live_lock_in_and_instalments_exists():
    """§4.2's cancellation domain must have real constraints to state, not just
    a happy path where nothing is owed."""
    billing = yaml.safe_load(BILLING_FIXTURES.read_text(encoding="utf-8"))
    constrained = [
        number
        for number, record in billing["contracts"].items()
        if record.get("lock_in_until") and record.get("remaining_instalments")
    ]
    assert constrained, (
        "No contract has both a live lock-in and outstanding instalments; "
        "the cancellation domain has nothing substantive to tell the customer."
    )


def test_duplicate_matches_disambiguate_by_address():
    """§4.1 instruction 3 and §15 row 2: disambiguation is by service address,
    never by name. Two candidates sharing an address would make the instruction
    impossible to follow."""
    billing = yaml.safe_load(BILLING_FIXTURES.read_text(encoding="utf-8"))
    matches = billing["duplicate_matches"]
    assert len(matches) >= 2
    addresses = [m["service_address"] for m in matches]
    assert len(set(addresses)) == len(addresses), (
        "Two candidates share a service address, so the customer cannot tell "
        "them apart by the only criterion the instructions allow."
    )
