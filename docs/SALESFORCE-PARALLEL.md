# How this would be done on Salesforce

Material for two slides. It is not the content of the panel — it is the evidence
that building outside the platform was **a decision and not a fallback**.

The thesis in one sentence: *the specification is platform-independent, and this
implementation proves it by building elsewhere — but every piece has an
Agentforce counterpart, and in half the cases the native one is better.*

---

## Slide 1 — The mapping

| This implementation | On Salesforce |
|---|---|
| `DOMAINS` with a scope statement and its own instructions | **Topics** in Agent Builder — classification description and instructions per topic |
| The per-domain capability tuple | **Actions** assigned to the topic (Apex invocable, Flow, prompt template) |
| `crm.handover_record` | **Case**, with record type, priority and queue |
| `queue` derived from the domain | **Assignment rules** + **Omni-Channel** routing |
| The Postgres role `fibrapunto_agent` | **Agent User** with its own permission set |
| `REVOKE SELECT (email, phone)` | **Field-Level Security** on the agent's profile |
| `privacy/detectors.py` and the four enforcement points | **Einstein Trust Layer** — data masking |
| `MODEL_AGENT` as a configuration string | **Trust Layer** / BYOLLM |
| `platform.trace_event` | Trust Layer audit trail |
| `evals/` | **Testing Center** for agents |

The line to say out loud while it is on screen:

> "§4 of the specification describes Topics without calling them that. That is
> not a coincidence: I wrote it trying to say *what* is needed, not *which
> product*. The fact that it maps this cleanly is the check that the spec really
> is platform-independent."

---

## Slide 2 — Where the platform wins, and where it does not

This is the slide that counts. The first one demonstrates knowledge; this one
demonstrates judgement.

### Free on Salesforce, days of work here

- **Field-Level Security.** Declarative, auditable, with a UI an administrator
  uses without calling an engineer. I have `GRANT`s generated from a YAML file
  and a renderer to maintain. The platform wins outright.
- **Case, Queue, Omni-Channel.** Routing, escalation, SLAs, reassignment: here
  that is one table and one view. There it is a mature product.
- **PII masking.** The Trust Layer does it out of the box. My checksummed
  detectors are ~200 lines to maintain and to extend for every new format.
- **Zero retention with the provider.** Contractual. I get it with a flag on
  OpenRouter and declare the subprocessor in the README.

### To be built anyway, on any platform

- **Typed outcomes (§5.1).** An Apex invocable action declares a return type,
  but an unhandled exception reaches the planner as a generic error. The
  guarantee that "no untyped exception reaches the reasoning layer" is a
  discipline you write, not a default you inherit. The same is true here:
  LangGraph's `ToolNode` does the wrong thing in *both* branches, which is why
  the node is ours.
- **The grounding guard.** That every figure in a reply came from a check made
  in *this* conversation is not something any platform guarantees: it requires
  knowing the domain.
- **The deterministic half of the evaluation.** A Testing Center gives you the
  structure; that routing accuracy is computed by code from reported facts
  rather than from a judge's opinion is a design choice.
- **The turn budget, the router hysteresis, the confirmation lock.** All product
  rules.

### The closing line

> "If I rebuilt this for a customer on Agentforce, I would rewrite about half
> this code and delete the other half — because the platform already covers it
> better. What would not change is the design document: the boundaries, the
> typed outcomes, what gets measured. That is the part I wanted to show, and it
> is why I built it somewhere every claim can be inspected rather than
> demonstrated."

---

## Note for whoever presents

Two risks in this section:

1. **Do not overclaim.** An architect in the room knows Agentforce better than
   anyone who has read the documentation for a week. The claims above are at the
   level of concepts — Topics, Actions, FLS, Trust Layer, Omni-Channel — and at
   that level they hold. If someone asks for a configuration detail, the answer
   is "I don't know, I didn't build on an org for this": it is true, and better
   than an approximation that will be corrected.
2. **Do not make it the centre.** It is two slides out of forty-five minutes.
   The content is the agent and the reasoning behind it; this section exists
   only to take the question "but can he work on the platform?" off the table.
