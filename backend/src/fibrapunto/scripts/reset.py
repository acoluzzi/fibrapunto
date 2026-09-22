"""Reset to a known data state (§17.12, §6.2).

"The reset is scripted and idempotent so that a demo can be repeated from a
clean state on demand." Run it twice and the second run leaves the database
byte-identical to the first — tests/test_reset_idempotent.py checksums both.

Runs as the database **owner**, alongside the migrations. Truncating tables and
restarting identity sequences is schema-owner work; the platform role is for
what the application does platform-side (traces, transcripts, the operator
view), and giving it TRUNCATE purely so this script could run would be widening
a grant to make an error go away — see docs/FRICTION-LOG.md entry 1.

What it is emphatically not run as is the agent's identity: the agent has no
write grant on any CRM table at all (§8).
"""

from __future__ import annotations

import asyncio
import sys
from datetime import date
from decimal import Decimal
from typing import Any

import asyncpg
import yaml

from fibrapunto.settings import REPO_ROOT, get_settings

FIXTURES = REPO_ROOT / "db" / "fixtures" / "seed.yaml"

# §4.1's worked example gives the customer case number 00001042. Restarting the
# sequence here means the first record opened in any demo carries that number,
# so the transcript in the spec and the transcript on screen agree.
FIRST_REFERENCE = 1042


async def reset(conn: asyncpg.Connection) -> dict[str, int]:
    fixtures: dict[str, Any] = yaml.safe_load(FIXTURES.read_text(encoding="utf-8"))

    async with conn.transaction():
        # CASCADE reaches platform.turn and platform.trace_event through their
        # foreign keys. RESTART IDENTITY so ids are stable across resets, which
        # is what makes the idempotence check a simple checksum.
        await conn.execute(
            """
            TRUNCATE crm.handover_record,
                     crm.contract,
                     crm.customer,
                     platform.trace_event,
                     platform.turn,
                     platform.conversation,
                     platform.eval_run
            RESTART IDENTITY CASCADE
            """
        )

        customer_ids: dict[str, int] = {}
        for customer in fixtures["customers"]:
            customer_ids[customer["key"]] = await conn.fetchval(
                """
                INSERT INTO crm.customer (full_name, email, phone)
                VALUES ($1, $2, $3) RETURNING id
                """,
                customer["full_name"],
                customer["email"],
                customer["phone"],
            )

        for contract in fixtures["contracts"]:
            await conn.execute(
                """
                INSERT INTO crm.contract (
                    contract_number, customer_id, modem_serial, plan_name,
                    monthly_fee_eur, activation_date, lock_in_until,
                    service_address, status
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9::crm.contract_status)
                """,
                contract["contract_number"],
                customer_ids[contract["customer"]],
                contract["modem_serial"],
                contract["plan_name"],
                Decimal(str(contract["monthly_fee_eur"])),
                _as_date(contract["activation_date"]),
                _as_date(contract["lock_in_until"]),
                contract["service_address"],
                contract["status"],
            )

        await conn.execute(
            f"ALTER SEQUENCE crm.handover_reference_seq RESTART WITH {FIRST_REFERENCE}"
        )

    return {
        "customers": len(fixtures["customers"]),
        "contracts": len(fixtures["contracts"]),
        "next_reference": FIRST_REFERENCE,
    }


def _as_date(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


async def _run() -> int:
    settings = get_settings()
    if not FIXTURES.exists():
        print(f"Fixtures not found: {FIXTURES}", file=sys.stderr)
        return 1

    conn = await asyncpg.connect(settings.dsn("owner"))
    try:
        summary = await reset(conn)
        contracts = await conn.fetch(
            """
            SELECT c.contract_number, c.plan_name, c.lock_in_until, cu.full_name
            FROM crm.contract c
            JOIN crm.customer cu ON cu.id = c.customer_id
            ORDER BY c.contract_number
            """
        )
    finally:
        await conn.close()

    print(
        f"\nReset complete — {summary['customers']} customers, "
        f"{summary['contracts']} contracts. "
        f"Next handover reference: {summary['next_reference']:08d}\n"
    )
    for row in contracts:
        lock_in = row["lock_in_until"].isoformat() if row["lock_in_until"] else "nessun vincolo"
        print(
            f"  {row['contract_number']}  {row['full_name']:<16} {row['plan_name']:<12} {lock_in}"
        )
    print()
    return 0


def main() -> int:
    return asyncio.run(_run())


if __name__ == "__main__":
    raise SystemExit(main())
