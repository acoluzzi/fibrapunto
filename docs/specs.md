# Fibrapunto Assistant — design specification

A customer service agent for a fictional broadband operator.

This document is deliberately **technology-agnostic**. It describes what the agent does, what
it must never do, how it is bounded, and how it is measured — without naming a platform, a
model vendor, a runtime or a framework. Any implementation that satisfies §17 satisfies this
specification.

The reason for writing it this way is not neutrality for its own sake. A design that only
exists as configuration inside one product cannot be reviewed, argued with, or rebuilt. This
document is the part that survives a change of platform.

---

## 1. Context and purpose

**The business.** Fibrapunto is an invented fibre operator with roughly 400,000
residential customers. Customers pay monthly by SEPA direct debit or card. Every customer has
a contract number, a modem serial and a billing status. Billing runs on a legacy system that
predates the CRM and is reachable only through an API.

**The user.** A residential customer writing from the app or the website. They may be twenty
or seventy. They write in English, often with typos, often pasting the text of an SMS they
received and asking what it means.

**The job to be done.** Resolve alone what can be resolved alone — a rejected direct debit, a
question about an invoice — and hand to a human what cannot, _with the context already
collected_, so that the human does not start from nothing.

**A successful run produces exactly one of two outcomes:**

1. the customer knows what happened and what to do, and needs nobody else; or
2. a handover record exists, carrying type, priority, summary and every field the receiving
   team needs, routed to the right queue, and the customer knows someone will contact them.

Anything else — an answer that sounds helpful but is invented, a conversation that dies
without a record, a promise the company will not keep — is a failed run even if the customer
does not complain.

**What it replaces.** Today these contacts land on a generic web form or a human chat, and the
operator spends the first minutes of every conversation asking for the contract number,
working out what the problem is, and opening the billing system. This design moves those
minutes out of the human queue; and when it does hand over, it hands over a case that has
already been worked.

### Success metrics

| Metric                  | Definition                                                                                                | Measured by                                                     | Target on the test set            |
| ----------------------- | --------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------- | --------------------------------- |
| Autonomous resolution   | Billing conversations closed without a handover record                                                    | Evaluation harness                                              | ≥ 70% of resolvable billing cases |
| Handover completeness   | Handover records carrying every required field                                                            | Query over records created during runs                          | 100%                              |
| Cancellation discipline | Cancellations handled autonomously — must be zero                                                         | Harness classifier: _did it execute or promise a cancellation?_ | 0                                 |
| No invention            | Answers containing billing figures while the billing system is unavailable or the contract does not exist | Harness, with the billing stub forced into failure modes        | 0                                 |
| Routing accuracy        | Conversations reaching the correct domain, including mid-conversation switches                            | Harness, expected versus actual                                 | ≥ 90%                             |
| Escalation precision    | Escalations that a reviewer agrees were warranted                                                         | Manual review of a sample                                       | ≥ 90%                             |

These are proxies. What a customer would measure in production is time to first response from
the receiving team, repeat contact rate, and satisfaction. The proxies exist because those
take months to move and this has ten days.

---

## 2. Actors

| Actor                    | Who                                                               | Role                                                                                                    |
| ------------------------ | ----------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------- |
| Customer                 | Residential subscriber                                            | Writes; provides the contract number; receives an answer or a commitment that someone will contact them |
| Agent                    | The conversational system, running under its own service identity | Classifies, calls capabilities, answers, opens handover records, escalates                              |
| Billing operations       | Human administrative team                                         | Receives billing dispute records, already worked                                                        |
| Retention team           | Human team handling cancellations                                 | Receives high-priority cancellation records with reason, tenure and contractual constraints             |
| Billing system           | External legacy system, reached over an API                       | Answers payment-status lookups by contract number                                                       |
| Operator's administrator | Whoever runs the CRM at Fibrapunto                                | Owns the service identity, its authorizations, the credential store and the queues                      |
| Engineer                 | Me                                                                | Designs, builds, measures, hands over; carries platform friction back to the product team               |

The last row is not decoration. A deployed agent that nobody at the customer can safely modify
is a liability, and the hand-back to the administrator is part of the deliverable.

---

## 3. Scope

**In scope.** Two conversation domains: failed or disputed direct debits and billing questions;
and cancellation requests. Both are high-volume, both are currently handled by people, and the
two differ in an instructive way — one is meant to be resolved, the other is meant to be
escalated well.

**Out of scope, deliberately.** Technical troubleshooting of the line and the modem is designed
(§4.3) but not built. Sales, upgrades and retention offers are not the agent's business.
Anything touching the contract itself — activation, suspension, change of plan, change of
payment method — is human work.

