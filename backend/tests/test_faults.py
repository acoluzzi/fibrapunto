"""The live failure switches (faults.py, §7, §15).

Each switch is tested at the point where the real module would fail, and each
failure is checked to take the production failure path — a typed outcome, a
503, a non-fatal log line — rather than a special demo path.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from fibrapunto import faults
from fibrapunto.capabilities.outcomes import HandoverFailed
from fibrapunto.capabilities.transfer_to_human import within_service_hours
from fibrapunto.credentials.store import CredentialStoreLocked, FileSecretStore
from fibrapunto.llm import chat
from fibrapunto.llm.registry import ModelRole
from fibrapunto.perimeter.gateway import CrmGateway
from fibrapunto.perimeter.policy import svc_agent
from fibrapunto.settings import get_settings


@pytest.fixture
def switches(monkeypatch):
    monkeypatch.setattr(get_settings(), "fault_injection", True)
    faults.reset()
    yield faults
    faults.reset()


def test_off_by_default_nothing_can_be_switched(monkeypatch):
    """A build that did not ask for fault injection reports every module up,
    whatever was set."""
    monkeypatch.setattr(get_settings(), "fault_injection", False)
    faults.update(crm="down")
    try:
        assert faults.current().crm == "up"
        faults.fail_if_down("crm")  # must not raise
    finally:
        faults.reset()


def test_an_invalid_switch_value_is_refused(switches):
    with pytest.raises(ValueError):
        switches.update(crm="sideways")


# --- CRM -----------------------------------------------------------------------


async def test_crm_down_fails_case_creation_as_a_typed_outcome(switches):
    from fibrapunto.capabilities.create_handover_record import create_handover_record
    from fibrapunto.domains.registry import DomainKey
    from fibrapunto.state.models import ConversationState

    switches.update(crm="down")
    state = ConversationState(current_domain=DomainKey.cancellation)
    state.collected.contract_number = "FP-2023-048812"
    state.collected.told_to_customer = ["x"]

    result = await create_handover_record(
        state=state,
        gateway=CrmGateway(pool=None),  # never reached: the switch fails first
        principal=svc_agent(),
        subject="Cancellation request",
        trigger="categorical:cancellation",
    )
    assert isinstance(result, HandoverFailed)
    assert state.collected.handover_reference is None


async def test_crm_down_still_enforces_the_perimeter_first(switches):
    """The switch sits after the policy check: a denied column is still a
    denial, not a convenient 'CRM down'."""
    from fibrapunto.perimeter.policy import PerimeterError

    switches.update(crm="down")
    with pytest.raises(PerimeterError):
        await CrmGateway(pool=None).create_handover_record(svc_agent(), {"internal_comments": "x"})


# --- colleagues ----------------------------------------------------------------


def test_colleagues_switch_overrides_the_clock(switches):
    switches.update(colleagues="available")
    assert within_service_hours() is True
    switches.update(colleagues="unavailable")
    assert within_service_hours() is False


def test_schedule_is_the_real_opening_hours(switches):
    from datetime import datetime

    from fibrapunto.capabilities.transfer_to_human import ROME

    switches.update(colleagues="schedule")
    assert within_service_hours(datetime(2026, 9, 21, 10, tzinfo=ROME)) is True  # Monday
    assert within_service_hours(datetime(2026, 9, 20, 10, tzinfo=ROME)) is False  # Sunday


# --- model provider ------------------------------------------------------------


async def test_model_down_is_the_reasoning_layer_being_unavailable(switches):
    """The API turns this into its 503 — the same thing a real outage does."""

    class NeverCalled:
        async def ainvoke(self, _messages):
            raise AssertionError("the provider must not be called while it is down")

    switches.update(model="down")
    with pytest.raises(chat.ReasoningLayerUnavailable, match="unreachable"):
        await chat.ainvoke(NeverCalled(), [], role=ModelRole.agent)


# --- credential store ----------------------------------------------------------


def test_credential_store_down_cannot_be_read(switches, tmp_path):
    from cryptography.fernet import Fernet

    store = FileSecretStore(path=tmp_path / "c.json", master_key=Fernet.generate_key().decode())
    store.rotate("billing", "token")
    switches.update(credential_store="down")
    with pytest.raises(CredentialStoreLocked):
        store.get("billing")


async def test_credential_store_down_makes_billing_unavailable(switches, monkeypatch, tmp_path):
    """Through the real capability: the token cannot be resolved, so the
    lookup never leaves the process, and the typed boundary (§5.1) hands the
    agent serviceUnavailable instead of an exception."""
    import httpx
    from cryptography.fernet import Fernet
    from fibrapunto.capabilities.outcomes import BillingServiceUnavailable
    from fibrapunto.integrations.billing_client import BillingClient
    from mock_billing.main import app as billing_app

    original = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda *a, **k: original(*a, transport=httpx.ASGITransport(app=billing_app), **k),
    )
    store = FileSecretStore(path=tmp_path / "c.json", master_key=Fernet.generate_key().decode())
    store.rotate("billing", "billing-dev-token")
    client = BillingClient(base_url="http://billing.test", token_provider=store.provider("billing"))

    from fibrapunto.capabilities.billing_lookup import billing_lookup

    switches.update(credential_store="down")
    result = await billing_lookup(
        "FP-2023-048812", client=client, customer_turns=["my contract is FP-2023-048812"]
    )
    assert isinstance(result, BillingServiceUnavailable)


# --- trace store ---------------------------------------------------------------


async def test_trace_store_down_never_fails_the_turn(switches, monkeypatch):
    from fibrapunto.api import app as app_module

    written = []

    class Store:
        async def store_events(self, events):
            written.append(events)

    monkeypatch.setattr(app_module, "_store", Store())

    assert await app_module._persist("store_events", lambda s: s.store_events([1])) is True
    switches.update(trace_store="down")
    assert await app_module._persist("store_events", lambda s: s.store_events([2])) is False
    assert written == [[1]]


async def test_a_real_store_error_is_non_fatal_too(switches, monkeypatch):
    from fibrapunto.api import app as app_module

    class Broken:
        async def store_events(self, events):
            raise OSError("disk full")

    monkeypatch.setattr(app_module, "_store", Broken())
    assert await app_module._persist("store_events", lambda s: s.store_events([])) is False


# --- the endpoints --------------------------------------------------------------


def test_the_switch_endpoints(switches):
    from fibrapunto.api.app import app

    client = TestClient(app)
    assert client.get("/api/faults").json()["crm"] == "up"
    assert client.post("/api/faults", json={"crm": "down"}).json()["crm"] == "down"
    assert client.get("/api/faults").json()["crm"] == "down"
    assert client.post("/api/faults", json={"colleagues": "maybe"}).status_code == 422


def test_the_endpoints_do_not_exist_when_fault_injection_is_off(monkeypatch):
    from fibrapunto.api.app import app

    monkeypatch.setattr(get_settings(), "fault_injection", False)
    client = TestClient(app)
    assert client.get("/api/faults").status_code == 404
    assert client.post("/api/faults", json={"crm": "down"}).status_code == 404
