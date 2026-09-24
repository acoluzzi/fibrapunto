"""The output-boundary guards (§11, §1, §9).

§11's argument is that a guardrail existing at only one layer is fragile, "and
the layer it usually exists at — the prompt — is the softest one". These tests
exercise the hard layers: a reply containing an invented figure is rejected by
code, whatever the prompt said.

The last section is §11's own test methodology, made executable:

    "Layer independence is the point. Each layer is tested by removing the layer
    above it and confirming the behaviour still holds. If instruction-level
    rules are what prevent a privacy failure, layer 1 is wrong."
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from fibrapunto.capabilities.runtime import CapabilityCall
from fibrapunto.domains.registry import DomainKey
from fibrapunto.guardrails import pipeline
from fibrapunto.guardrails.pipeline import GuardName
from fibrapunto.privacy import detectors
from fibrapunto.privacy.detectors import SensitiveKind
from fibrapunto.state.models import (
    BillingFacts,
    ConversationState,
    OpenedCase,
    Turn,
    TurnRole,
)

# A real IBAN shape that passes mod-97, and a Luhn-valid test card.
VALID_IBAN = "IT60X0542811101000000123456"
VALID_CARD = "4111 1111 1111 1111"


def grounded_state() -> ConversationState:
    """A conversation where 29,90 € and 19 September are verified facts."""
    state = ConversationState(current_domain=DomainKey.billing)
    state.collected.contract_number = "FP-2023-048812"
    state.collected.billing = BillingFacts(
        status="failed",
        failure_reason="fondi insufficienti",
        amount=Decimal("29.90"),
        next_retry_date=date(2026, 9, 19),
    )
    state.turns.append(
        Turn(role=TurnRole.customer, text="contratto FP-2023-048812, pagamento respinto")
    )
    return state


def ungrounded_state() -> ConversationState:
    """The billing system did not answer. Nothing is verified."""
    state = ConversationState(current_domain=DomainKey.billing)
    state.turns.append(Turn(role=TurnRole.customer, text="I got a text message"))
    state.capability_calls.append(
        CapabilityCall(
            capability="billing_lookup",
            outcome="serviceUnavailable",
            started_at=date.today(),  # type: ignore[arg-type]
            duration_ms=8000,
            incident_id="abc123def456",
        )
    )
    return state


# --- grounding: §1's "no invention: 0" ---------------------------------------


def test_a_verified_figure_passes():
    state = grounded_state()
    reply = "The charge of €29.90 was refused. Next attempt on 19 September."
    assert pipeline.inspect(reply, state) == []


def test_an_invented_amount_is_blocked():
    """The figure is plausible, correctly formatted, and wrong. Nothing in the
    reply's shape distinguishes it from a real one — which is why the check is
    against the lookup result rather than against the text."""
    state = grounded_state()
    violations = pipeline.inspect("The charge of €24.90 was refused.", state)

    assert violations and violations[0].guard is GuardName.grounding
    assert "24.90" in violations[0].problems[0]


def test_an_invented_date_is_blocked():
    state = grounded_state()
    violations = pipeline.inspect("The next attempt is due on 25 October.", state)
    assert violations[0].guard is GuardName.grounding


def test_with_the_billing_system_down_no_figure_can_be_grounded():
    """§15 row 1, as a structural property rather than an observation.

    With the stub in timeout mode there is no verified figure at all, so *any*
    figure fails. The agent cannot invent one because there is nothing for it
    to be grounded against.
    """
    state = ungrounded_state()
    violations = pipeline.inspect("Your charge of €29.90 was refused on 12 September.", state)
    assert violations[0].guard is GuardName.grounding
    assert len(violations[0].problems) >= 2, "both the amount and the date are invented"


def test_repeating_a_figure_the_customer_gave_is_not_invention():
    """§4.1's dispute example: the customer says their plan is €24.90. Reading
    that back is not a claim about billing — it is quoting the customer."""
    state = grounded_state()
    state.turns.append(Turn(role=TurnRole.customer, text="but my plan is €24.90, not €29.90"))
    assert pipeline.inspect("You say your plan is €24.90. Is that right?", state) == []


def test_ordinary_numbers_in_prose_are_not_figures():
    """ "two working days" must not trip the guard, or every reply fails and
    the guard gets switched off — which is how guards die."""
    state = grounded_state()
    reply = "A colleague will get back to you within two working days. You have 2 instalments left."
    assert pipeline.inspect(reply, state) == []


def test_an_unverified_contract_number_is_blocked():
    state = grounded_state()
    violations = pipeline.inspect("I checked contract FP-2099-123456.", state)
    assert violations[0].guard is GuardName.grounding


# --- promises: §1's "cancellation discipline: 0" -----------------------------


@pytest.mark.parametrize(
    "reply",
    [
        "I have cancelled your contract.",
        "The cancellation has been completed.",
        "Your contract has been closed as requested.",
        "I have issued a refund for the difference.",
        "I will refund the difference.",
        "I have credited the amount.",
        "I have updated your payment method.",
        "I can offer you a discount if you stay with us.",
    ],
)
def test_forbidden_commitments_are_blocked(reply):
    """§1 measures "did it execute **or promise** a cancellation". The tools
    make executing impossible; this covers the promising."""
    violations = pipeline.inspect(reply, grounded_state())
    assert any(v.guard is GuardName.promises for v in violations), reply


def test_declining_to_do_those_things_is_fine():
    state = grounded_state()
    reply = "I can't issue refunds or change amounts, but I can pass your request to a colleague."
    assert not any(v.guard is GuardName.promises for v in pipeline.inspect(reply, state))


def test_a_reference_number_that_does_not_exist_is_blocked():
    """§12.4's backstop. The reference comes from the database; any other number
    presented as one was invented."""
    state = grounded_state()
    violations = pipeline.inspect("I've opened case no. 00009999.", state)
    assert any("does not exist" in p for v in violations for p in v.problems)


def test_the_real_reference_passes():
    state = grounded_state()
    state.collected.handovers["billing_dispute"] = OpenedCase(
        reference="00001042", contract_linked=True
    )
    assert not any(
        v.guard is GuardName.promises
        for v in pipeline.inspect("I've opened case no. 00001042.", state)
    )


def test_every_case_the_conversation_opened_is_real():
    """A dispute and then a cancellation: two cases, and naming either is true."""
    state = grounded_state()
    state.collected.handovers["billing_dispute"] = OpenedCase(
        reference="00001042", contract_linked=True
    )
    state.collected.handovers["cancellation_request"] = OpenedCase(
        reference="00001043", contract_linked=True
    )
    reply = "Your dispute is case no. 00001042 and the cancellation is case no. 00001043."
    assert not any(v.guard is GuardName.promises for v in pipeline.inspect(reply, state))


# --- visibility: §5.2, §17.6 -------------------------------------------------


def test_an_outcome_code_never_reaches_the_customer():
    state = grounded_state()
    violations = pipeline.inspect(
        "The check returned serviceUnavailable, please try again later.", state
    )
    assert any(v.guard is GuardName.visibility for v in violations)


def test_an_internal_queue_name_never_reaches_the_customer():
    """§13 gives the customer "a colleague in billing operations", not
    "billing_operations"."""
    violations = pipeline.inspect(
        "I've sent the case to the billing_operations queue.", grounded_state()
    )
    assert any(v.guard is GuardName.visibility for v in violations)


def test_an_incident_id_never_reaches_the_customer():
    state = ungrounded_state()
    violations = pipeline.inspect("An error occurred, reference abc123def456.", state)
    assert any(v.guard is GuardName.visibility for v in violations)


def test_the_blocklist_is_derived_from_the_models():
    """Adding an outcome must extend the guard automatically — a hand-kept list
    falls behind the contract, and silently."""
    from fibrapunto.guardrails.output_visibility import OUTCOME_LITERALS

    assert {"serviceUnavailable", "notFound", "createdWithoutContractLink"} <= (OUTCOME_LITERALS)


# --- language: §10 criterion 3 -----------------------------------------------


def test_internal_vocabulary_is_flagged():
    """§10 criterion 3, retargeted — see docs/DEVIATIONS.md. The Italian guard
    watched for English technical words leaking in; the English one watches for
    the system's own vocabulary, which is what the model has been reading for
    three thousand tokens and will reach for if the reply gets lazy."""
    violations = pipeline.inspect(
        "The lookup timed out, so I've queued a handover record.", grounded_state()
    )
    assert any(v.guard is GuardName.language for v in violations)


def test_the_over_familiar_register_is_flagged():
    """The base identity forbids chumminess. English has no lei/tu, so the check
    is lexical rather than grammatical — but an older customer greeted with "Hi
    there" is the same error the Italian guard was written for."""
    violations = pipeline.inspect("Hey! No worries, I can sort that for you.", grounded_state())
    assert any(v.guard is GuardName.language for v in violations)