**Out of scope, structurally.** The agent has no memory across conversations. It cannot
identify a customer except through a contract number the customer supplies in that
conversation. It never writes to the contract record. These are not gaps to be closed later
without a decision; each one is load-bearing and is argued for where it appears.

---

## 4. Conversation domains

The agent is not one instruction set. It is a router plus a small number of bounded domains,
each with its own description, its own reasoning instructions and its own set of permitted
capabilities.

> **A note on vocabulary.** Different platforms call these _topics_, _subagents_, _skills_, or
> _intents_. This document calls them **domains**. What matters is the property, not the name:
> a domain has a scope statement used for classification, instructions that apply only inside
> it, and an explicitly enumerated set of capabilities it may call. A design where every
> instruction applies to every conversation does not satisfy this section.

Why split at all: instructions that apply everywhere get long, contradict each other, and
degrade classification. Instructions that apply to one domain can be strict without collateral
damage. And a mid-conversation switch from billing to cancellation is then a routing decision
made by the system, not an `if` buried in a prompt.

### 4.1 Billing and direct debit

**Scope statement** — used to decide whether a conversation belongs here.

> Questions about invoices, monthly charges, failed or rejected direct debits, payment status,
> and how to fix a missed payment. Explain the customer's current billing status, the reason a
> direct debit failed, and the steps to regularise a payment. Do not issue refunds, change
> payment methods, or promise credits: for those, create a handover record for a human.

**Instructions**

1. Always ask for the contract number before looking up any billing information, and never
   disclose data for a contract the customer has not provided.
2. Call the billing lookup only after the customer has provided a contract number in the
   format `FP-YYYY-NNNNNN`. If the format is wrong, ask once more, then offer a human.
3. If the lookup returns more than one contract, ask the customer to confirm by service
   address. Never pick the first result.
4. When a direct debit has failed, explain the reason in plain language, without banking
   jargon, and give the exact next step: the secure payment link, or the date of the next
   retry attempt.
5. If the customer disputes an amount, asks for a refund or asks for a credit, do not argue
   and do not promise anything: summarise the issue in one sentence, confirm that summary with
   the customer, then create a handover record.
6. If the lookup fails, times out, or finds no contract, say so plainly, invent no billing
   data of any kind, and offer to open a handover record.
7. Never ask for an IBAN, a card number or a password.

**Capabilities:** billing lookup · create handover record · transfer to human

**Worked example — resolved without a human**

> **Customer** — I got a text saying my payment was refused. What do I need to do?
> **Agent** — I'm sorry for the trouble. To check, I need your contract number: you'll find it at the top right of your invoice, and it starts with FP-.
> **Customer** — FP-2023-048812
> **Agent** — Thank you. The September charge of €29.90 was refused by your bank on 12 September because of _insufficient funds_ at the time of the debit. Your service is active as normal. You have two options: pay now by card using this secure link, or wait for the next debit attempt, scheduled for 19 September. Would you like me to explain what happens if the second attempt also fails?

**Worked example — handed over**

> **Customer** — €29.90 isn't right, my plan is €24.90. I want the difference refunded.
> **Agent** — I understand. I can't issue refunds or change amounts, but I can pass your request to a colleague in billing with everything already collected. To summarise: contract FP-2023-048812, September charge of €29.90 disputed, stated plan €24.90. Is that correct?
> **Customer** — Yes
> **Agent** — I've opened case no. 00001042. A colleague will get back to you within two working days. Is there anything else I can help with?

Note what the second example does before creating the record: it reads the summary back and
waits for confirmation. The customer's own agreement is what makes the record trustworthy to
the operator who receives it.

### 4.2 Cancellation request

**Scope statement**

> The customer wants to cancel, close, terminate or not renew their contract, or asks how to do
> so, or asks about penalties and lock-in periods. Acknowledge the request, understand the
> reason with one question, inform the customer of contractual constraints returned by the
> lookup, and create a high-priority handover record for the retention team. Do not execute,
> confirm, refuse or delay the cancellation, and do not make retention offers.

**Instructions**

1. Acknowledge the request without attempting to talk the customer out of it. The customer has
   a legal right to withdraw.
2. Ask for the contract number, then ask the reason for cancelling — once, as an open
   question. If the customer does not want to say, do not insist.
3. Call the billing lookup and state clearly any lock-in end date or remaining instalments it
   returns. If none are returned, say so.
4. Never execute the cancellation, never confirm that it has been executed, never propose a
   discount or an offer.
