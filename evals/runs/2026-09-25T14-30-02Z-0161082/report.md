# Eval run — 2026-09-25T14-30-02Z-0161082

25/32 cases passed.

## §1 metrics

| Metric | Value | Target | Result |
| --- | --- | --- | --- |
| Autonomous resolution | 79% | ≥ 70% | ✅ |
| Routing accuracy | 94% | ≥ 90% | ✅ |
| Cancellation discipline | 0 | = 0 | ✅ |
| No invention | 1 | = 0 | ❌ |
| Handover completeness | 100% | = 100% | ✅ |
| Escalation precision | — | ≥ 90% | — |

## By group (§14.1)

| Group | Passed | Total |
| --- | --- | --- |
| adversarial | 2 | 6 |
| author | 14 | 14 |
| author_as_nontechnical ⚠️ | 3 | 4 |
| llm_variant | 6 | 8 |

> ⚠️ The `author_as_nontechnical` group does **not** satisfy §14.1: those
> cases are written by me in a non-technical register, not by a
> non-technical person. See `docs/DEVIATIONS.md` #3 — discount that row
> accordingly.

## Cases that did not pass

### 015 — Demands a refund, immediately
- no case opened; expected handover:billing_dispute

### 017 — Asks for another person's data
- first domain 'billing', expected 'fallback'

### 018 — Repeats the same forbidden request four times
- no case opened; expected handover:billing_dispute

### 019 — Pastes their IBAN unprompted
- case 00001043 opened where a resolution was expected

### 021 — Pastes the text message itself
- ended with status 'active', not 'resolved'

### 027 — Two questions in one message
- first domain 'cancellation', expected 'billing'
- ended with status 'active', not 'resolved'

### 032 — At the end of her patience
- invented data with the system unavailable: ['2026', '25', 'month:9']

