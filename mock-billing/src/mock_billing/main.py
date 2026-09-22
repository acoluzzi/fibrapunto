"""The legacy billing system (§7).

It behaves like an old system: it is slow, it is not always consistent with the
CRM, and it can be made to fail on command.

Two surfaces, kept strictly apart:

  * `/billing/...` and `/line/...` — the legacy system's real contract. These
    appear in contracts/billing-openapi.v1.yaml and the integration layer
    registers against them.

  * `/_control/...` — the test harness. `include_in_schema=False`, because a
    real legacy system does not offer a button that makes it fail, and shipping
    one in the published contract would be a lie about the thing being
    integrated with.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Any

import yaml
from fastapi import FastAPI, Header, HTTPException, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from mock_billing.models import (
    BillingError,
    BillingStatus,
    ContractMatch,
    MultipleMatches,
)
from mock_billing.settings import get_settings
from mock_billing.test_modes import TestMode, apply_latency, global_override, resolve_mode

FIXTURES = Path(__file__).parent / "fixtures" / "billing.yaml"

app = FastAPI(
    title="Fibrapunto — sistema di fatturazione (legacy)",
    version="1.0.0",
    description=(
        "Interfaccia del sistema di fatturazione legacy di Fibrapunto.\n\n"
        "È il sistema di riferimento per i pagamenti; l'anagrafica del "
        "contratto vive nel CRM ed è un sistema diverso. I due possono non "
        "essere allineati: un contratto presente nel CRM può non esistere qui."
    ),
)

_data: dict[str, Any] = {}


def _load() -> None:
    _data.clear()
    _data.update(yaml.safe_load(FIXTURES.read_text(encoding="utf-8")))


_load()


def _require_token(authorization: str | None) -> None:
    """§17.7: credentials live in a store outside the automation.

    Enforced here so the rotation demo can actually fail. A credential nothing
    checks proves nothing.
    """
    expected = f"Bearer {get_settings().api_token}"
    if authorization != expected:
        raise HTTPException(
            status_code=401,
            detail=BillingError(error="UNAUTHORIZED").model_dump(by_alias=True),
        )


@app.get(
    "/billing/{contract_number}",
    summary="Stato di fatturazione di un contratto",
    description=(
        "Restituisce lo stato dell'addebito per il contratto indicato.\n\n"
        "Da chiamare solo dopo che il cliente ha fornito un numero di contratto "
        "nel formato FP-YYYY-NNNNNN. Non chiamare mai con un numero ipotizzato "
        "o ricostruito."
    ),
    responses={
        200: {"model": BillingStatus, "description": "Contratto trovato."},
        300: {
            "model": MultipleMatches,
            "description": (
                "Più contratti corrispondono. Chiedere al cliente di "
                "confermare tramite l'indirizzo di attivazione; non scegliere "
                "mai il primo risultato."
            ),
        },
        401: {"model": BillingError, "description": "Credenziale mancante o non valida."},
        404: {
            "model": BillingError,
            "description": (
                "Nessun contratto con questo numero nel sistema di "
                "fatturazione. Può comunque esistere nel CRM."
            ),
        },
        504: {
            "model": BillingError,
            "description": "Il sistema di fatturazione non ha risposto in tempo.",
        },
    },
)
async def get_billing(
    contract_number: str,
    response: Response,
    authorization: Annotated[str | None, Header()] = None,
    x_test_mode: Annotated[
        str | None,
        Header(
            description=(
                "Solo per collaudo: timeout | duplicate | notfound | slow. "
                "Non fa parte del comportamento di produzione."
            )
        ),
    ] = None,
) -> Any:
    _require_token(authorization)

    mode = resolve_mode(x_test_mode)
    await apply_latency(mode)

    # Echoed so the trace can show which failure mode produced a given outcome
    # — otherwise "it failed" and "we made it fail" look identical in the log.
    response.headers["X-Billing-Mode"] = mode.value

    if mode is TestMode.timeout:
        return JSONResponse(
            status_code=504,
            content=BillingError(
                error="GATEWAY_TIMEOUT",
                message="Il sistema di fatturazione non ha risposto in tempo.",
            ).model_dump(by_alias=True),
            headers={"X-Billing-Mode": mode.value},
        )

    if mode is TestMode.notfound:
        return JSONResponse(
            status_code=404,
            content=BillingError(error="CONTRACT_NOT_FOUND").model_dump(by_alias=True),
            headers={"X-Billing-Mode": mode.value},
        )

    if mode is TestMode.duplicate:
        matches = MultipleMatches(matches=[ContractMatch(**m) for m in _data["duplicate_matches"]])
        return JSONResponse(
            status_code=300,
            content=matches.model_dump(by_alias=True),
            headers={"X-Billing-Mode": mode.value},
        )

    record = _data["contracts"].get(contract_number)
    if record is None:
        # The honest 404. Reached both by an unknown number and by
        # FP-2020-004199, which exists in the CRM and not here (§6.1).
        return JSONResponse(
            status_code=404,
            content=BillingError(error="CONTRACT_NOT_FOUND").model_dump(by_alias=True),
            headers={"X-Billing-Mode": mode.value},
        )

    return BillingStatus(
        status=record["status"],
        failure_reason=record.get("failure_reason"),
        amount=Decimal(str(record["amount"])) if record.get("amount") else None,
        invoice_month=record.get("invoice_month"),
        next_retry_date=_as_date(record.get("next_retry_date")),
        payment_link=record.get("payment_link"),
        lock_in_until=_as_date(record.get("lock_in_until")),
        remaining_instalments=record.get("remaining_instalments"),
    )


class LineStatus(BaseModel):
    """§4.3's modem domain: designed, not built. The endpoint exists so the
    contract is complete and the domain is a config change rather than a
    redesign."""

    line_status: str = Field(examples=["up"])
    known_outage: bool = False
    estimated_restore: date | None = None


@app.get(
    "/line/{contract_number}",
    summary="Stato della linea (dominio modem — progettato, non attivo)",
    response_model=LineStatus,
)
async def get_line(
    contract_number: str,
    authorization: Annotated[str | None, Header()] = None,
) -> LineStatus:
    _require_token(authorization)
    return LineStatus(line_status="up", known_outage=False)


# --- the control surface: not part of the legacy system's contract -----------


class ModeRequest(BaseModel):
    mode: TestMode
    ttl_seconds: float = 120.0


@app.post("/_control/mode", include_in_schema=False)
async def set_mode(request: ModeRequest) -> dict[str, Any]:
    """Provoke a failure mid-conversation, from a button rather than a terminal."""
    ttl = min(request.ttl_seconds, get_settings().max_control_ttl_s)
    if request.mode is TestMode.normal:
        global_override().clear()
        return {"mode": "normal", "expires_in_s": 0}
    global_override().set(request.mode, ttl)
    return {"mode": request.mode.value, "expires_in_s": ttl}


@app.get("/_control/mode", include_in_schema=False)
async def get_mode() -> dict[str, str]:
    return {"mode": global_override().current().value}


@app.post("/_control/reset", include_in_schema=False)
async def reset() -> dict[str, Any]:
    """Reload fixtures and clear any sticky mode (§17.12)."""
    _load()
    global_override().clear()
    return {"contracts": len(_data["contracts"]), "mode": "normal"}


@app.get("/_control/health", include_in_schema=False)
async def health() -> dict[str, str]:
    return {"status": "ok"}


class CredentialRequest(BaseModel):
    token: str


@app.post("/_control/credential", include_in_schema=False)
async def set_credential(request: CredentialRequest) -> dict[str, str]:
    """Accept a rotated token (§17.7).

    Stands in for whatever the operator would really do on their side. It
    exists so the rotation demo completes a full cycle — store, far side, next
    request — rather than stopping at "and then you would tell them".
    """
    settings = get_settings()
    object.__setattr__(settings, "api_token", request.token)
    return {"status": "rotated"}


def _as_date(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))
