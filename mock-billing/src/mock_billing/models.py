"""The billing system's wire contract (§7).

These models generate contracts/billing-openapi.v1.yaml, which is the
machine-readable interface definition §7 requires. The integration layer
registers against that document rather than against hand-written request code,
so the schema is the contract between the two sides and it is versioned with
the repository.

Field names are camelCase on the wire because that is what §5's capability table
specifies. They are snake_case in Python. The alias generator does the
translation in one place rather than in twelve.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel


class WireModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        json_schema_serialization_defaults_required=True,
    )


class PaymentStatus(StrEnum):
    paid = "paid"
    failed = "failed"
    pending = "pending"


class BillingStatus(WireModel):
    """A 200 response: the billing system found the contract and has an answer."""

    status: PaymentStatus = Field(
        description=("Stato dell'addebito per il mese in corso. Può essere comunicato al cliente."),
        examples=[PaymentStatus.failed],
    )
    failure_reason: str | None = Field(
        default=None,
        description=(
            "Motivo del rifiuto, in italiano piano e senza gergo bancario. "
            "Valorizzato solo quando status è 'failed'."
        ),
        examples=["fondi insufficienti al momento dell'addebito"],
    )
    amount: Decimal | None = Field(
        default=None,
        description="Importo dell'addebito in euro.",
        examples=[Decimal("29.90")],
    )
    invoice_month: str | None = Field(
        default=None,
        description="Mese di fatturazione, formato YYYY-MM.",
        examples=["2026-09"],
    )
    next_retry_date: date | None = Field(
        default=None,
        description=(
            "Data del prossimo tentativo di addebito. "
            "Da comunicare al cliente quando un addebito è stato respinto."
        ),
    )
    payment_link: str | None = Field(
        default=None,
        description="Link sicuro per il pagamento immediato con carta.",
    )
    lock_in_until: date | None = Field(
        default=None,
        description=(
            "Fine del vincolo contrattuale. Null significa nessun vincolo: "
            "in quel caso dirlo esplicitamente al cliente, non tacere."
        ),
    )
    remaining_instalments: int | None = Field(
        default=None,
        description="Rate del modem ancora da pagare. Zero significa nessuna rata residua.",
        examples=[2],
    )


class ContractMatch(WireModel):
    """One candidate when the legacy system returns namesakes."""

    contract_number: str = Field(examples=["FP-2023-048712"])
    service_address: str = Field(
        description=(
            "Indirizzo di attivazione. È l'unico criterio ammesso per far "
            "scegliere al cliente fra più contratti: mai il nome."
        ),
        examples=["Via Garibaldi 12, Bologna"],
    )


class MultipleMatches(WireModel):
    """A 300 response: more than one contract matched.

    §4.1 instruction 3: ask the customer to confirm by service address, and
    never pick the first result.
    """

    matches: list[ContractMatch]


class BillingError(WireModel):
    """A 404 or 504 response. Deliberately carries no billing data at all —
    there is nothing here for a reasoning layer to mistake for an answer."""

    error: Literal["CONTRACT_NOT_FOUND", "GATEWAY_TIMEOUT", "UNAUTHORIZED"]
    message: str | None = None
