# Demo script

**45 minutes. Region Leads, FDEs, architects.**

The through-line, if you only remember one sentence: **every number in §1 with a
target of zero or one hundred percent is achieved by making the failure
impossible, not by making it rare.**

## The 45 minutes

| | Minutes | What |
|---|---|---|
| **A** | 0–4 | Framing: the problem, and why the design document comes first |
| **B** | 4–16 | The demo — seven beats, below |
| **C** | 16–26 | Decisions, including the two Salesforce parallel slides |
| **D** | 26–30 | What is measured, and what is not yet |
| — | 30–45 | Questions |

Thirty minutes of content, fifteen of questions. If you overrun, **cut from C,
never from B** — the demo is the evidence and the decisions can be compressed to
the two slides plus one sentence each.

No purely commercial profiles in the room, so the technical depth is fine and
you can open on a `GRANT`. But Region Leads want to know *why it matters*, not
only how it works: every beat below has a line about the why, and those lines
are the ones to keep when time is short.

---

## Before you start

```bash
make demo          # up + reset, prints the URLs
```

- chat + trace + metrics → <http://localhost:5173>
- the operator's queue → <http://localhost:5173/operator>
- the billing system → <http://localhost:8081/docs>

Every port is bound to `127.0.0.1`: nothing is reachable from the venue
network, only from this laptop.

**Sign in to /operator before going on stage.** It opens on a sign-in form;
the password is `OPERATOR_PASSWORD` from `.env`, and the session lasts eight
hours. Without a password set, the form says operator access is disabled and
the view refuses every request, by design.

Have a second terminal open at the repo root. Two beats run there.

**Check first:** the trace panel shows `session.started`, and the metrics panel's
perimeter row reads `denied` for `svc_agent`. If either is missing, `make reset`.

---

---

## A · Framing · 4 min

Three things, no slides needed beyond a title:

1. **The problem.** An Italian broadband operator. Billing questions resolved
   autonomously; cancellations never executed by the agent, only prepared for a
   human. Two domains that work, not three that half-work.
2. **The design document came first**, and it is deliberately
   technology-agnostic — it names no platform, no vendor, no framework. That is
   what makes §17 a checklist an implementation either satisfies or does not.
3. **The claim being made** is not "it chats well". It is that four things the
   document asserts are *structural in the code rather than written in a
   prompt*. The next twelve minutes are those four things.

> Do not read the spec's table of contents aloud. One sentence on the problem,
> one on why the document exists, one on what is about to be shown.

---

## B · The demo · 12 min

Seven beats. Each shows a claim being structural rather than described.

---

## 1 · A conversation that works · ~2 min

> "Good morning, I got a text saying my payment was refused. What's going on?"

The agent asks for the contract number before discussing anything. **Say that
out loud** — §4.1's first instruction, and §16 states the trade-off honestly: it
costs every customer one turn, including the ones in a hurry, and it prevents
the agent from ever discussing the wrong contract.

> "FP-2023-048812"

Watch the trace panel, not the chat. `router.decision` → `domain.entered` →
`capability.invoked` → `capability.result` with outcome `found`. The domain and
the outcome are **reported facts**, which is the reason §14's metrics can be
computed without asking a model what it thinks happened.

Point at the capability list in the metrics panel: three tools, bound to this
domain, not to the agent.

## 2 · Break the billing system mid-conversation · ~2 min

Press **Restart**, then **New Chat**. A fresh conversation, on purpose: the
amount from beat 1 is already held in that conversation's state, so the agent
could answer from it without a new lookup and the outage would never show.

> "Hi, I need to know how much my next bill is."

The agent asks for the contract number. **Now** press **Provoke now → Failure**
in the control strip — before the number is given, so the lookup it triggers is
the one that fails. Then:

> "FP-2023-048812"

The agent says the system is not responding and offers to open a case. It does
**not** invent an amount.

The point is not that it behaved well. Open the trace: the outcome is
`serviceUnavailable`, a *typed* result carrying its own instruction, not an
exception string. And the grounding guard is standing behind it — with nothing
verified this conversation, any figure at all would be blocked.

Press **Provoke now → Restore** before moving on, so beat 3 runs against a
working billing system.

If someone asks "but would it really not invent one?", that is beat 6.

## 3 · Change the subject · ~2 min

> "You know what, forget it. I want to cancel."

Two things to point at, in this order:

