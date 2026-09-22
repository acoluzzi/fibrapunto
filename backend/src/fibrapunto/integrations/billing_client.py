"""The billing system client (§7, §5.1).

Every path out of this module returns a typed outcome. There is no path that
raises, because the layer above is the reasoning layer and §5.1 is explicit
about what happens when it is handed a generic failure: "a language model
presented with a generic failure and a customer waiting for an answer will tend
to fill the gap."

Mapping, and note that two quite different things collapse to the same outcome
on purpose:

    200 + valid body      -> BillingFound
    300                   -> BillingMultipleMatches
    404                   -> BillingNotFound
    504                   -> BillingServiceUnavailable
    read timeout          -> BillingServiceUnavailable   (client gave up first)
    401 / connection error-> BillingServiceUnavailable
    contract violation    -> BillingServiceUnavailable

The timeout pair matters. In `timeout` mode the stub sleeps past the client's
deadline, so the client aborts with a read timeout and the 504 never arrives.
Both are real production paths, they are different branches, and only one of
them usually gets tested. `test_capability_never_raises.py` covers both.

A credential never appears in this module's arguments or its logs. The client is
constructed with a *handle*; the value is resolved inside the transport layer
(§17.7).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx
import structlog

from fibrapunto.capabilities.outcomes import (
    BillingFound,
    BillingLookupResult,
    BillingMultipleMatches,
    BillingNotFound,
    BillingServiceUnavailable,
    ContractMatch,
)
from fibrapunto.integrations.registration import (
    ContractViolation,
    ExternalServiceRegistration,
)
from fibrapunto.settings import get_settings

log = structlog.get_logger(__name__)

BILLING_PATH_TEMPLATE = "/billing/{contract_number}"
SOURCE_SYSTEM = "legacy-billing"


class BillingClient:
    def __init__(
        self,
        base_url: str | None = None,
        timeout_s: float | None = None,
        registration: ExternalServiceRegistration | None = None,
        token_provider: Any = None,
    ) -> None:
        settings = get_settings()
        self._base_url = (base_url or settings.billing_base_url).rstrip("/")
        # §16: "The integration timeout is the line that decides when to stop
        # waiting and hand over; it is a product decision dressed as a
        # configuration value, and it should be set with the operator."
        self._timeout_s = timeout_s or settings.billing_timeout_seconds
        self._registration = registration or ExternalServiceRegistration()
        self._token_provider = token_provider

    async def lookup(
        self, contract_number: str, test_mode: str | None = None
    ) -> BillingLookupResult:
        incident_id = uuid.uuid4().hex[:12]
        headers: dict[str, str] = {}

        if self._token_provider is not None:
            headers["Authorization"] = f"Bearer {self._token_provider()}"
        if test_mode:
            headers["X-Test-Mode"] = test_mode

        url = f"{self._base_url}/billing/{contract_number}"

        try:
            async with httpx.AsyncClient(timeout=self._timeout_s) as client:
                response = await client.get(url, headers=headers)
        except httpx.TimeoutException:
            # The stub is still sleeping. We gave up first — which is the point
            # of having a timeout at all.
            log.warning("billing.timeout", incident_id=incident_id, timeout_s=self._timeout_s)
            return BillingServiceUnavailable(incident_id=incident_id)
        except httpx.HTTPError as exc:
            log.warning("billing.transport_error", incident_id=incident_id, error=str(exc))
            return BillingServiceUnavailable(incident_id=incident_id)

        return self._interpret(response, incident_id)

    def _interpret(self, response: httpx.Response, incident_id: str) -> BillingLookupResult:
        try:
            body = response.json() if response.content else None
        except ValueError:
            log.warning("billing.unparseable_body", incident_id=incident_id)
            return BillingServiceUnavailable(incident_id=incident_id)

        try:
            self._registration.validate_response(BILLING_PATH_TEMPLATE, response.status_code, body)
        except ContractViolation as violation:
            # The far side broke its own published contract. From here that is
            # indistinguishable from not answering, and it must produce no data.
            log.error(
                "billing.contract_violation",
                incident_id=incident_id,
                status=response.status_code,
                detail=violation.detail,
            )
            return BillingServiceUnavailable(incident_id=incident_id)

        match response.status_code:
            case 200:
                return self._to_found(body, incident_id)
            case 300:
                return BillingMultipleMatches(
                    matches=[
                        ContractMatch(
                            contract_number=m["contractNumber"],
                            service_address=m["serviceAddress"],
                        )
                        for m in body["matches"]
                    ]
                )
            case 404:
                return BillingNotFound()
            case _:
                # 504, 401, and anything else the contract declares but we have
                # no better answer for. Never leaks the status to the model.
                log.warning(
                    "billing.unavailable",
                    incident_id=incident_id,
                    status=response.status_code,
                )
                return BillingServiceUnavailable(incident_id=incident_id)

    def _to_found(self, body: dict[str, Any], incident_id: str) -> BillingLookupResult:
        try:
            amount = Decimal(str(body["amount"])) if body.get("amount") is not None else None
        except (InvalidOperation, TypeError):
            # A malformed amount is exactly the kind of thing that must not
            # become a plausible-looking figure in an Italian sentence.
            log.error("billing.bad_amount", incident_id=incident_id, raw=body.get("amount"))
            return BillingServiceUnavailable(incident_id=incident_id)

        return BillingFound(
            status=body["status"],
            failure_reason=body.get("failureReason"),
            amount=amount,
            invoice_month=body.get("invoiceMonth"),
            next_retry_date=body.get("nextRetryDate"),
            payment_link=body.get("paymentLink"),
            lock_in_until=body.get("lockInUntil"),
            remaining_instalments=body.get("remainingInstalments"),
            source_system=SOURCE_SYSTEM,
            fetched_at=datetime.now(UTC).isoformat(),
        )
