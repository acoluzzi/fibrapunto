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
    # Committed, but a colleague has not decided: held, not confirmed.
    assert again["held_eur"] == "60.00"
    assert again["confirmed_eur"] == "0.00"
    assert again["remaining_eur"] == "60.00"


def test_a_colleague_settles_the_hold(client):
    conversation = str(uuid4())
    decision = {"conversation_id": conversation}
    client.post(
        "/offers/loyalty-5-off/accept",
        json={"contract_number": "FP-2021-017350", "conversation_id": conversation},
    )
    confirmed = client.post("/offers/loyalty-5-off/confirm", json=decision).json()
    assert (confirmed["held_eur"], confirmed["confirmed_eur"]) == ("0.00", "60.00")
    assert confirmed["spent_eur"] == "60.00", "still committed: confirming moves, never adds"
    assert (confirmed["accepted"], confirmed["confirmed"]) == (0, 1)
    again = client.post("/offers/loyalty-5-off/confirm", json=decision).json()
    assert again["confirmed_eur"] == "60.00", "confirming twice spends once"
    # Once confirmed, a release does nothing: the money is gone.
    released = client.post("/offers/loyalty-5-off/release", json=decision).json()
    assert released["confirmed_eur"] == "60.00"

    # Nothing held on this conversation: nothing to confirm.
    other = {"conversation_id": str(uuid4())}
    assert client.post("/offers/loyalty-5-off/confirm", json=other).status_code == 409


def test_declining_releases_the_hold(client):
    conversation = str(uuid4())
    client.post(
        "/offers/loyalty-5-off/accept",
        json={"contract_number": "FP-2021-017350", "conversation_id": conversation},
    )
    released = client.post(
        "/offers/loyalty-5-off/release", json={"conversation_id": conversation}
    ).json()
    assert released["spent_eur"] == "0.00"
    assert released["remaining_eur"] == "120.00"
    campaign = next(c for c in client.get("/campaigns").json() if c["offer_id"] == "loyalty-5-off")
    assert (campaign["accepted"], campaign["confirmed"], campaign["declined"]) == (0, 0, 1)


def test_campaigns_carry_the_catalogue_and_the_tally(client):
    conversation = str(uuid4())
    client.post(
        "/offers/loyalty-5-off/accept",
        json={"contract_number": "FP-2021-017350", "conversation_id": conversation},
    )
    by_id = {c["offer_id"]: c for c in client.get("/campaigns").json()}
    assert set(by_id) == {"loyalty-5-off", "upgrade-same-price", "downgrade-start"}
    loyalty = by_id["loyalty-5-off"]
    assert loyalty["cost_eur"] == "60.00"
    assert loyalty["plans"] is None, "any plan"
    assert (loyalty["accepted"], loyalty["held_eur"], loyalty["spent_eur"]) == (1, "60.00", "0.00")
    assert by_id["upgrade-same-price"]["plans"] == ["Fibra 300"]
    assert by_id["downgrade-start"]["cost_eur"] == "0.00"


def test_the_analyst_can_change_the_budget(client):
    accept(client, "loyalty-5-off")  # 60 held of 120
    changed = client.put("/budget", json={"total_eur": "200"}).json()
    assert (changed["total_eur"], changed["remaining_eur"]) == ("200.00", "140.00")
    assert "loyalty-5-off" in offer_ids(client), "applies to the next offer shown"

    # Below what is already committed: nothing discounted until holds go back.
    squeezed = client.put("/budget", json={"total_eur": "50"}).json()
    assert squeezed["remaining_eur"] == "0.00"
    assert squeezed["spent_eur"] == "60.00", "what was committed stays counted"
    assert offer_ids(client) == ["downgrade-start"]

    assert client.put("/budget", json={"total_eur": "-1"}).status_code == 422
    # Reset restores the configured budget.
    client.post("/_control/reset")
    assert client.get("/budget").json()["total_eur"] == "120.00"


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
