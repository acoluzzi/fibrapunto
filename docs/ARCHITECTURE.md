# Architecture

How the code maps onto [docs/specs.md](specs.md).

The spec is technology-agnostic and ends with §17: a twelve-item checklist that
an implementation either satisfies or does not. This document is the mapping —
for each item, the file that satisfies it and the test that proves it.

Read it in one of two ways. **Auditing a claim:** go straight to the §17 table
and follow the test column. **Understanding the system:** read "A turn, end to
end" first, then the four load-bearing structures.

~6,900 lines of Python across 50 modules, ~220 tests.

---

## The §17 checklist

| # | Requirement | Where | Proof |
|---|---|---|---|
| 1 | Scoped domains, each with its own enumerated capability list | [domains/registry.py](../backend/src/fibrapunto/domains/registry.py) · [prompts/](../backend/src/fibrapunto/prompts/) | `test_conversation_wiring.py::test_each_domain_binds_only_its_own_capabilities` and `::test_a_tool_outside_the_domain_is_refused_not_executed` |
| 2 | A distinct service identity, enforced at the data layer | [perimeter/policy.py](../backend/src/fibrapunto/perimeter/policy.py) · [db/migrations/003_roles.sql](../db/migrations/003_roles.sql) | `test_perimeter_raw_sql.py` — connects **as the agent role**, no Python in the loop |
| 3 | Field-level read restriction | [contracts/perimeter-policy.yaml](../contracts/perimeter-policy.yaml) → `004_grants.generated.sql` | same file: `SELECT email` raises SQLSTATE 42501 |
| 4 | Typed capability outcomes, no untyped exception reaching the model | [capabilities/runtime.py](../backend/src/fibrapunto/capabilities/runtime.py) · [outcomes.py](../backend/src/fibrapunto/capabilities/outcomes.py) | `test_capability_never_raises.py` — all five stub modes plus a fuzzer |
| 5 | Descriptions on every capability, input and output | [capabilities/base.py](../backend/src/fibrapunto/capabilities/base.py) · [graph/build.py](../backend/src/fibrapunto/graph/build.py) | `test_billing_typed_outcomes.py` |
| 6 | An output visibility flag | `CustomerVisible()` / `InternalOnly()` in [base.py](../backend/src/fibrapunto/capabilities/base.py) | `test_guardrails.py::test_the_blocklist_is_derived_from_the_models` |
| 7 | A credential store external to the automation and the instructions | [credentials/store.py](../backend/src/fibrapunto/credentials/store.py) | `test_credentials_not_in_prompt.py` — serialises a full outbound request and greps |
| 8 | A programmatic conversation interface | [conversation.py](../backend/src/fibrapunto/conversation.py) · [api/app.py](../backend/src/fibrapunto/api/app.py) | the eval harness drives it over HTTP and never imports the backend |
| 9 | Model version pinning, with notice of change | [llm/registry.py](../backend/src/fibrapunto/llm/registry.py) | `test_model_registry.py` — `assert_served_model` on every call |
| 10 | Conversation-level logging sufficient to reconstruct the run | [observability/](../backend/src/fibrapunto/observability/) | 17 event types; `platform.trace_event` |
| 11 | A handover path carrying a structured payload | [handover/](../backend/src/fibrapunto/handover/) | `test_handover_record.py` — all three outcomes reachable |
| 12 | An environment resettable to a known data state | [scripts/reset.py](../backend/src/fibrapunto/scripts/reset.py) | `test_reset_idempotent.py` — run twice, checksum, compare |

§17 singles out **4, 6 and 9** as the ones most often missing. All three are
enforced structurally here rather than by convention: 4 by a decorator no
capability can bypass, 6 by a blocklist derived from the model schemas at import
time, 9 by an assertion on the served model id on every single call.

---

## A turn, end to end

One `POST /api/sessions/{id}/turns`, in order. Each step names the section that
requires it.

