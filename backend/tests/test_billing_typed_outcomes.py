"""§5.1 at the integration boundary.

    "No capability may raise an untyped error into the reasoning layer. Every
    failure must arrive as a named outcome the instructions can refer to."

    "The rule was verified rather than assumed: a lookup for a non-existent
    contract number returns notFound and the agent says it cannot find the
    contract and offers a human, with no invented figures — repeatably."

So: verify it. Every one of the stub's failure modes, plus transport failures
and a contract violation, driven through the real client.
"""

from __future__ import annotations

import httpx
import pytest
from fibrapunto.capabilities.outcomes import (
    BillingFound,
    BillingMultipleMatches,
    BillingNotFound,
    BillingServiceUnavailable,
)
from fibrapunto.integrations.billing_client import BillingClient
from fibrapunto.integrations.registration import ExternalServiceRegistration
from mock_billing.main import app as billing_app

pytestmark = pytest.mark.asyncio

TOKEN = "billing-dev-token"
CONTRACT = "FP-2023-048812"


@pytest.fixture(autouse=True)
def _no_latency(monkeypatch):
    """The stub is deliberately slow (§7). That is right for a demo and wrong
    for a test suite, so the baseline delay is removed — but NOT the timeout and
    slow-mode delays, which are the behaviour under test."""
    from mock_billing.settings import Settings, get_settings

    get_settings.cache_clear()
    monkeypatch.setattr(
        Settings, "model_config", {**Settings.model_config, "env_prefix": "BILLING_"}
    )
    settings = get_settings()
    object.__setattr__(settings, "base_latency_ms", 0.0)
    object.__setattr__(settings, "latency_jitter_ms", 0.0)
    object.__setattr__(settings, "slow_mode_delay_s", 0.05)
    object.__setattr__(settings, "timeout_mode_delay_s", 2.0)
    yield
    get_settings.cache_clear()


def make_client(timeout_s: float = 5.0) -> BillingClient:
    """A BillingClient pointed at the stub. The `patched_httpx` fixture is what
    actually routes it in-process."""
    return BillingClient(
        base_url="http://billing.test",
        timeout_s=timeout_s,
        token_provider=lambda: TOKEN,
    )