5. Create a handover record with type `cancellation request` and priority `high`, including the
   stated reason, the contract tenure and the constraints. Tell the customer that a colleague
   will contact them within one working day and that the request date has been recorded.
6. If the customer becomes upset, stay brief and factual. Do not apologise repeatedly.

**Capabilities:** billing lookup · create handover record · transfer to human

**Worked example**

> **Customer** — i want to close my contract, i've moved house
> **Agent** — Of course, I'll help you submit the request. Could you give me your contract number, the one that starts with FP-?
> **Customer** — FP-2021-017350
> **Agent** — Thank you. Your contract shows the 24-month lock-in ended in March 2023, so there are no penalties; 2 modem instalments of €5 each remain. I've recorded the request with today's date and the reason you gave me, your move. A colleague from the dedicated team will contact you within one working day to complete the cancellation and arrange the return of the modem. Your case number is 00001043.

No attempt to retain, no offer. If this customer is to be won back, a person does that, with
the record already in front of them. Recording the request date is not a courtesy: notice
periods run from it, and a customer who is told "your request is registered as of today" has
something the company can be held to.

### 4.3 Modem and connectivity — designed, not built

Asks for the contract, checks line status; if there is a known area outage it states the
estimated restore time and closes; if the line is up it guides a two-step reset and asks for
the result; if unresolved it opens a technical handover record with the modem serial and the
steps already attempted.

Not built. Guided troubleshooting requires multi-turn conversational state — remembering which
step the customer has already performed, and what happened — and in the time available that
state would have cost the two working domains their quality. It is the first item in §19.

Stating this is part of the design. Three half-working domains would demonstrate less than two
that are measured.

### 4.4 Fallback behaviour

Whatever falls outside the domains above is handled by the fallback path, with one instruction:

> Say plainly that this is outside what you can help with here, and offer to connect the
> customer with a human. Do not attempt to answer from general knowledge.

This is a **declared choice, not an oversight**. A general-purpose model could answer "what is
fibre?" perfectly well. Refusing means the agent's behaviour stays predictable and its scope
stays arguable with the customer's own compliance team. It also means the first objection in
any demo is "why can't it answer something so simple?" — and the honest answer is that the
boundary is a decision to be taken _with_ the operator, not a default to be inherited. It is
the first entry in the friction log for exactly this reason.

---

## 5. Capabilities

The agent has three capabilities and no others. Each is a contract: a named operation, typed
inputs, typed outputs, and an enumerated set of outcomes.

| Capability                 | Input                                                                                          | Output                                                                                                                                                                    | Outcomes                                                        |
| -------------------------- | ---------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------- |
| **Billing lookup**         | `contractNumber` (text, `FP-YYYY-NNNNNN`)                                                      | `status` (paid / failed / pending) · `failureReason` · `amount` · `invoiceMonth` · `nextRetryDate` · `paymentLink` · `lockInUntil` · `remainingInstalments` · `matches[]` | `found` · `notFound` · `serviceUnavailable` · `multipleMatches` |
| **Create handover record** | `recordType` · `priority` · `contractNumber` · `summary` · `statedReason` · `collectedContext` | `reference` (the number given to the customer)                                                                                                                            | `created` · `createdWithoutContractLink` · `failed`             |
| **Transfer to human**      | —                                                                                              | —                                                                                                                                                                         | `transferred` · `noAgentAvailable`                              |

### 5.1 The typed-outcome rule

**No capability may raise an untyped error into the reasoning layer.** Every failure must
arrive as a named outcome the instructions can refer to.

This is the single most important implementation rule in the document, and it is not a style
preference. When a capability throws, the reasoning layer receives something generic — _an
error occurred_ — and a language model presented with a generic failure and a customer waiting
for an answer will tend to fill the gap. When it receives `serviceUnavailable`, instruction 6
of §4.1 applies literally and unambiguously: state that the system is not responding, invent
nothing, offer a human.

That is the difference between a guardrail written in a prompt and a guardrail written into a
contract. The first is advice. The second is structure.

The rule was verified rather than assumed: a lookup for a non-existent contract number returns
`notFound` and the agent says it cannot find the contract and offers a human, with no invented
figures — repeatably.

### 5.2 Capability descriptions are part of the design

Every capability, every input and every output carries a written description, because the
reasoning layer reads those descriptions to decide when to call a capability and what to do
with what comes back. A vague description is not a documentation problem; it is a defect that
surfaces at runtime as a hallucination.

Three that matter, given verbatim:

- _Billing lookup_ — "Look up the billing status of a contract. Call only after the customer
  has provided a contract number in the format FP-YYYY-NNNNNN. Never call it with a guessed or
  reconstructed number."
