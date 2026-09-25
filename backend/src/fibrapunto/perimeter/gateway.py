"""The only door to CRM data (§8, §17.2).

Every read passes through here, and every method takes a `Principal`. There is
no default identity and no module-level connection: choosing who you are acting
as is a decision the caller makes explicitly, every time.

Two things this module must never do:

  * **`SELECT *`.** With column grants in force it is denied outright on
    `crm.customer` — asking for every column includes the two that are denied.
    That is a feature, and it is why every query below names its columns.

  * **Swallow a denial.** A `PerimeterError` raised here is caught by
    `@typed_capability` and mapped to an outcome, so it never reaches the model
    as itself. It is not caught *here*, because this layer's job is to be right
    about authorization, not to decide what the conversation does about it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

import asyncpg
import structlog

from fibrapunto import faults
from fibrapunto.perimeter.policy import FieldNotAuthorized, Principal
from fibrapunto.settings import get_settings

log = structlog.get_logger(__name__)

INSUFFICIENT_PRIVILEGE = "42501"


@dataclass(frozen=True, slots=True)
class CustomerSummary:
    """What the agent may know about a customer: a name, and nothing else.

    There is no `email` attribute. Not None, not redacted — absent. §9's
    "minimisation by authorization" means the field never enters the process,
    so it cannot be repeated in a conversation or sent to a model by any route,
    including a bug.
    """

    id: int
    full_name: str


@dataclass(frozen=True, slots=True)
class ContractRecord:
    contract_number: str
    customer_id: int
    modem_serial: str
    plan_name: str
    monthly_fee_eur: Decimal
    activation_date: date
    lock_in_until: date | None
    service_address: str
    status: str


class CrmGateway:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    @classmethod
    async def connect(cls, principal: Principal) -> CrmGateway:
        """Open a pool under the identity's own database role.

        The role is read from the policy, so the Postgres connection and the
        Python check cannot disagree about who is acting.
        """
        settings = get_settings()
        role = {
            "fibrapunto_agent": "agent",
            "fibrapunto_platform": "platform",
        }[principal.db_role]
        pool = await asyncpg.create_pool(settings.dsn(role), min_size=1, max_size=5)
        return cls(pool)

    async def close(self) -> None:
        await self._pool.close()

    # --- reads ---------------------------------------------------------------

    async def get_contract(
        self, principal: Principal, contract_number: str
    ) -> ContractRecord | None:
        columns = (
            "contract_number",
            "customer_id",
            "modem_serial",
            "plan_name",
            "monthly_fee_eur",
            "activation_date",
            "lock_in_until",
            "service_address",
            "status",
        )
        principal.check("contract", "read", columns)
        faults.fail_if_down("crm")

        row = await self._fetchrow(
            principal,
            f"SELECT {', '.join(columns)} FROM crm.contract WHERE contract_number = $1",
            contract_number,
            entity="contract",
        )
        return ContractRecord(**dict(row)) if row else None

    async def get_customer(self, principal: Principal, customer_id: int) -> CustomerSummary | None:
        """Name only — and the column list is the enforcement, not a convention.

        Asking for `email` here raises before a query is built; asking Postgres
        for it raises after. Both paths are tested, and the second one is the
        authority (tests/test_perimeter_raw_sql.py).
        """
        columns = ("id", "full_name")
        principal.check("customer", "read", columns)

        row = await self._fetchrow(
            principal,
            f"SELECT {', '.join(columns)} FROM crm.customer WHERE id = $1",
            customer_id,
            entity="customer",
        )
        return CustomerSummary(id=row["id"], full_name=row["full_name"]) if row else None

    async def read_customer_field(self, principal: Principal, customer_id: int, field: str) -> Any:
        """Read one named field. Exists so the perimeter can be *probed*.

        §8: "Test the boundary, do not trust it." This is what
        `make perimeter-proof` calls with both identities to produce the
        side-by-side diff, and what an adversarial eval case exercises when it
        asks for a customer's email address.
        """
        principal.check("customer", "read", (field,))
        row = await self._fetchrow(
            principal,
            f"SELECT {field} FROM crm.customer WHERE id = $1",
            customer_id,
            entity="customer",
            field=field,
        )
        return row[field] if row else None

    # --- the one write (§8: create on handover record, nothing else) ---------

    async def create_handover_record(self, principal: Principal, values: dict[str, Any]) -> str:
        """Insert a handover record and return the reference the DATABASE chose.

        `reference` and `handled_by_agent` are absent from the column list on
        purpose: the agent has no INSERT grant on either, so the reference comes
        from a sequence it may only USAGE (§12.4) and the agent-handled flag
        defaults true (§13). Both invariants are the database's, not ours.

        One case per conversation and record type is the database's too
        (migration 005). A concurrent duplicate does nothing, and the reference
        returned is the one already written — the same case, not a second one
        and not an error. The losing insert still draws a number from the
        sequence, so references can skip; they are unique, not contiguous.
        """
        columns = tuple(values)
        principal.check("handover_record", "create", columns)
        faults.fail_if_down("crm")

        placeholders = ", ".join(f"${i + 1}" for i in range(len(columns)))
        insert = (
            f"INSERT INTO crm.handover_record ({', '.join(columns)}) "
            f"VALUES ({placeholders}) "
            f"ON CONFLICT (origin_session_id, record_type) DO NOTHING "
            f"RETURNING reference"
        )
        existing = (
            "SELECT reference FROM crm.handover_record "
            "WHERE origin_session_id = $1 AND record_type = $2"
        )
        async with self._pool.acquire() as conn:
            try:
                reference = await conn.fetchval(insert, *values.values())
                if reference is None:
                    # Lost the race: the conflicting insert has committed by
                    # now (ON CONFLICT waits for it), so its row is visible.
                    reference = await conn.fetchval(
                        existing, values["origin_session_id"], values["record_type"]
                    )
                return reference
            except asyncpg.InsufficientPrivilegeError as exc:
                raise self._as_field_denial(principal, "handover_record", "create", exc) from exc

    # --- identity verification (migration 008) --------------------------------
    #
    # Two functions the agent's role may EXECUTE and nothing else: the answers
    # table is unreadable to it. Not in the policy file's entity list because
    # they are not columns; the database grant is the whole perimeter here.

    async def security_question(
        self, principal: Principal, contract_number: str
    ) -> tuple[str, str] | None:
        """(question_key, question) to ask for this contract, or None if the
        CRM does not know the contract."""
        faults.fail_if_down("crm")
        row = await self._fetchrow(
            principal,
            "SELECT question_key, question FROM crm.security_question($1)",
            contract_number,
            entity="contract",
        )
        return (row["question_key"], row["question"]) if row else None

    async def verify_security_answer(
        self, principal: Principal, contract_number: str, question_key: str, answer: str
    ) -> bool:
        faults.fail_if_down("crm")
        async with self._pool.acquire() as conn:
            return bool(
                await conn.fetchval(
                    "SELECT crm.verify_security_answer($1, $2, $3)",
                    contract_number,
                    question_key,
                    answer,
                )
            )

    # --- cases across conversations --------------------------------------------

    async def cases_for_contract(
        self, principal: Principal, contract_number: str
    ) -> list[dict[str, Any]]:
        """This contract's cases, newest first, as the agent may see them — no
        internal comments (§8). Read only after the customer is verified."""
        columns = ("reference", "record_type", "status", "created_at", "collected_context")
        principal.check("handover_record", "read", columns)
        faults.fail_if_down("crm")
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT reference, record_type::text AS record_type, status::text AS status,
                       created_at, collected_context->'retention_offer' AS offer
                  FROM crm.handover_record
                 WHERE contract_number = $1 OR claimed_contract_number = $1
                 ORDER BY created_at DESC
                 LIMIT 10
                """,
                contract_number,
            )
        return [dict(r) for r in rows]

    async def open_case_of_type(
        self, principal: Principal, contract_number: str, record_type: str
    ) -> tuple[str, bool] | None:
        """An open case of this type on this contract, from any conversation:
        (reference, contract_linked). Reused rather than duplicated."""
        principal.check("handover_record", "read", ("reference", "contract_link_missing", "status"))
        faults.fail_if_down("crm")
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT reference, contract_link_missing
                  FROM crm.handover_record
                 WHERE (contract_number = $1 OR claimed_contract_number = $1)
                   AND record_type = $2::crm.handover_type
                   AND status <> 'closed'
                 ORDER BY created_at DESC
                 LIMIT 1
                """,
                contract_number,
                record_type,
            )
        return (row["reference"], not row["contract_link_missing"]) if row else None

    # --- internals -----------------------------------------------------------

    async def _fetchrow(
        self,
        principal: Principal,
        query: str,
        *args: Any,
        entity: str,
        field: str | None = None,
    ) -> asyncpg.Record | None:
        async with self._pool.acquire() as conn:
            try:
                return await conn.fetchrow(query, *args)
            except asyncpg.InsufficientPrivilegeError as exc:
                raise self._as_field_denial(principal, entity, "read", exc, field) from exc

    @staticmethod
    def _as_field_denial(
        principal: Principal,
        entity: str,
        operation: str,
        exc: asyncpg.InsufficientPrivilegeError,
        field: str | None = None,
    ) -> FieldNotAuthorized:
        """Turn Postgres's denial into one that names the column.

        This is the constructive half of docs/FRICTION-LOG.md entry 1. Postgres
        says "permission denied for table customer" and does not say which
        column — which makes `GRANT SELECT ON crm.customer` the easiest fix and
        hands the agent the customer's email. Naming the field removes that
        temptation from whoever is debugging at the time.
        """
        log.error(
            "perimeter.denied",
            principal=principal.name,
            entity=entity,
            operation=operation,
            field=field,
            sqlstate=exc.sqlstate,
            enforced_by="postgres",
        )
        return FieldNotAuthorized(
            principal=principal.name,
            entity=entity,
            field=field or "(unnamed column)",
            operation=operation,  # type: ignore[arg-type]
            enforced_by="postgres",
            sqlstate=exc.sqlstate,
        )
