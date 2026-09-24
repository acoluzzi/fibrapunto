# Friction log

What fought back, what it cost, and what I would take to the platform team.

§8.1 argues that this belongs in the deliverable rather than in a private
notebook: the cost of diagnosing a bad error message is a number to bring to a
vendor, not an embarrassment to absorb. The entries below are kept in the same
spirit — each one names the friction, the cost, and the decision it forced.

---

## 1. Postgres column denials do not name the column

**Where:** the authorization perimeter (§8), day 1.

**What happened.** The perimeter is enforced by Postgres column grants: the
agent role has `SELECT (id, full_name)` on `crm.customer` and nothing more. That
works exactly as intended. But the denial reads:

```
$ psql -U fibrapunto_agent -c "SELECT email FROM crm.customer;"
ERROR:  permission denied for table customer
```

It names the **table**, not the column, and not the grant that is missing. An
engineer who did not write the policy sees "permission denied for table
customer" while holding a working `SELECT id, full_name FROM crm.customer`, and
has no thread to pull.

**Why it matters.** This is §8.1's complaint arriving from a different vendor,
which suggests it is a category of problem rather than one product's bug. The
fastest way to make the error stop is `GRANT SELECT ON crm.customer` — one word
shorter than the correct grant, always available, always works, and it silently
hands the agent the customer's email and phone number. **The error message makes
the insecure fix easier to reach than the secure one.**

**What it cost.** About 40 minutes across writing the policy renderer and the
first test run, most of it spent confirming that the denial really was
column-level and not a schema or role mistake.

**Decision.** Do not widen the grant. Instead, put a policy check in front of
the query that produces the error the database should have produced:

```
svc_agent may not read crm.customer.email. Denied by contracts/perimeter-policy.yaml.
```

Postgres remains the authority — `tests/test_perimeter_raw_sql.py` connects as
the agent role with no application code in the loop and asserts the denial
there. The Python layer exists only so that nobody is ever tempted by the
one-word fix. That ordering is the point: the readable error is a courtesy, the
column grant is the perimeter.

**To the platform team.** An authorization error should name the specific
missing grant. Every minute it does not is pressure toward a wider permission,
and that pressure is strongest on the engineer least familiar with the system —
which is exactly the wrong person to be making that call under time pressure.

---

## 2. LangGraph's tool-error default does not fit §5.1

**Where:** the reasoning layer, day 1.

**What happened.** §5.1 requires that no capability raise an untyped error into
the reasoning layer: every failure must arrive as a *named outcome* the
instructions can refer to. LangGraph's prebuilt `ToolNode` has a default error
handler. Verified against 1.2.12 rather than recalled:

```python
def _default_handle_tool_errors(e: Exception) -> str:
    if isinstance(e, ToolInvocationError):
        return e.message
    raise e
```

Two branches, and **neither is what the spec asks for.** A malformed tool call
returns free text to the model — an untyped error in front of the reasoning
layer, which is the exact failure mode §5.1 describes. Anything else re-raises
and takes the conversation down, which is not a guardrail either; it is an
outage. The spec wants a third thing that the default does not offer: a named
outcome in both cases.

**Decision.** Do not use the prebuilt node. `graph/tool_node.py` dispatches
through `@typed_capability`, which maps both classes of failure onto the typed
outcome union. `tests/test_capability_never_raises.py` asserts that the prebuilt
node stays out and that nothing survives the boundary.

**Worth stating plainly:** this is not a criticism of LangGraph. Its default is
reasonable for a general agent, where letting the model retry a malformed call
is usually right. It is wrong *here*, because §5.1's argument is that a language
model handed a generic failure and a waiting customer will fill the gap. The
lesson is about inherited defaults rather than about this framework: a guardrail
you did not choose is not a guardrail you can defend.

---

## 3. OpenRouter reserves credit against `max_tokens`, not against usage

**Where:** the model provider smoke test (§10), day 3.

