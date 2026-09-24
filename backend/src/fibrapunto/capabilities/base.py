"""Field visibility (§5.2, §17.6).

"Outputs are also marked for whether they may be shown to the customer.
Technical outcome codes are for the reasoning layer and must never be surfaced
in the conversation."

The flag cannot be enforced by withholding the value at the tool boundary — the
model *must* see `outcome: "serviceUnavailable"`, that is the entire point of
§5.1. So it is enforced at the *output* boundary instead, and the guard that
does it is derived from these markers rather than from a hand-maintained list:
adding a new outcome automatically extends the check.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class Visibility(StrEnum):
    internal = "internal"
    """For the reasoning layer only. Must never appear in customer-facing text."""

    customer = "customer"
    """May be repeated to the customer, subject to the grounding guard."""


def CustomerVisible(description: str, **kwargs: Any) -> Any:  # noqa: N802
    """A value the agent may tell the customer.

    The description is written for the model, because §5.2 is
    explicit that the reasoning layer reads these to decide what to do with what
    comes back — "a vague description is not a documentation problem; it is a
    defect that surfaces at runtime as a hallucination".
    """
    return Field(
        description=description,
        json_schema_extra={"visibility": Visibility.customer.value},
        **kwargs,
    )


def InternalOnly(description: str, **kwargs: Any) -> Any:  # noqa: N802
    """A value the reasoning layer may use but must never surface."""
    return Field(
        description=description,
        json_schema_extra={"visibility": Visibility.internal.value},
        **kwargs,
    )


def internal_fields(model: type[BaseModel]) -> frozenset[str]:
    """Field names on `model` marked internal-only.

    Read by guardrails/output_visibility.py to build its check from the models
    themselves, so the guard cannot fall behind the contract.
    """
    names: set[str] = set()
    for name, field in model.model_fields.items():
        extra = field.json_schema_extra
        if isinstance(extra, dict) and extra.get("visibility") == Visibility.internal.value:
            names.add(name)
    return frozenset(names)
