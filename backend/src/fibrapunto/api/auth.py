"""Who may open the operator view and the admin routes.

The operator view reads as platform_admin: customer email and phone, internal
comments, every case by its sequential reference. Served on the same origin as
the chat with no check, that is the whole perimeter of §8 undone one layer up —
the agent's role cannot read `customer.email`, and anyone with the chat URL
could.

This is a demo gate, not an identity system: one shared operator password,
exchanged at a login form for a signed session cookie. The cookie is HttpOnly,
so page scripts never hold it, and SameSite=Strict, so another site cannot ride
on it. Scripts (the eval harness, curl) may send the same password as HTTP Basic
instead; the server never *asks* for Basic, so a browser never shows its prompt.
In production the operator UI is a separate service behind SSO, and none of
this exists.

Fails closed. No OPERATOR_PASSWORD configured means every request is refused,
not that the check is skipped — an unset variable must never be the thing that
opens the door.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import hmac
import time

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel

from fibrapunto.settings import get_settings

OPERATOR_USER = "operator"
COOKIE = "fibrapunto_operator"
SESSION_SECONDS = 8 * 3600

router = APIRouter(prefix="/api/operator", tags=["operator"])


def _password() -> str:
    password = get_settings().operator_password.get_secret_value()
    if not password:
        raise HTTPException(503, "operator access is disabled: OPERATOR_PASSWORD is not set")
    return password


def _signature(password: str, expires: int) -> str:
    # Keyed on the password: changing it signs every existing session out.
    key = hashlib.sha256(b"fibrapunto-operator-session\0" + password.encode()).digest()
    return hmac.new(key, str(expires).encode(), hashlib.sha256).hexdigest()


def _session_valid(token: str | None, password: str) -> bool:
    if not token or "." not in token:
        return False
    expires, signature = token.split(".", 1)
    if not expires.isdigit() or int(expires) < time.time():
        return False
    return hmac.compare_digest(signature, _signature(password, int(expires)))


def _basic_valid(header: str | None, password: str) -> bool:
    if not header or not header.lower().startswith("basic "):
        return False
    try:
        user, _, given = base64.b64decode(header[6:]).decode().partition(":")
    except (binascii.Error, UnicodeDecodeError):
        return False
    # Both compared, both in constant time: `and` would short-circuit and leak
    # through timing which half was wrong.
    return hmac.compare_digest(user, OPERATOR_USER) & hmac.compare_digest(given, password)


def require_operator(request: Request) -> None:
    password = _password()
    if _session_valid(request.cookies.get(COOKIE), password):
        return
    if _basic_valid(request.headers.get("authorization"), password):
        return
    # No WWW-Authenticate header, deliberately: that is what makes a browser
    # pop its own login dialog over a page it has already half-rendered.
    raise HTTPException(401, "operator login required")


class Login(BaseModel):
    password: str


@router.post("/login", status_code=204)
async def login(body: Login, response: Response) -> None:
    password = _password()
    if not hmac.compare_digest(body.password, password):
        # A fixed pause on failure: cheap, and it makes guessing slow.
        await asyncio.sleep(0.5)
        raise HTTPException(401, "wrong password")
    expires = int(time.time()) + SESSION_SECONDS
    response.set_cookie(
        COOKIE,
        f"{expires}.{_signature(password, expires)}",
        max_age=SESSION_SECONDS,
        path="/api",
        httponly=True,
        samesite="strict",
        # The demo is plain http on localhost. Behind TLS this must be True.
        secure=False,
    )


@router.post("/logout", status_code=204)
async def logout(response: Response) -> None:
    response.delete_cookie(COOKIE, path="/api", httponly=True, samesite="strict")


@router.get("/session", dependencies=[Depends(require_operator)])
async def session() -> dict[str, bool]:
    """Whether this browser is signed in — the page asks before rendering."""
    return {"operator": True}
