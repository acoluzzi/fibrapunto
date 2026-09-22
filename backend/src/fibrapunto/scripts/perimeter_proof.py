"""Run the same query under both identities, side by side (§8).

    "Test the boundary, do not trust it. Ask the agent for the email address
    associated with a contract. It must fail on authorization, not on an
    instruction."

A conversational demo of that is weak: the agent has no tool for it, so nothing
observable happens. This is the strong version — one query, two identities, two
outcomes, printed. It is also available at POST /_admin/perimeter-probe for the
UI's perimeter panel.

    make perimeter-proof
"""

from __future__ import annotations

import asyncio

import asyncpg

from fibrapunto.perimeter.policy import PerimeterError, Principal, platform_admin, svc_agent
from fibrapunto.settings import get_settings

GREEN, RED, DIM, BOLD, RESET = "\033[32m", "\033[31m", "\033[2m", "\033[1m", "\033[0m"

# (entity, column) pairs that make the boundary visible: one the agent needs,
# two it is denied, one that belongs to a record it may not read in full.
PROBES = [
    ("crm.customer", "full_name"),
    ("crm.customer", "email"),
    ("crm.customer", "phone"),
]


async def probe(principal: Principal, table: str, column: str) -> tuple[bool, str]:
    """Attempt the read. Returns (allowed, detail)."""
    role = {"fibrapunto_agent": "agent", "fibrapunto_platform": "platform"}[principal.db_role]
    conn = await asyncpg.connect(get_settings().dsn(role))
    try:
        value = await conn.fetchval(f"SELECT {column} FROM {table} ORDER BY id LIMIT 1")
        return True, str(value)
    except asyncpg.InsufficientPrivilegeError as exc:
        return False, f"DENIED (SQLSTATE {exc.sqlstate})"
    finally:
        await conn.close()


async def _run() -> int:
    agent, admin = svc_agent(), platform_admin()

    print(f"\n{BOLD}The same query, two identities (§8){RESET}")
    print(f"{DIM}Enforced by Postgres column grants. No application code in the loop.{RESET}\n")
    print(f"  {'column':<22} {'svc_agent':<34} platform_admin")
    print(f"  {'-' * 22} {'-' * 34} {'-' * 26}")

    denials = 0
    for table, column in PROBES:
        agent_ok, agent_detail = await probe(agent, table, column)
        admin_ok, admin_detail = await probe(admin, table, column)

        if not agent_ok:
            denials += 1
            left = f"{RED}{agent_detail}{RESET}"
        else:
            left = f"{GREEN}{agent_detail}{RESET}"
        right = admin_detail if admin_ok else f"{RED}{admin_detail}{RESET}"

        padding = " " * max(0, 34 - len(agent_detail))
        print(f"  {table.split('.')[-1]}.{column:<14} {left}{padding} {right}")

    print()

    # What the policy layer says, which is the readable half of the answer.
    print(f"{BOLD}Why{RESET}  {DIM}(contracts/perimeter-policy.yaml){RESET}")
    for _, column in PROBES:
        try:
            agent.check("customer", "read", (column,))
            print(f"  {GREEN}✓{RESET} customer.{column}: granted")
        except PerimeterError as exc:
            print(f"  {RED}✗{RESET} {exc}")

    print(
        f"\n{DIM}The agent's denial is not a refusal it was instructed to make. "
        f"There is no code path,\nand no grant. §9's minimisation by "
        f"authorization: what it cannot read it cannot repeat.{RESET}\n"
    )
    return 0 if denials else 1


def main() -> int:
    return asyncio.run(_run())


if __name__ == "__main__":
    raise SystemExit(main())
