# Decisions

Every choice that shaped this build, why it was made, and what it cost.

[docs/specs.md](specs.md) says what the agent does and is deliberately silent on
how. This file is the other half: the reasoning that turned that document into
this repository. It exists because a decision nobody recorded is indistinguish-
able from an accident, and because the most useful thing to hand the next
engineer is not the choice but the argument.

Three kinds of entry are worth reading first: the ones that **changed** (2), the
ones that were **reversed** (9), and the ones still **open** (11). Those are
where the judgement is.

**Status key** — `settled` · `open` (gate and date given) · `reversed` (kept for
the record) · `deviation` (departs from the spec; also in [DEVIATIONS.md](DEVIATIONS.md))

**On the numbers.** They are assigned in the order decisions were *made* and are
stable thereafter — source files cite them (`DECISIONS #16`), so renumbering
would silently break those references. Entries are grouped by topic rather than
by number, so the sequence runs out of order where a later decision belongs
beside an earlier one.

---

## Frame

### 1. Build it self-contained, not on a platform · `settled`

**Decision.** No Salesforce org, no tenant, no external platform. `docker compose up`
on a laptop, with a model provider as the only outside dependency.

**Why.** §17 is a platform-independent checklist, and the interesting question is
whether its twelve items can be *satisfied* — not which product ticks them. A
self-contained build means every claim is inspectable: the authorization
perimeter is a `GRANT` you can read, not a permission set you have to be shown.

**Cost.** Does not demonstrate Agentforce product knowledge, which for a
Salesforce panel is a real loss. Mitigated by mapping each §17 item onto the
platform primitive it corresponds to in [ARCHITECTURE.md](ARCHITECTURE.md).

---

## The reasoning layer

### 2. LangGraph 1.2.12, not Pydantic AI · `settled` · *changed mid-build*

**Decision.** LangGraph. The first plan said Pydantic AI; it was changed before
any agent code was written.

**Why it was Pydantic AI.** `Agent(model, system_prompt, tools=[...])` maps
one-to-one onto §4's domain, and its default error behaviour already satisfied
§5.1 — no exception reaches the model unless you explicitly ask for it.

**Why it changed.** Defensibility. In an interview a framework you can argue for
beats one you must first introduce. A design conversation that opens with
"what's Pydantic AI?" has already lost the room. This is not a technical
argument and it does not pretend to be — it is a judgement about the audience,
and the audience is part of the requirement.

**What it cost.** The §5.1 guarantee stopped being free (see 3). Heavier
dependency tree. Published comparisons put LangGraph at ~10s average end-to-end
against 5.7–7s for leaner frameworks, mostly checkpointing overhead we avoid by
not persisting graph state.

**What it gained, beyond recognition.** `StateGraph` makes §4 literal: a `route`
node with conditional edges into per-domain nodes, each binding only its own
capability tuple. `graph.get_graph().draw_mermaid()` renders the architecture,
so the diagram in the docs cannot drift from the code. And LangGraph is 1.x —
the version-churn risk that applies to a 0.x framework does not apply here.

### 3. Do not use LangGraph's prebuilt `ToolNode` · `settled`

**Decision.** `graph/tool_node.py` is ours, dispatching through
`@typed_capability`.

**Why.** Verified against 1.2.12 rather than recalled — its default handler is:

```python
def _default_handle_tool_errors(e: Exception) -> str:
    if isinstance(e, ToolInvocationError):
        return e.message
    raise e
```

Two branches, neither of which §5.1 accepts. A malformed tool call returns free
text to the model, which is precisely the untyped-error-in-front-of-the-
reasoning-layer failure the spec calls "the single most important implementation
rule in the document". Anything else re-raises and takes the conversation down,
which is not a guardrail but an outage. The spec wants a third thing the default
does not offer: a **named outcome** in both cases.

**Cost.** ~60 lines we own and test, instead of an import.

**Worth saying plainly.** This is not a criticism of LangGraph — letting a model
retry its own malformed call is reasonable for a general agent. It is wrong
*here*. The transferable lesson is about inherited defaults: a guardrail you did
not choose is not a guardrail you can defend.

### 4. Typed outcomes carry their own instructions · `settled`

