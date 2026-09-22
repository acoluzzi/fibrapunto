"""The credential store (§17.7).

    "Credentials for the billing system live in a credential store held by the
    platform, not in the automation and not in the instructions. The agent does
    not know them and therefore cannot leak them."

"External to the automation and to the instructions" is three testable
properties, not a feeling:

  1. **No credential is a parameter of anything the agent touches.** The
     capability calls `billing_client.lookup(contract_number)`. There is no
     token argument, no config object, no environment read inside the
     capability. The client holds a *handle*; the value is resolved inside the
     transport layer at request time.

  2. **No credential can reach the model.**
     `tests/test_credentials_not_in_prompt.py` serialises an outbound request
     and greps for the secret.

  3. **No credential can be written into an instruction.** `assert_clean()`
     scans the prompt and contract files at startup and refuses to boot if a
     stored secret appears in one.

The backend is a Fernet-encrypted file rather than a real vault. The encrypted
blob is committed so the demo runs from a clean clone; the master key is not.
That is a deliberate scope cut (docs/DECISIONS.md #20): the `SecretStore`
protocol is the seam where an operator's actual vault plugs in, and shipping the
seam is a better answer than half-wiring HashiCorp.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Protocol

from cryptography.fernet import Fernet, InvalidToken
from pydantic import SecretStr

from fibrapunto.settings import REPO_ROOT, get_settings

STORE_PATH = REPO_ROOT / "secrets" / "credentials.enc.json"

BILLING_API_TOKEN = "billing_api_default"
"""The handle. This string may appear anywhere; the value it resolves to may not."""


class CredentialNotFound(KeyError):
    pass


class CredentialStoreLocked(RuntimeError):
    """No master key, or the wrong one. Deliberately fatal at startup rather
    than at the first customer conversation."""


class SecretStore(Protocol):
    def get(self, handle: str) -> SecretStr: ...
    def rotate(self, handle: str, value: str) -> None: ...
    def handles(self) -> list[str]: ...


class FileSecretStore:
    """Fernet-encrypted JSON on disk."""

    def __init__(self, path: Path | None = None, master_key: str | None = None) -> None:
        self._path = path or STORE_PATH
        # `None` means "read it from configuration"; an explicit empty string
        # means "there is no key", which is an error rather than an invitation
        # to fall back. Conflating the two would let a caller that *thought* it
        # was supplying a key silently use the ambient one.
        key = (
            get_settings().fibrapunto_master_key.get_secret_value()
            if master_key is None
            else master_key
        )
        if not key:
            raise CredentialStoreLocked(
                "FIBRAPUNTO_MASTER_KEY is not set. Generate one with:\n"
                '  python -c "from cryptography.fernet import Fernet; '
                'print(Fernet.generate_key().decode())"'
            )
        self._fernet = Fernet(key.encode())

    def get(self, handle: str) -> SecretStr:
        data = self._read()
        if handle not in data:
            raise CredentialNotFound(handle)
        return SecretStr(data[handle])

    def rotate(self, handle: str, value: str) -> None:
        data = self._read()
        data[handle] = value
        self._write(data)

    def handles(self) -> list[str]:
        return sorted(self._read())

    def provider(self, handle: str):
        """A zero-argument callable that resolves the handle at call time.

        This is what a client is constructed with. The distinction matters: the
        client holds a way to *obtain* the credential, not the credential — so a
        rotation takes effect on the next request with no restart, and a client
        object captured in a log or a traceback carries no secret.
        """
        return lambda: self.get(handle).get_secret_value()

    def _read(self) -> dict[str, str]:
        if not self._path.exists():
            return {}
        try:
            return json.loads(self._fernet.decrypt(self._path.read_bytes()))
        except InvalidToken as exc:
            raise CredentialStoreLocked(
                f"{self._path} could not be decrypted with the configured master key."
            ) from exc

    def _write(self, data: dict[str, str]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_bytes(self._fernet.encrypt(json.dumps(data, indent=2).encode()))
        self._path.chmod(0o600)


def assert_clean(store: SecretStore, files: list[Path]) -> None:
    """Refuse to start if a stored secret appears in an instruction or contract.

    §17.7 says the store is external "to the automation **and to the
    instructions**". A prompt file with a token pasted into it satisfies neither
    half, and is the kind of thing that arrives via a hurried debugging session
    and stays for good.
    """
    values = {store.get(handle).get_secret_value() for handle in store.handles()}
    values = {v for v in values if len(v) >= 8}  # avoid matching trivial strings

    for path in files:
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for value in values:
            if value in text:
                raise CredentialStoreLocked(
                    f"A stored credential appears in {path}. Credentials live in "
                    f"the store, never in instructions or contracts (§17.7)."
                )
