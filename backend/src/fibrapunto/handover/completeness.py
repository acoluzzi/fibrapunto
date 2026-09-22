"""What makes a record "worked" (§13, §1).

    "A record is 'worked' when all of the following are populated."

§1 measures handover completeness at **100%**, and that target is only
reachable if incompleteness is impossible rather than unlikely. So the check
runs *before* the insert and returns `failed` carrying the missing field names —
the reasoning layer can then go back and collect them, which is a better outcome
than a thin record discovered by the receiving team a day later.

The same check is recomputed independently in SQL (`crm.v_handover_completeness`),
so the metric is not measured by the code path that wrote the row.
"""

from __future__ import annotations

from typing import Any

# Keys every record must carry, whatever its type, AND that must not be empty.
# An empty value here means the case was not actually worked.
_ALWAYS_NON_EMPTY = (
    "what_customer_was_told",
    "escalation_trigger",
)

# Keys that must be *present* but may legitimately be empty.
#
# `lookups` is the case that matters: a customer who asks for a human in their
# first message never triggers one, and "no lookups were attempted" is honest
# information the receiving operator needs — it tells them the case arrives
# unworked and why. Treating an empty list as missing would make that record
# impossible to create, which is §12.4's failure mode arriving through the front
# door: the company would not be told, because the agent had nothing to report.
_ALWAYS_PRESENT = ("lookups",)

# §13's table, by record type.
_BY_TYPE: dict[str, tuple[str, ...]] = {
    "billing_dispute": (
        "billing_status",
        "disputed_point",
    ),
    "cancellation_request": (
        "request_date",
        "tenure",
        "constraints",
    ),
    "technical": ("steps_attempted",),
}


def missing_fields(record_type: str, context: dict[str, Any], subject: str) -> list[str]:
    """Which required fields are absent or empty."""
    missing: list[str] = []

    if not subject or not subject.strip():
        missing.append("subject")

    for key in _ALWAYS_PRESENT:
        if key not in context:
            missing.append(key)

    for key in _ALWAYS_NON_EMPTY + _BY_TYPE.get(record_type, ()):
        value = context.get(key)
        if value is None or value == "" or value == []:
            missing.append(key)

    return missing


def is_complete(record_type: str, context: dict[str, Any], subject: str) -> bool:
    return not missing_fields(record_type, context, subject)


def required_for(record_type: str) -> tuple[str, ...]:
    return _ALWAYS_NON_EMPTY + _ALWAYS_PRESENT + _BY_TYPE.get(record_type, ())