**What happened.** The first request against a funded key failed:

```
402 — This request requires more credits, or fewer max_tokens.
      You requested up to 65536 tokens, but can only afford 4000.
```

Nothing had been spent. `langchain-openai` sends no `max_tokens` unless told to,
so the model's ceiling is requested by default, and OpenRouter checks
affordability against **the reservation rather than the expected usage**. The
reply that request would actually have produced was about fifty tokens.

**Why it matters beyond the error.** The failure mode is benign here — a 402
before anything ran. The same default on a well-funded account is not benign: it
silently removes any ceiling on what a runaway loop can spend, and §16 makes
cost per conversation an explicit design criterion. Discovering this as a
refusal was the lucky version; discovering it as an invoice is the other one.

**What it cost.** About fifteen minutes, most of it reading the error properly
rather than assuming the model slug was wrong.

**Decision.** An explicit output budget per role, in `llm/registry.py`, derived
from the spec's own worked examples — §4.1's replies are roughly eighty words
each:

| Role | Budget | Why |
| --- | --- | --- |
| agent | 1024 | a customer reply, possibly with a tool call |
| router | 256 | a small JSON object: domain, confidence, sentiment |
| judge | 1024 | the §14.2 schema, including a rationale |
| persona | 512 | one customer turn |

`test_every_role_has_an_explicit_output_budget` asserts every role has one and
that it is plausible, so a new role cannot be added without someone deciding
what it is allowed to spend.

It also consolidated model construction into `llm/chat.py`. Three things must
hold on every request — the pin, the retention restriction, and the budget — and
a second construction site is exactly where one of them quietly stops holding.

**To the platform team.** Reserving against the requested ceiling is defensible,
but the SDK defaulting to the model maximum while the gateway bills against that
default is a bad pairing. A caller who sets nothing gets the largest possible
reservation, which is the opposite of the safe default.

---

## 4. The same opaque authorization error, a third time — and the limit of layer 1

**Where:** creating the first handover record (§13), day 5.

**What happened.** The agent called `create_handover_record`, and the insert was
refused:

```
svc_agent may not create handover_record.(unnamed column).
Denied by postgres (SQLSTATE 42501).
```

There is no such column. The missing grant was `USAGE` on
`crm.handover_record_id_seq` — the sequence behind `id bigserial`. Postgres
named neither the sequence nor the operation, and the message actively points
the wrong way: it says *column*, so the reader looks at columns.

**Why this entry is not a repeat of entry 1.** Entry 1's mitigation was a Python
policy layer that produces a readable denial before the query runs. That layer
**structurally cannot catch this one**: it validates column lists, and a
sequence is not a column. Every named column *was* granted, so layer 1 passed
the request through and layer 2 refused it — with the same unhelpful message
entry 1 was written about.

That is worth stating plainly rather than quietly patching, because it bounds a
claim made earlier in this build: *layer 1 is a courtesy, layer 2 is the
perimeter*. Here the courtesy was unavailable, and the perimeter was the only
thing talking. The mitigation is real but partial, and pretending otherwise
would be the more comfortable write-up.

**The trap, again.** The obvious response is `GRANT INSERT ON crm.handover_record`
— wider, and it does not even fix the problem, because the missing privilege is
on a different object. An engineer under time pressure could plausibly widen the
table grant, observe that it still fails, and widen further. The error message
rewards escalation and punishes precision.

**What it cost.** About twenty minutes, most of it re-reading the INSERT column
list against the policy and finding them identical.

**Decision.** Grant `USAGE` on the sequence, explicitly, with a comment in
`contracts/perimeter-policy.yaml` explaining why it is easy to miss. Not a
widened table grant. The narrow fix is three words longer than the wrong one.

**To the platform team.** A privilege error should name the object and the
privilege — `permission denied for sequence crm.handover_record_id_seq (USAGE)`
— not the table the statement happened to mention. This is the third distinct
instance in five days, from two different vendors, which suggests the category
rather than the product is the problem.