@pytest.fixture
def patched_httpx(monkeypatch):
    """Route the client's httpx calls into the stub's ASGI app."""
    original = httpx.AsyncClient

    def factory(*args, **kwargs):
        kwargs.setdefault("transport", httpx.ASGITransport(app=billing_app))
        return original(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", factory)
    return factory


# --- the five modes of §7 ----------------------------------------------------


async def test_found(patched_httpx):
    result = await make_client().lookup(CONTRACT)
    assert isinstance(result, BillingFound)
    assert result.status == "failed"
    assert str(result.amount) == "29.90", "the exact amount must survive the round trip"
    assert result.failure_reason == "insufficient funds at the time of the charge"


# Absent and duplicated contracts are data, not modes: the numbers decide.
UNKNOWN = "FP-2099-999999"
DUPLICATED = "FP-2019-002217"


async def test_not_found(patched_httpx):
    result = await make_client().lookup(UNKNOWN)
    assert isinstance(result, BillingNotFound)
    assert "invent no contract data" in result.guidance.lower()


async def test_contract_in_crm_but_not_in_billing(patched_httpx):
    """§6.1's most instructive failure: the two systems of record disagree.

    Indistinguishable from a mistyped number at this layer — which is exactly
    why the agent must not guess which one it is.
    """
    result = await make_client().lookup("FP-2020-004199")
    assert isinstance(result, BillingNotFound)


async def test_multiple_matches(patched_httpx):
    result = await make_client().lookup(DUPLICATED)
    assert isinstance(result, BillingMultipleMatches)
    assert len(result.matches) == 2
    assert len({m.service_address for m in result.matches}) == 2
    assert all(m.service_address for m in result.matches), (
        "disambiguation is by address; a match without one is useless (§4.1.3)"
    )


@pytest.mark.parametrize("mode", ["unauthorized", "malformed"])
async def test_a_refused_credential_or_a_broken_body_is_service_unavailable(patched_httpx, mode):
    """The two billing switches added for the live demo. A 401 is our
    credential refused (§17.7); a 200 that breaks the contract must be refused
    whole rather than have figures read out of it (§7)."""
    result = await make_client().lookup(CONTRACT, test_mode=mode)
    assert isinstance(result, BillingServiceUnavailable)


async def test_slow_mode_still_succeeds(patched_httpx):
    """`slow` is a latency problem, not a failure. It must stay below the
    client timeout or it stops testing anything distinct from `timeout`."""
    result = await make_client(timeout_s=5.0).lookup(CONTRACT, test_mode="slow")
    assert isinstance(result, BillingFound)


async def test_timeout_mode_gives_up_before_the_504(patched_httpx):
    """The client aborts first, so the 504 never arrives. One of two paths to
    the same outcome — both are real, and this is the one usually untested."""
    result = await make_client(timeout_s=0.2).lookup(CONTRACT, test_mode="timeout")
    assert isinstance(result, BillingServiceUnavailable)
    assert result.incident_id


async def test_504_maps_to_the_same_outcome_as_a_client_timeout(patched_httpx):
    """The other path: we waited long enough to receive the 504."""
    result = await make_client(timeout_s=10.0).lookup(CONTRACT, test_mode="timeout")
    assert isinstance(result, BillingServiceUnavailable)


# --- failures the stub cannot produce ----------------------------------------


async def test_missing_credential_does_not_leak_auth_detail(patched_httpx):
    """401 becomes serviceUnavailable. The model is never told the difference
    between "we are misconfigured" and "billing is down" — neither licenses it
    to invent a figure, and only one of them is embarrassing to say out loud."""
    client = BillingClient(base_url="http://billing.test", token_provider=lambda: "wrong")
    result = await client.lookup(CONTRACT)
    assert isinstance(result, BillingServiceUnavailable)


async def test_connection_refused_is_typed(monkeypatch):
    def exploding(*args, **kwargs):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(httpx.AsyncClient, "get", exploding)
    result = await BillingClient(base_url="http://nowhere.test").lookup(CONTRACT)
    assert isinstance(result, BillingServiceUnavailable)


async def test_contract_violation_becomes_service_unavailable(monkeypatch):
    """§5.1 applied at the integration boundary.

    Billing answers 200 with a body its own published contract forbids. That is
    not an answer, and the reasoning layer must never see it — otherwise a
    missing field becomes a None the model narrates around.
    """

    async def get(self, url, **kwargs):
        return httpx.Response(200, json={"status": "wildly-invalid"})

    monkeypatch.setattr(httpx.AsyncClient, "get", get)
    result = await BillingClient(base_url="http://billing.test").lookup(CONTRACT)
    assert isinstance(result, BillingServiceUnavailable)


# --- the invariant itself ----------------------------------------------------


@pytest.mark.parametrize(
    ("mode", "number"),
    [
        ("normal", "FP-2023-048812"),
        ("normal", UNKNOWN),
        ("normal", DUPLICATED),
        ("timeout", "FP-2023-048812"),
        ("slow", "FP-2023-048812"),
        ("bogus-mode", "FP-2023-048812"),
    ],
)
async def test_no_mode_ever_raises(patched_httpx, mode, number):
    """The whole point, stated as one assertion.

    Whatever the far side does, the reasoning layer receives a named outcome.
    An unknown test mode is included because a caller typo must not become an
    exception either.
    """
    result = await make_client(timeout_s=1.0).lookup(number, test_mode=mode)
    assert hasattr(result, "outcome")
    assert result.outcome in {
        "found",
        "notFound",
        "serviceUnavailable",
        "multipleMatches",
        "rejected",
    }


async def test_a_valid_number_is_never_ambiguous_by_accident(patched_httpx):
    """With the duplicate *mode* gone, the only way to a multiple match is a
    number the data holds twice. A normal contract is found, always."""
    for number in ("FP-2023-048712", "FP-2021-017350", "FP-2023-048812"):
        assert isinstance(await make_client().lookup(number), BillingFound)


async def test_the_contract_declares_every_outcome_we_map():
    """If the published contract stops declaring 504, the mapping above is a
    lie and we want to know at import time, not in front of an audience."""
    registration = ExternalServiceRegistration()
    declared = registration.expected_statuses("/billing/{contract_number}")
    assert {200, 300, 404, 504}.issubset(declared)


# --- the number as the customer typed it (§5.2) -------------------------------
#
# An eval run: verified for FP-2023-048812 from "fp 2023 048812", then the
# lookup rejected the same number as malformed. Separators and case are the
# customer's keyboard, not the number.


def test_separators_and_case_are_normalised_nothing_else_is():
    from fibrapunto.capabilities.preconditions import normalise

    assert normalise("fp 2023 048812") == "FP-2023-048812"
    assert normalise("FP2023048812") == "FP-2023-048812"
    assert normalise(" fp-2023-048812 ") == "FP-2023-048812"
    assert normalise("2023048812") == "2023048812", "no prefix: still malformed"
    assert normalise("FP-2023-04881") == "FP-2023-04881", "a missing digit is not repaired"


def test_a_number_typed_with_spaces_still_came_from_the_customer():
    from fibrapunto.capabilities.preconditions import came_from_customer

    assert came_from_customer("FP-2023-048812", ["the number is fp 2023 048812"])
    assert not came_from_customer("FP-2023-048813", ["the number is fp 2023 048812"])
    assert not came_from_customer("", ["anything"])
