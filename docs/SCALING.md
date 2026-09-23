# Scaling

What breaks first, in what order, and what the fix costs.

This is deliberately not a list of things one could do to any system. It is an
audit of *this* repository: every limit below is named with the file that
imposes it, and the ones that are already fine are listed too, because knowing
what you do **not** have to rebuild is the more useful half.

Four axes, and they fail in a different order than people expect:

| Axis | First hard limit | Where |
|---|---|---|
| **Concurrency** — more conversations at once | replica #2 | [api/app.py:39](../backend/src/fibrapunto/api/app.py#L39) |
| **Breadth** — more domains | ~8–10 domains | [router/classifier.py](../backend/src/fibrapunto/router/classifier.py) |
| **Depth** — more capabilities per domain | comfortably past 50 | — already fine |
| **Tenancy** — more operators | the first one | [perimeter/policy.py](../backend/src/fibrapunto/perimeter/policy.py) |

The interesting result is the third row. Adding capabilities — the thing that
sounds like it should be the scaling problem for an agent — is the axis this
design already handles, and it handles it because of §17.1, not because of
anything done for performance.

---

## Part 1 — Concurrency

### 1.1 Sessions live in a process dictionary · *breaks at replica #2*

```python
_sessions: dict[str, Conversation] = {}
```