---

## 5. A free-tier in-flight ceiling makes multi-turn verification impractical

**Where:** verifying the cancellation and guardrail paths end to end, day 7.

**What happened.** Repeated failures on the *second or third* model call of a
conversation:

```
402 — This request would exceed your available credits given your current
      in-flight requests.   reason: in_flight_budget_exhausted
      Retry-After: 120
```

Not credit exhaustion: $0.15 spent against a $20 key limit. The account is
`is_free_tier: true` — the $20 is a *spending cap*, not purchased credit — and
OpenRouter sizes its in-flight reservation ceiling accordingly. Each request
reserves its `max_tokens` (entry 3), those reservations settle slowly, and a
conversation issues several calls in quick succession. Two turns is enough to
hit it.

**Why it matters beyond the inconvenience.** It bounds what can be verified at
all:

| Activity | Calls | Feasible on free tier |
| --- | --- | --- |
| Single-turn probe | 2 | yes |
| Two-turn conversation | 4–5 | intermittently |
| §14's eval run, 30 cases × ~5 turns | 300+ | no |

The evaluation suite is not a nice-to-have in this design — §1's metrics are the
deliverable, and §14 says "nothing in this document is believed because it was
designed". A tier that cannot run the harness cannot produce the evidence.

**What it cost.** Roughly forty minutes across several attempts, and the
verification is still outstanding.

**Decision.** Purchase credits before day 9. Until then, verification is
offline-first: everything deterministic is unit-tested against the real database
and the real stub, and live runs are reserved for what genuinely needs a model.
That split was already the working method for cost reasons; this makes it a
constraint rather than a preference.

**Worth noting for anyone repeating this.** The failure mode is easy to
misread. The error says "available credits", the balance says plenty remain, and
the natural conclusion is a bug in the request. The word that matters is
`in_flight`, and it appears only in the metadata.

---

## 6. Three failures the test suite could not have caught, found in one conversation

**Where:** the first end-to-end run of eval case 005 in a browser, day 10.

**What happened.** 229 tests green, lint clean, containers building. Then one
conversation through the UI — billing unavailable, customer accepts a handover —
and three separate defects surfaced at once. None of them could have been caught
by the suite, and the reason why is the interesting part.

**6a — The credential store was not mounted into the container.**
`docker-compose.yml` never mounted `./secrets`, so `FileSecretStore` inside the
backend found nothing and every `billing_lookup` failed with
`CredentialNotFound`. The tests run on the *host*, where the file is present.

The part worth keeping: the typed-outcome layer did its job perfectly.
`CredentialNotFound` became `serviceUnavailable` with an incident id, the agent
said the billing system was not responding, and it invented nothing. **The agent
behaved impeccably while the demo was broken** — which is precisely what §5.1
promises and also precisely why the failure was invisible. A system that
degrades gracefully will hide its own misconfiguration.

**6b — `disputed_point` was required on every billing escalation.** §13's table
lists "what the customer disputes" among a billing dispute's fields, and the
completeness matrix required it unconditionally. But the billing domain routes
*every* escalation to `record_type = billing_dispute`, including conversations
where nothing is disputed. Here the customer asked "how much do I owe?", the lookup
never succeeded, and no figure had ever existed to dispute.

So the record failed validation, and therefore **was never written, and
therefore the company was never told.** That is §12.4's worst branch reached
through the front door, and it contradicts §13's own rule — *"an incomplete
record beats no record"* — which `create_handover_record.py` quotes correctly
twenty lines below the check that violated it.

**6c — The trace panel received nothing.** Two independent bugs in one line of
SSE serialisation. Events were emitted *named* (`event: router.decision`), and
`EventSource.onmessage` only fires for unnamed events — named ones require
`addEventListener` per type, seventeen of them. And the payload was handed to
`sse-starlette` as a dict, which serialises it with `str()`: Python repr, single
quotes, `None`. Not JSON, and `JSON.parse` would have rejected it even if the
handler had fired.

