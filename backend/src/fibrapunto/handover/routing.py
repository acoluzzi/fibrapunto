"""Record type → queue and priority (§13).

Derived by code, never chosen by the model, and backed by CHECK constraints on
`crm.handover_record` so the pairing cannot be wrong even if someone bypasses
this module.

§13's table, and §1's "cancellation discipline" metric, then stop depending on
the model having been told the right thing.
"""

from __future__ import annotations

from dataclasses import dataclass

from fibrapunto.domains.registry import DomainKey


@dataclass(frozen=True, slots=True)
class Routing:
    record_type: str
    priority: str
    queue: str
    queue_label: str
    """What the customer is told. "a colleague in billing operations", not
    "billing_operations" — §5.2's visibility rule applied to a queue name."""

    response_window: str
    """§12.4: do not promise a response time the company cannot keep."""


_BY_TYPE = {
    "billing_dispute": Routing(
        record_type="billing_dispute",
        priority="medium",
        queue="billing_operations",
        queue_label="a colleague in billing operations",
        response_window="within two working days",
    ),
    "cancellation_request": Routing(
        record_type="cancellation_request",
        priority="high",
        queue="retention",
        queue_label="a colleague in the retention team",
        response_window="within one working day",
    ),
    "technical": Routing(
        record_type="technical",
        priority="medium",
        queue="technical_support",
        queue_label="a colleague in technical support",
        response_window="within two working days",
    ),
}


def for_domain(domain: DomainKey) -> Routing:
    """The routing a handover from this domain gets.

    The domain decides, because §12.1's categorical triggers are properties of
    the subject matter rather than judgements about a particular conversation:
    a cancellation is high priority to Retention whether the customer was calm
    or furious.
    """
    match domain:
        case DomainKey.cancellation:
            return _BY_TYPE["cancellation_request"]
        case DomainKey.modem:
            return _BY_TYPE["technical"]
        case _:
            return _BY_TYPE["billing_dispute"]


def for_type(record_type: str) -> Routing:
    return _BY_TYPE[record_type]
