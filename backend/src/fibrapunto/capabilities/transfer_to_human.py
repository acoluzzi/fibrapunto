"""Transfer to a human — the third and last capability (§5, §12.4).

§5 gives it no inputs and two outcomes. The second one is the interesting half:

    "**No human is available** — out of hours, or the queue is saturated. The
    agent must not promise a response time the company cannot keep. It states
    that the request is recorded with today's date, gives the reference, states
    the actual service window, and stops. A promise of 'someone will call you
    shortly' made at 23:40 on a Saturday is a worse outcome than an honest
    'nobody is available until Monday morning'."

So the service window is a fact the capability returns, not a sentence the model
composes. A model asked to be reassuring at 23:40 will be reassuring.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal
from zoneinfo import ZoneInfo

import structlog
from pydantic import BaseModel, Field

from fibrapunto.capabilities.base import CustomerVisible, InternalOnly
from fibrapunto.capabilities.runtime import typed_capability

log = structlog.get_logger(__name__)

CAPABILITY_NAME = "transfer_to_human"

DESCRIPTION = (
    "Hand the conversation to a human colleague. Use it when the customer asks "
    "explicitly, or when the request is outside what you can help with. Do not "
    "ask the customer why they want a person and do not try to solve it first."
)

ROME = ZoneInfo("Europe/Rome")

# The operator's hours. §16 calls the integration timeout "a product decision
# dressed as a configuration value"; this is the same, and more visibly so —
# it decides what the company promises a customer at 23:40 on a Saturday.
SERVICE_DAYS = range(0, 5)  # Monday..Friday
SERVICE_OPEN_HOUR = 8
SERVICE_CLOSE_HOUR = 20


class TransferAccepted(BaseModel):
    outcome: Literal["transferred"] = "transferred"
    wait_estimate: str = CustomerVisible("Estimated wait before reaching a colleague.")


class NoAgentAvailable(BaseModel):
    """§12.4's first failure case. Honest, not reassuring."""

    outcome: Literal["noAgentAvailable"] = "noAgentAvailable"
    service_window: str = CustomerVisible(
        "When the service is actually open. State it exactly as it is: "
        "do NOT say 'shortly' or 'as soon as possible'."
    )
    guidance: str = InternalOnly(
        "Operating instruction for the reasoning layer.",
        default=(
            "There is no colleague available right now. Do NOT promise that "
            "someone will call back shortly. Say when the service reopens, "
            "remind the customer of the case number if one exists, and stop "
            "there. A promise the company cannot keep is worse than an honest "
            "'Monday morning'."
        ),
    )


TransferResult = Annotated[TransferAccepted | NoAgentAvailable, Field(discriminator="outcome")]

TRANSFER_OUTCOMES: frozenset[str] = frozenset({"transferred", "noAgentAvailable"})


def within_service_hours(now: datetime | None = None) -> bool:
    now = now or datetime.now(ROME)
    return now.weekday() in SERVICE_DAYS and SERVICE_OPEN_HOUR <= now.hour < SERVICE_CLOSE_HOUR


def service_window_text() -> str:
    return f"Monday to Friday, {SERVICE_OPEN_HOUR}:00 to {SERVICE_CLOSE_HOUR}:00"


@typed_capability(
    CAPABILITY_NAME,
    on_unexpected=lambda incident_id: NoAgentAvailable(service_window=service_window_text()),
)
async def transfer_to_human(*, now: datetime | None = None) -> TransferResult:
    """Hand the conversation to a person, if there is one."""
    if not within_service_hours(now):
        log.info("transfer.outside_service_hours")
        return NoAgentAvailable(service_window=service_window_text())

    return TransferAccepted(wait_estimate="a few minutes")
