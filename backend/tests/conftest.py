"""The test run gets its own database, never the demo's.

The database tests reset what they touch: they truncate the CRM and the
platform schema before running. Pointed at the same database as the running
demo, a `make test` silently wiped every case and conversation on screen and
restarted case numbering — while the recommendation system's budget, in its own
process, kept counting the offers those deleted cases had carried. The
dashboard then showed two offers accepted and the operator view one case.

So the suite runs against `fibrapunto_test` (override with FIBRAPUNTO_TEST_DB),
created and migrated here once per session. The roles are cluster-wide, so the
perimeter tests exercise the same grants the demo runs under.

Set before anything imports `fibrapunto.settings`: environment variables win
over `.env`, and the settings object is cached on first use.
"""

from __future__ import annotations

import asyncio
import os

TEST_DB = os.environ.get("FIBRAPUNTO_TEST_DB", "fibrapunto_test")
os.environ["POSTGRES_DB"] = TEST_DB

import asyncpg  # noqa: E402
import pytest  # noqa: E402
from fibrapunto.scripts.apply_migrations import apply_all  # noqa: E402
from fibrapunto.settings import get_settings  # noqa: E402


async def _prepare() -> None:
    settings = get_settings()
    assert settings.postgres_db == TEST_DB, "the suite must never touch the demo database"
    admin = await asyncpg.connect(settings.dsn("owner").rsplit("/", 1)[0] + "/postgres")
    try:
        exists = await admin.fetchval("SELECT 1 FROM pg_database WHERE datname = $1", TEST_DB)
        if not exists:
            await admin.execute(f'CREATE DATABASE "{TEST_DB}"')
    finally:
        await admin.close()
    await apply_all()


@pytest.fixture(scope="session", autouse=True)
def _test_database() -> None:
    try:
        asyncio.run(_prepare())
    except (OSError, asyncpg.PostgresError) as exc:
        # No database at all: the tests that need one fail on their own, with
        # their own message; the ones that do not still run.
        print(f"\n[conftest] test database not prepared: {exc}")