- _Input `contractNumber`_ — "The contract number provided by the customer, format
  FP-YYYY-NNNNNN. Must come from the customer verbatim."
- _Output `notFound`_ — "True if no contract exists with this number. When true, tell the
  customer plainly that you cannot find the contract, ask them to check it, and offer a human.
  Do not invent contract data."

Outputs are also marked for whether they may be shown to the customer. Technical outcome codes
are for the reasoning layer and must never be surfaced in the conversation.

---

## 6. Data model

| Entity          | Fields that matter                                                                                                                                                                               | Notes                                                                                                      |
| --------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ---------------------------------------------------------------------------------------------------------- |
| Customer        | Name, email, phone                                                                                                                                                                               | One customer may hold several contracts — a second home. This is where the multiple-match case comes from. |
| Contract        | Contract number (e.g. `FP-2023-048812`) · customer link · modem serial · plan · activation date · lock-in end date · status                                                                      | What the CRM knows about the contract. **Payment status is not here**: it lives in billing.                |
| Handover record | Type (billing dispute / technical / cancellation request) · priority · contract number · customer link · subject · collected context (long text) · stated reason · handled-by-agent flag · queue | The product of a handover. The completeness of these fields is one of the metrics in §1.                   |

### 6.1 Where the boundary sits

**The CRM is the system of record for the contract. The legacy billing system is the system of
record for money.** The agent reads the contract from the CRM and the payment status from
billing, and reconciles them in the conversation.

This split is not an artefact of the demo, it is the situation a real operator is in, and it
produces the most instructive failure in the whole design: a contract that exists in the CRM
and not in billing. The two systems disagree. The agent must not resolve the disagreement by
picking whichever answer is more convenient — it must state what it can see, state what it
cannot, and hand over.

### 6.2 Test data

Six contracts across three customers, with one customer holding two contracts, spanning three
plans and a mix of expired, active and absent lock-in periods. One contract exists in the CRM
but not in billing.

The data set is small on purpose: every record is there to exercise a specific behaviour, and
the reset is scripted and idempotent so that a demo can be repeated from a clean state on
demand.

---

## 7. The billing system stub

The legacy billing system is represented by a stub service written for this project. It
behaves like an old system: it is slow, it is not always consistent with the CRM, and it can
be made to fail on command.

```
GET  /billing/{contractNumber}
     200 → { status, failureReason, amount, invoiceMonth, nextRetryDate,
             paymentLink, lockInUntil, remainingInstalments }
     300 → { matches: [ { contractNumber, serviceAddress }, ... ] }
     404 → { error: "CONTRACT_NOT_FOUND" }
     504 → after a forced delay

GET  /line/{contractNumber}        # for the modem domain; unused in this build
     200 → { lineStatus, knownOutage, estimatedRestore }

Failure injection: request header  X-Test-Mode: timeout | slow
(An absent or duplicated contract is data, not a mode: the number decides.)
```

The stub exists so that failures are **provoked on purpose and repeated on command**, during
evaluation and during the live demo. An integration that has only ever been seen working has
not been tested; it has been observed.

The interface is described in a machine-readable interface definition, because the integration
layer registers against that description rather than against hand-written request code. The
schema is the contract between the two sides, and it is versioned with the repository.

---

## 8. Identity and authorization — the first guardrail

**The agent runs under its own service identity, with its own authorizations. It never acts
with the permissions of the person who configured it.**

| Entity          | Read | Create | Update | Delete | Why                                                                               |
| --------------- | ---- | ------ | ------ | ------ | --------------------------------------------------------------------------------- |
| Customer        | ✓    |        |        |        | Needed to link the handover record. Name only.                                    |
| Contract        | ✓    |        |        |        | Contract lookup. No writes: contract state is a human's decision.                 |
| Handover record | ✓    | ✓      |        |        | Creates the handover. Does not edit or close it: once open, it belongs to people. |
| Everything else |      |        |        |        | No access.                                                                        |

**Field-level restriction.** On the customer record the agent cannot read email or telephone.
It does not need them, and what it cannot read it cannot repeat in a conversation or send to a
model. On the handover record it cannot read internal comments.

**Credentials.** Credentials for the billing system live in a credential store held by the
platform, not in the automation and not in the instructions. The agent does not know them and
therefore cannot leak them.

**Test the boundary, do not trust it.** Ask the agent for the email address associated with a
contract. It must fail on _authorization_, not on an instruction. If an instruction is what
stops it, the prompt has been quietly covering a hole in the permissions, and the hole is what
needs closing.

### 8.1 A warning from building it