**Decision.** Each failure outcome is a Pydantic model in a discriminated union,
and carries a `guidance` field marked internal-only:

```python
class BillingServiceUnavailable(BaseModel):
    outcome: Literal["serviceUnavailable"] = "serviceUnavailable"
    guidance: str = InternalOnly(
        "The billing system is not responding. Tell the customer plainly, "
        "give NO amount and NO date, and offer to open a case.")
```

**Why.** §5.2 requires a description on every output, and §5.1's argument is that
a named outcome makes the relevant instruction apply "literally and
unambiguously". Delivering that instruction *in the payload* rather than in a
system prompt read three thousand tokens earlier makes it adjacent to the
failure it governs.

**Cost.** Slightly larger tool results. Instruction text lives in two places
(prompt and outcome) and can drift — mitigated by the outcome text being the
authority for failure handling.

### 5. The model decides what is said; code decides what happens · `settled`

**Decision.** Escalation triggers, queue, priority, and the handover payload are
computed from typed state. The model contributes the wording, the one-sentence
summary, and the judgement of what the customer means.

**Why.** §12.1 is explicit that categorical escalations "are not judgement calls
and the agent is not permitted to weigh them". §16 accepts that the reasoning
layer introduces variance and argues the typed contracts and the perimeter are
what keep that variance away from anything that matters.

**Consequence worth stating.** The §1 metrics with a target of 0 or 100% are not
achieved by prompting. Cancellation discipline is zero because the cancellation
domain is handed `[billing_lookup, create_handover_record, transfer_to_human]`
and there is no tool to execute one. Handover completeness is 100% because a
missing field is a `ValidationError` at payload-build time.

### 6. Escalation is one-directional · `settled`

**Decision.** Code can force an escalation the model did not ask for. The
customer and the model can always request one. **Neither can block one.**

**Why.** §12.1 lists customer-initiated escalation as a first-class trigger,
"honoured immediately and without resistance or a retention attempt" — so the
model must never be able to talk a customer out of it. And categorical triggers
fire regardless of how well the conversation is going, so the model must never
be able to keep a cancellation to itself.

**Detection is the model's, honouring is code's.** Recognising "basta, voglio
parlare con qualcuno" — or sustained anger with no explicit request — is a
language judgement. What happens next is not.

**The limit.** §12.4: honouring is not promising. If no human is available the
agent gives the reference and the real service window. "Someone will call you
shortly" at 23:40 on a Saturday is a worse outcome than an honest Monday.

---

## Data and authorization

### 7. Postgres, not SQLite · `settled`

**Decision.** Postgres 16, with the agent connecting as a role that has been
denied specific columns.

**Why.** This is the highest-leverage decision in the build and it is not about
data. §8 requires that asking for a customer's email "fail on *authorization*,
not on an instruction", and §17.2 requires enforcement "at the data layer rather
than by instruction". SQLite has no roles and no column grants, so the perimeter
would be a Python `if` — which is the hole §8.1 warns about, wearing a
guardrail's clothes.

With Postgres:

```
$ psql -U fibrapunto_agent -c "SELECT email FROM crm.customer;"
ERROR:  permission denied for table customer
```

No application code in the loop. `tests/test_perimeter_raw_sql.py` asserts it.

**Cost.** One more compose service, ~150MB. Opaque denials (see
[FRICTION-LOG.md](FRICTION-LOG.md) entry 1).

### 8. Grants are generated from one policy file · `settled`

**Decision.** `contracts/perimeter-policy.yaml` is the source of truth; it
renders to `db/migrations/004_grants.generated.sql`, and `test_grants_drift.py`
fails if the committed SQL is stale.

**Why.** §8's table has to exist in exactly one place or it becomes
documentation, and §8 is explicit that a guardrail living only in documentation
is not one. Two invariants fall out of the grants rather than out of prompts:
`handled_by_agent` is omitted from the agent's INSERT grant and defaults true
(§13), and the reference number comes from a sequence the agent may only
`USAGE`, so it cannot state a reference it did not receive (§12.4).

### 9. The `TRUNCATE` grant, added then withdrawn · `reversed`

**Decision.** Reset runs as the database owner. The `TRUNCATE` grant briefly
given to `fibrapunto_platform` was removed.

