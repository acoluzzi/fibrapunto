"""An anonymous browser identity, so a browser can find its conversations again.

This identifies a browser installation, **not a person**. It is not a login and
it authenticates nothing about the customer: the contract number the customer
types remains data the agent checks, never a credential. What it buys is
narrow — after a restart, or a reload, this browser can list and resume the
conversations it started, and no other browser can.

The token is 256 random bits, handed to the browser as an HttpOnly cookie
(page scripts never see it), SameSite=Strict (another site cannot ride on it),
Secure everywhere but localhost, scoped to /api. Only an HMAC-SHA-256 of it is
stored, keyed off the master key, so a copy of the table cannot be replayed as
a cookie.

A conversation that belongs to another browser is answered 404, not 403: the
answer must not confirm that it exists.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import asyncpg
from fastapi import HTTPException, Request, Response

from fibrapunto.settings import get_settings

COOKIE = "fibrapunto_customer"


def _key() -> bytes:
    master = get_settings().fibrapunto_master_key.get_secret_value()
    return hashlib.sha256(b"fibrapunto-browser-principal\0" + master.encode()).digest()


def digest(token: str) -> bytes:
    return hmac.new(_key(), token.encode(), hashlib.sha256).digest()


def _lifetime() -> timedelta:
    # As long as the transcripts it can reach are kept (§9): a principal that
    # outlived its conversations would point at nothing.
    return timedelta(days=get_settings().transcript_retention_days)


def _set_cookie(request: Request, response: Response, token: str) -> None:
    host = request.url.hostname or ""
    response.set_cookie(
        COOKIE,
        token,
        max_age=int(_lifetime().total_seconds()),
        path="/api",
        httponly=True,
        samesite="strict",
        secure=host not in ("localhost", "127.0.0.1"),
    )


async def principal_for(pool: asyncpg.Pool, token: str | None) -> UUID | None:
    """The live principal this token belongs to, or None. Expired or revoked
    principals are refused exactly like unknown ones."""
    if not token:
        return None
    return await pool.fetchval(
        """
        SELECT id FROM platform.browser_principal
         WHERE token_hash = $1 AND revoked_at IS NULL AND expires_at > now()
        """,
        digest(token),
    )


async def access(pool: asyncpg.Pool, request: Request, response: Response) -> UUID:
    """Create the browser's principal, or refresh the one it has.

    Sliding expiry: a browser in use keeps its conversations reachable for the
    retention period after its last visit. A new token is not issued on every
    call — the existing one is renewed — so two tabs do not race each other.
    """
    token = request.cookies.get(COOKIE)
    principal = await principal_for(pool, token)
    expires = datetime.now(UTC) + _lifetime()
    if principal is not None and token:
        await pool.execute(
            """
            UPDATE platform.browser_principal
               SET last_seen_at = now(), expires_at = $2
             WHERE id = $1
            """,
            principal,
            expires,
        )
    else:
        token = secrets.token_urlsafe(32)  # 256 bits
        principal = uuid4()
        await pool.execute(
            """
            INSERT INTO platform.browser_principal (id, token_hash, expires_at)
            VALUES ($1, $2, $3)
            """,
            principal,
            digest(token),
            expires,
        )
    _set_cookie(request, response, token)
    return principal


async def require_principal(pool: asyncpg.Pool, request: Request) -> UUID:
    principal = await principal_for(pool, request.cookies.get(COOKIE))
    if principal is None:
        raise HTTPException(401, "no browser identity: call POST /api/customer/access first")
    return principal
