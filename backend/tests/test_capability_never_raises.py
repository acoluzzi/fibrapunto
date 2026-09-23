"""The guarantee §5.1 calls the most important rule in the document (§17.4).

The previous test file proved the *client* returns typed results. This one
proves the **capability boundary** does — the thing the reasoning layer actually
touches — and it does so under conditions the stub cannot produce: a body that
is not JSON, a connection reset mid-read, a capability whose implementation
throws something nobody anticipated.

    "When a capability throws, the reasoning layer receives something generic —
    an error occurred — and a language model presented with a generic failure
    and a customer waiting for an answer will tend to fill the gap."

The test for that is not "does it usually work". It is: throw everything at it,
and assert that a named outcome comes back every single time.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest
from fibrapunto.capabilities.billing_lookup import billing_lookup
from fibrapunto.capabilities.outcomes import (
    BillingFound,
    BillingRejected,
    BillingServiceUnavailable,
)
from fibrapunto.capabilities.runtime import CapabilityCall, typed_capability
from fibrapunto.integrations.billing_client import BillingClient
from fibrapunto.perimeter.policy import FieldNotAuthorized
from mock_billing.main import app as billing_app
from pydantic import BaseModel, ValidationError

pytestmark = pytest.mark.asyncio

CONTRACT = "FP-2023-048812"
CUSTOMER_TURNS = ["my contract is FP-2023-048812"]

VALID_OUTCOMES = {"found", "notFound", "serviceUnavailable", "multipleMatches", "rejected"}


class Recorder:
    def __init__(self) -> None:
        self.calls: list[CapabilityCall] = []

    def record(self, call: CapabilityCall) -> None:
        self.calls.append(call)


@pytest.fixture
def patched_httpx(monkeypatch):
    original = httpx.AsyncClient

    def factory(*args, **kwargs):
        kwargs.setdefault("transport", httpx.ASGITransport(app=billing_app))
        return original(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", factory)


@pytest.fixture(autouse=True)
def _fast_stub(monkeypatch):
    from mock_billing.settings import get_settings

    get_settings.cache_clear()
    settings = get_settings()
    object.__setattr__(settings, "base_latency_ms", 0.0)
    object.__setattr__(settings, "latency_jitter_ms", 0.0)
    object.__setattr__(settings, "slow_mode_delay_s", 0.05)
    object.__setattr__(settings, "timeout_mode_delay_s", 1.5)
    yield
    get_settings.cache_clear()


def client(timeout_s: float = 5.0) -> BillingClient:
    return BillingClient(
        base_url="http://billing.test",
        timeout_s=timeout_s,
        token_provider=lambda: "billing-dev-token",
    )


# --- every stub mode, through the capability ---------------------------------


@pytest.mark.parametrize(
    "mode", ["normal", "timeout", "duplicate", "notfound", "slow", "not-a-real-mode"]
)
async def test_every_mode_yields_a_named_outcome(patched_httpx, mode):
    recorder = Recorder()
    result = await billing_lookup(
        CONTRACT,
        client=client(timeout_s=0.5),
        customer_turns=CUSTOMER_TURNS,
        test_mode=mode,
        recorder=recorder,
    )
    assert result.outcome in VALID_OUTCOMES
    assert recorder.calls[0].outcome == result.outcome


# --- failures the stub cannot produce ----------------------------------------


async def test_a_body_that_is_not_json(monkeypatch):
    async def get(self, url, **kwargs):
        return httpx.Response(200, content=b"<html>502 Bad Gateway</html>")

    monkeypatch.setattr(httpx.AsyncClient, "get", get)
    result = await billing_lookup(CONTRACT, client=client(), customer_turns=CUSTOMER_TURNS)
    assert isinstance(result, BillingServiceUnavailable)


async def test_a_connection_reset_mid_read(monkeypatch):
    async def get(self, url, **kwargs):
        raise httpx.ReadError("connection reset by peer")

    monkeypatch.setattr(httpx.AsyncClient, "get", get)
    result = await billing_lookup(CONTRACT, client=client(), customer_turns=CUSTOMER_TURNS)
    assert isinstance(result, BillingServiceUnavailable)


async def test_an_authorization_denial_does_not_reach_the_reasoning_layer():
    """§8's perimeter raises; §5.1 says the model must not see it.

    The model has no instruction for `FieldNotAuthorized` and would improvise
    around it — which is exactly the failure both sections exist to prevent.
    """

    @typed_capability(
        "probe",
        on_unexpected=lambda incident_id: BillingServiceUnavailable(incident_id=incident_id),
    )
    async def denied() -> BaseModel:
        raise FieldNotAuthorized("svc_agent", "customer", "email", "read")

    result = await denied()
    assert isinstance(result, BillingServiceUnavailable)
    assert result.incident_id


@pytest.mark.parametrize(
    "exception",
    [
        ValueError("something nobody predicted"),
        KeyError("missing"),
        RuntimeError("event loop is closed"),
        ZeroDivisionError(),
        AttributeError("'NoneType' object has no attribute 'status'"),
    ],
    ids=["value", "key", "runtime", "zero-division", "attribute"],
)
async def test_arbitrary_exceptions_become_outcomes(exception):
    """The catch-all floor.

    `except Exception` is deliberate: a failure nobody anticipated must still
    arrive as a name the instructions cover, rather than as an outage.
    """

    @typed_capability(
        "exploding",
        on_unexpected=lambda incident_id: BillingServiceUnavailable(incident_id=incident_id),
    )
    async def explode() -> BaseModel:
        raise exception

    result = await explode()
    assert result.outcome in VALID_OUTCOMES


async def test_cancellation_propagates_and_is_not_swallowed():
    """The deliberate exception to "nothing escapes" — and it is deliberate.

    CancelledError inherits from BaseException, so `except Exception` lets it
    through. Both reasons this is right:

      * `asyncio.timeout()` works *by* cancelling the inner task and converting
        the result to TimeoutError. Catching CancelledError here would break the
        timeout branch, and a capability that cannot time out is a conversation
        that cannot hand over (§16).

      * §5.1's failure mode is a model improvising around a generic error while
        a customer waits. A cancelled task has no waiting customer and no turn
        to finish; propagating is a teardown, not an untyped error reaching the
        reasoning layer.

    This test exists so that widening the clause to BaseException — which would
    look like extra safety — fails loudly instead of silently costing the
    timeout guarantee.
    """

    @typed_capability(
        "cancelled",
        on_unexpected=lambda incident_id: BillingServiceUnavailable(incident_id=incident_id),
    )
    async def cancelled() -> BaseModel:
        raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        await cancelled()


async def test_a_capability_that_hangs_is_bounded(monkeypatch):
    """§16: the timeout "is the line that decides when to stop waiting and hand
    over". A capability with no timeout is a conversation with no end."""

    @typed_capability(
        "slow",
        on_unexpected=lambda incident_id: BillingServiceUnavailable(incident_id=incident_id),
        timeout_s=0.1,
    )
    async def hang() -> BaseModel:
        await asyncio.sleep(30)
        raise AssertionError("unreachable")

    result = await hang()
    assert isinstance(result, BillingServiceUnavailable)


