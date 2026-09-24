"""Typed capability outcomes (§5, §5.1, §17.4, §17.6).

    "No capability may raise an untyped error into the reasoning layer. Every
    failure must arrive as a named outcome the instructions can refer to. This
    is the single most important implementation rule in the document, and it is
    not a style preference."

Two design choices worth defending out loud:

1. **Every failure outcome carries its own `guidance`.** §5.2 gives the verbatim
   description for `notFound` — "tell the customer plainly that you cannot find
   the contract, ask them to check it, and offer a human. Do not invent contract
   data." That instruction is delivered here, in the payload, rather than in a
   system prompt the model read three thousand tokens earlier. §5.1's argument is
   that a named outcome makes the relevant instruction apply "literally and
   unambiguously"; putting the instruction *next to* the failure makes it apply
   adjacently too.

2. **`rejected` is a deliberate addition to §5's enumeration.** §5.2 requires the
   contract number to "come from the customer verbatim". That has to be enforced
   in code, and enforcement must return something typed — folding it into
   `notFound` would be the agent reporting what was supposed to happen rather
   than what did, which is the §12.4 principle inverted. Recorded in
   docs/DEVIATIONS.md rather than hidden.

Guidance text is in the agent's own output language, because these strings are
prompt: they are read by the reasoning layer at the moment the failure arrives,
and a mid-prompt language switch is the kind of thing that shows up in the reply.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, Field

from fibrapunto.capabilities.base import CustomerVisible, InternalOnly


class ContractMatch(BaseModel):
    """One candidate contract when the legacy system returns namesakes."""

    contract_number: str = CustomerVisible("The contract number that was found.")
    service_address: str = CustomerVisible(
        "Service address. The only criterion allowed for having the customer "
        "choose between several contracts: never the first or last name."
    )


# --- billing lookup ----------------------------------------------------------


class BillingFound(BaseModel):
    """The lookup succeeded. These are the only billing figures that may ever
    appear in a reply — the grounding guard rejects any other."""

    outcome: Literal["found"] = "found"

    status: Literal["paid", "failed", "pending"] = CustomerVisible(
        "Status of this month's direct debit."
    )
    failure_reason: str | None = CustomerVisible(
        "Why the direct debit was refused, already in plain words. "
        "Pass it on without banking jargon and without codes."
    )
    amount: Decimal | None = CustomerVisible("The charge in euros. Write it as €29.90.")
    invoice_month: str | None = CustomerVisible("Billing month, format YYYY-MM.")
    next_retry_date: date | None = CustomerVisible(
        "Date of the next retry attempt. State it whenever a direct debit has failed."
    )
    payment_link: str | None = CustomerVisible(
        "Secure link to pay now by card. This is the alternative to offer the "
        "customer: never ask for card details in chat."
    )
    lock_in_until: date | None = CustomerVisible(
        "End of the lock-in period. If it is null do NOT stay silent: say "
        "explicitly that there are no constraints."
    )
    remaining_instalments: int | None = CustomerVisible(
        "Router instalments still to pay. Zero means none remaining."
    )

    source_system: str = InternalOnly("Which system the data came from. Never mention it.")
    fetched_at: str = InternalOnly("When the data was read. Internal use.")


class BillingNotFound(BaseModel):
    """No contract with this number exists in billing.

    Reached both by a mistyped number and by the contract that exists in the CRM
    and not in billing (§6.1) — the agent cannot tell the two apart, which is
    exactly why it must not guess.
    """

    outcome: Literal["notFound"] = "notFound"
    guidance: str = InternalOnly(
        "Operating instruction for the reasoning layer. Never show it to the customer.",
        default=(
            "No contract exists with this number. Tell the customer plainly, "
            "ask them to check the number, and offer to open a case with a "
            "colleague. Invent NO contract data of any kind: no amounts, no "
            "dates, no status."
        ),
    )


class BillingServiceUnavailable(BaseModel):
    """The billing system did not answer, or answered something that does not
    match its own published contract.

    §15: "If the capability leaks an exception instead of a typed outcome, the
    model fills the gap. This is why §5.1 exists."
    """

    outcome: Literal["serviceUnavailable"] = "serviceUnavailable"
    guidance: str = InternalOnly(
        "Operating instruction for the reasoning layer. Never show it to the customer.",
        default=(
            "The billing system is not responding. Tell the customer plainly, "
            "give NO amount and NO date, and offer to open a case. Do not "
            "promise timescales that are not yours to promise."
        ),
    )
    incident_id: str = InternalOnly(
        "Identifier for correlating with the technical log. Never show it."
    )


class BillingMultipleMatches(BaseModel):
    """More than one contract matched. §4.1 instruction 3: never pick the first."""

    outcome: Literal["multipleMatches"] = "multipleMatches"
    matches: list[ContractMatch] = CustomerVisible(
        "The contracts found. Ask the customer which is theirs by service "
        "address. Never choose yourself, and never use the name."
    )
    guidance: str = InternalOnly(
        "Operating instruction for the reasoning layer. Never show it to the customer.",
        default=(
            "If after one clarifying question the customer still has not "
            "identified the contract, do not insist: offer a colleague."
        ),
    )


class BillingRejected(BaseModel):
    """The call was refused before it left the process — a precondition failed.

    Not in §5's enumeration; see the module docstring and docs/DEVIATIONS.md.
    """

    outcome: Literal["rejected"] = "rejected"
    reason: Literal["contract_number_not_from_customer", "format_invalid"] = InternalOnly(
        "Why the call was refused. Internal use."
    )
    guidance: str = InternalOnly(
        "Operating instruction for the reasoning layer. Never show it to the customer.",
        default=(
            "You do not have a valid contract number from the customer. Ask "
            "for one, in the format FP-YYYY-NNNNNN. If the format is wrong a "
            "second time, offer a colleague rather than insisting."
        ),
    )


BillingLookupResult = Annotated[
    BillingFound
    | BillingNotFound
    | BillingServiceUnavailable
    | BillingMultipleMatches
    | BillingRejected,
    Field(discriminator="outcome"),
]

# Every outcome literal across every capability. guardrails/output_visibility.py
# builds its blocklist from this, so a new outcome is covered the moment it is
# declared rather than when someone remembers to update a list.
BILLING_OUTCOMES: frozenset[str] = frozenset(
    {"found", "notFound", "serviceUnavailable", "multipleMatches", "rejected"}
)


# --- create handover record (§5, §13) ----------------------------------------


# The closing sentence — case number, who picks it up, by when — is a
# commitment, so it is templated (escalation/engine.py) and appended by code
# after the model's reply. The model is told not to write it; if it does anyway,
# its version is removed before the template goes on.
_CLOSING_IS_TEMPLATED = (
    "The case is registered. The sentence giving the customer the case number "
    "and when they will be contacted is added automatically after your reply: "
    "do NOT write the case number, the response time, or that someone will "
    "contact them. Say only what else the customer needs to know."
)


class HandoverCreated(BaseModel):
    """The record exists and carries a reference the customer can be given."""

    outcome: Literal["created"] = "created"
    reference: str = CustomerVisible(
        "The case number, assigned by the system. Given to the customer by the closing template."
    )
    queue_label: str = CustomerVisible(
        "The team picking it up, in words the customer will understand."
    )
    response_window: str = CustomerVisible(
        "When the customer will be contacted by. Do not promise anything different."
    )
    guidance: str = InternalOnly(
        "Operating instruction for the reasoning layer.", default=_CLOSING_IS_TEMPLATED
    )


class HandoverCreatedWithoutContractLink(BaseModel):
    """§13: "When the contract cannot be linked. The record is created anyway,
    without the link, carrying an explicit note that the contract was not found.
    An incomplete record beats no record: the customer has still asked for
    something, and the company has still been told."
    """

    outcome: Literal["createdWithoutContractLink"] = "createdWithoutContractLink"
    reference: str = CustomerVisible("The case number.")
    queue_label: str = CustomerVisible("The team picking it up.")
    response_window: str = CustomerVisible("When they will be contacted by.")
    guidance: str = InternalOnly(
        "Operating instruction for the reasoning layer.",
        default=(
            "The case was opened but could not be linked to a contract. The "
            "closing sentence tells the customer a colleague will ask them to "
            "confirm the number. " + _CLOSING_IS_TEMPLATED
        ),
    )


class HandoverFailed(BaseModel):
    """§12.4: "The agent must not state a reference number it did not receive.

    It says plainly that it could not register the request, gives the
    alternative channel, and does not claim that anything has been recorded."

    There is no `reference` field on this model. Not empty, not None — absent,
    so there is nothing for the reasoning layer to mistake for one.
    """

    outcome: Literal["failed"] = "failed"
    missing_fields: list[str] = InternalOnly(
        "Required fields that were not collected, if that is why it failed.",
        default_factory=list,
    )
    guidance: str = InternalOnly(
        "Operating instruction for the reasoning layer.",
        default=(
            "The request could NOT be registered. Tell the customer plainly. "
            "Give NO case number: none exists. Point them to the alternative "
            "channel (the freephone line or the customer area) and do not say "
            "that anything has been picked up."
        ),
    )
    incident_id: str = InternalOnly(
        "Identifier for the technical log. Never show it to the customer.",
        default="",
    )


class HandoverAwaitingConfirmation(BaseModel):
    """§4.1 instruction 5: the case is not opened until the customer confirms.

    Returned instead of opening a billing dispute when the customer has not yet
    agreed to the one-sentence summary. The summary is stored in
    `ConversationState.pending_confirmation`; the customer's "yes" on the next
    turn is what opens the case — by code, from the stored summary, so the
    model cannot skip the read-back by being persuasive about it.
    """

    outcome: Literal["awaitingConfirmation"] = "awaitingConfirmation"
    summary: str = CustomerVisible("The one-sentence summary to read back to the customer.")
    guidance: str = InternalOnly(
        "Operating instruction for the reasoning layer.",
        default=(
            "No case has been opened yet. Read this summary back to the "
            "customer in one sentence and ask them to confirm it is correct. "
            "Do NOT say that a case has been opened or that anyone will "
            "contact them."
        ),
    )


HandoverResult = Annotated[
    HandoverCreated | HandoverCreatedWithoutContractLink | HandoverFailed,
    Field(discriminator="outcome"),
]

HANDOVER_OUTCOMES: frozenset[str] = frozenset(
    {"created", "createdWithoutContractLink", "failed", "awaitingConfirmation"}
)