**What happened.** `make reset` failed for want of `TRUNCATE`. The grant was
added — with a defensible argument, since that role already held `DELETE` on the
same tables. Then `RESTART IDENTITY` failed too, for want of sequence ownership,
and the second failure prompted a re-examination of the first.

**Why it was reversed.** Truncating tables and restarting identity sequences is
schema-owner work. The platform role exists for what the *application* does
platform-side — traces, transcripts, the operator view. The grant was solving
the wrong problem.

**Why it is recorded rather than quietly reverted.** Ten minutes earlier,
[FRICTION-LOG.md](FRICTION-LOG.md) entry 1 was written about exactly this
pressure: widening a grant until an error stops. The pull was no weaker for
being aimed at a non-agent identity, and being the author of the warning was no
protection against it. That is the entry's real evidence.

---

## Models

### 10. OpenRouter, with the pin verified per call · `settled`

**Decision.** All model access through OpenRouter, with
`provider: {allow_fallbacks: true, data_collection: "deny"}` and an assertion
that the served model equals the requested one.

**Why.** Model IDs become config strings, which makes §10's "the choice is the
operator's, and the design keeps it reversible" demonstrable rather than
asserted. Judge-from-a-different-vendor (§14.2) costs one API key instead of
three accounts. `data_collection: "deny"` restricts routing to providers that do
not retain prompts — a real §9 control rather than a README promise.

**Cost.** 5.5% fee on credit purchases; token rates pass through. An extra
subprocessor to declare under §9 — stated in the README rather than hidden.

**The trap.** OpenRouter offers no native pinning guarantee, and a "pinned"
eval run that silently changed model is worse than no pin at all. The pin is
therefore verified on every response, not requested: `assert_served_model`
compares the served id with the requested one, and `smoke_openrouter.py` does
the same on every run.

**Revised (29 September 2026).** `allow_fallbacks` was `false` in the belief
that it protected the pin. It does not: an OpenRouter fallback is to another
*provider* of the same model, never to another model (that is the separate
`models` list, which is never sent). What `false` did do was make the agent
unavailable whenever the first-choice provider was unhealthy — a 404 from one
endpoint while four others served the model. Fallbacks are now allowed; the
retention filter still applies to every candidate, and the served-model check
still fails loudly.

### 11. Judge separation enforced by vendor, not by model id · `settled`

**Decision.** Startup raises if `judge.vendor == agent.vendor`. A DB `CHECK`
constraint on `platform.eval_run` enforces the same thing for recorded runs.

**Why.** §10: "the model that grades the evaluation transcripts must not be the
model that produced them. Grading one's own output is not measurement." Vendor
level rather than id level because two checkpoints of one family share failure
modes — a judge that hallucinates in the same places as the subject will agree
with it enthusiastically.

---

## Knowledge and scope

### 12. No retrieval · `settled` at the day-7 freeze — **not built**

**Gate outcome (day 7).** Days 1–7 ran to schedule, which was the condition for
building tier 2. It was not built anyway, and the reason changed between setting
the gate and reaching it:

1. **The evaluation suite became the scarce thing.** A free-tier provider
   ceiling (FRICTION-LOG 5) put day 9's harness at risk, and §14 opens with
   "nothing in this document is believed because it was designed". Three
   capabilities with a metrics table are worth more than four without one — the
   same argument §16 makes about domains.

2. **The dependency was underweighted when the gate was written.** Tier 2
   requires extending the grounding guard to procedural claims *first*, or it
   reopens the §5.1 hole it was supposed to respect. That extension would land
   on the same day as the eval work, competing with it rather than following it.

The tiering below stands as the design. Tier 1 shipped on day 4 and is in
production use by the demo; tiers 2 and 3 are documented and unbuilt.

---

**Original entry, for the reasoning:**

**Decision.** No RAG in this build. Domain instructions live in operator-editable
files. A retrieval capability is designed but not built.

**The distinction that matters.** §4.4 refuses **general knowledge** — "what is
fibre?" — and that refusal is permanent and deliberate: the scope boundary should
be arguable with a compliance team, not inherited from whatever the model
absorbed. **Company procedural knowledge** is a different thing, and the spec has
no answer for it. §19 lists six things to do next; a knowledge base is not among
them. That is a genuine gap, surfaced during review rather than during design.