def test_the_customer_facing_queue_label_is_not_flagged():
    """The near-miss that made this list wrong once already: "a colleague in
    billing operations" is the CustomerVisible queue_label and must pass, while
    the internal `billing_operations` must not."""
    reply = "A colleague in billing operations will get back to you within two working days."
    assert not any(v.guard is GuardName.language for v in pipeline.inspect(reply, grounded_state()))


# --- the pipeline ------------------------------------------------------------


def test_a_clean_reply_passes_through_untouched():
    state = grounded_state()
    reply = "The charge of €29.90 was refused for insufficient funds."
    result = pipeline.resolve(reply, state)
    assert result.clean and result.text == reply and not result.substituted


def test_the_repair_instruction_names_the_specific_problem():
    """ "rewrite without inventing anything" produces a differently invented
    reply. The instruction has to give the model something to act on."""
    state = grounded_state()
    violations = pipeline.inspect("The amount is €24.90.", state)
    instruction = pipeline.repair_instruction(violations)
    assert "24.90" in instruction


def test_a_failed_repair_substitutes_rather_than_sending():
    """§16 accepts model variance on reading the customer. It does not accept
    variance in what the company says."""
    state = grounded_state()
    bad = "The charge of €24.90 was refused."
    result = pipeline.resolve(bad, state, repaired_reply=bad)

    assert result.substituted
    assert "24.90" not in result.text
    assert "colleague" in result.text, "the fallback must still offer a human (§11)"


