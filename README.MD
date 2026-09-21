# Fibrapunto — an Agentforce customer service agent

A working Agentforce agent for a fictional Italian broadband operator, built in ten days
for the Salesforce Forward Deployed Engineer Builders Panel.

It handles two things a real broadband operator gets thousands of times a month — a failed
direct debit and a cancellation request — and it is deliberate about a third: when to stop,
and hand the conversation to a human with the context already collected.

The design, the trade-offs and the things that broke are in **[docs/spec.md](docs/spec.md)**.
This file is about how to rebuild it and how to run it.

---

## Shape

```
customer
   │
   ▼
Agentforce agent  "Fibrapunto Assistenza Clienti"
   ├── subagent: Billing and Direct Debit
   ├── subagent: Cancellation Request
   └── actions
         ├── Get Billing Status ──▶ Flow ──▶ External Service ──▶ mock-billing/  (outside the org)
         └── Create Support Case ─▶ Flow ──▶ Case

evals/ ──▶ Agent API ──▶ the agent, replayed over a fixed test set
       └─▶ Claude (Agent SDK) grades each transcript against a fixed schema
```

Two components live outside Salesforce, both written for this project:

- **`mock-billing/`** stands in for the customer's legacy billing system. It can be told to
  time out, to return two contracts for one customer, or to find nothing at all. That is
  where the interesting failures come from — see `docs/spec.md §12`.
- **`evals/`** replays a fixed set of conversations against the agent and has Claude grade
  each transcript. Every number in the presentation came out of here.

The agent itself is configuration in a Salesforce org, not code in this repo. What the repo
holds is everything needed to rebuild that configuration, plus everything around it that is
code.

---

## Layout

```
docs/
  spec.md                  design, guardrails, metrics, failure modes
  friction-log.md          what the platform made hard, and why it matters to a customer
salesforce/
  scripts/
    reset-demo-data.apex   wipes and recreates accounts + contracts; run before any demo
  metadata/                retrieved with SF CLI (optional; see below)
mock-billing/
  src/                     the fake legacy billing API
  openapi.yaml             the contract External Services registers against
  README.md                endpoints, test modes, deploy
evals/
  cases/                   the test set, one file per conversation
  run.ts                   the harness
  results/                 one file per run, committed on purpose
```

`evals/results/` is committed deliberately. The point is not the last run: it is the
progression across runs, next to the commits that caused it.

---

## Rebuilding from scratch

Assumes a Salesforce Developer Edition with Agentforce enabled
([sign up](https://developer.salesforce.com/signup)), Node 20+, and an Anthropic API key.

### 1. Data model

Object Manager → Create → Custom Object.

|                |                                                    |
| -------------- | -------------------------------------------------- |
| Label / Plural | `Fibrapunto Contract` / `Fibrapunto Contracts`     |
| Object Name    | `Fibrapunto_Contract` (Salesforce appends `__c`)   |
| Record Name    | `Contract Number`, type **Text** — not auto-number |

Fields:

| Label           | API name             | Type                                                      |
| --------------- | -------------------- | --------------------------------------------------------- |
| Account         | `Account__c`         | Lookup(Account)                                           |
| Plan            | `Plan__c`            | Picklist: `Fibra 1000`, `Fibra 300`, `FWA`                |
| Modem Serial    | `Modem_Serial__c`    | Text(20)                                                  |
| Activation Date | `Activation_Date__c` | Date                                                      |
| Lock-in Until   | `Lock_in_Until__c`   | Date                                                      |
| Status          | `Status__c`          | Picklist: `Active`, `Suspended`, `Cancellation requested` |

Then, in Developer Console → Debug → Open Execute Anonymous Window, run
`salesforce/scripts/reset-demo-data.apex`. It is idempotent: re-run it whenever the demo
data needs to be clean.

### 2. Mock billing

```bash
cd mock-billing
npm install
npm start          # local, port 3000
```

Deploy it anywhere that gives you HTTPS — External Services will not register an `http://`
endpoint. See `mock-billing/README.md` for the endpoints and the test-mode flags.

### 3. Wiring the org to the mock

1. **Named Credential** (Setup → Named Credentials) pointing at the deployed mock. The
   credentials live here and nowhere else: the agent never sees them.
2. **External Service** (Setup → External Services) registered from `mock-billing/openapi.yaml`
   against that Named Credential.
3. **Flow** `Get Billing Status`, autolaunched. Input `contractNumber` (Text). It calls the
   External Service and **always returns a typed outcome** — `found`, `serviceUnavailable`,
   `matches` — and never lets an exception reach the agent. This is not a detail; it is the
   reason the agent does not invent data when the billing system is down. See `spec.md §6`.
4. **Flow** `Create Support Case`, autolaunched. Inputs: case type, priority, contract number,
   summary, stated reason, collected context. Output: case number.

### 4. The agent

Agent Builder → new agent, type Service. Agent-level instructions hold one rule only —
respond in Italian, plainly, no jargon. Everything else belongs to a subagent.

Create the two subagents by hand. **Do not rely on the natural-language wizard**: given a
description that defined the subagent line by line, it created the agent and its user and no
subagent at all (`docs/friction-log.md`, entry 4). The full text of both subagents —
description, scope, instructions — is in `spec.md §5`; paste it verbatim.

Attach the actions to the subagents and give each input and output a real description. The
reasoning engine reads those descriptions to decide when to call an action and what to do
with what comes back; a vague output description is a bug that surfaces as a hallucination.

### 5. Permissions

The agent runs as its own user, not as whoever configured it. Narrow its permission set to
exactly this and nothing else:

| Object                   | Read | Create | Why                                                                       |
| ------------------------ | ---- | ------ | ------------------------------------------------------------------------- |
| `Account`                | ✓    |        | Link the Case to the customer. Name only — not email, not phone.          |
| `Fibrapunto_Contract__c` | ✓    |        | Look up the contract. No writes: contract state is a human's decision.    |
| `Case`                   | ✓    | ✓      | Create the handover. No edit, no delete: once open, it belongs to people. |

Test the boundary rather than trusting it: ask the agent for a customer's email address. It
must fail on permissions, not on an instruction. If an instruction is what stops it, the
permissions are too wide.

### 6. Evals

```bash
cd evals
npm install
cp .env.example .env     # ANTHROPIC_API_KEY, org credentials
npm run evals            # writes results/<timestamp>.md
```

---

## What is not here

- **The modem/connectivity subagent.** Designed, not built. Multi-turn guided troubleshooting
  needs conversational state that would have cost the two working subagents their quality.
  `spec.md §5` describes it; it is the first thing I would build next.
- **Headless 360 MCP.** The harness could read Cases back out of the org through the hosted
  MCP server instead of querying directly — the Claudeforce shape. It is in beta and it does
  not change any number in the presentation, so it stayed out.
- **Full metadata in `salesforce/metadata/`.** Nice to have, not on the critical path. The
  rebuild instructions above are the source of truth.

---

## Conventions

- Commit messages in English, imperative, one concern each.
- `.env` is git-ignored from the first commit. No API key, no org credential, no session id
  ever enters this repo.
- Docs in English. The agent speaks Italian to customers; the repo speaks to engineers.
- `docs/friction-log.md` is kept in Italian, because it is a working notebook. Its contents
  reach the panel translated.
