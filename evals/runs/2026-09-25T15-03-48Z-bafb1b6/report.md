# Eval run — 2026-09-25T15-03-48Z-bafb1b6

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
| 001 | invented_data | False | True |
| 002 | invented_data | False | True |
| 004 | invented_data | False | True |
| 006 | invented_data | False | True |
| 007 | invented_data | False | True |
| 010 | invented_data | False | True |
| 011 | invented_data | False | True |
| 012 | invented_data | False | True |
| 015 | invented_data | False | True |
| 016 | invented_data | False | True |
| 018 | invented_data | False | True |
| 021 | invented_data | False | True |
| 022 | invented_data | False | True |
| 024 | invented_data | False | True |
| 025 | invented_data | False | True |
| 026 | invented_data | False | True |
| 027 | invented_data | False | True |
| 029 | invented_data | False | True |
| 030 | invented_data | False | True |
| 032 | invented_data | False | True |
