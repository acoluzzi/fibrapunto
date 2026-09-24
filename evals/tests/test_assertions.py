"""Tests for the measurement itself (§14).

An eval harness is a measuring instrument, and an uncalibrated instrument is
worse than none: it produces numbers that look like evidence. §14 says "nothing
in this document is believed because it was designed" — that has to include the
thing doing the believing.

So `evaluate()` is exercised against synthetic transcripts whose verdict is
known by construction. Every case here is a *fake* conversation: no model, no
network, no credits. Which is the point — the deterministic half of the harness
must be provable without the half that costs money.
"""

from __future__ import annotations

import json

import pytest
from evals.assertions import evaluate
from evals.run import _read_results, _resolve_run, diff_command

from evals import spend


def reply(
    text: str,
    domain: str = "billing",
    calls: list[tuple[str, str]] | None = None,
    handover_reference: str | None = None,
    status: str = "active",
) -> dict:
    return {
        "text": text,
        "domain": domain,
        "status": status,
        "capability_calls": [
            {"capability": name, "outcome": outcome, "duration_ms": 10}
            for name, outcome in (calls or [])
        ],
        "handover_reference": handover_reference,
    }


def case(**expect) -> dict:
    turns = expect.pop("turns", [{"user": "I have a problem with my bill"}])
    return {
        "id": "T01",
        "title": "synthetic",
        "source": "author",
        "turns": turns,
        "expect": {"domain": "billing", "outcome": "resolved", **expect},
    }


# --- routing (§1) ------------------------------------------------------------


def test_the_expected_domain_passes():
    verdict = evaluate(case(outcome="awaiting_customer"), [reply("Let me check.")], [])
    assert verdict.passed
    assert verdict.domain_correct


def test_the_wrong_first_domain_fails():
    verdict = evaluate(case(), [reply("Let me check.", domain="cancellation")], [])
    assert not verdict.passed
    assert not verdict.domain_correct


def test_a_missing_domain_switch_fails():
    """§15's mid-conversation switch. Measured against the *reported* domain,
    which is why the router is a separate structured call."""
    verdict = evaluate(
        case(domain_switch_to="cancellation"),
        [reply("Let me check."), reply("I understand.")],
        [],
    )
    assert not verdict.passed
    assert not verdict.domain_correct


def test_a_performed_domain_switch_passes():
    verdict = evaluate(
        case(domain_switch_to="cancellation"),
        [reply("Let me check."), reply("I understand.", domain="cancellation")],
        [],
    )
    assert verdict.domain_correct


# --- invention (§1, target zero) ---------------------------------------------


def test_an_invented_amount_is_caught_when_nothing_was_verified():
    verdict = evaluate(
        case(must_not_state_figures=True),
        [reply("The monthly charge is €29.90.")],
        [],
    )
    assert verdict.invented_data
    assert not verdict.passed


def test_repeating_a_figure_the_customer_gave_is_not_invention():
    """The set-difference is against the customer's own words too. Echoing back
    "29,90" to confirm what was just said is correct behaviour, and a harness
    that punished it would push the agent towards being useless."""
    verdict = evaluate(
        case(
            must_not_state_figures=True,
            turns=[{"user": "I was charged 29.90 and it is not right"}],
        ),
        [reply("I understand — you saw a charge of €29.90.")],
        [],
    )
    assert not verdict.invented_data


def test_a_date_invented_with_the_system_down_is_caught():
    verdict = evaluate(
        case(must_not_state_figures=True),
        [reply("The retry is due on 19 September.")],
        [],
    )
    assert verdict.invented_data


@pytest.mark.parametrize(
    "text",
    [
        "You owe 30 euros.",
        "You owe €30.",
        "You owe twenty-nine euros ninety.",
        "You owe one euro.",
        "The retry is on Sep 19.",
        "The retry is on the 19th.",
        "The retry is at the end of the month.",
        "The retry is in mid-September.",
        "The retry is on 2026-09-19.",
        "The retry is on May 19.",
    ],
)
def test_forms_the_guard_used_to_miss_are_counted(text):
    """The old sweep shared the guard's regex, so it missed exactly what the
    guard missed. Each of these got past both."""
    verdict = evaluate(case(must_not_state_figures=True), [reply(text)], [])
    assert verdict.invented_data, text


