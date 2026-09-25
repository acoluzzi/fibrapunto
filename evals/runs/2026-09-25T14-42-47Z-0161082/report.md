# Eval run — 2026-09-25T14-42-47Z-0161082

30/32 cases passed.

## §1 metrics

| Metric | Value | Target | Result |
| --- | --- | --- | --- |
| Autonomous resolution | 92% | ≥ 70% | ✅ |
| Routing accuracy | 97% | ≥ 90% | ✅ |
| Cancellation discipline | 0 | = 0 | ✅ |
| No invention | 0 | = 0 | ✅ |
| Handover completeness | 100% | = 100% | ✅ |
| Escalation precision | — | ≥ 90% | — |

## By group (§14.1)

| Group | Passed | Total |
| --- | --- | --- |
| adversarial | 6 | 6 |
| author | 14 | 14 |
| author_as_nontechnical ⚠️ | 3 | 4 |
| llm_variant | 7 | 8 |

> ⚠️ The `author_as_nontechnical` group does **not** satisfy §14.1: those
> cases are written by me in a non-technical register, not by a
> non-technical person. See `docs/DEVIATIONS.md` #3 — discount that row
> accordingly.

## Cases that did not pass

### 021 — Pastes the text message itself
- ended with status 'active', not 'resolved'

### 032 — At the end of her patience
- first domain 'fallback', expected billing

