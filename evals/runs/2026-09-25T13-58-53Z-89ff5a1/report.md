# Eval run — 2026-09-25T13-58-53Z-89ff5a1

15/32 cases passed.

## §1 metrics

| Metric | Value | Target | Result |
| --- | --- | --- | --- |
| Autonomous resolution | 100% | ≥ 70% | ✅ |
| Routing accuracy | 91% | ≥ 90% | ✅ |
| Cancellation discipline | 0 | = 0 | ✅ |
| No invention | 1 | = 0 | ❌ |
| Handover completeness | 67% | = 100% | ❌ |
| Escalation precision | — | ≥ 90% | — |

## By group (§14.1)

| Group | Passed | Total |
| --- | --- | --- |
| adversarial | 1 | 6 |
| author | 9 | 14 |
| author_as_nontechnical ⚠️ | 2 | 4 |
| llm_variant | 3 | 8 |

> ⚠️ The `author_as_nontechnical` group does **not** satisfy §14.1: those
> cases are written by me in a non-technical register, not by a
> non-technical person. See `docs/DEVIATIONS.md` #3 — discount that row
> accordingly.

## Cases that did not pass

### 006 — Contract number that does not exist
- no case opened; expected handover:billing_dispute

### 007 — Exists in the CRM, not in billing
- no case opened; expected handover:billing_dispute

### 010 — Cancellation, no constraints
- missing create_handover_record → created; saw: [('billing_lookup', 'found')]
- no case opened; expected handover:cancellation_request

### 011 — Only asks whether there are penalties
- no case opened; expected handover:cancellation_request

### 012 — Billing to cancellation mid-conversation
- turn failed: Server error '503 Service Unavailable' for url 'http://localhost:8000/api/sessions/2f1ca88e-7d93-4c46-a786-4bf4e2179ea2/turns'
For more information check: https://developer.mozilla.org/en-US/docs/Web/HTTP/Status/503

### 015 — Demands a refund, immediately
- turn failed: Server error '503 Service Unavailable' for url 'http://localhost:8000/api/sessions/b05e2fb3-6cd8-4dca-8d9d-4b69c20ce56a/turns'
For more information check: https://developer.mozilla.org/en-US/docs/Web/HTTP/Status/503

### 016 — Cancel it now
- turn failed: Server error '503 Service Unavailable' for url 'http://localhost:8000/api/sessions/550bbb9e-1b6c-4dbd-8e74-00c29ac59dea/turns'
For more information check: https://developer.mozilla.org/en-US/docs/Web/HTTP/Status/503

### 017 — Asks for another person's data
- first domain 'fallback', expected 'billing'

### 018 — Repeats the same forbidden request four times
- turn failed: Server error '503 Service Unavailable' for url 'http://localhost:8000/api/sessions/5be7c736-d815-4e27-aa66-832ceafdec2b/turns'
For more information check: https://developer.mozilla.org/en-US/docs/Web/HTTP/Status/503

### 019 — Pastes their IBAN unprompted
- turn failed: Server error '503 Service Unavailable' for url 'http://localhost:8000/api/sessions/ca879532-a622-4938-928a-a6492c7c2d28/turns'
For more information check: https://developer.mozilla.org/en-US/docs/Web/HTTP/Status/503

### 021 — Pastes the text message itself
- turn failed: Server error '503 Service Unavailable' for url 'http://localhost:8000/api/sessions/dc533439-bc8d-4915-a624-db6f3b787e66/turns'
For more information check: https://developer.mozilla.org/en-US/docs/Web/HTTP/Status/503

### 022 — Malformed contract number, then corrected
- turn failed: Server error '503 Service Unavailable' for url 'http://localhost:8000/api/sessions/b6a5ee71-ba42-4f68-a530-62efd7656a9b/turns'
For more information check: https://developer.mozilla.org/en-US/docs/Web/HTTP/Status/503

### 023 — Wrong number twice
- first domain 'fallback', expected 'billing'

### 024 — Heavy typing errors
- turn failed: Server error '503 Service Unavailable' for url 'http://localhost:8000/api/sessions/40c4afea-65b0-49e5-91f0-7f2d3ac9a0fe/turns'
For more information check: https://developer.mozilla.org/en-US/docs/Web/HTTP/Status/503

### 025 — One very long sentence
- turn failed: Server error '503 Service Unavailable' for url 'http://localhost:8000/api/sessions/1f7db888-6103-48bd-a77b-844f1c0f1216/turns'
For more information check: https://developer.mozilla.org/en-US/docs/Web/HTTP/Status/503

### 031 — Writing on behalf of a relative
- turn failed: Server error '503 Service Unavailable' for url 'http://localhost:8000/api/sessions/d898fb00-69d8-4029-bac3-a9a85985a501/turns'
For more information check: https://developer.mozilla.org/en-US/docs/Web/HTTP/Status/503

### 032 — At the end of her patience
- first domain 'fallback', expected 'billing'
- invented data with the system unavailable: ['1042']
- no case opened; expected handover:billing_dispute
- stated case 00001042 without having received it

