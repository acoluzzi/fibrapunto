"""The published contract matches the stub (§7).

contracts/billing-openapi.v1.yaml is what the integration layer registers
against. If the stub's interface changes and the document does not, the
integration is validating against a fiction — and the ContractViolation path
that protects §5.1 stops protecting anything.
"""

from __future__ import annotations

import pytest
import yaml
from fibrapunto.integrations.registration import (
    ContractViolation,
    ExternalServiceRegistration,
)
from mock_billing.export_openapi import OUTPUT_PATH, render

BILLING_PATH = "/billing/{contract_number}"


def test_committed_contract_is_not_stale():
    assert OUTPUT_PATH.exists(), "Run: uv run python -m mock_billing.export_openapi"
    assert OUTPUT_PATH.read_text(encoding="utf-8") == render(), (
        "contracts/billing-openapi.v1.yaml no longer matches the stub.\n"
        "Regenerate it, and bump info.version if the change is breaking:\n"
        "  uv run python -m mock_billing.export_openapi"
    )


def test_the_control_surface_is_not_published():
    """§7's contract describes a legacy billing system. A real one does not
    offer an endpoint that makes it fail on command, and publishing ours would
    misrepresent what is being integrated with.

    Checks the declared paths rather than the raw text — the banner comment
    mentions `_control` precisely to explain why it is absent.
    """
    document = yaml.safe_load(OUTPUT_PATH.read_text(encoding="utf-8"))
    leaked = [path for path in document["paths"] if path.startswith("/_")]
    assert not leaked, f"Test-harness endpoints leaked into the contract: {leaked}"


def test_registration_accepts_a_well_formed_response():
    registration = ExternalServiceRegistration()
    registration.validate_response(
        BILLING_PATH,
        200,
        {
            "status": "failed",
            "failureReason": "insufficient funds",
            "amount": "29.90",
            "invoiceMonth": "2026-09",
            "nextRetryDate": "2026-09-19",
            "paymentLink": "https://pay.example/x",
            "lockInUntil": None,
            "remainingInstalments": 0,
        },
    )


def test_registration_rejects_an_invalid_enum_value():
    """The far side inventing a new payment status is a contract violation, not
    something to pass upward and hope the model handles."""
    registration = ExternalServiceRegistration()
    with pytest.raises(ContractViolation):
        registration.validate_response(BILLING_PATH, 200, {"status": "sort-of-paid"})


def test_registration_rejects_an_undeclared_status_code():
    """§7 enumerates the outcomes. A 500 where the contract allows
    200/300/404/504 is the far side behaving in a way nobody designed for."""
    registration = ExternalServiceRegistration()
    with pytest.raises(ContractViolation) as exc:
        registration.validate_response(BILLING_PATH, 500, {"error": "boom"})
    assert "not declared" in exc.value.detail


def test_contract_carries_a_semver_version():
    """§14 attributes a behaviour change to the change that caused it; the
    interface version is part of that record."""
    registration = ExternalServiceRegistration()
    major, minor, patch = registration.version.split(".")
    assert all(part.isdigit() for part in (major, minor, patch))
