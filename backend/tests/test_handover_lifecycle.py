"""A case is done when a person has dealt with it, not when it is well formed.

`crm.v_handover_completeness` says whether the record carries every §13 field;
`status` says whether the operator has acted. A refund request is complete the
moment the agent opens it and is not done until someone in billing operations
closes it. These tests pin the rules both writers of `status` share.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from fibrapunto.api import auth, operator
from fibrapunto.handover import lifecycle
from pydantic import SecretStr


@pytest.mark.parametrize(
    ("current", "target"),
    [
        ("open", "in_progress"),
        ("open", "closed"),
        ("in_progress", "closed"),
        ("in_progress", "open"),
        ("closed", "in_progress"),
    ],
)
def test_every_operator_move_is_allowed(current, target):
    assert lifecycle.check(current, target, {}) is True


def test_the_same_state_is_a_harmless_no_op():
    assert lifecycle.check("closed", "closed", {}) is False


def test_a_closed_case_does_not_go_back_to_the_untouched_queue():
    with pytest.raises(lifecycle.TransitionRefused, match="closed to open"):
        lifecycle.check("closed", "open", {})


def test_an_unknown_status_is_refused():
    with pytest.raises(lifecycle.TransitionRefused, match="unknown"):
        lifecycle.check("open", "done", {})


def test_a_case_with_an_undecided_offer_cannot_be_closed_around_it():
    """The accept/decline decision writes the status too. Closing by hand
    while the customer waits for that decision would leave them waiting for
    an answer nobody is going to give."""
    context = {"retention_offer": {"status": "accepted", "title": "Loyalty €5 off"}}
    with pytest.raises(lifecycle.TransitionRefused, match="confirm or decline"):
        lifecycle.check("in_progress", "closed", context)
    # Taking it is fine — that is how the decision gets made.
    assert lifecycle.check("open", "in_progress", context) is True
    # And once decided, closing is fine.
    context["retention_offer"]["status"] = "confirmed"
    assert lifecycle.check("in_progress", "closed", context) is True


def test_the_offer_decision_uses_the_same_vocabulary():
    assert lifecycle.after_offer_decision(confirmed=True) == "closed"
    assert lifecycle.after_offer_decision(confirmed=False) == "in_progress"
    assert set(lifecycle.STATUSES) >= {
        lifecycle.after_offer_decision(True),
        lifecycle.after_offer_decision(False),
    }


# --- the endpoint sits behind the operator gate like the rest of the view ----

PASSWORD = "correct-horse"


def _client(monkeypatch) -> TestClient:
    monkeypatch.setattr(
        auth, "get_settings", lambda: SimpleNamespace(operator_password=SecretStr(PASSWORD))
    )
    operator.set_pool(None)
    app = FastAPI()
    app.include_router(auth.router)
    app.include_router(operator.router)
    return TestClient(app)


def test_changing_a_status_needs_operator_credentials(monkeypatch):
    client = _client(monkeypatch)
    response = client.patch("/api/operator/records/00001042/status", json={"status": "closed"})
    assert response.status_code == 401


def test_past_the_gate_only_the_database_stops_it(monkeypatch):
    client = _client(monkeypatch)
    assert client.post("/api/operator/login", json={"password": PASSWORD}).status_code == 204
    response = client.patch("/api/operator/records/00001042/status", json={"status": "closed"})
    assert response.status_code == 503


def test_a_status_outside_the_enum_never_reaches_the_database(monkeypatch):
    client = _client(monkeypatch)
    client.post("/api/operator/login", json={"password": PASSWORD})
    response = client.patch("/api/operator/records/00001042/status", json={"status": "done"})
    assert response.status_code == 422
