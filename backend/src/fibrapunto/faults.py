"""Live failure switches for the modules this system depends on (§7, §15).

    "failures are provoked on purpose and repeated on command, during
    evaluation and during the live demo."

The billing system's switch lives in the billing stub, because billing is the
external system and failing it from the outside is the honest test. Everything
else the agent depends on is switched here, at the point where the real
failure would surface — so what the customer sees is the production failure
path, not a simulation of it:

  * crm              — the CRM gateway raises, so opening a case fails as a
                       typed HandoverFailed and §12.4's closing is sent.
  * colleagues       — whether anyone is there to take a transfer (§12.4).
                       "schedule" is the real opening hours.
  * model            — the model provider is unreachable; the turn fails with
                       the API's 503, as it would for real.
  * credential_store — the billing token cannot be read (§17.7), so the lookup
                       fails as serviceUnavailable before leaving the process.
  * trace_store      — the observability database is down. The conversation
                       must carry on without it.

Off unless FAULT_INJECTION is true, and when off `current()` always reports
every module up, so a production build cannot be switched into a failure by a
stray request. Global and in-memory: one demo, one set of switches.
"""

from __future__ import annotations

from typing import Literal

import structlog
from pydantic import BaseModel

from fibrapunto.settings import get_settings

log = structlog.get_logger(__name__)

UpDown = Literal["up", "down"]


class Faults(BaseModel):
    crm: UpDown = "up"
    colleagues: Literal["schedule", "available", "unavailable"] = "schedule"
    model: UpDown = "up"
    credential_store: UpDown = "up"
    trace_store: UpDown = "up"


_current = Faults()


class InjectedFault(ConnectionError):
    """Raised where the real module would fail. A ConnectionError, because
    that is what each of these looks like from the caller's side."""


def enabled() -> bool:
    return get_settings().fault_injection


def current() -> Faults:
    return _current if enabled() else Faults()


def update(**changes: str) -> Faults:
    global _current
    _current = _current.model_copy(
        update=Faults(**{**_current.model_dump(), **changes}).model_dump()
    )
    log.warning("faults.updated", **_current.model_dump())
    return _current


def reset() -> None:
    global _current
    _current = Faults()


def fail_if_down(module: Literal["crm", "model", "credential_store", "trace_store"]) -> None:
    if getattr(current(), module) == "down":
        raise InjectedFault(f"{module.replace('_', ' ')} unreachable (switched off in the demo)")
