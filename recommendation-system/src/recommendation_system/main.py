"""The recommendation system: which offers a customer may be made, within a budget.

A separate system from billing and the CRM, as it would be at an operator — it
owns the offer catalogue, its own view of which plan each customer is on, and
the discount budget. The agent never chooses an offer and never invents one:
it asks this system, presents what comes back, and reports acceptance here.

**The budget.** A discounted offer has a cost — the discount, over its
duration. The cost is committed when the customer *accepts*, not when an offer
is shown: from then on it is **held** against the budget, and when a colleague
confirms the offer it becomes **spent**. A declined offer releases its hold.
Once what remains cannot cover an offer's cost, that offer is no longer
returned; offers that cost nothing (a cheaper plan) always are. The analyst
decides the budget (RECOMMENDATIONS_BUDGET_EUR to start with, editable while
running); the dashboards show it.

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
    # Everything committed: held and confirmed together. What eligibility is
    # measured against, and what `remaining_eur` is the complement of.
    spent_eur: Decimal
    # Accepted by a customer, not yet decided by a colleague. Part of spent.
    held_eur: Decimal
    # Confirmed by a colleague: given away for good. Part of spent.
    confirmed_eur: Decimal
    remaining_eur: Decimal
    accepted: int
    confirmed: int
    discounts_available: bool


class BudgetChange(BaseModel):
    total_eur: Decimal


class Campaign(Offer):
    """An offer as the analyst sees it: the catalogue entry with what it has
    done so far. Every count is over the whole run of the system."""

    plans: list[str] | None
    accepted: int  # held: accepted by a customer, awaiting a colleague
    confirmed: int
    declined: int
    held_eur: Decimal
    spent_eur: Decimal


class _Ledger:
    """What has been committed, on which (conversation, offer), and whether a
    colleague has confirmed it — so accepting the same offer twice in one
    conversation is charged once, and confirming or releasing twice acts once.

    `total` is None until an analyst sets it; the configured budget applies."""

    def __init__(self) -> None:
        self.held: dict[tuple[str, str], Decimal] = {}
        self.confirmed: dict[tuple[str, str], Decimal] = {}
        self.declined: dict[str, int] = {}
        self.total: Decimal | None = None

    @property
    def held_total(self) -> Decimal:
        return sum(self.held.values(), Decimal("0"))

    @property
    def confirmed_total(self) -> Decimal:
        return sum(self.confirmed.values(), Decimal("0"))

    @property
    def spent(self) -> Decimal:
        return self.held_total + self.confirmed_total

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
    if _ledger.total is not None:
        return _ledger.total
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


def _campaign(entry: dict[str, Any]) -> Campaign:
    offer = _offer(entry)
    held = [c for (_, oid), c in _ledger.held.items() if oid == offer.offer_id]
    confirmed = [c for (_, oid), c in _ledger.confirmed.items() if oid == offer.offer_id]
    return Campaign(
        **offer.model_dump(),
        plans=entry.get("plans"),
        accepted=len(held),
        confirmed=len(confirmed),
        declined=_ledger.declined.get(offer.offer_id, 0),
        held_eur=_money(sum(held, Decimal("0"))),
        spent_eur=_money(sum(confirmed, Decimal("0"))),
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
    """Record the customer's acceptance and hold the offer's cost against the
    budget, until a colleague confirms (spent) or declines (released).

    Idempotent per (conversation, offer). 409 if the offer is no longer
    available — the budget ran out between showing and accepting, or it was
    never eligible — so the agent can say so instead of promising it.
    """
    _require_up()
    key = (str(request.conversation_id), offer_id)
    if key not in _ledger.held and key not in _ledger.confirmed:
        offer = next(
            (o for o in _eligible(request.contract_number) if o.offer_id == offer_id), None
        )
        if offer is None:
            raise HTTPException(409, "offer no longer available")
        _ledger.held[key] = offer.cost_eur
    return await budget()


class DecisionRequest(BaseModel):
    conversation_id: UUID


@app.post("/offers/{offer_id}/confirm", response_model=Budget)
async def confirm(offer_id: str, request: DecisionRequest) -> Budget:
    """A colleague confirmed an offer the customer had accepted: its hold
    becomes spend. Idempotent — confirming twice spends once. 409 if nothing
    is held for this (conversation, offer): there is nothing to confirm."""
    _require_up()
    key = (str(request.conversation_id), offer_id)
    if key in _ledger.confirmed:
        return await budget()
    cost = _ledger.held.pop(key, None)
    if cost is None:
        raise HTTPException(409, "no accepted offer to confirm")
    _ledger.confirmed[key] = cost
    return await budget()


@app.post("/offers/{offer_id}/release", response_model=Budget)
async def release(offer_id: str, request: DecisionRequest) -> Budget:
    """A colleague declined an offer the customer had accepted: its hold goes
    back to the budget. Idempotent — releasing twice refunds once."""
    _require_up()
    cost = _ledger.held.pop((str(request.conversation_id), offer_id), None)
    if cost is not None:
        _ledger.declined[offer_id] = _ledger.declined.get(offer_id, 0) + 1
    return await budget()


@app.get("/budget", response_model=Budget)
async def budget() -> Budget:
    """For the dashboards. Readable even when offers are switched off."""
    remaining = _remaining()
    cheapest = min(
        (c for c in (_offer(e).cost_eur for e in _catalogue()) if c > 0), default=Decimal("0")
    )
    return Budget(
        total_eur=_total(),
        spent_eur=_money(_ledger.spent),
        held_eur=_money(_ledger.held_total),
        confirmed_eur=_money(_ledger.confirmed_total),
        remaining_eur=remaining,
        accepted=len(_ledger.held),
        confirmed=len(_ledger.confirmed),
        discounts_available=cheapest > 0 and remaining >= cheapest,
    )


@app.put("/budget", response_model=Budget)
async def set_budget(request: BudgetChange) -> Budget:
    """The analyst's decision: what the operator is prepared to give away.

    Takes effect at once for the next offer shown. It may be set below what is
    already committed — the remaining budget is then zero and no discounted
    offer is made until holds are released — but never below zero. Reset
    (`make reset`) restores the configured value."""
    if request.total_eur < 0:
        raise HTTPException(422, "the budget cannot be negative")
    _ledger.total = _money(request.total_eur)
    return await budget()


@app.get("/campaigns", response_model=list[Campaign])
async def campaigns() -> list[Campaign]:
    """The catalogue with what each offer has done: for the analyst, and for
    the operator to price an accepted offer. Readable even when offers are
    switched off."""
    return [_campaign(entry) for entry in _catalogue()]


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