**Why none of this was caught.** Each defect lives in a seam the tests
deliberately do not cross:

| Defect | Seam | Why the suite missed it |
| --- | --- | --- |
| 6a | host ↔ container | tests run on the host, where the file exists |
| 6b | required fields ↔ real conversations | fixtures supplied every field, so the conditional case never arose |
| 6c | backend ↔ browser | the API is tested with `httpx`, which does not care about SSE framing |

That is not an argument for more unit tests. It is an argument for the thing the
verification debt was always about: **a suite that never leaves the process
cannot test the boundaries between processes.** The eval harness drives real
HTTP for exactly this reason, and it had not been run either.

**What it cost.** About an hour: twenty minutes on 6b, which announced itself in
the logs, and most of the rest on 6c, where the stream connected, frames
arrived, and the panel stayed empty — a failure with no error anywhere.

**Decisions.**
- Mount `./secrets:/app/secrets:**ro**`. Read-only is not incidental: the agent
  resolves a handle at request time and never writes, rotation is an operator
  action on the host, and a running agent able to rewrite its own credential
  store would be a strange thing to have built.
- Require `disputed_point` only when a lookup actually returned a figure. The
  valuable half of the check survives — if the agent read an amount and the
  customer objected, a record that omits the objection is still refused.
- Emit trace events unnamed and serialise them here. The name is in the payload,
  which is where every consumer already reads it from.

**The general lesson, which is the one I would keep.** All three defects sat
behind a green suite, and two of them were *masked by correct behaviour
elsewhere*: graceful degradation hid 6a, and the §12.4 fallback message hid 6b
by explaining the failure so reasonably that it read as intended. Designing for
honest failure makes a system pleasant to operate and harder to debug, and the
mitigation is not better unit tests — it is running the thing end to end,
earlier, through the same surface a user will touch.

---

## 7. An adversarial review found what the suite was built to agree with

**Where:** a red-team pass over the whole repository after the English switch,
read against the panel brief rather than against the spec.

**What happened.** 200 unit tests green (the 37 database tests need Postgres
running and were not part of this pass). A review that set out to break each of
the README's headline claims, rather than to confirm them, found defects in four
of them. Every one of them was verified against the code before it was written
down here.

**7a — Subsystems that exist, pass their tests, and are never called.**

| What | Where | What actually happens |
| --- | --- | --- |
| Read-back confirmation gate (§4.1 instruction 5) | `state/models.py:83` | `PendingConfirmation` is never assigned, so the router's confirmation lock never fires. A customer's "Yes" can be routed to fallback, which has no `create_handover_record`, and the case is never opened |
| Templated closing messages (§12.4) | `escalation/engine.py:42` | `closing_message` is called only from tests. The sentences with legal weight — reference, response window — are model output after all, and `status` never leaves `active` |
| Escalation trigger on the record (§12.5) | `graph/tool_node.py:242` | triggers are evaluated *after* the reply, so a case opened in the same turn as an outage reads the previous turn's trigger or falls back to `amount_dispute` |

The tests for all three set the state by hand (`pending_confirmation`,
`pending_trigger`) and then assert on what follows. **They prove the mechanism
works when it is engaged; nothing proves it is ever engaged.** That is the same
seam as entry 6, one level in: not host ↔ container, but unit ↔ wiring.

**7b — The grounding guard and the metric share one instrument.**
- Figures written as "30 euros", "€30", "Sep 19", "the 19th" or "end of the
  month" are not recognised as figures, and the year of a date is never checked.
- ISO dates are misread (`2026-09-19` as day 9, month 19), so a correct answer
  copied from the agent's own state block can be blocked.