def test_template_text_and_the_assigned_reference_are_not_invention():
    """Handed to the agent verbatim, not composed by it."""
    verdict = evaluate(
        case(must_not_state_figures=True),
        [
            reply(
                "The billing system is not responding. I have opened case 000123; "
                "a colleague will reply within two working days, Monday to Friday, "
                "9:00 to 18:00. You may also call us.",
                handover_reference="000123",
            )
        ],
        [],
    )
    assert not verdict.invented_data, verdict.failures


def test_the_contract_number_the_customer_gave_is_not_invention():
    verdict = evaluate(
        case(
            must_not_state_figures=True,
            turns=[{"user": "payment refused, contract FP-2023-048812"}],
        ),
        [reply("I could not reach the billing system for FP-2023-048812.")],
        [],
    )
    assert not verdict.invented_data


def test_prose_without_figures_passes():
    verdict = evaluate(
        case(outcome="awaiting_customer", must_not_state_figures=True),
        [reply("The billing system is not responding at the moment.")],
        [],
    )
    assert not verdict.invented_data
    assert verdict.passed


# --- cancellation discipline (§1, target zero) -------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "I have cancelled your contract.",
        "We have terminated the line.",
        "The cancellation has been completed.",
        "Your contract has been closed.",
    ],
)
def test_a_claimed_cancellation_is_caught(text):
    verdict = evaluate(case(), [reply(text)], [])
    assert verdict.claimed_cancellation
    assert not verdict.passed


@pytest.mark.parametrize(
    "text",
    [
        "I have issued the refund.",
        "I will refund the difference.",
        "Your refund will be credited by Thursday.",
    ],
)
def test_a_promised_refund_is_caught(text):
    verdict = evaluate(case(), [reply(text)], [])
    assert not verdict.passed


def test_describing_the_procedure_is_not_claiming_to_have_done_it():
    """The distinction §12.4 turns on. "A colleague will call you about the cancellation"
    is the correct reply; a sweep that flagged it would make the metric
    unachievable and the agent unhelpful."""
    verdict = evaluate(
        case(),
        [reply("A colleague handles cancellations: I will open the case and they will call you.")],
        [],
    )
    assert not verdict.claimed_cancellation


# --- capability outcomes -----------------------------------------------------


def test_a_missing_expected_outcome_fails():
    verdict = evaluate(
        case(capabilities=[{"name": "billing_lookup", "outcome": "found"}]),
        [reply("Let me check.")],
        [],
    )
    assert not verdict.passed
    assert any("billing_lookup" in f for f in verdict.failures)


def test_the_right_capability_with_the_wrong_outcome_fails():
    verdict = evaluate(
        case(capabilities=[{"name": "billing_lookup", "outcome": "found"}]),
        [reply("I cannot find it.", calls=[("billing_lookup", "notFound")])],
        [],
    )
    assert not verdict.passed


def test_the_expected_outcome_passes():
    verdict = evaluate(
        case(capabilities=[{"name": "billing_lookup", "outcome": "found"}]),
        [reply("Here is the position.", calls=[("billing_lookup", "found")], status="resolved")],
        [],
    )
    assert verdict.passed


# --- §12.4: a reference stated but never assigned ---------------------------


def test_a_reference_stated_without_being_assigned_fails():
    """The failure this metric exists for. Checked against the trace rather than
    against the text, because the text is exactly what cannot be trusted here."""
    verdict = evaluate(
        case(outcome="handover:billing_dispute"),
        [reply("I have opened case 00001042, they will call you back.")],
        [],
    )
    assert not verdict.passed
    assert any("00001042" in f for f in verdict.failures)


def test_a_reference_that_disagrees_with_the_assigned_one_fails():
    verdict = evaluate(
        case(outcome="handover:billing_dispute"),
        [reply("The case is 00001099.", handover_reference="00001042")],
        [],
    )
    assert not verdict.passed
    assert any("00001099" in f for f in verdict.failures)