```
customer text
   │
   ├─ 1. redaction.for_model                          §9   ← the only optional step
   │     detectors validate (IBAN mod-97, Luhn, CF check char),
   │     kinds recorded in state, digits replaced
   │
   ├─ 2. router.classify → router.policy.apply        §4   ← skipped on a confirmation "Yes"
   │     model proposes a domain; hysteresis and the
   │     confirmation lock decide
   │
   ├─ 3. graph.run_domain_turn                        §4   ← binds ONLY this domain's tools
   │     stable prompt block (cached) + volatile state block
   │     │
   │     └─ tool_node.dispatch                        §5.1 ← @typed_capability
   │          every failure becomes a named outcome;
   │          results absorbed into CollectedState
   │          │
   │          └─ perimeter.gateway                    §8   ← the only door to CRM data
   │               connects as fibrapunto_agent; never SELECT *
   │
   ├─ 4. guardrails.pipeline.resolve                  §11  ← grounding · promises · visibility · language
   │     one repair attempt, then substitute
   │
   ├─ 5. redaction.for_customer                       §9   ← unconditional
   │
   ├─ 6. escalation.triggers.evaluate                 §12.1 ← measured against state, not text
   │     categorical triggers are not judgement calls
   │
   └─ 7. store_transcript (redacted) + trace events   §9, §17.10
```

Two things about this path are worth stating because they are easy to miss.

**The facts live in `CollectedState`, not in the transcript.** That is what
makes §15's mid-conversation domain switch work without re-asking the contract
number, and it is why step 3's prompt can drop the previous domain's tool
traffic on a switch without losing anything.

**Step 4 runs before step 5, and they are different kinds of thing.** A guard
inspects and may block or trigger a repair; redaction unconditionally removes.
Putting redaction inside the guard pipeline would have made it repairable,
which is not what §9 asks for.

---

## The four load-bearing structures

### 1. Typed outcomes carry their own instruction (§5.1)

Discriminated unions on an `outcome` literal. The part that matters is that each
failure outcome carries its own `guidance`, marked `InternalOnly`:

```python
class BillingServiceUnavailable(BaseModel):
    outcome: Literal["serviceUnavailable"] = "serviceUnavailable"
    guidance: str = InternalOnly(..., default=(
        "The billing system is not responding. Tell the customer plainly, "
        "give NO amount and NO date, and offer to open a case."))
```

§5.2's output description delivered as *payload*, arriving with the failure,
rather than as a line in a system prompt the model read 3,000 tokens ago.

`@typed_capability` is the single wrapper that guarantees nothing else can
happen. It deliberately does **not** catch `CancelledError`: `asyncio.timeout()`
is implemented via cancellation, so catching it would break the timeout path
that produces `serviceUnavailable` in the first place. That is asserted by a
test, because it was originally asserted by a test that had it backwards.

### 2. The perimeter the database enforces (§8, §17.2, §17.3)

`contracts/perimeter-policy.yaml` transcribes §8's table and renders to SQL:

```sql
GRANT SELECT (id, full_name) ON crm.customer TO fibrapunto_agent;  -- email, phone absent
GRANT SELECT ON crm.contract TO fibrapunto_agent;                  -- no UPDATE, no DELETE
-- reference DEFAULT nextval(seq), agent has USAGE only
-- no grant on schema platform at all
```

Three consequences that are structural rather than intended:

- The agent **cannot state a handover reference it did not receive** (§12.4) —
  the sequence is the database's to allocate.
- The agent **cannot read its own transcripts**, for the same reason it cannot
  read an email.
- `handled_by_agent` is omitted from the INSERT grant and defaults `true`, so
  §13's flag is an invariant rather than a field someone remembers to set.

Proven three ways, increasing in strength: the conversation refuses; `make
perimeter-proof` runs the same call under both identities side by side; and
`test_perimeter_raw_sql.py` connects as the agent role with no Fibrapunto code
in the loop.

### 3. Guards that make metrics structural (§11, §1)

§1's metrics with a target of **0** or **100%** must not be achieved by prompt
tuning. Each is converted into something that cannot happen:

| Metric | Why it is structural |
|---|---|
| no invented data — 0 | `output_grounding` set-differences every figure against verified results in `CollectedState` and the customer's own words. With the stub down nothing is grounded, so any figure is blocked. |
| autonomous cancellations — 0 | the capability does not exist in any domain's tuple |
| handover completeness — 100% | the payload is built from typed state, so a missing field is a `ValidationError` at build time |
| reference correctness | the agent has no INSERT grant on `reference` |

