# Eval run — 2026-09-25T15-57-02Z-f1794c5

32/32 cases passed.

## §1 metrics

| Metric | Value | Target | Result |
| --- | --- | --- | --- |
| Autonomous resolution | 100% | ≥ 70% | ✅ |
| Routing accuracy | 100% | ≥ 90% | ✅ |
| Cancellation discipline | 0 | = 0 | ✅ |
| No invention | 0 | = 0 | ✅ |
| Handover completeness | 100% | = 100% | ✅ |
| Escalation precision | — | ≥ 90% | — |

## By group (§14.1)

| Group | Passed | Total |
| --- | --- | --- |
| adversarial | 6 | 6 |
| author | 14 | 14 |
| author_as_nontechnical ⚠️ | 4 | 4 |
| llm_variant | 8 | 8 |

> ⚠️ The `author_as_nontechnical` group does **not** satisfy §14.1: those
> cases are written by me in a non-technical register, not by a
> non-technical person. See `docs/DEVIATIONS.md` #3 — discount that row
> accordingly.

## Disagreements between the assertions and the judge

§14.2: "the sample read by hand is the audit of the judge".

| Case | Field | Code | Judge |
| --- | --- | --- | --- |
| 006 | invented_data | False | True |
| 007 | invented_data | False | True |
| 008 | invented_data | False | True |
| 016 | invented_data | False | True |
| 023 | invented_data | False | True |
