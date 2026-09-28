"""The operator view reads what the agent may not (§8), so it is gated.

Without this, the perimeter held for the agent's role and leaked one layer up:
/api/operator/records/<ref> returned customer email and phone to anyone with the
chat's URL, and case references are sequential, so every case was one
increment away.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from fibrapunto.api import analyst, auth, operator
from pydantic import SecretStr

PASSWORD = "correct-horse"


def _settings(**passwords: str) -> SimpleNamespace:
    return SimpleNamespace(
        operator_password=SecretStr(passwords.get("operator", "")),
        developer_password=SecretStr(passwords.get("developer", "")),
        analyst_password=SecretStr(passwords.get("analyst", "")),
    )


def _client(monkeypatch, password: str, **others: str) -> TestClient:
    monkeypatch.setattr(auth, "get_settings", lambda: _settings(operator=password, **others))
    # No pool: a request that gets past the gate answers 503, which is how the
    # test tells "authorised" from "refused" without a database.
    operator.set_pool(None)
    analyst.set_pool(None)
    app = FastAPI()
    for role_router in auth.routers.values():
        app.include_router(role_router)
    app.include_router(operator.router)
    app.include_router(analyst.router)
    return TestClient(app)


@pytest.mark.parametrize("path", ["/api/operator/records", "/api/operator/records/FP-000001"])
def test_no_credentials_is_refused(monkeypatch, path):
    response = _client(monkeypatch, PASSWORD).get(path)
    assert response.status_code == 401


def test_the_refusal_never_triggers_the_browser_prompt(monkeypatch):
    # A WWW-Authenticate header makes the browser pop its own dialog over the
    # page. The login is a form; the server must never ask for Basic.
    response = _client(monkeypatch, PASSWORD).get("/api/operator/session")
    assert response.status_code == 401
    assert "www-authenticate" not in response.headers


def test_login_sets_an_httponly_strict_cookie_that_opens_the_view(monkeypatch):
    client = _client(monkeypatch, PASSWORD)
    response = client.post("/api/operator/login", json={"password": PASSWORD})
    assert response.status_code == 204
    cookie = response.headers["set-cookie"].lower()
    assert "httponly" in cookie
    assert "samesite=strict" in cookie

    assert client.get("/api/operator/session").status_code == 200
    # Past the gate, stopped only by the missing database.
    records = client.get("/api/operator/records")
    assert records.status_code == 503
    assert "no database connection" in records.json()["detail"]


def test_the_wrong_password_sets_no_cookie(monkeypatch):
    client = _client(monkeypatch, PASSWORD)
    response = client.post("/api/operator/login", json={"password": "guess"})
    assert response.status_code == 401
    assert "set-cookie" not in response.headers
    assert client.get("/api/operator/session").status_code == 401


def test_a_forged_or_expired_cookie_is_refused(monkeypatch):
    client = _client(monkeypatch, PASSWORD)
    client.cookies.set(auth.COOKIE, "9999999999.not-a-signature", path="/api")
    assert client.get("/api/operator/session").status_code == 401
    client.cookies.set(auth.COOKIE, f"1.{auth._signature(PASSWORD, 1)}", path="/api")  # noqa: SLF001
    assert client.get("/api/operator/session").status_code == 401


def test_changing_the_password_signs_existing_sessions_out(monkeypatch):
    client = _client(monkeypatch, PASSWORD)
    client.post("/api/operator/login", json={"password": PASSWORD})
    monkeypatch.setattr(auth, "get_settings", lambda: _settings(operator="rotated"))
    assert client.get("/api/operator/session").status_code == 401


def test_logout_ends_the_session(monkeypatch):
    client = _client(monkeypatch, PASSWORD)
    client.post("/api/operator/login", json={"password": PASSWORD})
    client.post("/api/operator/logout")
    assert client.get("/api/operator/session").status_code == 401


def test_scripts_may_send_basic_auth(monkeypatch):
    # The eval harness has no browser and no form.
    client = _client(monkeypatch, PASSWORD)
    assert client.get("/api/operator/session", auth=("operator", PASSWORD)).status_code == 200
    assert client.get("/api/operator/session", auth=("operator", "guess")).status_code == 401


def test_an_unset_password_closes_the_view_rather_than_opening_it(monkeypatch):
    client = _client(monkeypatch, "")
    assert client.post("/api/operator/login", json={"password": ""}).status_code == 503
    response = client.get("/api/operator/records", auth=("operator", ""))
    assert response.status_code == 503
    assert "OPERATOR_PASSWORD" in response.json()["detail"]


# --- three roles, three gates --------------------------------------------------


def test_each_role_has_its_own_gate_and_cookie(monkeypatch):
    client = _client(monkeypatch, PASSWORD, developer="dev-pass", analyst="analyst-pass")
    for role in ("developer", "analyst"):
        assert client.get(f"/api/{role}/session").status_code == 401

    # An operator's session opens the operator view and nothing else.
    client.post("/api/operator/login", json={"password": PASSWORD})
    assert client.get("/api/operator/session").status_code == 200
    assert client.get("/api/developer/session").status_code == 401
    assert client.get("/api/analyst/session").status_code == 401
    assert client.get("/api/analyst/overview").status_code == 401

    # The analyst's password does not open the operator view either.
    client.post("/api/analyst/login", json={"password": "analyst-pass"})
    assert client.get("/api/analyst/session").status_code == 200
    # Past the analyst gate, stopped only by the missing database.
    overview = client.get("/api/analyst/overview")
    assert overview.status_code == 503
    assert "no database connection" in overview.json()["detail"]

    client.post("/api/analyst/logout")
    assert client.get("/api/analyst/session").status_code == 401
    assert client.get("/api/operator/session").status_code == 200, "the other session stays"


def test_the_same_password_on_two_roles_still_gives_distinct_cookies(monkeypatch):
    # The demo sets one password for all three. The cookie is keyed on the role
    # as well, so the operator's cookie is not a developer's cookie.
    client = _client(monkeypatch, PASSWORD, developer=PASSWORD)
    client.post("/api/operator/login", json={"password": PASSWORD})
    assert client.get("/api/developer/session").status_code == 401
    forged = client.cookies.get(auth.cookie_name("operator"))
    client.cookies.set(auth.cookie_name("developer"), forged, path="/api")
    assert client.get("/api/developer/session").status_code == 401


def test_basic_auth_names_the_role(monkeypatch):
    client = _client(monkeypatch, PASSWORD, analyst="analyst-pass")
    assert client.get("/api/analyst/session", auth=("analyst", "analyst-pass")).status_code == 200
    assert client.get("/api/analyst/session", auth=("operator", "analyst-pass")).status_code == 401


def test_an_unset_role_password_closes_that_view_only(monkeypatch):
    client = _client(monkeypatch, PASSWORD)  # developer and analyst unset
    response = client.get("/api/analyst/overview", auth=("analyst", ""))
    assert response.status_code == 503
    assert "ANALYST_PASSWORD" in response.json()["detail"]
    assert client.post("/api/developer/login", json={"password": ""}).status_code == 503
    assert client.get("/api/operator/session", auth=("operator", PASSWORD)).status_code == 200