**Why not now.** Two domains, ~14 instructions. That fits in a prompt, and §4
argues it should — "instructions that apply to one domain can be strict without
collateral damage". Putting always-applicable instructions behind a similarity
search makes something deterministic probabilistic, for nothing.

**The trap if it is added carelessly.** Naive retrieval quietly undoes §5.1.
Typed outcomes exist so the model reacts to a named result instead of improvising
around a vague one; retrieve a paragraph, let the model paraphrase it, and
ungrounded generation is back with a more authoritative-sounding source. Any
retrieval here needs the same discipline the capabilities have — typed result,
visibility flag, and the grounding guard extended to cover procedural claims and
not only figures and dates. **That extension is the real cost, not the lookup.**

**Three tiers:**

| Tier | What | Status |
|---|---|---|
| 1 | Domain instructions as versioned, operator-editable files, hashed into every trace | **Building, day 4.** The operator changes a procedure without an engineer and without a redeploy — §19's "domains as versioned code" arriving early |
| 2 | `consulta_procedura(argomento)` → `ProcedureFound(testo, riferimento, aggiornata_il)` \| `ProcedureNotFound`. Exact match over a small structured set, not vectors | **Gated.** ~½ day. Built at the day-7 freeze only if days 1–7 ran to schedule |
| 3 | Real vector retrieval | Not built, not sketched. Only once the procedure set outgrows exact matching, and only after the grounding guard is extended |

**Why a gate rather than "later".** Tier 2 competes for the same half-day as the
guardrails and eval work the §1 metrics depend on, and §16's own argument applies:
two domains that work and are measured beat three half-built. A dated decision
point is honest; "later" is not.

### 13. The modem domain ships disabled · `settled`

**Decision.** `domains/modem.py` exists with `enabled=False`.

**Why.** §4.3 designs it and declines to build it, because guided troubleshooting
needs multi-turn state that would have cost the two working domains their
quality. Twenty lines make that a fact in the code rather than a claim in a
document. §16: "Three domains would demo better. Two that work and are measured
are worth more."

---

## Measurement

### 14. Deterministic assertions first, the judge second · `settled`

**Decision.** The §1 metrics are computed from the trace. The judge gives an
independent second opinion on the same questions.

**Why.** Routing accuracy comes from routing-decision events. No-invention comes
from set-differencing the amounts and dates in the reply against those in the
capability results. Completeness comes from a SQL view that recomputes it
independently of the code path that wrote the row. A model's opinion is not
needed for any of those, and is weaker than code for all of them.

**What the judge is for.** Register, tone, whether the writing is any good — and
the §14.2 audit: **disagreements between assertion and judge are the audit of
the judge**, dumped by `make eval-audit`.

**This is only possible because** `AgentReply` returns which domain fired and
which capabilities ran with which outcomes as *facts*, not as things to be
inferred from a transcript.

### 15. The harness is a separate workspace member with no backend dependency · `settled`

**Decision.** `evals/` does not depend on the backend package and drives the same
HTTP API the UI uses.

**Why.** §14.2 requires "an external program, outside the agent's platform".
Importing the agent in-process would quietly violate that, and would mean §17.8's
programmatic interface never gets exercised by the thing it exists for.

---

## Privacy

### 16. Inbound redaction on by default · `deviation` from §9

**Decision.** `REDACT_INBOUND=true`. Detected IBANs and card numbers are replaced
with `[[REDACTED:IBAN]]` before the turn leaves the process.

**Why.** §9 states an honest limitation: "by the time that rule applies, the text
has already reached the model for that turn. Nothing in the agent's design can
prevent that." That is true *if you do not pre-redact*. With pre-redaction the
model still knows an IBAN was pasted — so it can still say "non mi mandi mai
l'IBAN in chat" — but the provider never sees the digits.

**What it changes.** The limitation narrows rather than closes: the value still
transits process memory, and a format the detectors do not know still gets
through. That is a sharper and more defensible statement than the spec's, and it
delivers §19's redaction pipeline early. **The spec is not edited**; the
divergence is recorded here and in [DEVIATIONS.md](DEVIATIONS.md), and the toggle
stays so both behaviours can be demoed.

