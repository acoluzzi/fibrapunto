"""Register against the published contract, not against hand-written code (§7).

    "The interface is described in a machine-readable interface definition,
    because the integration layer registers against that description rather than
    against hand-written request code. The schema is the contract between the
    two sides, and it is versioned with the repository."

This is the Salesforce External Services idea, built rather than described: the
committed OpenAPI document is loaded at boot, and **every response is validated
against the schema the contract declares for that status code**.

Why that matters beyond tidiness — it is §5.1 applied at the integration
boundary. If billing starts returning `amount` as a string, or drops
`nextRetryDate`, the failure surfaces *here*, as `serviceUnavailable`, rather
than three layers up as a `ValidationError` or — worse — as a `None` the model
quietly narrates around. A response that does not match the contract is not an
answer, and the reasoning layer never sees it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator
from jsonschema import ValidationError as SchemaValidationError

from fibrapunto.settings import REPO_ROOT

CONTRACT_PATH = REPO_ROOT / "contracts" / "billing-openapi.v1.yaml"


class ContractViolation(Exception):
    """The far side answered something its own contract does not allow.

    Caught by the capability wrapper and mapped to `serviceUnavailable`: from
    the agent's point of view a system that breaks its contract is a system that
    is not responding, and it must invent nothing either way.
    """

    def __init__(self, path: str, status: int, detail: str) -> None:
        super().__init__(f"{path} returned {status} violating its contract: {detail}")
        self.path = path
        self.status = status
        self.detail = detail


class ExternalServiceRegistration:
    """A service registered from its OpenAPI description."""

    def __init__(self, contract_path: Path | None = None) -> None:
        self._path = contract_path or CONTRACT_PATH
        self._document: dict[str, Any] = yaml.safe_load(self._path.read_text(encoding="utf-8"))
        self._validators: dict[tuple[str, int], Draft202012Validator] = {}

    @property
    def version(self) -> str:
        """Semver of the contract. Recorded in the trace so a behaviour change
        can be attributed to an interface change (§14)."""
        return self._document["info"]["version"]

    @property
    def title(self) -> str:
        return self._document["info"]["title"]

    def declares(self, path_template: str, status: int) -> bool:
        operation = self._document["paths"].get(path_template, {}).get("get", {})
        return str(status) in operation.get("responses", {})

    def expected_statuses(self, path_template: str) -> frozenset[int]:
        operation = self._document["paths"].get(path_template, {}).get("get", {})
        return frozenset(int(s) for s in operation.get("responses", {}))

    def validate_response(self, path_template: str, status: int, body: Any) -> None:
        """Raise ContractViolation if `body` is not what the contract promises.

        An *undeclared* status code is itself a violation: §7 enumerates the
        outcomes, and a 500 appearing where the contract says 200/300/404/504 is
        the far side behaving in a way nobody designed for.
        """
        if not self.declares(path_template, status):
            raise ContractViolation(
                path_template,
                status,
                f"status not declared; contract allows "
                f"{sorted(self.expected_statuses(path_template))}",
            )

        validator = self._validator_for(path_template, status)
        if validator is None:
            return  # no schema declared for this status — nothing to check

        try:
            validator.validate(body)
        except SchemaValidationError as exc:
            location = "/".join(str(p) for p in exc.absolute_path) or "(root)"
            raise ContractViolation(path_template, status, f"{location}: {exc.message}") from exc

    def _validator_for(self, path_template: str, status: int) -> Draft202012Validator | None:
        key = (path_template, status)
        if key in self._validators:
            return self._validators[key]

        operation = self._document["paths"][path_template]["get"]
        content = operation["responses"][str(status)].get("content", {})
        schema = content.get("application/json", {}).get("schema")
        if schema is None:
            return None

        # Resolve $ref against the document itself — the schemas live under
        # components/schemas and are referenced, not inlined.
        resolved = {**schema, "$defs": self._document.get("components", {}).get("schemas", {})}
        validator = Draft202012Validator(
            _rewrite_refs(resolved),
            format_checker=Draft202012Validator.FORMAT_CHECKER,
        )
        self._validators[key] = validator
        return validator


def _rewrite_refs(node: Any) -> Any:
    """Point `#/components/schemas/X` at `#/$defs/X`.

    jsonschema resolves `$ref` inside the document it is given; OpenAPI puts its
    schemas somewhere jsonschema does not look. Rewriting is simpler and more
    predictable than installing a custom resolver.
    """
    if isinstance(node, dict):
        return {
            key: (
                value.replace("#/components/schemas/", "#/$defs/")
                if key == "$ref" and isinstance(value, str)
                else _rewrite_refs(value)
            )
            for key, value in node.items()
        }
    if isinstance(node, list):
        return [_rewrite_refs(item) for item in node]
    return node
