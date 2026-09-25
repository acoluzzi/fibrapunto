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


def _billing_was_read(context: dict[str, Any]) -> bool:
    """Did a lookup actually return this customer's billing data?

    Keyed on the typed outcome rather than on the display value of
    `billing_status`, which is prose and would drift.
    """
    return any(entry.get("outcome") == "found" for entry in context.get("lookups", ()))


def _required(record_type: str, context: dict[str, Any]) -> tuple[str, ...]:
    """The required fields for this record, given what the conversation produced.

    §13's table describes a *worked* billing dispute: one where the amount was
    read and the customer said what was wrong with it. A billing escalation in
    which the lookup never succeeded is a different shape — the customer has a
    question nobody could answer yet, and there is no figure in existence for
    them to dispute.

    Demanding `disputed_point` there was not strictness, it was a modelling
    error, and it cost the thing §13 cares about most: the record failed to
    validate, so no record was written, so **the company was never told**. That
    is §12.4's worst branch reached through the front door, and it contradicts
    §13's own rule — "an incomplete record beats no record" — which this file's
    sibling applies correctly to the unlinked-contract case.

    Found by running case 005 against a live model; see docs/FRICTION-LOG.md.
    """
    required = _BY_TYPE.get(record_type, ())
    if record_type == "billing_dispute" and not _billing_was_read(context):
        required = tuple(key for key in required if key != "disputed_point")
    return required


def missing_fields(
    record_type: str,
    context: dict[str, Any],
    subject: str,
    stated_reason: str | None = None,
) -> list[str]:
    """Which required fields are absent or empty.

    Must agree with `crm.v_handover_completeness`, which recomputes the same
    rule in SQL for §1's metric. It did not: the view required a cancellation's
    stated reason and this check did not, so a record could be written that the
    metric then counted as incomplete. `stated_reason` is a column, not a
    context key, hence the separate argument.
    """
    missing: list[str] = []

    if not subject or not subject.strip():
        missing.append("subject")

    if record_type == "cancellation_request" and not (stated_reason or "").strip():
        missing.append("stated_reason")

    for key in _ALWAYS_PRESENT:
        if key not in context:
            missing.append(key)

    for key in _ALWAYS_NON_EMPTY + _required(record_type, context):
        value = context.get(key)
        if value is None or value == "" or value == []:
            missing.append(key)

    return missing


def is_complete(
    record_type: str, context: dict[str, Any], subject: str, stated_reason: str | None = None
) -> bool:
    return not missing_fields(record_type, context, subject, stated_reason)


def required_for(record_type: str) -> tuple[str, ...]:
    return _ALWAYS_NON_EMPTY + _ALWAYS_PRESENT + _BY_TYPE.get(record_type, ())
