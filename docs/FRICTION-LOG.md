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
from the spec's own worked examples — §4.1's replies are roughly eighty words of
Italian:

| Role | Budget | Why |
| --- | --- | --- |
| agent | 1024 | an Italian reply, possibly with a tool call |
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