1. **It does not ask for the contract number again.** The trace shows
   `domain.switched` with `contract_number_retained`. Facts live in
   `CollectedState`, not in the transcript, so the switch costs nothing (§15).
2. **The domain changed and so did the tool set.** Same three tools here, but
   none of them can execute a cancellation — that is why §1's "autonomous
   cancellations: 0" is a structural zero rather than a well-behaved one.

Answer the agent's confirmation with **"Yes, that's correct"**. It opens a case and
gives a reference number.

## 4 · Ask for something it must not have · ~1 min

> "Can you tell me the email address on the contract?"

It cannot. Now show *why*, in the metrics panel: the same check under two
identities, `svc_agent` **denied** and `platform_admin` **allowed**. Be precise
about what that row is: the policy file (`contracts/perimeter-policy.yaml`)
evaluated in Python, which exists so a denial has a readable reason. The
database enforcing it is beat 5.

Then the line that matters:

> "The guardrail is not in the prompt. It is in the perimeter — the column is
> not granted to the role the agent connects as."

## 5 · Prove it without the application · ~1 min

Second terminal:

```bash
make perimeter-proof
```

Same gateway call, both identities, printed side by side.

Then the stronger one — this is the beat that separates a demo from a claim:

```bash
uv run pytest backend/tests/test_perimeter_raw_sql.py -v
```

These tests connect to Postgres **as `fibrapunto_agent` with no Fibrapunto code
in the loop**. `SELECT email` raises SQLSTATE 42501. Nothing in the Python can
influence the outcome.

> "§8 says test the boundary, do not trust it. A Python check can be refactored
> around by someone who does not know why it is there. A missing column grant
> cannot."

## 6 · Remove the layer above · ~1 min

```bash
uv run pytest backend/tests/test_guardrails.py -k "without_any_prompt" -v
```

§11's own test methodology, made executable: the grounding and promises guards
are re-run **with the prompt removed entirely**, and they still hold.

This is the answer to beat 2's sceptic. The agent does not decline to invent an
amount because it was asked nicely.

## 7 · The record a human actually receives · ~2 min

Open **/operator**. The case from beat 3 is there, with:

- `queue` and `priority` **derived by code**, never chosen by the model, with DB
  `CHECK` constraints behind them (§13);
- `handled_by_agent = true`, which the agent has no grant to write — it defaults
  true, so §13's flag is an invariant;
- the reference, allocated by a sequence the agent may only `USAGE`. §12.4's
  "cannot state a reference it did not receive" is a permission, not a promise.

The customer's email and phone are on this page, read as `platform_admin`.
Say it before someone asks:

> "This is a demo operator UI behind one shared password. In production it is a
> separate service behind SSO — it reads what the agent is denied, so it must
> never share an origin or a login with the chat."

Do not close here — go to the decisions. The metrics panel belongs in D.

---

## C · Decisions · 10 min

Four things, in this order. The first three are one slide or one sentence each;
the fourth is the pair of Salesforce slides.

**1 · Why a design document first (1 min).** Because §17 is a checklist, and a
checklist only means something if it was written before the code. Every
deviation from it is recorded in `DEVIATIONS.md` and **the spec was never edited
to agree with the build** — a design document that absorbs every implementation
compromise stops being evidence of anything.

**2 · Why LangGraph, and where it was wrong (2 min).** Explicit graph, per-node
tool binding, so §4's "a router plus a small number of bounded domains" is drawn
rather than described. But its prebuilt `ToolNode` fails §5.1 in *both* branches
— one returns an error string to the model, the other re-raises — so the node is
ours. That is the more interesting half: a guarantee written into a contract
beats one inherited from a default you did not choose.

**3 · Why Postgres was the highest-leverage choice (2 min).** Not about data. It
is the only self-contained way to make §8's boundary fail on *authorization*
rather than on an instruction. The trade-off is real and belongs in the friction
log: the denial arrives as `permission denied for table customer`, which §8.1
warns pushes engineers to widen permissions until the error stops. The
constructive answer was parsing SQLSTATE 42501 into a readable
`FieldNotAuthorized`.

**4 · How this maps to Agentforce (5 min).** Two slides —
[SALESFORCE-PARALLEL.md](SALESFORCE-PARALLEL.md). The first is the mapping;
the second is where the platform wins and where you would build it anyway. Land
on its closing line: half this code would be deleted on Agentforce because the
platform covers it better, and the design document would not change at all.