### 17. Detectors pair a pattern with a checksum · `settled`

**Decision.** IBAN validated mod-97, card numbers by Luhn, national tax ID by
check character.

**Why.** Regex alone produces false positives, and a redaction demo that mangles
an ordinary number in front of an audience discredits the whole pipeline.

### 21. Four enforcement points; only the first is optional · `settled`

**Decision.** Redaction is applied at four boundaries — inbound to the model,
into the stored transcript, into the handover record, outbound to the customer.
`REDACT_INBOUND` governs **only** the first.

**Why.** The toggle exists to demonstrate §9's honest limitation (see 16). If
storage redaction were conditional on it, flipping it during a demo would start
writing raw IBANs to disk — the toggle would silently be a data-protection
switch rather than a model-exposure switch, which is the opposite of what it is
for. §11's argument about layers, applied to §9: each boundary defends itself.

The fourth point looks redundant while inbound redaction is on, because the
model never held the digits and structurally cannot echo them. It is the only
guard left when the toggle is off — which is exactly the moment a missing one
would be missed.

**What it cost.** Redaction now runs up to four times on the same text. At these
volumes that is not measurable, and the alternative — one carefully-placed call
plus a comment asking future callers to be careful — is the class of control
§8.1 criticises.

**Enforced at the write boundary, not by the caller.** `store_transcript` is the
only write path to `platform.turn` and redacts whatever it is given;
`build_values` is the only INSERT path for a handover record and scrubs every
free-text field. A column named `text_redacted` is documentation, and
documentation is not an enforcement mechanism.

### 22. Retention nulls the words and keeps the skeleton · `settled`

**Decision.** The hourly sweep sets `text_redacted = NULL` and replaces trace
event attributes, keeping the rows, their timings, their outcomes and the event
names.

**Why.** Three reasons, and the first is the one that decided it:

- §1's metrics are computed over these rows. Deleting them would make every
  historical number drift as the retention window moved, and **a measurement
  that changes because time passed is not a measurement.**
- §14 wants "the progression across runs". A run from six weeks ago has to stay
  comparable to today's, which it cannot be if its denominator evaporated.
- A data subject asking what is held gets a truthful, complete answer: a row
  saying a conversation happened, when, and how it ended, with no content. That
  is a better answer than a missing row, which is indistinguishable from never
  having existed.

**The non-obvious part.** Trace event *attributes* are purged wholesale rather
than field by field. `text_redacted` is obviously content and gets handled
carefully; an attributes blob accumulates whatever a call site passed it. Rather
than trusting every present and future `emit()` to be careful, the sweep
replaces the whole object.

---

## Delivery

### 18. One SSE channel for both tokens and trace events · `settled`

**Decision.** Assistant text and trace events arrive on the same stream.

**Why.** It preserves the interleaving between "the agent said X" and "the
capability returned outcome Y", and that interleaving is what makes the trace
panel convincing rather than decorative. SSE over WebSocket because the traffic
is server-to-client only, reconnection with `Last-Event-ID` is built into the
browser, and the harness can drive the same REST API with plain `httpx`.

### 19. Vite over Next.js · `settled`

**Decision.** Vite + React 19 + TypeScript + Tailwind + shadcn/ui.

**Why.** No SSR to reason about, one static `dist/`. Next.js would add a Node
runtime to compose and give a panel more surface to ask about that is not the
point of the build.

### 20. Scope cuts, made up front · `settled`

Cut deliberately rather than discovered on day 9: HashiCorp Vault (the
`SecretStore` protocol plus a Fernet file backend — the seam is the point);
OpenTelemetry (Postgres JSONB with OTel-compatible field names, so the migration
is mechanical); token streaming, multi-session persistence, auth, an ORM; and the
test set at ~30 cases rather than 40, keeping §14.1's group proportions.

---

## Open questions

| # | Question | Decide by |
|---|---|---|
| 12 | Build `consulta_procedura`? | Day-7 feature freeze |
| — | Does the grounding guard need to cover procedural claims? | Only if 12 is yes |
| — | Which OpenRouter slugs are current? The catalogue moves | Day 2, `make smoke` |