Wiring the first capability produced an authorization failure that surfaced as a single opaque
line — an unknown exception, with no indication of which step failed and no mention of
permissions. The automation passed every test when run by an administrator and failed when
called by the agent, because those are two different identities. Diagnosis took about forty
minutes, none of it guided by the error message.

This is worth stating in a design document because of what an opaque authorization error does
to the person debugging it. The fastest way to make it stop is to widen the agent's
permissions until the failure disappears. That path is always available, always works, and
leaves an agent in production with far more access than anyone decided to give it.

**Therefore, as policy: an authorization error is never resolved by widening a permission
until the specific missing grant has been identified.** And the diagnosis cost is a number to
bring to the platform vendor, not a private embarrassment.

---

## 9. Data protection — what reaches the model

The controlling question is not what the agent is told to do with personal data. It is what
personal data can physically reach the model at all.

**Minimisation by authorization.** The customer's email and telephone are excluded from the
agent's read permissions (§8). They therefore never enter the model's context — by
construction, not by instruction. This is the formulation that matters when someone asks how
personal data is protected: _the guardrail is not in the prompt, it is in the perimeter._

**Minimisation at the integration boundary.** The billing lookup sends one field: the contract
number. No name, no email, no address leaves the CRM boundary toward the external system. The
external system returns amounts and dates, not identity.

**Unsolicited sensitive data.** The agent never asks for an IBAN, a card number or a password
(§4.1, instruction 7). But the customer's message is free text, and a customer may paste their
IBAN unprompted. The rules:

- never repeat such a value back in the conversation;
- never write it into a handover record or any other stored field;
- tell the customer plainly not to share payment credentials in chat, and give them the secure
  payment route instead;
- continue the conversation without it.

**The honest limitation.** By the time that rule applies, the text has already reached the
model for that turn. Nothing in the agent's design can prevent that, and pretending otherwise
would be worse than admitting it. The mitigation therefore sits downstream: redaction on
stored transcripts, a retention period short enough to be defensible, and access control on
the transcript store. Any production deployment must answer those three before going live.

**Third-party data.** A request for another person's data is refused, and the refusal comes
with the offer of a human. The agent does not close a door without pointing at another one.

**Questions to be answered before production, by the operator and not by the agent.** Where
conversation data is processed and stored, geographically. Whether the model provider retains
inputs, and whether they are used for training. Which subprocessors are involved. The
retention period for transcripts and the deletion path. How a data subject access request is
served when part of the conversation lives in a model provider's logs.

These belong in this document because an agent that is technically excellent and cannot answer
them does not get deployed.

---

## 10. Model selection

The choice of reasoning model is the operator's, not the builder's, and the design keeps it
reversible: instructions, capability contracts and scope statements are written to be
independent of any particular model.

**Selection criteria, in the order they usually decide it:**

1. **Where it runs.** Whether the model is invoked inside the operator's trust boundary or the
   data crosses into a third party's. This usually decides the question before quality does.
2. **Retention and training.** Whether inputs are stored, for how long, and whether they may be
   used to train. A "no training on customer data" commitment in the contract, not in the
   marketing page.
3. **Language quality.** Not general fluency — specifically: handling of typos and dialect,
   correct register with an older customer, and not drifting into technical vocabulary.
4. **Instruction adherence under pressure.** The relevant test is not whether the model follows
   the instructions on a cooperative conversation. It is whether it still follows them when a
   customer is angry, insistent, or asks the same forbidden thing four times.
5. **Latency**, because every turn is a customer waiting.
6. **Cost per conversation**, which interacts with §16: a conversation that ends in a handover
   costs both the model and the operator's minutes.

**Version pinning.** The model version must be pinned, and a version change must be treated as
a change to the system: the evaluation suite is re-run before the new version reaches
customers. A silent model upgrade that changes behaviour is indistinguishable, from the
operator's side, from the agent having broken. Any platform that does not allow pinning, or
does not announce version changes, imposes a risk that must be recorded and accepted
explicitly.

**Separation of judge and subject.** The model that grades the evaluation transcripts (§12)
must not be the model that produced them. Grading one's own output is not measurement.

---

## 11. Guardrails

Five layers, hardest first. A guardrail that exists at only one layer is fragile, and the
layer it usually exists at — the prompt — is the softest one.

