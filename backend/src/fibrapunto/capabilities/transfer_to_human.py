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
    "Passa la conversazione a un operatore. Usala quando il cliente lo chiede "
    "esplicitamente, oppure quando la richiesta è fuori da ciò di cui puoi "
    "occuparti. Non chiedere al cliente perché vuole un operatore e non provare "
    "a risolvere prima."
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
    wait_estimate: str = CustomerVisible("Attesa stimata prima di parlare con l'operatore.")


class NoAgentAvailable(BaseModel):
    """§12.4's first failure case. Honest, not reassuring."""

    outcome: Literal["noAgentAvailable"] = "noAgentAvailable"
    service_window: str = CustomerVisible(
        "Quando il servizio è realmente disponibile. Comunicalo così com'è: "
        "NON dire 'a breve' o 'appena possibile'."
    )
    guidance: str = InternalOnly(
        "Istruzione operativa per il livello di ragionamento.",
        default=(
            "Adesso non c'è nessun operatore disponibile. NON promettere che "
            "qualcuno richiamerà a breve. Di' quando il servizio riapre, "
            "ricorda al cliente il numero di pratica se ne esiste uno, e "
            "fermati lì. Una promessa che l'azienda non può mantenere è "
            "peggio di un 'lunedì mattina' onesto."
        ),
    )


TransferResult = Annotated[TransferAccepted | NoAgentAvailable, Field(discriminator="outcome")]

TRANSFER_OUTCOMES: frozenset[str] = frozenset({"transferred", "noAgentAvailable"})


def within_service_hours(now: datetime | None = None) -> bool:
    now = now or datetime.now(ROME)
    return now.weekday() in SERVICE_DAYS and SERVICE_OPEN_HOUR <= now.hour < SERVICE_CLOSE_HOUR


def service_window_text() -> str:
    return f"dal lunedì al venerdì, dalle {SERVICE_OPEN_HOUR} alle {SERVICE_CLOSE_HOUR}"


@typed_capability(
    CAPABILITY_NAME,
    on_unexpected=lambda incident_id: NoAgentAvailable(service_window=service_window_text()),
)
async def transfer_to_human(*, now: datetime | None = None) -> TransferResult:
    """Hand the conversation to a person, if there is one."""
    if not within_service_hours(now):
        log.info("transfer.outside_service_hours")
        return NoAgentAvailable(service_window=service_window_text())

    return TransferAccepted(wait_estimate="pochi minuti")
