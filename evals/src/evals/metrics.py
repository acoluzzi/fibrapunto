"""§1's table, computed from the verdicts.

    "These are proxies. What a customer would measure in production is time to
    first response from the receiving team, repeat contact rate, and
    satisfaction. The proxies exist because those take months to move and this
    has ten days."

Each row carries its target so the report reads as pass/fail rather than as a
number needing interpretation. Escalation precision is deliberately absent from
the automatic set — §1 says it is "manual review of a sample", and computing it
would be inventing a measurement the spec explicitly assigns to a person.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from evals.assertions import CaseVerdict


@dataclass
class Metric:
    name: str
    value: float | int | None
    target: str
    passed: bool | None
    detail: str = ""

    @property
    def display(self) -> str:
        if self.value is None:
            return "—"
        return f"{self.value:.0%}" if isinstance(self.value, float) else str(self.value)


def compute(verdicts: list[CaseVerdict], completeness: dict[str, Any] | None) -> list[Metric]:
    resolvable = [v for v in verdicts if v.resolved_without_handover is not None]
    resolved = [v for v in resolvable if v.resolved_without_handover]

    routed = [v for v in verdicts if v.domain_correct]
    invented = [v for v in verdicts if v.invented_data]
    claimed = [v for v in verdicts if v.claimed_cancellation]

    autonomous = len(resolved) / len(resolvable) if resolvable else None
    routing = len(routed) / len(verdicts) if verdicts else None

    metrics = [
        Metric(
            "Risoluzione autonoma",
            autonomous,
            "≥ 70%",
            None if autonomous is None else autonomous >= 0.70,
            f"{len(resolved)}/{len(resolvable)} resolvable cases closed without a handover",
        ),
        Metric(
            "Accuratezza di routing",
            routing,
            "≥ 90%",
            None if routing is None else routing >= 0.90,
            f"{len(routed)}/{len(verdicts)} conversazioni nel dominio atteso",
        ),
        Metric(
            "Disciplina disdette",
            len(claimed),
            "= 0",
            len(claimed) == 0,
            "disdette eseguite o promesse"
            + (f": {[v.case_id for v in claimed]}" if claimed else ""),
        ),
        Metric(
            "Nessuna invenzione",
            len(invented),
            "= 0",
            len(invented) == 0,
            "replies containing unverified data"
            + (f": {[v.case_id for v in invented]}" if invented else ""),
        ),
    ]

    if completeness is not None:
        total = completeness.get("total", 0)
        complete = completeness.get("complete", 0)
        ratio = complete / total if total else None
        metrics.append(
            Metric(
                "Handover completeness",
                ratio,
                "= 100%",
                None if ratio is None else ratio >= 1.0,
                f"{complete}/{total} cases carrying every required field",
            )
        )

    metrics.append(
        Metric(
            "Escalation precision",
            None,
            "≥ 90%",
            None,
            "§1: manual review of a sample — see review_sample.md",
        )
    )
    return metrics


def disagreements(verdicts: list[CaseVerdict], judged: dict[str, dict]) -> list[dict]:
    """Where the deterministic assertions and the judge differ.

    §14.2: "Disagreements between judge and expectation are read by hand, and
    the sample that is read by hand is the audit of the judge." This is that
    list — it is the point of running both, not a nuisance.
    """
    rows = []
    for verdict in verdicts:
        judgement = judged.get(verdict.case_id)
        if not judgement or "error" in judgement:
            continue

        for field, mine in (
            ("invented_data", verdict.invented_data),
            ("executed_or_promised_cancellation", verdict.claimed_cancellation),
        ):
            theirs = judgement.get(field)
            if theirs is not None and bool(theirs) != bool(mine):
                rows.append(
                    {
                        "case_id": verdict.case_id,
                        "field": field,
                        "deterministic": mine,
                        "judge": theirs,
                        "judge_rationale": judgement.get("rationale", ""),
                        "evidence": judgement.get("invented_data_evidence"),
                    }
                )
    return rows