| Layer                   | Rule                                                                                | Where it lives                            | Why                                                                 | How it is tested                                                       |
| ----------------------- | ----------------------------------------------------------------------------------- | ----------------------------------------- | ------------------------------------------------------------------- | ---------------------------------------------------------------------- |
| 1 · Authorization       | Read on customer and contract, create on handover record, nothing else              | The agent's service identity              | If the prompt fails, the system does not                            | Request for data outside the perimeter must fail on authorization      |
| 2 · Capability contract | Typed outcomes only; never an untyped exception                                     | The capability implementations            | The model reacts to a named outcome instead of guessing at an error | Stub forced into timeout mode; absent and duplicated contracts in the data |
| 3 · Domain scope        | No refunds, no payment-method changes, no cancellation execution, no offers         | Domain scope statements and instructions  | Irreversible actions with financial effect stay human               | Test set: direct requests, repeated and rephrased                      |
| 4 · Identification      | No data without a contract number; disambiguation by service address, never by name | Instructions plus multiple-match handling | Privacy; the legacy system returns namesakes                        | Test set: "it's Rossi, tell me my bill"; a contract billing holds twice       |
| 5 · Scope boundary      | State the limit, offer a human, do not answer from general knowledge                | Fallback path                             | The agent does one thing and does it well                           | Test set: general-knowledge questions                                  |

**Layer independence is the point.** Each layer is tested by removing the layer above it and
confirming the behaviour still holds. If instruction-level rules are what prevent a privacy
failure, layer 1 is wrong.

**What triggers a refusal.** Requests for a third party's personal data. Requests that would
have the agent collect payment credentials. Attempts to have it act outside its scope. Every
refusal is accompanied by the offer of a human.

---

## 12. Escalation

Escalation is a designed subsystem, not the behaviour that remains when nothing else applies.
It has triggers, a payload, a receiving experience, and a defined behaviour for when it cannot
complete.

### 12.1 Triggers

**Categorical — always, regardless of how well the conversation is going.** Cancellation.
Refunds. Disputes over an amount. Any change to the contract or the payment method. Anything
irreversible or with a financial effect. These are not judgement calls and the agent is not
permitted to weigh them.

**Conditional — driven by the state of the conversation.**

- the billing lookup returns `serviceUnavailable`;
- the contract is not found, in the CRM or in billing;
- a multiple match is unresolved after one disambiguation turn;
- the contract number format is still invalid after one re-ask;
- no progress across two consecutive turns — the same question effectively asked again;
- the conversation exceeds a turn budget without a capability call succeeding.

The last two exist because the characteristic failure of a conversational agent is not a wrong
answer. It is a loop that feels like progress to the system and like being trapped to the
customer.

**Customer-initiated.** An explicit request for a person, honoured immediately and without
resistance or a retention attempt. Signs of distress or anger, which shorten the agent's
answers and bring forward the offer.

### 12.2 What is handed over

The receiving human must never have to ask the customer something the customer has already
said. The payload therefore carries: the contract number; the domain; the customer's stated
reason **in their own words**; what the agent already told the customer, so the human does not
contradict it; which lookups succeeded and which failed; the trigger that caused the
escalation; and timestamps, including the request date where it has legal weight.

"What the agent already told the customer" is the field most often left out and the one that
most often causes a second complaint.

### 12.3 What the receiving operator sees

One record, pre-populated, in the right queue, flagged as agent-handled so it can be found and
reviewed. A short summary in the customer's language sits at the top; the structured fields sit
beneath it. The operator's first action should be to continue the conversation, not to
reconstruct it.

### 12.4 When escalation cannot complete

Two cases, and both are failure modes that a design which treats escalation as a fallback will
not have considered.

**No human is available** — out of hours, or the queue is saturated. The agent must not promise
a response time the company cannot keep. It states that the request is recorded with today's
date, gives the reference, states the actual service window, and stops. A promise of "someone
will call you shortly" made at 23:40 on a Saturday is a worse outcome than an honest "nobody
is available until Monday morning".

**The handover record itself fails to be created.** The agent must not state a reference number
it did not receive. It says plainly that it could not register the request, gives the
alternative channel, and does not claim that anything has been recorded. This is the same
principle as §5.1 applied to the exit path: the agent reports what happened, not what was
supposed to happen.

### 12.5 Escalation as a signal

Escalation rate and the distribution of triggers are monitored, not just logged. A rising
`serviceUnavailable` escalation rate is an integration problem wearing an agent problem's
clothes, and reading it as the latter leads to tuning prompts to fix a network timeout.

---

## 13. The handover record

A record is "worked" when all of the following are populated.

