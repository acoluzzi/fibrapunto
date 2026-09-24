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
from fastapi import FastAPI, Header, Request, Response
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
    title="Fibrapunto — billing system (legacy)",
    version="1.0.0",
    description=(
        "Interface to Fibrapunto's legacy billing system.\n\n"
        "It is the system of record for payments; the customer record for a "
        "contract lives in the CRM, which is a different system. The two may be "
        "out of step: a contract present in the CRM may not exist here."
    ),
)

_data: dict[str, Any] = {}


def _load() -> None:
    _data.clear()
    _data.update(yaml.safe_load(FIXTURES.read_text(encoding="utf-8")))


_load()


class Unauthorized(Exception):
    """Raised by the token check; turned into a contract-shaped 401 below."""


def _require_token(authorization: str | None) -> None:
    """§17.7: credentials live in a store outside the automation.

    Enforced here so the rotation demo can actually fail. A credential nothing
    checks proves nothing.

    Deliberately NOT `HTTPException`: FastAPI wraps that in `{"detail": ...}`,
    so the 401 body would be `{"detail": {"error": "UNAUTHORIZED"}}` while the
    published contract declares a bare `BillingError`. That mismatch is a real
    contract violation, and the integration layer caught it on the first live
    conversation — which is exactly what ExternalServiceRegistration is for,
    but the stub should not be the one breaking its own contract.
    """
    if authorization != f"Bearer {get_settings().api_token}":
        raise Unauthorized


@app.exception_handler(Unauthorized)
async def _unauthorized_handler(request: Request, exc: Unauthorized) -> JSONResponse:
    return JSONResponse(
        status_code=401,
        content=BillingError(error="UNAUTHORIZED").model_dump(by_alias=True),
    )


@app.get(
    "/billing/{contract_number}",
    summary="Billing status for a contract",
    description=(
        "Returns the charge status for the given contract.\n\n"
        "To be called only after the customer has provided a contract number "
        "in the format FP-YYYY-NNNNNN. Never call it with a guessed or "
        "reconstructed number."
    ),
    responses={
        200: {"model": BillingStatus, "description": "Contract found."},
        300: {
            "model": MultipleMatches,
            "description": (
                "Several contracts match. Ask the customer to confirm by "
                "service address; never pick the first result."
            ),
        },
        401: {"model": BillingError, "description": "Missing or invalid credential."},
        404: {
            "model": BillingError,
            "description": (
                "No contract with this number in the billing system. It may still exist in the CRM."
            ),
        },
        504: {
            "model": BillingError,
            "description": "The billing system did not respond in time.",
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
                "Testing only: timeout | slow | unauthorized | malformed. It is "
                "not part of production "
                "behaviour. A missing or duplicated contract is not a mode — it "
                "is the data: look up a number that is absent, or held twice."
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
                message="The billing system did not respond in time.",
            ).model_dump(by_alias=True),
            headers={"X-Billing-Mode": mode.value},
        )

    if mode is TestMode.unauthorized:
        return JSONResponse(
            status_code=401,
            content=BillingError(error="UNAUTHORIZED").model_dump(by_alias=True),
            headers={"X-Billing-Mode": mode.value},
        )

    if mode is TestMode.malformed:
        # A 200 that breaks the contract: a status the schema does not allow
        # and an amount that is not a number. Figures read out of this would be
        # invented by the parser, so the client must refuse the whole body.
        return JSONResponse(
            status_code=200,
            content={"status": "ok-ish", "amount": "twenty-nine ninety"},
            headers={"X-Billing-Mode": mode.value},
        )

    # What the data says. A number billing holds more than once is ambiguous
    # for whoever types it (§15 row 2); a number it does not hold is a 404.
    duplicated = _data.get("duplicates", {}).get(contract_number)
    if duplicated:
        matches = MultipleMatches(matches=[ContractMatch(**m) for m in duplicated])
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
    summary="Line status (modem domain — designed, not enabled)",
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
    # None: sticky until changed (the control strip's switches). A number:
    # expires on its own (`make billing-mode`, a terminal nobody is watching).
    ttl_seconds: float | None = 120.0


@app.post("/_control/mode", include_in_schema=False)
async def set_mode(request: ModeRequest) -> dict[str, Any]:
    """Provoke a failure mid-conversation, from a button rather than a terminal."""
    ttl = (
        None
        if request.ttl_seconds is None
        else min(request.ttl_seconds, get_settings().max_control_ttl_s)
    )
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
