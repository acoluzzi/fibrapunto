# Eval run — 2026-09-25T13-09-08Z-89ff5a1

0/3 cases passed.

## §1 metrics

| Metric | Value | Target | Result |
| --- | --- | --- | --- |
| Autonomous resolution | — | ≥ 70% | — |
| Routing accuracy | 100% | ≥ 90% | ✅ |
| Cancellation discipline | 0 | = 0 | ✅ |
| No invention | 0 | = 0 | ✅ |
| Handover completeness | — | = 100% | — |
| Escalation precision | — | ≥ 90% | — |

## By group (§14.1)

| Group | Passed | Total |
| --- | --- | --- |
| author | 0 | 3 |

## Cases that did not pass

### 001 — Payment-refused text message, resolved without a case
- turn failed: Client error '404 Not Found' for url 'http://localhost:8000/api/sessions/7cee2329-245e-4996-8b76-79cd69bd5a30/turns'
For more information check: https://developer.mozilla.org/en-US/docs/Web/HTTP/Status/404

### 005 — Billing is not responding
- no case opened; expected handover:billing_dispute

### 009 — Cancelling after moving house
- missing create_handover_record → created; saw: [('billing_lookup', 'found')]
- no case opened; expected handover:cancellation_request

