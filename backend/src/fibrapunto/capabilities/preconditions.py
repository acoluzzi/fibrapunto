"""Preconditions checked before a capability runs (§5.2, §4.1).

§5.2 gives the verbatim description of the billing-lookup input:

    "The contract number provided by the customer, format FP-YYYY-NNNNNN.
     **Must come from the customer verbatim.**"

A description tells the model what to do. It does not stop the model doing
otherwise — and §4.1 instruction 2 is explicit that the lookup happens only
after the customer has supplied a number, and §5.2's capability description says
"never call it with a guessed or reconstructed number".

So the rule is enforced here, in code, by checking that the string actually
appears in something the customer typed. A model that hallucinates a plausible
contract number gets `rejected`, not a lookup.

Precondition failures return outcomes rather than raising, for the §5.1 reason:
the reasoning layer needs a name it has instructions for.
"""

from __future__ import annotations

import re

CONTRACT_NUMBER_PATTERN = re.compile(r"\bFP-\d{4}-\d{6}\b")

# Deliberately loose: anything the customer might have *meant* as a contract
# number. Used to tell "wrong format" apart from "never mentioned one", because
# §4.1 instruction 2 prescribes different behaviour for each — re-ask once for a
# bad format; ask for the first time if none was given.
CONTRACT_NUMBER_ATTEMPT = re.compile(r"\bFP[-\s]?\d{2,4}[-\s]?\d{3,8}\b", re.IGNORECASE)


def is_well_formed(contract_number: str) -> bool:
    return bool(CONTRACT_NUMBER_PATTERN.fullmatch(contract_number.strip()))


def extract_contract_numbers(text: str) -> list[str]:
    """Well-formed contract numbers appearing in `text`."""
    return CONTRACT_NUMBER_PATTERN.findall(text.upper())


def looks_like_an_attempt(text: str) -> bool:
    """True if the customer seems to have tried to give a contract number.

    Distinguishes §4.1's "ask once more" from "ask for the first time" — and
    the distinction matters for the escalation trigger that fires when the
    format is still wrong after one re-ask (§12.1).
    """
    return bool(CONTRACT_NUMBER_ATTEMPT.search(text))


def came_from_customer(contract_number: str, customer_turns: list[str]) -> bool:
    """Did the customer actually type this number?

    The check that makes §5.2's "must come from the customer verbatim" true
    rather than requested. Whitespace and case are normalised — a customer
    writing `fp-2023-048812` has still supplied it — but nothing else is: a
    number the model assembled from a partial one, or recalled from another
    conversation, does not match.
    """
    wanted = contract_number.strip().upper()
    return any(wanted in turn.upper() for turn in customer_turns)
