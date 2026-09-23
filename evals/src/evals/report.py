"""Render a run as Markdown, committed beside the code (§14).

"Results are versioned with the code. The interesting artefact is not the
last run; it is the progression across runs, sitting next to the changes
that caused it."
"""

from __future__ import annotations

from typing import Any

from evals.assertions import CaseVerdict
from evals.metrics import Metric


def render_report(
    run_id: str, metrics: list[Metric], verdicts: list[CaseVerdict], audit: list[dict]
) -> str:
    passed = sum(1 for v in verdicts if v.passed)
    lines = [
        f"# Eval run — {run_id}",
        "",
        f"{passed}/{len(verdicts)} cases passed.",
        "",
        "## §1 metrics",
        "",
        "| Metric | Value | Target | Result |",
        "| --- | --- | --- | --- |",
    ]
    for metric in metrics:
        mark = "—" if metric.passed is None else ("✅" if metric.passed else "❌")
        lines.append(f"| {metric.name} | {metric.display} | {metric.target} | {mark} |")

    lines += [
        "",
        "## By group (§14.1)",
        "",
        "| Group | Passed | Total |",
        "| --- | --- | --- |",
    ]
    groups: dict[str, list[CaseVerdict]] = {}
    for verdict in verdicts:
        groups.setdefault(verdict.source, []).append(verdict)
    for source, items in sorted(groups.items()):
        ok = sum(1 for v in items if v.passed)
        note = " ⚠️" if source == "author_as_nontechnical" else ""
        lines.append(f"| {source}{note} | {ok} | {len(items)} |")

    if "author_as_nontechnical" in groups:
        lines += [
            "",
            "> ⚠️ The `author_as_nontechnical` group does **not** satisfy §14.1: those",
            "> cases are written by me in a non-technical register, not by a",
            "> non-technical person. See `docs/DEVIATIONS.md` #3 — discount that row",
            "> accordingly.",
        ]

    failures = [v for v in verdicts if not v.passed]
    if failures:
        lines += ["", "## Cases that did not pass", ""]
        for verdict in failures:
            lines.append(f"### {verdict.case_id} — {verdict.title}")
            lines += [f"- {failure}" for failure in verdict.failures]
            lines.append("")

    if audit:
        lines += [
            "",
            "## Disagreements between the assertions and the judge",
            "",
            '§14.2: "the sample read by hand is the audit of the judge".',
            "",
            "| Case | Field | Code | Judge |",
            "| --- | --- | --- | --- |",
        ]
        for row in audit:
            lines.append(
                f"| {row['case_id']} | {row['field']} | {row['deterministic']} | {row['judge']} |"
            )

    return "\n".join(lines) + "\n"


def render_review_sample(verdicts: list[CaseVerdict], transcripts: dict[str, Any]) -> str:
    """§1's escalation precision is "manual review of a sample" — so produce the
    sample with empty verdict slots, rather than inventing a number for a metric
    the spec assigns to a person."""
    escalated = [v for v in verdicts if v.handover_reference]
    lines = [
        "# Sample for manual review",
        "",
        '§1 measures *escalation precision* as "manual review of a sample". For',
        "each case below, a reviewer writes whether the escalation was warranted.",
        "The result goes in `reviewed.yaml`.",
        "",
    ]
    for verdict in escalated[:10]:
        lines += [
            f"## {verdict.case_id} — {verdict.title}",
            f"- case: {verdict.handover_reference}",
            "- warranted: <!-- yes / no -->",
            "- note: <!-- -->",
            "",
        ]
    if not escalated:
        lines.append("_No escalations in this run._")
    return "\n".join(lines) + "\n"
