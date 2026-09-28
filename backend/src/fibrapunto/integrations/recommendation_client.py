"""The recommendation system's client (recommendation-system/).

Never raises. Offers are an extra, never a dependency: a cancellation must be
registered whether or not this system answers, so every failure — down, slow,
malformed — comes back as "no offers" / "not available" and the conversation
carries on exactly as it would without offers.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any
from uuid import UUID

import httpx
import structlog
from pydantic import BaseModel

from fibrapunto.settings import get_settings

log = structlog.get_logger(__name__)


class RetentionOffer(BaseModel):
    offer_id: str
    title: str
    description: str
    monthly_discount_eur: Decimal
    duration_months: int
    cost_eur: Decimal


class RecommendationClient:
    def __init__(self, base_url: str | None = None, timeout_s: float = 3.0) -> None:
        self._base = (base_url or get_settings().recommendation_base_url).rstrip("/")
        self._timeout = timeout_s

    async def best_offer(self, contract_number: str) -> RetentionOffer | None:
        """The first eligible offer within budget, or None — including when the
        system cannot be reached."""
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.get(
                    f"{self._base}/offers", params={"contract_number": contract_number}
                )
                response.raise_for_status()
                offers = [RetentionOffer(**o) for o in response.json()["offers"]]
        except Exception as exc:  # noqa: BLE001 — offers are never a dependency
            log.warning("recommendations.unavailable", error=str(exc))
            return None
        return offers[0] if offers else None

    async def release(self, offer_id: str, conversation_id: UUID) -> bool:
        """Return a declined offer's hold to the budget. False if the system
        cannot be reached — the operator is told, the decision still stands."""
        return await self._decide("release", offer_id, conversation_id)

    async def confirm(self, offer_id: str, conversation_id: UUID) -> bool:
        """Turn a confirmed offer's hold into spend. False if the system cannot
        be reached — the operator is told, the decision still stands."""
        return await self._decide("confirm", offer_id, conversation_id)

    async def _decide(self, action: str, offer_id: str, conversation_id: UUID) -> bool:
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(
                    f"{self._base}/offers/{offer_id}/{action}",
                    json={"conversation_id": str(conversation_id)},
                )
        except Exception as exc:  # noqa: BLE001
            log.warning(f"recommendations.{action}_failed", error=str(exc))
            return False
        return response.status_code == 200

    # --- for the analyst and operator views ---------------------------------

    async def budget(self) -> dict[str, Any] | None:
        """The budget as the system holds it, or None when it cannot be reached."""
        return await self._get("/budget")

    async def campaigns(self) -> list[dict[str, Any]] | None:
        """The catalogue with what each offer has done, or None."""
        return await self._get("/campaigns")

    async def set_budget(self, total_eur: Decimal) -> dict[str, Any] | None:
        """The analyst's new total. None when the system cannot be reached or
        refuses the value; the caller says so rather than pretending."""
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.put(
                    f"{self._base}/budget", json={"total_eur": str(total_eur)}
                )
                response.raise_for_status()
                return response.json()
        except Exception as exc:  # noqa: BLE001
            log.warning("recommendations.set_budget_failed", error=str(exc))
            return None

    async def _get(self, path: str) -> Any | None:
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.get(f"{self._base}{path}")
                response.raise_for_status()
                return response.json()
        except Exception as exc:  # noqa: BLE001
            log.warning("recommendations.unavailable", path=path, error=str(exc))
            return None

    async def accept(
        self, offer: RetentionOffer, contract_number: str, conversation_id: UUID
    ) -> bool:
        """Record the acceptance and spend the budget. False if the offer is no
        longer available (budget gone) or the system cannot be reached — the
        agent then says so rather than promising it."""
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(
                    f"{self._base}/offers/{offer.offer_id}/accept",
                    json={
                        "contract_number": contract_number,
                        "conversation_id": str(conversation_id),
                    },
                )
        except Exception as exc:  # noqa: BLE001
            log.warning("recommendations.accept_failed", error=str(exc))
            return False
        if response.status_code != 200:
            log.info("recommendations.offer_unavailable", status=response.status_code)
            return False
        return True
