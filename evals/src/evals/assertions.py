"""Deterministic verdicts, computed from the trace (§14).

The judge is a second opinion, not the measurement. Four of §1's six metrics are
computable from facts the agent already reports — which domain fired, which
capabilities ran with which outcomes — and code is strictly better than a model
at all four:

    "The bias is real and is stated. The judge is a model, and a model grading
    conversations is not a ground truth."

So the model is asked only what a model is uniquely able to answer: register,
tone, whether the Italian is any good, whether an escalation was warranted.
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

# Figures an agent may only state if it verified them. Deliberately the same
# shapes the grounding guard uses — if the guard and the harness disagreed
# about what counts as a figure, one of them would be measuring the wrong thing.
_AMOUNT = re.compile(r"(?:€\s*)?(\d{1,3}(?:[,\s]\d{3})*[.,]\d{2})(?:\s*(?:€|eur|euros?))?", re.I)
_MONTHS = "january|february|march|april|may|june|july|august|september|october|november|december"
# Both orderings, because English writes a date both ways round and the agent
# uses both. Matching one would leave the other unchecked.
_DATE = re.compile(
    rf"\b(?:\d{{1,2}}(?:st|nd|rd|th)?\s+(?:{_MONTHS})|(?:{_MONTHS})\s+\d{{1,2}})\b", re.I
)

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
        verdict.fail(f"nessun passaggio a {switch_to!r}; domini visti: {domains}")

    # --- no invention (§1) ---------------------------------------------------
    # Set-difference, not a judge's opinion: a figure in the reply that is not
    # in a capability result and not in the customer's words was invented.
    if expect.get("must_not_state_figures"):
        figures = _AMOUNT.findall(agent_text) + _DATE.findall(agent_text)
        customer_text = " ".join(turn["user"] for turn in case["turns"])
        customer_figures = set(_AMOUNT.findall(customer_text)) | set(_DATE.findall(customer_text))
        invented = [f for f in figures if f not in customer_figures]
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