`output_visibility` walks the outcome union schemas at import time, so adding a
new outcome extends the check automatically — §17.6 without a hand-maintained
list.

### 4. Measurement where code answers first (§14)

Four of §1's six metrics are computed deterministically from the trace, because
`AgentReply` returns which domain fired and which capabilities ran with which
outcomes as **facts** rather than things to infer from a transcript.

The judge — a different vendor from the agent, asserted by a DB `CHECK`
constraint and by `JudgeMisconfigured` at startup — answers only what a model
can uniquely answer: register, tone, whether the Italian is any good.
**Disagreements between the two are the audit of the judge** (§14.2), dumped by
`make eval-audit`.

---

## §9: four enforcement points, three unconditional

| # | Boundary | Function | Optional? |
|---|---|---|---|
| 1 | inbound → model | `redaction.for_model` | **yes** — `REDACT_INBOUND` |
| 2 | → stored transcript | `redaction.for_storage` | no |
| 3 | → handover record | `redaction.for_handover` | no |
| 4 | → customer | `redaction.for_customer` | no |

Point 1 is a toggle because §9 states an honest limitation — *"by the time that
rule applies, the text has already reached the model for that turn"* — and being
able to demonstrate both behaviours is worth more than hiding one.

The other three are unconditional, and that is the point. If storage redaction
were conditional on the toggle, flipping it during a demo would start writing
raw IBANs to disk: the toggle would silently be a data-protection switch rather
than a model-exposure switch. `test_storage_redaction_holds_with_inbound_
redaction_off` is the test that holds this line.

Retention nulls the words and keeps the skeleton. A policy that deleted rows
would make every historical §1 number drift as the window moved, and a
measurement that changes because time passed is not a measurement.

---

## Where enforcement lives

§11 argues that a guardrail existing at only one layer is fragile. The layers
here, and what survives if the one above it is removed:

| Layer | Mechanism | If you delete the layer above |
|---|---|---|
| 1 — data | Postgres column grants | the agent still cannot produce an email; the data was never in context |
| 2 — capability | `@typed_capability`, enumerated tuples | the model still cannot execute a cancellation; no tool exists |
| 3 — output | the guard pipeline | grounding and promises still hold with no prompt at all |
| 4 — instruction | the domain prompt files | — |

`test_guardrails.py` contains §11's own methodology made executable: the
grounding and promises guards are re-tested with the prompt removed entirely.

---

## Repository map

| Path | What |
|---|---|
| `contracts/` | The three contracts: billing OpenAPI, capability definitions, the perimeter policy. All drift-tested against a fresh render. |
| `db/migrations/` | Four files. `004_grants.generated.sql` is rendered, never hand-edited. |
| `backend/src/fibrapunto/` | The agent (`domains`, `router`, `graph`, `capabilities`, `guardrails`, `escalation`, `state`) and the platform (`perimeter`, `privacy`, `credentials`, `integrations`, `observability`, `handover`, `api`) |
| `mock-billing/` | The legacy billing system (§7): slow, contract-versioned, fails on command |
| `evals/` | The harness (§14), external by construction — it does not depend on the backend package |
| `frontend/` | Chat, the system trace, the metrics, and the operator view |

---

## What is deliberately absent

Stated here rather than discovered, because §16's argument is that a stated
trade-off beats a hidden one.

- **The modem domain** exists as a registry entry with `enabled=False` (§4.3).
  Enabling it is a flag plus an instruction file, not a redesign.
- **No OpenTelemetry.** Event fields are named to be span-compatible (`name`,
  `attributes`, `start`, `duration`) so the migration is mechanical. See the
  friction log.
- **No cross-session memory** (§3), and that is load-bearing rather than a gap:
  it needs authentication and a data policy this design does not have.
- **Sessions are in-memory**, which is the first thing that breaks at more than
  one replica. See [SCALING.md](SCALING.md).

Departures from the spec are in [DEVIATIONS.md](DEVIATIONS.md); the reasoning
behind each choice is in [DECISIONS.md](DECISIONS.md); what fought back, and
what it cost, is in [FRICTION-LOG.md](FRICTION-LOG.md).
