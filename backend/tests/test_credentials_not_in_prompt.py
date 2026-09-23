"""The credential store is external to the automation AND the instructions (§17.7).

Three properties, each tested rather than asserted:

  1. No credential is a parameter of anything the agent touches.
  2. No credential can reach the model.
  3. No credential can be written into an instruction or a contract.

The third is the one that fails in practice. A token gets pasted into a prompt
during a hurried debugging session, the session ends, and it stays — in version
control, in every context window, and in whatever the model provider retains.
"""

from __future__ import annotations

import inspect
import json
from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from fibrapunto.capabilities import billing_lookup as billing_lookup_module
from fibrapunto.credentials.store import (
    BILLING_API_TOKEN,
    CredentialStoreLocked,
    FileSecretStore,
    assert_clean,
)
from fibrapunto.integrations.billing_client import BillingClient
from fibrapunto.settings import REPO_ROOT

SECRET = "billing-super-secret-value-do-not-leak"


@pytest.fixture
def store(tmp_path) -> FileSecretStore:
    s = FileSecretStore(path=tmp_path / "creds.enc.json", master_key=Fernet.generate_key().decode())
    s.rotate(BILLING_API_TOKEN, SECRET)
    return s


# --- 1. not a parameter ------------------------------------------------------


def test_the_capability_signature_has_no_credential_parameter():
    """The agent calls billing_lookup(contract_number, ...). There is no token
    argument for a credential to travel through, and therefore no route by
    which one could be logged, traced, or handed to the model."""
    parameters = set(inspect.signature(billing_lookup_module.billing_lookup).parameters)
    forbidden = {"token", "api_key", "authorization", "secret", "password", "credential"}
    assert not (parameters & forbidden), (
        f"billing_lookup accepts credential-shaped arguments: {parameters & forbidden}"
    )


def test_the_client_holds_a_provider_not_a_value(store):
    """A handle, resolved at request time.

    Two consequences: a rotation takes effect on the next request with no
    restart, and a client object captured in a traceback or a repr carries no
    secret.
    """
    client = BillingClient(token_provider=store.provider(BILLING_API_TOKEN))
    assert SECRET not in repr(client)
    assert SECRET not in json.dumps({k: str(v) for k, v in vars(client).items()}, default=str)
    # But it still resolves when actually needed.
    assert client._token_provider() == SECRET  # noqa: SLF001


def test_rotation_takes_effect_without_reconstructing_the_client(store):
    client = BillingClient(token_provider=store.provider(BILLING_API_TOKEN))
    assert client._token_provider() == SECRET  # noqa: SLF001

    store.rotate(BILLING_API_TOKEN, "a-brand-new-token")

    assert client._token_provider() == "a-brand-new-token", (  # noqa: SLF001
        "The same client object must pick up the rotated value — that is what "
        "makes §17.7's 'no code change, no restart' claim true."
    )


# --- 2. cannot reach the model -----------------------------------------------


def test_the_secret_is_not_in_the_capability_description():
    """What the model reads about this capability, in full."""
    surface = " ".join(
        [
            billing_lookup_module.DESCRIPTION,
            billing_lookup_module.INPUT_DESCRIPTION,
            billing_lookup_module.billing_lookup.__doc__ or "",
        ]
    )
    assert SECRET not in surface
    assert "Bearer" not in surface


def test_no_stored_credential_appears_in_the_prompt_or_contract_files(store):
    """The startup gate, exercised against the real files."""
    watched = [
        *(REPO_ROOT / "backend" / "src" / "fibrapunto" / "prompts").glob("*.md"),
        *(REPO_ROOT / "contracts").glob("*.yaml"),
    ]
    assert_clean(store, watched)


def test_the_gate_actually_fires(store, tmp_path):
    """A test that only ever passes proves nothing. Plant the secret and
    confirm the boot check refuses to start."""
    leaky = tmp_path / "domain_billing.it.md"
    leaky.write_text(f"Use the token {SECRET} to call billing.", encoding="utf-8")
    with pytest.raises(CredentialStoreLocked, match="appears in"):
        assert_clean(store, [leaky])


# --- 3. the store itself -----------------------------------------------------


def test_the_store_on_disk_is_not_readable_as_plaintext(store):
    raw = Path(store._path).read_bytes()  # noqa: SLF001
    assert SECRET.encode() not in raw, "the credential file is not encrypted"


def test_a_missing_master_key_fails_at_startup_not_mid_conversation(tmp_path):
    """Fatal early rather than during a customer's first question."""
    with pytest.raises(CredentialStoreLocked, match="FIBRAPUNTO_MASTER_KEY"):
        FileSecretStore(path=tmp_path / "creds.enc.json", master_key="")


def test_the_wrong_master_key_is_a_clear_failure(store, tmp_path):
    other = FileSecretStore(path=store._path, master_key=Fernet.generate_key().decode())  # noqa: SLF001
    with pytest.raises(CredentialStoreLocked, match="could not be decrypted"):
        other.get(BILLING_API_TOKEN)