---

## D · What is measured · 4 min

Open the metrics panel, then say the part that matters:

**Four of §1's six metrics are computed by code, not by a judge**, because
`AgentReply` reports which domain fired and which capabilities ran with which
outcomes as *facts*. The judge — a different vendor from the agent, enforced by
a database `CHECK` constraint — is asked only what a model can uniquely answer:
register, tone, whether the Italian is any good.

**Disagreements between the two are the audit of the judge**, not noise:
`make eval-audit`.

Then volunteer the gap before anyone finds it:

> "One group in §14's test set calls for cases written by a non-technical
> person. Mine are written by me in a non-technical register, which is not the
> same thing. It is in `DEVIATIONS.md`, and the harness prints that warning on
> every run rather than leaving it in a file nobody opens."

Close on [SCALING.md](SCALING.md) if there is appetite: what breaks first is
in-memory sessions at replica #2, and the connection pooler wall arrives at
replica #7.

---

## Questions you should expect

Prepared answers, because these are the predictable ones.

| Question | Where the answer lives |
|---|---|
| "Why not build it on the platform?" | DECISIONS #1 — every claim is inspectable; a `GRANT` you can read beats a permission set you have to be shown |
| "How do you stop it hallucinating a figure?" | beat 2 + beat 6 — the grounding guard, tested with no prompt at all |
| "What happens when the model changes?" | §10 — model ids are config strings, `assert_served_model` on every call, `pinned_on` recorded |
| "How would this scale?" | SCALING.md — replica #2, then #7, then two-stage routing before domain five |
| "What did you get wrong?" | FRICTION-LOG.md entry 4 — the sequence grant the Python layer structurally cannot catch |
| "Can anyone open the operator view?" | not without the operator password, and only from this laptop. Demo gate: in production it is a separate service behind SSO, never on the chat's origin |
| "What is not finished?" | the status table at the bottom of this file. Say it plainly. |

---

## Optional beats, if the room asks

**"What about personal data the customer sends you?"**

Paste an IBAN into the chat. The agent tells the customer not to send payment
credentials in chat and continues without it. The trace shows
`privacy.redaction_applied` with the **kind** and never the value.

Then say the honest part, because it is in the spec and volunteering it is
better than being asked: §9 admits that by the time that rule applies, the text
has already reached the model. The mitigation is that redaction runs *before*
the request leaves the process, and that the other three enforcement points —
storage, handover record, outbound — are unconditional. `GET /api/retention`
shows the retention period and, more usefully, `overdue: 0`.

**"How do you know any of this works?"**

```bash
make eval-quick      # deterministic assertions, no judge, free
make eval-audit      # where the judge and the code disagreed
```

§14.2's argument in one command: the disagreements *are* the audit of the judge.

**"What would you do differently?"**

[FRICTION-LOG.md](FRICTION-LOG.md) — five entries with hours attached. The one
worth telling is entry 4: a missing `USAGE` grant on a sequence, which the
Python policy layer **structurally cannot catch**, because it checks columns and
a sequence is not one.

---

## If something goes wrong

| Symptom | Fix |
|---|---|
| Agent replies with the safe fallback repeatedly | a guard is firing; open the trace, it names which one |
| Billing always fails | a `Provoke` TTL is still live — press **Provoke now → Restore**, or wait 120s |
| Empty operator queue | `make reset`, then re-run beat 3 |
| Chat does not stream | SSE needs `proxy_buffering off`; check the frontend container is the built image, not a stale one |
| Model errors mentioning credits | OpenRouter balance — the agent cannot run without it |

**Do not** debug live. Every beat above except 1 and 3 works from a fresh
`make demo`, so the recovery is to reset and pick up at the next beat.

---

## Status of these beats

Honest, because the alternative is finding out on stage.

| Beat | Verified |
|---|---|
| 4, 5, 6 | yes — they are tests in the committed suite, and they are the ones you run on screen |
| 7 | the record shape and its constraints are tested; the full path is not yet verified live |
| 1, 2, 3 | **not yet verified end-to-end against a live model.** Blocked on OpenRouter credits; see the verification debt in the README. |

`.github/workflows/ci.yml` runs lint, the artefact drift checks, the migrations
and the full suite against a real Postgres on every push. It has not executed
yet — this repository has no remote.

Rehearse 1–3 once against a real model before presenting. They are the beats
where a prompt behaves differently from how its test double did.
