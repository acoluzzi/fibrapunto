# Fibrapunto — a customer service agent for an invented fibre operator

A working conversational agent, built in ten days, that resolves billing and direct-debit
questions on its own and hands cancellation requests to a human with the case already worked.

The design is in [docs/specs.md](docs/specs.md). It is deliberately technology-agnostic: it
describes what the agent does, what it must never do, how it is bounded and how it is measured,
without naming a platform, a model vendor or a framework. **Anything in this repository that
contradicts that document is a bug in this repository.**

## What this is, and what it is not

This is a **self-contained build**. It runs from `docker compose up` on a laptop with no external
platform, no org and no tenant. The one external dependency is a model provider, reached through
OpenRouter so that the choice of model stays a config string (§10).

It is not a demo of a product. The interesting part is not that it talks; it is that four claims
the spec makes are **structural in the code rather than asserted in a prompt**:

| Claim                                                                      | Where it lives                   | How you check it                                                           |
| -------------------------------------------------------------------------- | -------------------------------- | -------------------------------------------------------------------------- |
| A capability never raises an untyped error into the reasoning layer (§5.1) | `capabilities/runtime.py`        | `make test` — the stub is driven through every failure mode and fuzzed     |
| The agent cannot read a customer's email (§8)                              | Postgres column grants           | `make perimeter-proof`, or connect as `fibrapunto_agent` in `psql` and try |
| The agent never invents a billing figure (§1, §15)                         | `guardrails/output_grounding.py` | Flip the stub to `timeout` mid-conversation and watch                      |
| Escalation is a designed subsystem, not a fallback (§12)                   | `escalation/triggers.py`         | `make eval` — trigger distribution is reported per run                     |

## Running it

```bash
cp .env.example .env     # add an OpenRouter key
make up                  # postgres + billing stub + backend + frontend
make reset               # load the known data state (§17.12) — idempotent
make demo                # open the browser
```

Then:

```bash
make test                # the invariants above
make eval                # ~30 conversations against the §1 metrics
make perimeter-proof     # the same query under both identities, side by side
```

## Where things are

| Path                          | What                                                                                      |
| ----------------------------- | ----------------------------------------------------------------------------------------- |
| `docs/specs.md`               | The design. The authoritative document.                                                   |
| `docs/DECISIONS.md`           | Every choice that shaped the build, why it was made, what it cost                         |
| `docs/ARCHITECTURE.md`        | How the code maps onto the spec's sections                                                |
| `docs/DEVIATIONS.md`          | Where the implementation departs from the spec, and why                                   |
| `docs/FRICTION-LOG.md`        | What fought back, with the cost in hours                                                  |
| `docs/DEMO-SCRIPT.md`         | The 45-minute running order, and what to say during each beat                             |
| `docs/SALESFORCE-PARALLEL.md` | How each piece maps onto Agentforce — and where the platform wins                         |
| `docs/SCALING.md`             | What breaks first as this grows, in what order, and what the fix costs                    |
| `contracts/`                  | The three contracts: billing OpenAPI, capability definitions, the authorization perimeter |
| `backend/`                    | The agent and the platform                                                                |
| `mock-billing/`               | The legacy billing system, which is slow and can be made to fail on command (§7)          |
| `evals/`                      | The test set, the harness, and every run that has ever been made                          |
| `frontend/`                   | Chat, the system trace, and the metrics                                                   |

## Data protection note

Conversation content is sent to a model provider reached via OpenRouter, which is therefore a
subprocessor. Requests are pinned to a single provider (`allow_fallbacks: false`) and restricted
to providers that do not retain prompts (`data_collection: "deny"`). Transcripts are stored
redacted and deleted on a retention schedule. The questions §9 says must be answered before
production are answered by the operator, not by this repository.

---

Built for the Salesforce Forward Deployed Engineer Builders Panel.
