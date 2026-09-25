# Eval run — 2026-09-25T13-26-01Z-89ff5a1

2/3 cases passed.

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
| author | 2 | 3 |

## Cases that did not pass

### 009 — Cancelling after moving house
- missing create_handover_record → created; saw: [('billing_lookup', 'found'), ('billing_lookup', 'found')]
- no case opened; expected handover:cancellation_request

