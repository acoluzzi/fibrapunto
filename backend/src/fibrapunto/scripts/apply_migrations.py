"""Apply db/migrations/*.sql in order, as the owner.

Deliberately not Alembic: there is one schema, it is created once, and the
interesting artefact is 004_grants.generated.sql, which is rendered from the
perimeter policy rather than hand-written. A migration framework would add a
version table and a mental model without adding a capability.

Idempotent: re-running is a no-op, which is what makes `make reset` safe to
press repeatedly during a demo (§17.12).
"""

from __future__ import annotations

import asyncio
import re
import sys
from pathlib import Path

import asyncpg

from fibrapunto.settings import REPO_ROOT, get_settings

MIGRATIONS_DIR = REPO_ROOT / "db" / "migrations"

# psql-style :'var' and :"var" placeholders. We are not going through psql, so
# substitute them here rather than shelling out to a client we would then have
# to ship in the image.
_QUOTED_LITERAL = re.compile(r":'(\w+)'")
_QUOTED_IDENT = re.compile(r':"(\w+)"')


def _substitute(sql: str, variables: dict[str, str]) -> str:
    def literal(match: re.Match[str]) -> str:
        value = variables[match.group(1)].replace("'", "''")
        return f"'{value}'"

    def identifier(match: re.Match[str]) -> str:
        value = variables[match.group(1)].replace('"', '""')
        return f'"{value}"'

    return _QUOTED_IDENT.sub(identifier, _QUOTED_LITERAL.sub(literal, sql))


async def _already_applied(conn: asyncpg.Connection, path: Path) -> bool:
    """Cheap idempotence: if the objects a migration creates already exist,
    skip it. Keyed on one distinctive object per file rather than a version
    table, because there are four files and they are not going to be reordered.
    """
    sentinel = {
        "001_crm.sql": "SELECT to_regclass('crm.customer') IS NOT NULL",
        "002_platform.sql": "SELECT to_regclass('platform.conversation') IS NOT NULL",
    }.get(path.name)

    if sentinel is None:
        # 003 and 004 are grant files: always re-apply, so that a policy edit
        # takes effect on the next `make reset` without a rebuild.
        return False
    return bool(await conn.fetchval(sentinel))


async def apply_all() -> int:
    settings = get_settings()
    variables = {
        "db_name": settings.postgres_db,
        "agent_password": settings.agent_db_password.get_secret_value(),
        "platform_password": settings.platform_db_password.get_secret_value(),
    }

    paths = sorted(MIGRATIONS_DIR.glob("*.sql"))
    if not paths:
        print(f"No migrations found in {MIGRATIONS_DIR}", file=sys.stderr)
        return 1

    conn = await asyncpg.connect(settings.dsn("owner"))
    try:
        for path in paths:
            if await _already_applied(conn, path):
                print(f"  skip   {path.name}  (already applied)")
                continue
            sql = _substitute(path.read_text(encoding="utf-8"), variables)
            await conn.execute(sql)
            print(f"  apply  {path.name}")
    finally:
        await conn.close()

    print("Migrations up to date.")
    return 0


def main() -> int:
    return asyncio.run(apply_all())


if __name__ == "__main__":
    raise SystemExit(main())
