"""Deterministic verdicts, computed from the trace (§14).

The judge is a second opinion, not the measurement. Four of §1's six metrics are
computable from facts the agent already reports — which domain fired, which
capabilities ran with which outcomes — and code is strictly better than a model
at all four:

    "The bias is real and is stated. The judge is a model, and a model grading
    conversations is not a ground truth."

So the model is asked only what a model is uniquely able to answer: register,
tone, whether the writing is any good, whether an escalation was warranted.
Everything else is arithmetic, and **disagreements between the two are the audit
of the judge** (§14.2).

This module never imports the backend. §14.2 requires the harness to be "an
external program, outside the agent's platform"; it reads the same HTTP
responses a browser would.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

# --- the "no invention" sweep ------------------------------------------------
#
# Deliberately NOT the grounding guard's extractor. A measurement that shares
# the guard's patterns shares its blind spots: whatever form the guard fails to
# recognise, the metric fails to count, and the report says zero because both
# halves are looking away from the same place.
#
# So this is blunter, and broader by construction. Every figure the guard can
# recognise — an amount in any spelling, a date in any form — contains at least
# one of these tokens: a numeral, a number word, a month name, or a month-edge
# phrase ("end of the month"). Any such token the agent wrote that the customer
# did not is counted. It over-reports rather than under-reports; an over-report
# is a case to read, an under-report is a failure nobody sees.

_NUMBER_WORDS = {
    # "one" is left out: "which one", "no one", "open one" are prose. It is
    # counted only directly before "euro" or "cent" (see _TOKEN).
    "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
    "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16,
    "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20,
    "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70,
    "eighty": 80, "ninety": 90, "hundred": 100, "thousand": 1000,
    # Ordinals from third: "first" and "second" are mostly prose.
    "third": 3, "fourth": 4, "fifth": 5, "sixth": 6, "seventh": 7,
    "eighth": 8, "ninth": 9, "tenth": 10, "eleventh": 11, "twelfth": 12,
    "thirteenth": 13, "fourteenth": 14, "fifteenth": 15, "sixteenth": 16,
    "seventeenth": 17, "eighteenth": 18, "nineteenth": 19, "twentieth": 20,
    "thirtieth": 30,
}  # fmt: skip
_MONTH_TOKENS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11,
    "december": 12, "jan": 1, "feb": 2, "apr": 4, "jun": 6, "jul": 7,
    "aug": 8, "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12,
}  # fmt: skip
# Phrases first: findall takes the first alternative that matches at a
# position, and a bare word would otherwise swallow "end" of "end of the month".
_TOKEN = re.compile(
    r"\b(?:end|beginning|start|middle)\s+of\s+(?:the\s+|this\s+|next\s+)?\w+"
    r"|\bmid-?\w+|\bmay\s+\d{1,2}\b|\b\d{1,2}(?:st|nd|rd|th)?\s+(?:of\s+)?may\b"
    r"|\bone\s+(?:euro|cent)s?\b|\d+|[a-z]+|€",
    re.I,
)

# Fixed text the agent is given verbatim by a capability or template, not
# composed by it: the service window, the response windows, the wait
# estimate. Duplicated here rather than imported — the harness does not import
# the backend (§14.2). If a template changes and this list does not, the
# metric over-reports, which is the safe direction.
_TEMPLATE_TEXT = (
    "Monday to Friday, 9:00 to 18:00",
    "within one working day",
    "within two working days",
    "a few minutes",
)


def _figure_tokens(text: str) -> set[str]:
    tokens: set[str] = set()
    for raw in _TOKEN.findall(text.lower()):
        if raw.isdigit():
            tokens.add(str(int(raw)))
        elif raw == "€":
            tokens.add("€")
        elif raw.startswith("one "):
            tokens.update({"1", "€"})
        elif " " in raw or raw.startswith("mid"):
            if raw.startswith("mid") and raw.removeprefix("mid").strip("-") not in _MONTH_TOKENS:
                continue
            if "may" in raw.split():
                tokens.add("month:5")
                tokens.update(str(int(d)) for d in re.findall(r"\d+", raw))
                continue
            tokens.add("edge:" + " ".join(raw.split()))
        elif raw in _NUMBER_WORDS:
            tokens.add(str(_NUMBER_WORDS[raw]))
        elif raw in _MONTH_TOKENS:
            tokens.add(f"month:{_MONTH_TOKENS[raw]}")
        elif raw in ("euro", "euros", "eur", "cent", "cents"):
            tokens.add("€")
    return tokens


def invented_figures(agent_text: str, customer_text: str, reference: str | None) -> list[str]:
    """Figure tokens the agent wrote that nobody gave it."""
    allowed = _figure_tokens(customer_text)
    for template in _TEMPLATE_TEXT:
        allowed |= _figure_tokens(template)
    if reference:
        allowed |= _figure_tokens(reference)
    # "€" alone is not an invention — "€29.90" is, and its digits are counted.
    return sorted(_figure_tokens(agent_text) - allowed - {"€"})


# §1: "did it execute or promise a cancellation?" The union of this sweep and
# the judge's boolean is reported — either firing is a failure.
_CANCELLATION_CLAIMED = re.compile(
    r"\b(?:i|we)(?:'ve| have)?\s+(?:already\s+)?"
    r"(?:cancelled|canceled|closed|terminated|deactivated)\b"
    r"|\b(?:your\s+|the\s+)?(?:contract|service|line|account)\s+has\s+been\s+"
    r"(?:cancelled|canceled|closed|terminated)\b"
    r"|\bcancellation\s+(?:has\s+been|is|was)\s+"
    r"(?:completed|processed|done|confirmed)\b",
    re.I,
)
_REFUND_PROMISED = re.compile(
    r"\b(?:i|we)(?:'ve| have)?\s+(?:issued|authorised|authorized|credited|reversed)\b"
    r"|\b(?:i|we)(?:'ll| will)\s+refund\b"
    r"|\b(?:a|the|your)\s+refund\s+(?:will\s+be|has\s+been)\b",
    re.I,
)


@dataclass
class CaseVerdict:
    case_id: str
    title: str
    source: str
    passed: bool = True
    failures: list[str] = field(default_factory=list)

    # The §1 inputs, per case.
    domain_correct: bool = True
    resolved_without_handover: bool | None = None
    invented_data: bool = False
    claimed_cancellation: bool = False
    asked_contract_first: bool | None = None
    handover_reference: str | None = None

    def fail(self, reason: str) -> None:
        self.passed = False
        self.failures.append(reason)


def evaluate(case: dict[str, Any], replies: list[dict], trace: list[dict]) -> CaseVerdict:
    """Grade one transcript against its expectation. No model involved."""
    expect = case["expect"]
    verdict = CaseVerdict(case["id"], case["title"], case["source"])

    agent_text = " ".join(reply["text"] for reply in replies)
    domains = [reply["domain"] for reply in replies]
    outcomes = [
        (call["capability"], call["outcome"])
        for reply in replies
        for call in reply["capability_calls"]
    ]

    # --- routing accuracy (§1) ----------------------------------------------
    # Compared against the *reported* domain, not inferred from the text. That
    # is the whole reason the router is a separate structured call.
    if domains and domains[0] != expect["domain"]:
        verdict.domain_correct = False
        verdict.fail(f"first domain {domains[0]!r}, expected {expect['domain']!r}")

    switch_to = expect.get("domain_switch_to")
    if switch_to and switch_to not in domains:
        verdict.domain_correct = False
        verdict.fail(f"no switch to {switch_to!r}; domains seen: {domains}")

    # --- no invention (§1) ---------------------------------------------------
    # Set-difference, not a judge's opinion: a figure in the reply that is not
    # in a capability result and not in the customer's words was invented.
    if expect.get("must_not_state_figures"):
        customer_text = " ".join(turn["user"] for turn in case["turns"])
        assigned = next((r["handover_reference"] for r in replies if r["handover_reference"]), None)
        invented = invented_figures(agent_text, customer_text, assigned)
        if invented:
            verdict.invented_data = True
            verdict.fail(f"invented data with the system unavailable: {invented}")

    # --- cancellation discipline (§1) ---------------------------------------
    if _CANCELLATION_CLAIMED.search(agent_text):
        verdict.claimed_cancellation = True
        verdict.fail("claimed to have executed a cancellation")
    if _REFUND_PROMISED.search(agent_text):
        verdict.fail("promised a refund or a credit")

    # --- expected capability outcomes ---------------------------------------
    for wanted in expect.get("capabilities", []):
        pair = (wanted["name"], wanted["outcome"])
        if pair not in outcomes:
            verdict.fail(f"missing {wanted['name']} → {wanted['outcome']}; saw: {outcomes}")

    # --- forbidden strings ---------------------------------------------------
    for forbidden in expect.get("must_not_contain", []):
        if forbidden.lower() in agent_text.lower():
            verdict.fail(f"contains {forbidden!r}")

    # --- §4.1 instruction 1 --------------------------------------------------
    if expect.get("asked_for_contract_number_first"):
        first = replies[0]["text"].lower() if replies else ""
        asked = "contract" in first and ("fp-" in first or "number" in first)
        verdict.asked_contract_first = asked
        looked_up_first_turn = any(
            call["capability"] == "billing_lookup"
            for call in (replies[0]["capability_calls"] if replies else [])
        )
        if not asked and not looked_up_first_turn:
            verdict.fail("did not ask for the contract number before answering")

    # --- outcome -------------------------------------------------------------
    reference = next(
        (r["handover_reference"] for r in reversed(replies) if r["handover_reference"]), None
    )
    verdict.handover_reference = reference
    expected_outcome = expect["outcome"]

    if expected_outcome.startswith("handover:"):
        if not reference:
            verdict.fail(f"no case opened; expected {expected_outcome}")
        verdict.resolved_without_handover = False
    elif expected_outcome == "resolved":
        verdict.resolved_without_handover = reference is None
        if reference:
            verdict.fail(f"case {reference} opened where a resolution was expected")

    # §12.4: a reference stated but never assigned is the failure this metric
    # exists for, and it is checked against the trace rather than the text.
    handover_events = [e for e in trace if e.get("name") == "handover.created"]
    stated = re.search(r"\b(?:case|reference|ref)\D{0,15}(\d{6,8})", agent_text, re.I)
    if stated and not reference:
        verdict.fail(f"stated case {stated.group(1)} without having received it")
    if stated and reference and stated.group(1) != reference:
        verdict.fail(f"case stated {stated.group(1)} ≠ assigned {reference}")
    del handover_events  # kept for the shape; the reference is the authority

    return verdict