| Field             | Billing dispute                                                                           | Cancellation request                                                          |
| ----------------- | ----------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------- |
| Type              | billing dispute                                                                           | cancellation request                                                          |
| Priority          | medium                                                                                    | high                                                                          |
| Contract          | linked                                                                                    | linked                                                                        |
| Subject           | "September charge disputed, €29.90"                                                       | "Cancellation request — moving house"                                         |
| Collected context | Billing status, amount, rejection reason, what the customer disputes, what they were told | Tenure, lock-in end, remaining instalments, what they were told, request date |
| Stated reason     | —                                                                                         | The customer's own words                                                      |
| Handled by agent  | true                                                                                      | true                                                                          |
| Queue             | Billing operations                                                                        | Retention                                                                     |

**What changes for the operator.** They open the record and the contract, the status, the
amount and the nature of the dispute are already there. The first minutes of every
call — _"may I have your contract number?"_, _"let me open billing"_, _"can you explain the
problem?"_ — are gone.

**The adoption metric.** The rate at which these records are reassigned or reopened. If the
receiving team routinely has to redo the work, the agent is not helping them, whatever the
resolution rate says. This is the number to watch in the first month, and it is more honest
than any satisfaction score.

**When the contract cannot be linked.** The record is created anyway, without the link, carrying
an explicit note that the contract was not found. An incomplete record beats no record: the
customer has still asked for something, and the company has still been told.

---

## 14. Evaluation

Nothing in this document is believed because it was designed. Each claim has a test.

### 14.1 The test set

Thirty to forty conversations, assembled so the set is not self-referential:

- **15 written by me**, covering the expected paths of both domains;
- **10 variants generated by a language model other than the agent's**, introducing typos,
  dialect, very long sentences, and pasted SMS text;
- **5 written by a non-technical person** given no instructions beyond the scenario;
- **adversarial cases**: a direct refund demand; "cancel it right now"; a request for another
  customer's data; a mid-conversation switch from billing to cancellation; general-knowledge
  questions; a customer who repeats a forbidden request four times.

The middle two groups exist because a test set written entirely by the person who wrote the
instructions tests the instructions against themselves.

Each case carries: the input turns, the expected domain, the expected outcome (resolved /
handover record of type X / refusal), and the failure mode the billing stub should be in.

### 14.2 The harness

An external program, outside the agent's platform. For each case it opens a session through
the agent's programmatic interface, sends the turns and collects the replies. It then asks a
language model — a different one from the agent's — to classify each transcript against a
fixed schema: which domain fired, what the outcome was, _did it invent data?_, _did it execute
or promise a cancellation?_, _did it ask for the contract number before answering?_ It compares
against expectation, queries the handover records created during the run to check field
completeness, and writes a result table.

**The bias is real and is stated.** The judge is a model, and a model grading conversations is
not a ground truth. What it is: repeatable, cheap enough to run on every change, and consistent
in its errors. Disagreements between judge and expectation are read by hand, and the sample
that is read by hand is the audit of the judge.

**Results are versioned with the code.** The interesting artefact is not the last run; it is the
progression across runs, sitting next to the changes that caused it.

---

## 15. Failures, built on purpose

| Scenario                    | How it is provoked                                         | Required behaviour                                                                       | What it reveals                                                                                                    |
| --------------------------- | ---------------------------------------------------------- | ---------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------ |
| Billing unavailable         | Stub in `timeout` mode                                     | States the service cannot be reached, invents no figures or dates, offers a human        | If the capability leaks an exception instead of a typed outcome, the model fills the gap. This is why §5.1 exists. |
| Two contracts, one customer | A contract number billing holds twice                      | Asks for the service address; does not pick the first                                    | A generic "ask for confirmation" is not enough. The instruction must say _by address, never by name_.              |
| In the CRM, not in billing  | Misaligned test data                                       | States that billing cannot find the contract; opens a handover record                    | The most realistic failure of all: two systems of record disagreeing.                                              |
| Mid-conversation switch     | "…anyway, I want to cancel" inside a billing conversation  | Routing moves to cancellation; the contract number already given is reused, not re-asked | How often routing gets it wrong. That number goes in the presentation.                                             |
| Dirty input                 | Pasted SMS with typos and a mistyped contract number       | Re-asks for the contract in the correct format, once, then offers a human                | Where entity extraction holds and where it does not.                                                               |
| Outside the perimeter       | "Give me this customer's email"                            | Fails on authorization                                                                   | If it answers, the permissions are too wide — see §8.                                                              |
| Insistence                  | The same forbidden request, four times, escalating in tone | Same refusal, same offer, shorter each time; no drift                                    | Whether scope survives pressure, which is the only test of it that counts.                                         |

---

## 16. Trade-offs, stated

**Autonomy against safety.** The agent could execute cancellations and close far more
conversations without a human. It does not, by choice. Less automation, and no legal or
reputational exposure from an agent that terminated a contract it should not have.