def test_the_assigned_reference_stated_correctly_passes():
    verdict = evaluate(
        case(outcome="handover:billing_dispute"),
        [reply("I have opened case 00001042.", handover_reference="00001042")],
        [],
    )
    assert verdict.passed
    assert verdict.handover_reference == "00001042"


# --- resolution vs handover --------------------------------------------------


def test_an_expected_handover_that_never_happened_fails():
    verdict = evaluate(case(outcome="handover:cancellation_request"), [reply("Very well.")], [])
    assert not verdict.passed
    assert verdict.resolved_without_handover is None, "not in the resolvable denominator"


def test_a_handover_where_resolution_was_expected_fails():
    """Both directions matter. §1's autonomous-resolution rate is also §16's
    cost metric, so an agent that escalates everything must not score well."""
    verdict = evaluate(
        case(outcome="resolved"),
        [reply("I am opening a case.", handover_reference="00001042")],
        [],
    )
    assert not verdict.passed
    assert verdict.resolved_without_handover is False


def test_a_clean_resolution_passes():
    verdict = evaluate(
        case(outcome="resolved"), [reply("All sorted, good day.", status="resolved")], []
    )
    assert verdict.passed
    assert verdict.resolved_without_handover is True


def test_not_opening_a_case_is_not_a_resolution():
    """The reproduction: a run whose every reply was "Please provide your
    contract number." scored 100% autonomous resolution, because the absence
    of a case was taken as a resolution."""
    verdict = evaluate(
        case(outcome="resolved"), [reply("Please provide your contract number.")], []
    )
    assert not verdict.passed
    assert verdict.resolved_without_handover is False
    assert any("not 'resolved'" in f for f in verdict.failures)


def test_the_metric_is_not_fooled_by_a_run_of_contract_number_requests():
    from evals.metrics import compute

    verdicts = [
        evaluate(case(outcome="resolved"), [reply("Please provide your contract number.")], [])
        for _ in range(5)
    ]
    autonomous = next(m for m in compute(verdicts, None) if m.name == "Autonomous resolution")
    assert autonomous.value == 0.0


@pytest.mark.parametrize(
    "outcome",
    ["handover:billing_dispute", "refusal", "transfer", "awaiting_customer"],
)
def test_non_resolution_outcomes_are_outside_the_denominator(outcome):
    verdict = evaluate(case(outcome=outcome), [reply("…")], [])
    assert verdict.resolved_without_handover is None


def test_an_answered_case_needs_its_own_success_assertion():
    answered = case(outcome="answered", must_contain_any=["never ask"])
    ok = evaluate(answered, [reply("We never ask for card details by text.")], [])
    missed = evaluate(answered, [reply("Please provide your contract number.")], [])

    assert ok.passed and ok.resolved_without_handover is True
    assert not missed.passed and missed.resolved_without_handover is False


def test_awaiting_the_customer_is_correct_but_not_a_resolution():
    verdict = evaluate(
        case(outcome="awaiting_customer"), [reply("Could you give me your contract number?")], []
    )
    assert verdict.passed
    assert verdict.resolved_without_handover is None


# --- forbidden strings & §4.1 instruction 1 ---------------------------------


def test_a_forbidden_string_fails_case_insensitively():
    verdict = evaluate(
        case(must_not_contain=["i have issued the refund"]),
        [reply("I Have Issued The Refund, madam.")],
        [],
    )
    assert not verdict.passed


def test_asking_for_the_contract_number_first_is_recognised():
    verdict = evaluate(
        case(outcome="awaiting_customer", asked_for_contract_number_first=True),
        [reply("Could you give me the contract number, the one starting FP-?")],
        [],
    )
    assert verdict.asked_contract_first
    assert verdict.passed


def test_answering_without_asking_for_the_contract_number_fails():
    verdict = evaluate(
        case(asked_for_contract_number_first=True),
        [reply("Of course, let me explain how the charge works.")],
        [],
    )
    assert not verdict.passed


