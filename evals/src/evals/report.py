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
        f"{passed}/{len(verdicts)} casi superati.",
        "",
        "## Metriche §1",
        "",
        "| Metrica | Valore | Obiettivo | Esito |",
        "| --- | --- | --- | --- |",
    ]
    for metric in metrics:
        mark = "—" if metric.passed is None else ("✅" if metric.passed else "❌")
        lines.append(f"| {metric.name} | {metric.display} | {metric.target} | {mark} |")

    lines += [
        "",
        "## Per gruppo (§14.1)",
        "",
        "| Gruppo | Superati | Totale |",
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
            "> ⚠️ Il gruppo `author_as_nontechnical` **non** soddisfa §14.1: quei casi",
            "> sono scritti da me in registro non tecnico, non da una persona non",
            "> tecnica. Vedi `docs/DEVIATIONS.md` §3 — il risultato di quella riga va",
            "> scontato di conseguenza.",
        ]

    failures = [v for v in verdicts if not v.passed]
    if failures:
        lines += ["", "## Casi non superati", ""]
        for verdict in failures:
            lines.append(f"### {verdict.case_id} — {verdict.title}")
            lines += [f"- {failure}" for failure in verdict.failures]
            lines.append("")

    if audit:
        lines += [
            "",
            "## Disaccordi fra asserzioni e giudice",
            "",
            "§14.2: «il campione letto a mano è l'audit del giudice».",
            "",
            "| Caso | Campo | Codice | Giudice |",
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
        "# Campione per la revisione manuale",
        "",
        "§1 misura la *precisione delle escalation* come «revisione manuale di un",
        "campione». Per ogni pratica qui sotto, un revisore scrive se l'escalation",
        "era giustificata. Il risultato va in `reviewed.yaml`.",
        "",
    ]
    for verdict in escalated[:10]:
        lines += [
            f"## {verdict.case_id} — {verdict.title}",
            f"- pratica: {verdict.handover_reference}",
            "- giustificata: <!-- sì / no -->",
            "- nota: <!-- -->",
            "",
        ]
    if not escalated:
        lines.append("_Nessuna escalation in questa run._")
    return "\n".join(lines) + "\n"