def test_a_style_only_violation_is_sent_rather_than_substituted():
    """Leaked internal vocabulary is worse writing than a clean reply and far
    better than a canned refusal. Substituting for it would make the guard more
    damaging than the thing it guards against."""
    state = grounded_state()
    reply = "The lookup shows your charge of €29.90 did not go through."
    result = pipeline.resolve(reply, state, repaired_reply=reply)

    assert not result.substituted
    assert result.text == reply
    assert result.violations


# --- §9: sensitive data ------------------------------------------------------


def test_an_iban_is_detected_and_redacted():
    text = f"my iban is {VALID_IBAN}, go ahead and charge it"
    redacted, kinds = detectors.redact(text)

    assert VALID_IBAN not in redacted
    assert "[[REDACTED:IBAN]]" in redacted
    assert kinds == [SensitiveKind.iban]
    assert "go ahead and charge it" in redacted, "the rest of the message survives"


# Every earlier test put the value at the end of the string, or before a comma
# — the only places a greedy pattern cannot overreach. These put it everywhere
# a customer does: mid-sentence, before a word, before an expiry date.
SPACED_IBAN = "IT60 X054 2811 1010 0000 0123 456"


@pytest.mark.parametrize(
    "text",
    [
        f"My IBAN is {VALID_IBAN} thanks",
        f"{VALID_IBAN} is my IBAN",
        f"My IBAN is {VALID_IBAN}",
        f"IBAN {SPACED_IBAN} thanks",
        f"use {SPACED_IBAN} for the direct debit please",
        f"{VALID_IBAN}thanks",
        f"iban: {VALID_IBAN.lower()} ok",
    ],
)
def test_an_iban_is_redacted_wherever_it_sits(text):
    redacted, kinds = detectors.redact(text)
    assert kinds == [SensitiveKind.iban], text
    assert "0123" not in redacted and "0542" not in redacted, redacted


@pytest.mark.parametrize(
    "text",
    [
        f"card {VALID_CARD} 12/27",
        f"card {VALID_CARD} exp 12/27",
        f"card {VALID_CARD} expiry 12/27 cvv 123",
        "card 4111-1111-1111-1111 12/27",
        "card 4111111111111111 12/27",
        f"{VALID_CARD} is the card",
    ],
)
def test_a_card_is_redacted_when_followed_by_its_expiry(text):
    redacted, kinds = detectors.redact(text)
    assert SensitiveKind.card in kinds, text
    assert "1111" not in redacted, redacted