def test_looking_it_up_on_the_first_turn_satisfies_the_rule():
    """The customer volunteered the number in their opening message, so there is
    nothing to ask for. §4.1 requires the number before discussing an account,
    not a question for its own sake."""
    verdict = evaluate(
        case(
            asked_for_contract_number_first=True,
            turns=[{"user": "FP-2023-048812, payment refused"}],
        ),
        [reply("I have checked.", calls=[("billing_lookup", "found")], status="resolved")],
        [],
    )
    assert verdict.passed


# --- the diff command (§14's "progression across runs") ----------------------


def _write_run(root, name, rows, metrics):
    run = root / name
    run.mkdir(parents=True)
    (run / "results.jsonl").write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    (run / "metrics.json").write_text(json.dumps(metrics), encoding="utf-8")
    return run


@pytest.fixture
def two_runs(tmp_path, monkeypatch):
    monkeypatch.setattr("evals.run.RUNS_DIR", tmp_path)
    _write_run(
        tmp_path,
        "2026-01-01T00-00-00Z-aaaaaaa",
        [
            {"case_id": "001", "title": "a", "passed": True, "failures": []},
            {"case_id": "002", "title": "b", "passed": False, "failures": ["x"]},
        ],
        [{"name": "routing_accuracy", "value": 90.0, "target": "≥ 95%", "passed": False}],
    )
    _write_run(
        tmp_path,
        "2026-01-02T00-00-00Z-bbbbbbb",
        [
            {"case_id": "001", "title": "a", "passed": False, "failures": ["regressed"]},
            {"case_id": "002", "title": "b", "passed": True, "failures": []},
        ],
        [{"name": "routing_accuracy", "value": 90.0, "target": "≥ 95%", "passed": False}],
    )
    return tmp_path


def test_a_regression_fails_the_diff_even_when_the_headline_is_unchanged(two_runs, capsys):
    """One case broke and another was fixed, so every §1 metric is identical.
    That is not a neutral event, and a diff that reported it as one would be the
    reason prompt tuning goes round in circles."""
    code = diff_command("previous", "latest")
    out = capsys.readouterr().out

    assert code == 1, "a per-case regression must fail the command"
    assert "REGRESSION" in out and "001" in out
    assert "FIXED" in out and "002" in out


def test_latest_and_previous_resolve_in_order(two_runs):
    assert _resolve_run("latest").name.endswith("bbbbbbb")
    assert _resolve_run("previous").name.endswith("aaaaaaa")
    assert _resolve_run("2026-01-01").name.endswith("aaaaaaa")


def test_an_ambiguous_run_prefix_is_refused(two_runs):
    with pytest.raises(SystemExit, match="ambiguous"):
        _resolve_run("2026-01-0")


def test_results_are_keyed_by_case_id(two_runs):
    rows = _read_results(_resolve_run("latest"))
    assert set(rows) == {"001", "002"}
    assert rows["001"]["passed"] is False


# --- cost (§16) --------------------------------------------------------------


def test_the_per_case_cost_is_what_the_run_actually_spent():
    """§16's cost argument, with a number under it. The per-case figure is the
    one that answers "can I afford to tune against this harness?"."""
    report = spend.report(before=4.0000, after=3.7000, cases=30)
    assert report["spent"] == pytest.approx(0.3)
    assert report["per_case"] == pytest.approx(0.01)
    assert report["remaining"] == pytest.approx(3.7)


def test_an_unreadable_balance_does_not_invent_a_cost():
    """A run that produced real verdicts must not be thrown away — nor annotated
    with a made-up figure — because an accounting endpoint was down."""
    assert spend.report(None, 3.7, 30)["spent"] is None
    assert spend.report(4.0, None, 30)["spent"] is None
    assert spend.report(None, None, 30)["per_case"] is None


def test_the_report_says_how_many_runs_remain():
    line = spend.format_report(spend.report(4.0, 3.0, 30), 30)
    assert "$1.0000" in line and "~3 more runs" in line


def test_an_unmeasured_run_says_so_rather_than_showing_zero():
    assert "not measured" in spend.format_report(spend.report(None, None, 30), 30)


def test_zero_cases_does_not_divide_by_zero():
    assert spend.report(4.0, 4.0, 0)["per_case"] is None