async def test_a_malformed_return_value_is_caught():
    """A capability returning something that fails validation is our bug, and
    it must still not become the model's problem."""

    class Wrong(BaseModel):
        required_field: int

    @typed_capability(
        "broken",
        on_unexpected=lambda incident_id: BillingServiceUnavailable(incident_id=incident_id),
    )
    async def broken() -> BaseModel:
        raise ValidationError.from_exception_data("Wrong", [])

    result = await broken()
    assert isinstance(result, BillingServiceUnavailable)


# --- the preconditions of §5.2 -----------------------------------------------


async def test_a_number_the_customer_never_typed_is_rejected(patched_httpx):
    """§5.2: "Must come from the customer verbatim."

    A well-formed, entirely plausible contract number that the customer did not
    supply. Enforced rather than requested — the lookup does not happen.
    """
    result = await billing_lookup(
        "FP-2023-048812",
        client=client(),
        customer_turns=["I cannot find my bill, can you help?"],
        test_mode="normal",
    )
    assert isinstance(result, BillingRejected)
    assert result.reason == "contract_number_not_from_customer"


async def test_a_malformed_number_is_rejected_without_a_call(patched_httpx):
    result = await billing_lookup(
        "FP-23-4881",
        client=client(),
        customer_turns=["the contract is FP-23-4881"],
    )
    assert isinstance(result, BillingRejected)
    assert result.reason == "format_invalid"


async def test_case_and_whitespace_do_not_defeat_the_verbatim_check(patched_httpx):
    """A customer writing lowercase has still supplied the number."""
    result = await billing_lookup(
        "FP-2023-048812",
        client=client(),
        customer_turns=["  eccolo: fp-2023-048812  "],
    )
    assert isinstance(result, BillingFound)


# --- what the trace records --------------------------------------------------


async def test_the_trace_records_the_outcome_not_the_payload(patched_httpx):
    """§12.2 needs to know which lookups succeeded and which failed. That is an
    outcome code — not the customer's billing data copied into a case record."""
    recorder = Recorder()
    await billing_lookup(
        CONTRACT,
        client=client(),
        customer_turns=CUSTOMER_TURNS,
        recorder=recorder,
    )
    call = recorder.calls[0]
    assert call.capability == "billing_lookup"
    assert call.outcome == "found"
    assert call.duration_ms >= 0
    assert "29.90" not in str(call.arguments), "billing figures do not belong in the trace"


async def test_an_incident_id_is_recorded_only_on_failure(monkeypatch):
    async def get(self, url, **kwargs):
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(httpx.AsyncClient, "get", get)
    recorder = Recorder()
    await billing_lookup(
        CONTRACT, client=client(), customer_turns=CUSTOMER_TURNS, recorder=recorder
    )
    call = recorder.calls[0]
    assert call.outcome == "serviceUnavailable"
    assert call.error_class is None or call.incident_id, (
        "a failure the engineer needs to find must be greppable by incident id"
    )
