"""Who may open the operator view, the developer console, the analyst view and
the admin routes.

Three roles, three gates, on the same origin as the customer's chat:

- **operator** reads as platform_admin: customer email and phone, internal
  comments, every case by its sequential reference. Served with no check, that
  is the whole perimeter of §8 undone one layer up — the agent's role cannot
  read `customer.email`, and anyone with the chat URL could.
- **developer** sees the system trace, the live failure switches and the
  session metrics — nothing a customer should be able to reach, and the
  switches act on every conversation at once.
- **analyst** reads the aggregate metrics and sets the offers budget, which
  decides what the assistant may give away.

This is a demo gate, not an identity system: one shared password per role,
exchanged at a login form for a signed session cookie. Each role has its own
cookie, so an operator's session opens nothing else. The cookie is HttpOnly, so
page scripts never hold it, and SameSite=Strict, so another site cannot ride on
it. Scripts (the eval harness, curl) may send the same password as HTTP Basic
with the role as the username; the server never *asks* for Basic, so a browser
never shows its prompt. In production each of these is a separate service
behind SSO, and none of this exists.

Fails closed. No password configured for a role means every request to it is
refused, not that the check is skipped — an unset variable must never be the
thing that opens the door.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import hmac
import time
from collections.abc import Callable
from typing import Literal, get_args

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel

from fibrapunto.settings import get_settings

Role = Literal["operator", "developer", "analyst"]
ROLES: tuple[Role, ...] = get_args(Role)

SESSION_SECONDS = 8 * 3600

# The environment variable each role's password comes from, named in the
# refusal so an unset one is diagnosed from the browser, not from the logs.
_VARIABLE: dict[Role, str] = {
    "operator": "OPERATOR_PASSWORD",
    "developer": "DEVELOPER_PASSWORD",
    "analyst": "ANALYST_PASSWORD",
}


def cookie_name(role: Role) -> str:
    return f"fibrapunto_{role}"


def _password(role: Role) -> str:
    settings = get_settings()
    password = getattr(settings, f"{role}_password").get_secret_value()
    if not password:
        raise HTTPException(503, f"{role} access is disabled: {_VARIABLE[role]} is not set")
    return password


def _signature(password: str, expires: int, role: Role = "operator") -> str:
    # Keyed on the password: changing it signs every existing session out. The
    # role is in the key too, so two roles sharing a password still get
    # cookies that do not open each other's view.
    key = hashlib.sha256(f"fibrapunto-{role}-session\0{password}".encode()).digest()
    return hmac.new(key, str(expires).encode(), hashlib.sha256).hexdigest()


def _session_valid(token: str | None, password: str, role: Role) -> bool:
    if not token or "." not in token:
        return False
    expires, signature = token.split(".", 1)
    if not expires.isdigit() or int(expires) < time.time():
        return False
    return hmac.compare_digest(signature, _signature(password, int(expires), role))


def _basic_valid(header: str | None, password: str, role: Role) -> bool:
    if not header or not header.lower().startswith("basic "):
        return False
    try:
        user, _, given = base64.b64decode(header[6:]).decode().partition(":")
    except (binascii.Error, UnicodeDecodeError):
        return False
    # Both compared, both in constant time: `and` would short-circuit and leak
    # through timing which half was wrong.
    return hmac.compare_digest(user, role) & hmac.compare_digest(given, password)


def require_role(role: Role) -> Callable[[Request], None]:
    """A FastAPI dependency that admits only this role's session or Basic."""

    def check(request: Request) -> None:
        password = _password(role)
        if _session_valid(request.cookies.get(cookie_name(role)), password, role):
            return
        if _basic_valid(request.headers.get("authorization"), password, role):
            return
        # No WWW-Authenticate header, deliberately: that is what makes a browser
        # pop its own login dialog over a page it has already half-rendered.
        raise HTTPException(401, f"{role} login required")

    check.__name__ = f"require_{role}"
    return check


require_operator = require_role("operator")
require_developer = require_role("developer")
require_analyst = require_role("analyst")


class Login(BaseModel):
    password: str


def _router(role: Role) -> APIRouter:
    router = APIRouter(prefix=f"/api/{role}", tags=[role])
    cookie = cookie_name(role)
    gate = require_role(role)

    @router.post("/login", status_code=204)
    async def login(body: Login, response: Response) -> None:
        password = _password(role)
        if not hmac.compare_digest(body.password, password):
            # A fixed pause on failure: cheap, and it makes guessing slow.
            await asyncio.sleep(0.5)
            raise HTTPException(401, "wrong password")
        expires = int(time.time()) + SESSION_SECONDS
        response.set_cookie(
            cookie,
            f"{expires}.{_signature(password, expires, role)}",
            max_age=SESSION_SECONDS,
            path="/api",
            httponly=True,
            samesite="strict",
            # The demo is plain http on localhost. Behind TLS this must be True.
            secure=False,
        )

    @router.post("/logout", status_code=204)
    async def logout(response: Response) -> None:
        response.delete_cookie(cookie, path="/api", httponly=True, samesite="strict")

    @router.get("/session")
    async def session(request: Request) -> dict[str, bool]:
        """Whether this browser is signed in — the page asks before rendering."""
        gate(request)
        return {role: True}

    return router


routers: dict[Role, APIRouter] = {role: _router(role) for role in ROLES}

# The operator's, by its historical name: app.py and the tests import it.
router = routers["operator"]
COOKIE = cookie_name("operator")
OPERATOR_USER = "operator"
