"""The boundary no exception crosses (§5.1, §17.4).

    "No capability may raise an untyped error into the reasoning layer. Every
    failure must arrive as a named outcome the instructions can refer to. This
    is the single most important implementation rule in the document."

Every capability is wrapped by `@typed_capability`. Known failures map to the
outcome that describes them; unknown ones map to the capability's designated
fallback with an incident id. Either way the reasoning layer receives a name it
has instructions for, and the engineer receives a stack trace keyed to the same
id.

Two rules the tests enforce, because prose does not enforce anything:

  * **`except Exception` is the floor, not the plan.** Known exceptions are
    mapped deliberately; the catch-all exists so that a failure nobody predicted
    still arrives as a name rather than as an outage.

  * **`ModelRetry`-style re-raising never happens.** LangGraph's prebuilt
    ToolNode would hand a malformed call back to the model as free text and
    re-raise everything else (see docs/FRICTION-LOG.md entry 2). We use neither
    behaviour: both become outcomes.
"""

from __future__ import annotations

import asyncio
import functools
import time
import traceback
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, ParamSpec, Protocol

import httpx
import structlog
from pydantic import BaseModel, ValidationError

from fibrapunto.integrations.registration import ContractViolation
from fibrapunto.perimeter.policy import PerimeterError

log = structlog.get_logger(__name__)

P = ParamSpec("P")


class FallbackFactory(Protocol):
    """Builds the outcome returned when nothing better applies.

    Each capability supplies its own, because "I could not do that" means a
    different named thing for a lookup than for a record creation.
    """

    def __call__(self, incident_id: str) -> BaseModel: ...


@dataclass(slots=True)
class CapabilityCall:
    """One invocation, as the trace and the §13 handover payload need it.

    `outcome` is the *name*, never the payload: §12.2 requires the handover to
    record which lookups succeeded and which failed, and that is an outcome
    code, not a customer's billing data copied into a case record.
    """

    capability: str
    outcome: str
    started_at: datetime
    duration_ms: int
    arguments: dict[str, Any] = field(default_factory=dict)
    incident_id: str | None = None
    error_class: str | None = None


class CapabilityRecorder(Protocol):
    def record(self, call: CapabilityCall) -> None: ...


def typed_capability(
    name: str,
    on_unexpected: FallbackFactory,
    timeout_s: float | None = None,
) -> Callable[[Callable[P, Awaitable[BaseModel]]], Callable[P, Awaitable[BaseModel]]]:
    """Guarantee that `fn` returns a typed outcome and never raises.

    `recorder` may be passed as a keyword to the wrapped call; it is consumed
    here rather than forwarded, so capability bodies stay unaware of tracing.
    """

    def decorator(
        fn: Callable[P, Awaitable[BaseModel]],
    ) -> Callable[P, Awaitable[BaseModel]]:
        @functools.wraps(fn)
        async def wrapper(*args: P.args, **kwargs: P.kwargs) -> BaseModel:
            recorder: CapabilityRecorder | None = kwargs.pop("recorder", None)  # type: ignore[assignment]
            incident_id = uuid.uuid4().hex[:12]
            started_at = datetime.now(UTC)
            start = time.perf_counter()
            error_class: str | None = None

            try:
                if timeout_s is not None:
                    async with asyncio.timeout(timeout_s):
                        result = await fn(*args, **kwargs)
                else:
                    result = await fn(*args, **kwargs)

            except PerimeterError as exc:
                # §8: an authorization failure is a real answer about what this
                # identity may do. It must never reach the model as itself —
                # the model has no instruction for "InsufficientPrivilegeError"
                # and would improvise around it.
                error_class = type(exc).__name__
                log.error(
                    "capability.authorization_denied",
                    capability=name,
                    incident_id=incident_id,
                    detail=str(exc),
                )
                result = on_unexpected(incident_id)

            except ContractViolation as exc:
                error_class = type(exc).__name__
                log.error(
                    "capability.contract_violation",
                    capability=name,
                    incident_id=incident_id,
                    detail=exc.detail,
                )
                result = on_unexpected(incident_id)

            except (TimeoutError, httpx.TimeoutException) as exc:
                error_class = type(exc).__name__
                log.warning("capability.timeout", capability=name, incident_id=incident_id)
                result = on_unexpected(incident_id)

            except httpx.HTTPError as exc:
                error_class = type(exc).__name__
                log.warning(
                    "capability.transport_error",
                    capability=name,
                    incident_id=incident_id,
                    error=str(exc),
                )
                result = on_unexpected(incident_id)

            except ValidationError as exc:
                error_class = type(exc).__name__
                log.error(
                    "capability.schema_mismatch",
                    capability=name,
                    incident_id=incident_id,
                    errors=exc.error_count(),
                )
                result = on_unexpected(incident_id)

            # NOTE: asyncio.CancelledError is deliberately NOT caught. It
            # inherits from BaseException, so it passes through the clause
            # below, and that is correct on both counts:
            #
            #   * asyncio.timeout() is implemented by cancelling the inner task
            #     and converting the result to TimeoutError. Swallowing
            #     CancelledError here would break the timeout branch above — the
            #     capability would hang rather than hand over (§16).
            #   * §5.1's failure mode is a model improvising around a generic
            #     error while a customer waits. A cancelled task has no waiting
            #     customer and no turn to complete; propagating is a teardown,
            #     not an untyped error in front of the reasoning layer.
            #
            # Widening this to BaseException would look like extra safety and
            # would cost the timeout guarantee. Don't.
            except Exception as exc:  # noqa: BLE001 - deliberate; see module docstring
                # The floor. Something happened that nobody anticipated, and the
                # conversation continues with a name the instructions cover.
                error_class = type(exc).__name__
                log.error(
                    "capability.unexpected",
                    capability=name,
                    incident_id=incident_id,
                    error_class=error_class,
                    traceback=traceback.format_exc(),
                )
                result = on_unexpected(incident_id)

            duration_ms = int((time.perf_counter() - start) * 1000)
            outcome = getattr(result, "outcome", "unknown")

            if recorder is not None:
                recorder.record(
                    CapabilityCall(
                        capability=name,
                        outcome=outcome,
                        started_at=started_at,
                        duration_ms=duration_ms,
                        arguments=_safe_arguments(kwargs),
                        incident_id=incident_id if error_class else None,
                        error_class=error_class,
                    )
                )

            log.info(
                "capability.completed",
                capability=name,
                outcome=outcome,
                duration_ms=duration_ms,
            )
            return result

        return wrapper

    return decorator


# Argument names whose values must never reach a log or a trace record (§9).
_NEVER_RECORD = frozenset({"token", "password", "authorization", "api_key", "secret"})


def _safe_arguments(kwargs: dict[str, Any]) -> dict[str, Any]:
    """Arguments as the trace may keep them.

    The contract number is kept — it is what makes a trace readable, and §12.2
    requires it in the handover anyway. Anything credential-shaped is dropped
    rather than redacted, because a redacted marker still says one was passed.
    """
    return {
        key: value
        for key, value in kwargs.items()
        if key.lower() not in _NEVER_RECORD and not isinstance(value, BaseModel)
    }