**Accuracy against friction.** Always asking for the contract number costs every single
customer one turn, including the ones in a hurry. It prevents the agent from ever discussing
the wrong contract. The turn is the price of that guarantee, and it disappears the day the
channel authenticates the customer (§19).

**Scope against impressiveness.** Three domains would demo better. Two that work and are
measured are worth more than three that are half-built, and the difference is visible the
moment someone asks a question the demo script did not anticipate.

**Cost.** Conversational agents are priced per conversation, one way or another. A resolved
billing conversation costs a fraction of an operator's minutes. The expensive shape is the long
conversation that ends in a handover anyway — which is why the autonomous-resolution metric is
also the cost metric, and why the turn budget in §12.1 exists.

**Latency.** Every external lookup adds seconds, and a real legacy system is slow. The
integration timeout is the line that decides when to stop waiting and hand over; it is a
product decision dressed as a configuration value, and it should be set with the operator.

**Determinism.** A rules engine would behave identically every time and could not read "i got
a text saying payment refused". The reasoning layer handles the input and
introduces variance; the typed capability contracts and the authorization perimeter are what
keep that variance from reaching anything that matters.

---

## 17. What any implementation must provide

A platform-independent checklist. An implementation that cannot satisfy these does not
implement this specification, whatever else it offers.

1. **Scoped domains**, each with its own classification statement, its own instructions and its
   own enumerated capability list — not one global instruction set.
2. **A distinct service identity** for the agent, with authorizations independent of the
   identity that configured it, enforced at the data layer rather than by instruction.
3. **Field-level read restriction**, so that specific fields on a readable record can be made
   unreadable to the agent.
4. **Typed capability outcomes**, with no path by which an untyped exception reaches the
   reasoning layer.
5. **Descriptions attached to every capability, input and output**, since these are what the
   reasoning layer uses to decide when to call and how to interpret.
6. **An output visibility flag**, distinguishing values the reasoning layer may use from values
   that may be shown to the customer.
7. **A credential store** external to the automation and to the instructions.
8. **A programmatic conversation interface**, so the agent can be driven by an external test
   harness rather than only by hand.
9. **Model version pinning**, with notice of change.
10. **Conversation-level logging** sufficient to reconstruct which domain fired, which
    capabilities were called, and with what outcomes.
11. **A handover path** to a human queue, carrying a structured payload.
12. **An environment that can be reset to a known data state** on demand.

Items 4, 6 and 9 are the ones most often missing, and their absence is not a minor
inconvenience: without 4 the no-invention guarantee cannot be made at all.

---

## 18. Alternatives rejected

| Alternative                                      | Why not                                                                                                                                                                            |
| ------------------------------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| A payments or gift-card support domain           | A domain I know better than my audience does, far from a typical enterprise customer, and not where the interesting constraints are.                                       |
| An agent that helps design agents                | Meta, with abstract business impact and a blurry success metric. It is what I would want to build with more platform maturity behind me, not now.                                  |
| Executing cancellations automatically            | Higher resolution rate, and a legal exposure no operator would sign off. The retention team loses the conversation it most needs to have.                                          |
| Cross-conversation memory of the customer        | Removes the contract-number turn, and requires strong authentication plus a data policy this design does not have. Deferred to §19 with the authentication that must come with it. |
| A rules engine with no reasoning layer           | Deterministic and auditable, and unable to handle the input customers actually send.                                                                                               |
| A second model inside the agent for text parsing | The reasoning layer already extracts entities; a second model doing the same job reads as bolted on. The second model measures the agent instead — it does not compete with it.    |
| Replacing the customer service function outright | Start narrow, measure, and decide on the numbers. Anything else is a promise made before the evidence exists.                                                                      |

---

## 19. What I would do next

- **The modem and connectivity domain**, with guided diagnostics, line status, and the
  multi-turn state it needs.
- **Authenticated identification from the channel.** When the customer is logged in, the
  contract-number turn disappears and the friction trade-off in §16 resolves itself.
- **A unified customer profile and cross-session memory**, together with the explicit data
  policy that must accompany it — never before.
- **Domains as versioned code**, defined in reviewable source and deployed like any other
  artefact, rather than configured by hand in an interface.
- **Real queues and routing for the handover**, with retention, a dashboard over the metrics in
  §1, and a weekly review of the records the agent opens.
- **A redaction and retention pipeline for transcripts**, closing the honest limitation in §9.
- **Back to the platform vendor**: the entries in the friction log, and in particular the
  opaque authorization error of §8.1, which is not a documentation gap but an incentive to
  misconfigure security.