[api/app.py:39](../backend/src/fibrapunto/api/app.py#L39). Two backend replicas
behind a load balancer and a customer's second turn arrives at the process that
never saw the first one, which answers `404 no such conversation`. This is not a
throughput ceiling that arrives at some interesting load — it arrives the moment
there is more than one process.

**The fix is small, and §3 is the reason.** "The agent has no memory across
conversations" means session state is short-lived and self-contained: there is
no cross-session identity model to design, no profile to merge, no consent
question to answer first. `ConversationState` and `CollectedState` are already
Pydantic models, so the change is serialise-on-write into Redis with a TTL
matching the session timeout, and rehydrate in `_require()`. A day, including
the tests.

Two things to get right while doing it:

- **Rehydrating must not resurrect the `pending_confirmation` lock incorrectly.**
  The §4.1 read-back gate lives in state; a botched round-trip turns a
  confirmation lock into a confirmed cancellation. That wants a test before the
  change, not after.
- **Optimistic concurrency on the session row.** Two turns posted in parallel
  for one conversation currently interleave inside one process; across processes
  they would last-write-wins over each other's `CollectedState`. A version column
  and a 409 is the honest answer.

### 1.2 The SSE stream is bound to the process holding the trace

[api/app.py:152](../backend/src/fibrapunto/api/app.py#L152) streams from
`convo.trace` — an in-memory object. Even with sessions in Redis, the browser's
`EventSource` must land on the process running the turn, or it watches an empty
trace.

Two options, and they are not equivalent:

- **Sticky routing** by conversation id. Ten minutes of ingress config. Keeps
  the interleaving that makes the trace panel worth having, and defers the
  problem rather than solving it — a replica restart drops every stream on it.
- **Fan out through Redis pub/sub**, with the trace store as the replay source
  on reconnect. Correct, survives restarts, and pairs with the `Last-EventID`
  handling the browser already does for free. Maybe two days.

Sticky routing first; pub/sub when a replica restart during a demo stops being
acceptable. Worth saying plainly: this is the one place where the design choice
that makes the demo good — one channel, replies and trace interleaved — is also
the one that costs the most to scale. I would still make it again.

### 1.3 Connection count, and why the perimeter survives a pooler

Each replica opens **three** pools at startup
([api/app.py:45](../backend/src/fibrapunto/api/app.py#L45)): the agent gateway
as `fibrapunto_agent` (max 5), the trace store as `platform` (max 5), and the
operator view as `platform_admin` (max 3). Thirteen connections per replica
against a default `max_connections = 100` puts the wall at **replica #7** — which
arrives *before* most people think to look at the database.

The good news is structural. The perimeter uses **distinct connection users**
rather than `SET ROLE` (there is no `SET ROLE` anywhere in the codebase — worth
knowing, because it is the thing that would have made this hard). PgBouncer keys
its pools on user+database, so transaction-mode pooling preserves the identity
boundary exactly: a connection checked out for the agent pool *is* a
`fibrapunto_agent` connection, and column grants apply as they do today.

Had the perimeter been implemented as `SET ROLE` on a shared superuser
connection — the shape it is tempting to reach for — transaction pooling would
have leaked roles across conversations, and the §8 boundary would have quietly
stopped existing under load. That it doesn't is a side effect of choosing real
roles for the *demonstrability* argument in
[DECISIONS.md](DECISIONS.md). Worth recording as a case where the
honest choice was also the scalable one.

### 1.4 The router is a second model call on every turn

[conversation.py:151](../backend/src/fibrapunto/conversation.py#L151) classifies
before the domain node runs. Two LLM round-trips per turn: one cheap, one not,
and the cheap one still costs a network hop of latency the customer feels.

This is the **first cost lever at volume**, ahead of any prompt tuning. In order
of what I would actually do:

1. **Already done:** the confirmation lock skips classification entirely when a
   `pending_confirmation` is answered with an acknowledgement
   ([conversation.py:147](../backend/src/fibrapunto/conversation.py#L147)). In a
   §4.1 flow that is a meaningful fraction of turns.
2. **Skip classification on the first turn of a session** when the entry point
   already implies the domain — a deep link from a "problem with my bill"
   page is a stronger signal than a classifier reading one sentence.
3. **A local embedding classifier** over the scope statements, with the LLM
   consulted only when the margin between the top two is thin. The hysteresis in
   [router/policy.py:37](../backend/src/fibrapunto/router/policy.py#L37) already
   expresses this shape — `SWITCH_THRESHOLD` is a confidence gate — so the
   change is swapping where confidence comes from, not adding a concept.

Note what is *not* on this list: making the agent call cheaper by shortening the
domain instructions. Those are §4's actual content; trading them for latency is
trading the thing being demonstrated.

---

## Part 2 — Breadth: more domains

### 2.1 The classification prompt grows linearly, and this is the real limit

`classifiable_domains()` feeds every scope statement into the classifier on
every turn. At two domains that is a few hundred tokens. At twenty it is the
dominant cost of the cheap call, paid on every turn of every conversation, and —
worse — classification accuracy degrades as the options crowd each other, which
is a §1 metric.

**Two-stage routing** is the answer, and it wants designing rather than tuning:
a coarse pass over ~5 families (billing / contract / technical / sales /
other), then a fine pass within the chosen family. Each call sees five options
instead of twenty, both stay cheap, and the confusion matrix gets smaller in a
way that is measurable with the harness that already exists.

The thing to be careful about: §15's mid-conversation switch must still work
*across* families, not just within one. A cancellation request arriving inside a
billing conversation crosses a family boundary, and that is precisely the case
§15 was built to handle. The hysteresis and `CollectedState` carry-over don't
change; the classifier's shape does.

### 2.2 The registry is Python; the prompts are not

[domains/registry.py:74](../backend/src/fibrapunto/domains/registry.py#L74)
defines `DOMAINS` as a module-level dict. Adding a domain is a deploy. The
instruction files, by contrast, are already hot-reloadable
([api/app.py:219](../backend/src/fibrapunto/api/app.py#L219)) — an operator can
change a procedure and the next conversation follows it.

That asymmetry is the gap §19 names: *"domains as versioned code, defined in
reviewable source and deployed like any other artefact, rather than configured
by hand in an interface."* The prompts got there; the registry didn't. Moving
the domain definitions into YAML beside the prompt files — scope statement,
capability tuple, escalation defaults, enabled flag — closes it, and the
capability tuple stays validated against `CapabilityName` at load time so §17.1's
enforcement doesn't weaken into a string comparison.

Deliberately **not** proposing a UI for this. §19 says "rather than configured by
hand in an interface" for a reason: a domain's capability list is a security
boundary, and security boundaries belong in code review.

### 2.3 What gets harder that isn't technical

At twenty domains the escalation matrix in
[handover/routing.py](../backend/src/fibrapunto/handover/routing.py) stops being
a table one person holds in their head, and §13's "the record is written for the
colleague who picks it up" needs per-queue required-field sets rather than
per-record-type ones. That is modelling work, not scaling work, but it arrives
on the same schedule and is usually what actually slows the rollout down.

---

## Part 3 — Depth: more capabilities

**This axis is already fine, and it is worth understanding why.**

Each domain binds only its own tuple. Fifty capabilities spread across twelve
domains still puts three or four tool schemas in any given prompt, because
[graph/build.py](../backend/src/fibrapunto/graph/build.py) builds the stub list
per domain. Context per call stays flat as the *system's* surface area grows —
which is the property people usually try to buy later with tool-retrieval
machinery, and here falls out of §17.1's enumerated capability list.

§17.1 was written as a safety argument: *"a design where every instruction
applies to every conversation does not satisfy this section."* The scaling
property is a consequence nobody designed for. It is the strongest thing in this
document, and the one I would lead with.

What does need doing, all of it small:

- The `match name:` dispatch in
  [graph/tool_node.py](../backend/src/fibrapunto/graph/tool_node.py) becomes a
  registry lookup. An afternoon.
- `perimeter-policy.yaml` grows rows. It is already data, already renders to SQL,
  already drift-tested — this is editing a file.
- The outcome unions grow. `output_visibility` walks them at import time, so the
  visibility guard extends itself; that was the point of building it that way.

---

## Part 4 — Tenancy: the one genuine architectural fork

Everything above is work. This one is a decision.

The §8 perimeter is Postgres **roles** with column grants, and that is exactly
why it is demonstrable: `make perimeter-proof` runs the same query under two
identities, and `test_perimeter_raw_sql.py` proves the denial with no Python in
the loop. Serving many operators from one deployment breaks the mechanism —
Postgres roles do not scale to thousands of tenants, and a role per tenant per
capability multiplies from there.

**The answer is two mechanisms doing two different jobs:**

- **Row-level security** with a `tenant_id` column and a policy bound to a
  session variable, for tenant isolation.
- **Column grants, unchanged**, for the field-level rule in §17.3. `email` and
  `phone` stay ungranted to the agent role in every tenant.

They compose — RLS filters rows, grants filter columns, and neither weakens the
other. What must not happen is the tempting simplification: dropping to one
application-level `WHERE tenant_id = $1` in the gateway. That is §8.1's failure
wearing different clothes — *"an authorization model expressed as instructions
is not an authorization model"* applies just as well to one expressed as a
query the application promises to always write.

The specific risk is worth naming because it is subtle: with a connection pooler
in transaction mode, RLS driven by `SET app.tenant_id` needs the variable set
inside the same transaction as the query, not on checkout. Get that wrong and
tenant A reads tenant B's rows intermittently, under load, in a way no test
catches. If this fork is taken, that test gets written first.

---

## Part 5 — Scaling the team, and the harness

### 5.1 The eval harness is what makes a team possible

One engineer editing prompts can hold the regressions in their head. Four
cannot. The harness in [evals/](../evals/) is the artefact that makes concurrent
prompt work safe, and its own scaling properties matter:

- **`make eval-quick` is free.** The deterministic-first split
  ([assertions.py](../evals/src/evals/assertions.py)) means four of §1's six
  metrics need no model at all. At 200 cases this is the difference between an
  eval that runs on every pull request and one that runs monthly because someone
  has to approve the spend.
- **The judge is the part that scales badly.** One call per case, and it is the
  expensive model by design (§14.2 requires a different vendor from the agent).
  At 200 cases the judge should move to a batch API — judging is not latency-
  sensitive and batch pricing is roughly half.
- **`--concurrency 2` is a provider ceiling, not ours.** The driver is fully
  async against HTTP; the limit is the in-flight budget documented in
  [FRICTION-LOG.md](FRICTION-LOG.md). On a paid account this is one flag.

### 5.2 The friction log is a scaling artefact

Not obviously — but the thing that actually slows a growing team is re-deriving
decisions. [DECISIONS.md](DECISIONS.md) and
[FRICTION-LOG.md](FRICTION-LOG.md) exist so the fourth engineer does not
re-litigate the perimeter design, and the reversed entries are the highest-value
ones: they are the only record of paths that look reasonable and are not.

---

## Part 6 — Cost at volume

§16 states the shape: *"the expensive shape is the long conversation that ends in
a handover anyway — which is why the autonomous-resolution metric is also the
cost metric."* Three observations about what that means at scale:

**Prompt caching gets better with volume, not worse.** The stable block —
identity plus domain instructions, split out in
[graph/build.py](../backend/src/fibrapunto/graph/build.py) with
`cache_control: ephemeral` — is byte-identical across *every conversation in the
system*, not merely across turns within one. At low traffic the cache expires
between conversations and the split earns little. At real throughput the hit rate
approaches one. This was built for per-conversation cost on day 4 and happens to
be the component that scales best.

**Authenticated identification is the largest single win available, and it is
already in §19.** *"When the customer is logged in, the contract-number turn
disappears and the friction trade-off in §16 resolves itself."* One fewer turn on
every conversation is a double-digit percentage of both cost and latency across
the whole system — larger than any model swap, and it is a channel integration
rather than an agent change.

**The turn budget is the tail-cost control.** §12.1's budget caps the
conversation that would otherwise run twenty turns and hand over anyway. At
volume the mean is fine and the tail is the bill; that trigger is what bounds it.

---

## What I would actually do, in order

1. **Sessions to Redis, sticky SSE routing.** Unblocks horizontal scaling. ~1 day.
2. **PgBouncer.** Before replica #7, not after. ~half a day.
3. **Two-stage routing.** Before domain five, while the confusion matrix is still
   small enough to reason about. ~2 days.
4. **Domain definitions to YAML.** Closes §19's gap; makes 3 cheaper. ~1 day.
5. **Judge to a batch API.** When the case count passes ~100.
6. **Tenancy fork, if and only if it is actually needed.** The RLS-plus-grants
   design above, with the transaction-scoped `tenant_id` test written first.

Everything above 3 is plumbing with known answers. Item 6 is the only one where
getting it wrong is expensive, and it is the only one I would want to argue
through with someone else before writing code.
