"""The recommendation system: which offers a customer may be made, within a budget.

A separate system from billing and the CRM, as it would be at an operator — it
owns the offer catalogue, its own view of which plan each customer is on, and
the discount budget. The agent never chooses an offer and never invents one:
it asks this system, presents what comes back, and reports acceptance here.

**The budget.** A discounted offer has a cost — the discount, over its
duration. The cost is spent when the customer *accepts*, not when an offer is
shown. Once what remains cannot cover an offer's cost, that offer is no longer
returned; offers that cost nothing (a cheaper plan) always are. The operator
decides the budget (RECOMMENDATIONS_BUDGET_EUR); the dashboard shows it.

In-memory, like the billing stub: a restart restores the full budget.
"""

from __future__ import annotations

import time
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal
from uuid import UUID

import yaml
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from recommendation_system.settings import get_settings

FIXTURES = Path(__file__).parent / "fixtures"

app = FastAPI(title="Fibrapunto — recommendation system", version="1.0.0")


class Offer(BaseModel):
    offer_id: str
    title: str
    description: str
    monthly_discount_eur: Decimal
    duration_months: int
    cost_eur: Decimal


class OfferList(BaseModel):
    offers: list[Offer]
    budget_remaining_eur: Decimal


class AcceptRequest(BaseModel):
    contract_number: str
    conversation_id: UUID


class Budget(BaseModel):
    total_eur: Decimal
    spent_eur: Decimal
    remaining_eur: Decimal
    accepted: int
    discounts_available: bool


class _Ledger:
    """What has been spent, and on which (conversation, offer) — so accepting
    the same offer twice in one conversation is charged once."""

    def __init__(self) -> None:
        self.spent = Decimal("0")
        self.accepted: dict[tuple[str, str], Decimal] = {}

    def reset(self) -> None:
        self.__init__()


_ledger = _Ledger()
_mode: dict[str, Any] = {"mode": "up", "since": time.monotonic()}


def _catalogue() -> list[dict[str, Any]]:
    return yaml.safe_load((FIXTURES / "catalogue.yaml").read_text(encoding="utf-8"))["offers"]


def _customers() -> dict[str, dict[str, Any]]:
    return yaml.safe_load((FIXTURES / "customers.yaml").read_text(encoding="utf-8"))["customers"]


CENT = Decimal("0.01")


def _money(value: Decimal) -> Decimal:
    return value.quantize(CENT)


def _total() -> Decimal:
    return _money(Decimal(str(get_settings().budget_eur)))


def _remaining() -> Decimal:
    return _money(max(Decimal("0"), _total() - _ledger.spent))


def _offer(entry: dict[str, Any]) -> Offer:
    discount = Decimal(str(entry["monthly_discount_eur"]))
    return Offer(
        offer_id=entry["offer_id"],
        title=entry["title"],
        description=entry["description"],
        monthly_discount_eur=discount,
        duration_months=entry["duration_months"],
        cost_eur=_money(discount * entry["duration_months"]),
    )


def _eligible(contract_number: str) -> list[Offer]:
    customer = _customers().get(contract_number)
    if customer is None:
        return []
    remaining = _remaining()
    offers = []
    for entry in _catalogue():
        plans = entry.get("plans")
        if plans and customer["plan"] not in plans:
            continue
        offer = _offer(entry)
        # The budget rule: a discount the remaining budget cannot cover is not
        # offered. Free offers are unaffected — they spend nothing.
        if offer.cost_eur > 0 and offer.cost_eur > remaining:
            continue
        offers.append(offer)
    return offers


def _require_up() -> None:
    if _mode["mode"] == "down":
        raise HTTPException(503, "recommendation system unavailable")


@app.get("/offers", response_model=OfferList)
async def offers(contract_number: str) -> OfferList:
    """The offers this customer may be made right now, best first."""
    _require_up()
    return OfferList(offers=_eligible(contract_number), budget_remaining_eur=_remaining())


@app.post("/offers/{offer_id}/accept", response_model=Budget)
async def accept(offer_id: str, request: AcceptRequest) -> Budget:
    """Record the customer's acceptance and spend the offer's cost.

    Idempotent per (conversation, offer). 409 if the offer is no longer
    available — the budget ran out between showing and accepting, or it was
    never eligible — so the agent can say so instead of promising it.
    """
    _require_up()
    key = (str(request.conversation_id), offer_id)
    if key not in _ledger.accepted:
        offer = next((o for o in _eligible(request.contract_number) if o.offer_id == offer_id), None)
        if offer is None:
            raise HTTPException(409, "offer no longer available")
        _ledger.accepted[key] = offer.cost_eur
        _ledger.spent += offer.cost_eur
    return await budget()


@app.get("/budget", response_model=Budget)
async def budget() -> Budget:
    """For the dashboard. Readable even when offers are switched off."""
    remaining = _remaining()
    cheapest = min(
        (c for c in (_offer(e).cost_eur for e in _catalogue()) if c > 0), default=Decimal("0")
    )
    return Budget(
        total_eur=_total(),
        spent_eur=_money(_ledger.spent),
        remaining_eur=remaining,
        accepted=len(_ledger.accepted),
        discounts_available=cheapest > 0 and remaining >= cheapest,
    )


# --- control surface: not part of the service's contract -----------------------


class ModeRequest(BaseModel):
    mode: Literal["up", "down"]


@app.get("/_control/mode", include_in_schema=False)
async def get_mode() -> dict[str, str]:
    return {"mode": _mode["mode"]}


@app.post("/_control/mode", include_in_schema=False)
async def set_mode(request: ModeRequest) -> dict[str, str]:
    """The dashboard's live switch: sticky until moved back."""
    _mode.update(mode=request.mode, since=time.monotonic())
    return {"mode": request.mode}


@app.post("/_control/reset", include_in_schema=False)
async def reset() -> dict[str, Any]:
    """Restore the full budget and switch the service back up (`make reset`)."""
    _ledger.reset()
    _mode.update(mode="up", since=time.monotonic())
    return {"budget_eur": str(_total())}


@app.get("/_control/health", include_in_schema=False)
async def health() -> dict[str, str]:
    return {"status": "ok"}
