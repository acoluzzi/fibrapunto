"""Where a case stands: open, in progress, closed (§12.3, §13).

Two things about a handover record are easy to confuse and must not be:

* **complete** — the record carries every field §13 requires. A data-quality
  fact, recomputed in SQL (`crm.v_handover_completeness`), and a §1 metric.
  A refund request that the agent worked properly is *complete* the moment
  it is opened.
* **status** — whether a person has dealt with it yet. Every record exists
  because the conversation needed a human (§12.1), so a record is never done
  just by being well formed. Only the operator moves it to `closed`.

This module owns the second. The rules are small, but they are the rules, and
the two writers (the operator's own action and the retention-offer decision)
go through the same table so they cannot drift.
"""

from __future__ import annotations

from typing import Any, Literal

Status = Literal["open", "in_progress", "closed"]

STATUSES: tuple[Status, ...] = ("open", "in_progress", "closed")

#: Every move an operator may make. A same-state request is not here: it is
#: answered as a no-op by `check`, so a double click is harmless.
ALLOWED: frozenset[tuple[Status, Status]] = frozenset(
    {
        ("open", "in_progress"),  # take
        ("open", "closed"),  # close without taking: fine for a one-liner
        ("in_progress", "closed"),  # close
        ("in_progress", "open"),  # release back to the queue
        ("closed", "in_progress"),  # reopen — §13 watches this rate
    }
)


class TransitionRefused(Exception):
    """Why the move is not allowed, in words the operator can act on."""


def check(current: str, target: str, context: dict[str, Any] | None = None) -> bool:
    """True when the move should be written, False when it is a no-op.

    Raises TransitionRefused otherwise. `context` is the record's
    `collected_context`: an offer the customer accepted is a decision that
    belongs to the retention team, and closing the case around it would leave
    the customer waiting for an answer nobody is going to give.
    """
    if target not in STATUSES:
        raise TransitionRefused(f"unknown status {target!r}")
    if current == target:
        return False
    if (current, target) not in ALLOWED:
        raise TransitionRefused(f"a case cannot go from {current} to {target}")
    offer = (context or {}).get("retention_offer") or {}
    if target == "closed" and offer.get("status") == "accepted":
        raise TransitionRefused(
            "the customer accepted an offer that has not been decided; confirm or decline it first"
        )
    return True


def after_offer_decision(confirmed: bool) -> Status:
    """What a colleague's decision on an accepted offer does to the case.

    Confirmed: the offer is applied and the customer told, so the case is
    done. Declined: the customer still wants what they came for (a
    cancellation, usually), and a colleague has now touched the case — it is
    in progress, not back in the untouched queue and not closed.
    """
    return "closed" if confirmed else "in_progress"
