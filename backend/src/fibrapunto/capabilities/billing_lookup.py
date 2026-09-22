"""Billing lookup — the first of the three capabilities (§5).

The description below is what the model reads to decide whether to call this,
and §5.2 is blunt about the stakes: "A vague description is not a documentation
problem; it is a defect that surfaces at runtime as a hallucination." It is
given verbatim from the spec, in Italian.

Note what the signature does *not* contain: any credential. The client is
constructed with a handle and resolves the value inside its transport (§17.7).
"""

from __future__ import annotations

import structlog

from fibrapunto.capabilities.outcomes import (
    BillingLookupResult,
    BillingRejected,
    BillingServiceUnavailable,
)
from fibrapunto.capabilities.preconditions import came_from_customer, is_well_formed
from fibrapunto.capabilities.runtime import typed_capability
from fibrapunto.integrations.billing_client import BillingClient
from fibrapunto.settings import get_settings

log = structlog.get_logger(__name__)

CAPABILITY_NAME = "billing_lookup"

DESCRIPTION = (
    "Consulta lo stato di fatturazione di un contratto. "
    "Chiamala solo dopo che il cliente ha fornito un numero di contratto nel "
    "formato FP-YYYY-NNNNNN. Non chiamarla mai con un numero ipotizzato o "
    "ricostruito."
)

INPUT_DESCRIPTION = (
    "Il numero di contratto fornito dal cliente, formato FP-YYYY-NNNNNN. "
    "Deve provenire dal cliente alla lettera."
)


@typed_capability(
    CAPABILITY_NAME,
    on_unexpected=lambda incident_id: BillingServiceUnavailable(incident_id=incident_id),
    timeout_s=get_settings().capability_timeout_s,
)
async def billing_lookup(
    contract_number: str,
    *,
    client: BillingClient,
    customer_turns: list[str],
    test_mode: str | None = None,
) -> BillingLookupResult:
    """Look up a contract's billing status.

    `customer_turns` is everything the customer has actually typed. It is a
    required argument rather than optional context, because §5.2's "must come
    from the customer verbatim" cannot be checked without it — and a rule that
    cannot be checked is a request.
    """
    number = contract_number.strip().upper()

    if not is_well_formed(number):
        # §4.1 instruction 2: ask once more, then offer a human. The `rejected`
        # outcome carries that instruction with it.
        log.info("billing_lookup.rejected", reason="format_invalid")
        return BillingRejected(reason="format_invalid")

    if not came_from_customer(number, customer_turns):
        # The model produced a well-formed number the customer never typed.
        # Plausible, checkable, and exactly the failure §5.2 legislates against.
        log.warning("billing_lookup.rejected", reason="contract_number_not_from_customer")
        return BillingRejected(reason="contract_number_not_from_customer")

    return await client.lookup(number, test_mode=test_mode)
