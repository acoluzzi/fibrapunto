# Eval run — 2026-09-25T16-14-17Z-a6a8e3a

4/5 cases passed.

## §1 metrics

| Metric | Value | Target | Result |
| --- | --- | --- | --- |
| Autonomous resolution | — | ≥ 70% | — |
| Routing accuracy | 100% | ≥ 90% | ✅ |
| Cancellation discipline | 0 | = 0 | ✅ |
| No invention | 1 | = 0 | ❌ |
| Handover completeness | 100% | = 100% | ✅ |
| Escalation precision | — | ≥ 90% | — |

## By group (§14.1)

| Group | Passed | Total |
| --- | --- | --- |
| adversarial | 1 | 1 |
| author | 3 | 3 |
| llm_variant | 0 | 1 |

## Cases that did not pass

### 023 — Wrong number twice
- invented data with the system unavailable: ['2023']


## Disagreements between the assertions and the judge

§14.2: "the sample read by hand is the audit of the judge".

| Case | Field | Code | Judge |
| --- | --- | --- | --- |
| 008 | invented_data | False | True |
| 023 | invented_data | True | False |