- A failed lookup for a *second* contract does not clear the first contract's
  `BillingFacts` (`graph/tool_node.py:178`). The first amount then grounds a
  reply about the second contract during an outage — §15's first row, reached
  through the guard rather than around it — and the handover carries it too.
- `evals/assertions.py` reuses the guard's regex on purpose. **Whatever the guard
  misses, the "no invention" metric misses too**, so a zero there means "no
  figure of a shape we already knew about", not "no invention".

**7c — Redaction matched the test inputs, not the customers.** Every test put
the IBAN at the end of the string. `"My IBAN is IT60X0542811101000000123456
thanks"` is not redacted: the greedy pattern swallows the next word, mod-97
fails, and there is no retry on a shorter match. A card number followed by its
expiry fails Luhn the same way. The checksums were meant to prevent false
positives; combined with greedy matching they produce false negatives.

**7d — Smaller gaps in claims that otherwise hold.**
- `{"contract_number": null}` from the model raises `AttributeError` in
  `_absorb_billing`, *outside* `@typed_capability` — §5.1 holds for the
  capability and fails one line after it. A malformed argument is also reported
  as the billing system being down.
- The one-case fix (375bc2a) rebuilds the route from the *current* domain: a
  billing dispute followed by "I want to cancel" tells the customer the
  retention team will call, and no cancellation record is ever written. It is
  sequential-safe, not concurrency-safe — `origin_session_id` has no unique
  constraint.
- The promises guard is a phrase list: "your cancellation is being processed"
  and "I can waive the early termination fee" both pass.
- The agent's DB role cannot read email — that is proven by raw SQL — but
  `/api/operator/records/<ref>` returns email and phone, unauthenticated, from
  the admin pool, on the same origin as the chat, with sequential references.
- The judge-vendor `CHECK` sits on `platform.eval_run`, which nothing writes to.

**7e — The documents had drifted from the build.** A three-day git history
under a "ten days" narrative, "every run that has ever been made" with no eval
run committed, untranslated metric labels and placeholders, a "fuzzed" boundary that is five parametrised exceptions. None of it is
a code defect; all of it is checkable in seconds by anyone with the repository,
and each one costs more credibility than the defect it describes.

**Why it matters.** Entry 6 was about seams between processes. This one is
about a seam in method: the suite, the guards and the eval assertions were all
written by the same hand, with the same inputs in mind, and they agree with each
other. Agreement is not evidence. The review found nothing the tests got wrong;
it found the things the tests were never shaped to ask.

**Decisions.** Being fixed in code:
- **Done.** Wire the confirmation gate, the closing messages and the trigger
  ordering into the live path. `create_handover_record` for a dispute
  (trigger `categorical:amount_dispute`) returns `awaitingConfirmation` and
  stores the summary in `PendingConfirmation`; the router's lock now holds any
  answer to the read-back ("Yes, that's correct" included, not only a bare
  "yes"); the customer's agreement opens the case **by code** from the stored
  summary, with no model call. Outage, not-found, no-progress and
  human-requested cases are §4.1 instruction 6 / §12.1 and open without a
  read-back. Every turn that opens a case, or fails to, ends with
  `escalation/engine.py`'s template — the model's own sentences naming a
  reference, a response time or a promise of contact are removed first — and
  that is where the status becomes `handed_over` or `escalated_no_record`.
  The trigger is evaluated when the case is opened, so an outage in the same
  turn records `conditional:service_unavailable`. Tests go through
  `Conversation.send` and set no escalation state: `test_handover_wiring.py`.
  Case 015 gained its confirmation turn — it expected a refund case in one
  turn, which instruction 5 does not allow.
- **Done**, found while wiring the above: a case opened on the customer's
  *first* message was never written. `what_customer_was_told` is required
  non-empty and filled after each reply, so on turn one it was empty and the
  record failed validation — every one-turn handover case (005, 006, 007,
  010, 011, 016, 032) ended with the company never told. The payload now
  records what the customer is told on that turn: the closing template's
  content (case number, team, response window), which is known before it is
  sent, plus the reason from the trigger — marked `[recorded by the system]`
  so the operator can tell it from a transcript line.
