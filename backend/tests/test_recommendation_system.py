"""The recommendation system on its own: eligibility, the budget, the switch."""

from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from recommendation_system.main import app


@pytest.fixture
def client(monkeypatch):
    # The budget arithmetic below assumes €120, whatever the configured budget.
    from recommendation_system.settings import get_settings

    monkeypatch.setattr(get_settings(), "budget_eur", 120.0)
    c = TestClient(app)
    c.post("/_control/reset")
    yield c
    c.post("/_control/reset")


def offer_ids(client, contract="FP-2021-017350"):
    return [
        o["offer_id"] for o in client.get(f"/offers?contract_number={contract}").json()["offers"]
    ]


def accept(client, offer_id, contract="FP-2021-017350"):
    return client.post(
        f"/offers/{offer_id}/accept",
        json={"contract_number": contract, "conversation_id": str(uuid4())},
    )


def test_eligibility_follows_the_plan(client):
    assert set(offer_ids(client, "FP-2021-017350")) == {
        "loyalty-5-off",
        "upgrade-same-price",
        "downgrade-start",
    }  # Fibra 300
    assert "upgrade-same-price" not in offer_ids(client, "FP-2023-048812")  # already Fibra 1000
    assert offer_ids(client, "FP-2099-999999") == []  # unknown customer: nothing


def test_accepting_spends_the_offers_cost_once(client):
    conversation = str(uuid4())
    body = {"contract_number": "FP-2021-017350", "conversation_id": conversation}
    first = client.post("/offers/loyalty-5-off/accept", json=body).json()
    again = client.post("/offers/loyalty-5-off/accept", json=body).json()
    assert first["spent_eur"] == "60.00"
    assert again["spent_eur"] == "60.00", "the same acceptance is charged once"


def test_once_the_budget_is_spent_no_discount_is_offered(client):
    assert accept(client, "loyalty-5-off").status_code == 200  # 60 of 120
    assert accept(client, "upgrade-same-price").status_code == 200  # 120 of 120

    remaining = offer_ids(client)
    assert remaining == ["downgrade-start"], "only the offer that costs nothing"
    budget = client.get("/budget").json()
    assert budget["remaining_eur"] == "0.00"
    assert budget["discounts_available"] is False
    assert accept(client, "loyalty-5-off").status_code == 409, "no longer available"


def test_switched_down_it_fails_like_an_outage(client):
    client.post("/_control/mode", json={"mode": "down"})
    assert client.get("/offers?contract_number=FP-2021-017350").status_code == 503
    assert accept(client, "loyalty-5-off").status_code == 503
    assert client.get("/budget").status_code == 200, "the dashboard can still read it"