@pytest.mark.parametrize(
    ("text", "kind", "leaked"),
    [
        ("card 4111.1111.1111.1111", SensitiveKind.card, "4111"),
        # A typo breaks Luhn, not the fact that it is someone's card.
        ("card 4111 1111 1111 1112", SensitiveKind.card, "4111"),
        # Split across a sentence, or across two messages: each card-shaped
        # fragment is caught on its own.
        ("my card is 4111 1111 1111 and then", SensitiveKind.card, "4111"),
        (
            "four one one one one one one one one one one one one one one one",
            SensitiveKind.card,
            "four one one one",
        ),
        ("IBAN IT60-X054-2811-1010-0000-0123-456", SensitiveKind.iban, "0123"),
        ("IBAN IT60.X054.2811.1010.0000.0123.456 ok", SensitiveKind.iban, "0123"),
        ("the code on the back is 123", SensitiveKind.cvv, "123"),
        ("the 3 digits on the back are 987", SensitiveKind.cvv, "987"),
        (f"card {VALID_CARD} 12/27 987", SensitiveKind.cvv, "987"),
        (f"card {VALID_CARD} exp 12/27, cvv 987", SensitiveKind.cvv, "987"),
        ("login: hunter22", SensitiveKind.password, "hunter22"),
        ("passcode = 9911", SensitiveKind.password, "9911"),
    ],
)
def test_the_forms_customers_actually_use_are_redacted(text, kind, leaked):
    redacted, kinds = detectors.redact(text)
    assert kind in kinds, text
    assert leaked not in redacted, redacted


@pytest.mark.parametrize(
    "text",
    [
        "call me on +39 347 123 4567",
        "the contract is FP-2023-048812",
        "invoice 2026-09-19 for 29.90",
        "the amount is 1.234,56",
        "order 12345678901234567890123",
        "I have one or two questions",
        "case 00001042",
        "I paid 1234 5678 over two years",
        "open 9:00 to 18:00",
        f"card {VALID_CARD} and I owe 29.90",  # the amount is not a CVV
    ],
)
def test_the_wider_net_leaves_ordinary_text_alone(text):
    redacted, kinds = detectors.redact(text)
    assert SensitiveKind.cvv not in kinds
    if VALID_CARD not in text:
        assert kinds == [] and redacted == text, redacted


def test_the_text_around_a_redaction_is_left_intact():
    redacted, _ = detectors.redact(f"card {VALID_CARD} exp 12/27")
    assert redacted == "card [[REDACTED:CARD]] exp 12/27"


def test_a_wrong_checksum_mid_sentence_is_still_left_alone():
    """The scan tries every possible end; none of them may validate by
    accident on an IBAN-shaped string that is not one."""
    text = "reference IT60X0542811101000000123457 thanks"
    assert detectors.redact(text) == (text, [])


def test_a_card_number_is_detected_by_luhn():
    redacted, kinds = detectors.redact(f"the card is {VALID_CARD}")
    assert SensitiveKind.card in kinds
    assert "4111" not in redacted


def test_an_invalid_iban_is_left_alone():
    """The checksum is what stops the guard mangling ordinary text. A redaction
    demo that eats a real sentence discredits the whole pipeline."""
    text = "the case code is IT99X0000000000000000000000"
    redacted, kinds = detectors.redact(text)
    assert kinds == [] and redacted == text


def test_an_ordinary_long_number_is_not_a_card():
    text = "the customer number is 1234567890123456"
    _, kinds = detectors.redact(text)
    assert SensitiveKind.card not in kinds


def test_a_contract_number_is_never_treated_as_sensitive():
    """It is the one identifier the agent is supposed to handle (§4.1)."""
    text = "the contract is FP-2023-048812"
    redacted, kinds = detectors.redact(text)
    assert kinds == [] and redacted == text


# --- §11: layer independence -------------------------------------------------


def test_the_grounding_guard_holds_without_any_prompt():
    """§11: "Each layer is tested by removing the layer above it and confirming
    the behaviour still holds."

    The prompt is not involved here at all — no model, no instructions, no
    domain. An invented figure is rejected by code. If the only thing stopping
    invention were the prompt, this test could not exist.
    """
    state = ungrounded_state()
    assert pipeline.inspect("You have €45.00 outstanding.", state)


def test_the_promises_guard_holds_without_any_prompt():
    assert pipeline.inspect("I have cancelled the contract.", ConversationState())