- **Done.** `BillingFacts` carries the contract it was fetched for, and any
  lookup for a *different* number drops it, whatever that lookup's outcome. A
  failed retry on the *same* contract keeps it: those are still verified facts
  about that contract, which is narrower than the "any outcome but `found`"
  first planned. Figure extraction moved to `guardrails/figures.py`: amounts in
  digits or words with any currency marker, dates as (year, month, day) with
  only the parts the text names, years checked, ISO read year-first, and "the
  19th" / "end of the month" / "mid-September" as mentions. The eval metric no
  longer shares any of it: `evals/assertions.py` counts every numeral, number
  word, month name and month-edge phrase the customer did not write — blunter,
  and a superset of what the guard sees, so a guard blind spot now shows up as
  a failed case instead of a clean zero. Tests: `test_grounding_coverage.py`.
- **Done.** The IBAN and card patterns now only find where a value may
  *start*; where it ends is decided by the checksum. `privacy/detectors.py`
  walks every candidate end at a group boundary and keeps the one that
  validates — for an IBAN, the ISO 13616 length for its country, so a word
  glued on after it no longer matters. Tests put each value at the start,
  middle and end, before a word, before an expiry date and before a CVV.
  One more defect surfaced on the way: the card match ended on its trailing
  separator, so "card 4111… exp" became "[[REDACTED:CARD]]exp".
- Validate tool arguments inside the typed boundary. (Still open.)
- **Done.** One-case idempotency is per record type, backed by a unique
  constraint. `CollectedState.handovers` keys each opened case by record type,
  so a cancellation after a dispute opens its own case and the customer is
  given *its* reference and *its* team. Migration 005 adds a unique index on
  `(origin_session_id, record_type)`; the gateway inserts with `ON CONFLICT DO
  NOTHING` and reads back the winner, so two concurrent requests get one case
  and the same reference — no error, and no new grant (the agent could already
  read those columns). The promises guard accepts any reference the
  conversation actually opened. The earlier race test opened the case once
  before its `gather`, so it never raced two first calls; the new one does.

- **Done**, found after the above: the triggers were still computed, logged
  and ignored — on an outage or a not-found it was the model's choice whether
  to offer a human. Now `Conversation._enforce` acts on them. Conditional and
  distress triggers end the reply with a templated offer held in
  `PendingEscalation`; "yes" opens the case by code with a templated subject
  and the trigger that fired, "no" or moving on lets it lapse, and each
  trigger is offered once. An explicit request for a person opens the case on
  that turn with no model call, so no retention attempt is possible — and is
  now told apart from anger, which only brings the offer forward (they had
  shared one flag, so anger alone was recorded as `customer:human_requested`).
  The trace's `escalation.triggered` event carries the `action` taken.
  Categorical triggers remain enforced by structure: the cancellation domain's
  only write is the handover, and a dispute cannot open without the read-back.
- **Done.** The handover record re-serialised the *raw* context over the
  redacted one on the line after `build_values`, so a value inbound redaction
  missed — or was switched off for — was stored in the CRM. §9's "each
  boundary defends itself" was false at exactly the boundary that lives
  longest. One line; `test_a_raw_iban_in_state_never_reaches_the_record`.

Stated rather than fixed, because the fix belongs to a production build: the
promises guard is a list of known phrasings, not a classifier. The operator
view now has a demo gate — one shared password, a signed session cookie, ports
bound to localhost (`api/auth.py`) — but a real deployment puts it in a
separate service behind SSO, never on the chat's origin.

**The general lesson.** A guardrail and the metric that measures it must not
share an implementation, and a test that sets up the state it then checks
proves nothing about whether production ever reaches that state. The cheapest
review in this build was the one that started from "this claim is false —
show me where".
