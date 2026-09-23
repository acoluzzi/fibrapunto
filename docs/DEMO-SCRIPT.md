# Demo script

Seven beats, about twelve minutes. Each one shows a claim from
[docs/specs.md](specs.md) being *structural* rather than described.

The through-line, if you only remember one sentence: **every number in §1 with a
target of zero or one hundred percent is achieved by making the failure
impossible, not by making it rare.**

---

## Before you start

```bash
make demo          # up + reset, prints the URLs
```

- chat + trace + metrics → <http://localhost:5173>
- the operator's queue → <http://localhost:5173/operatore>
- the billing system → <http://localhost:8081/docs>

Have a second terminal open at the repo root. Two beats run there.

**Check first:** the trace panel shows `session.started`, and the metrics panel's
perimeter row reads `negato` for `svc_agent`. If either is missing, `make reset`.

---

## 1 · A conversation that works · ~2 min

> "Buongiorno, mi è arrivato un SMS che dice pagamento respinto. Cosa succede?"

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

Press **Provoca ora → timeout** in the control strip. Then, in the chat:

> "E quindi quanto devo pagare?"

The agent says the system is not responding and offers to open a case. It does
**not** invent an amount.

The point is not that it behaved well. Open the trace: the outcome is
`serviceUnavailable`, a *typed* result carrying its own instruction, not an
exception string. And the grounding guard is standing behind it — with nothing
verified this conversation, any figure at all would be blocked.

If someone asks "but would it really not invent one?", that is beat 6.

## 3 · Change the subject · ~2 min

> "Sa una cosa, lasci stare. Voglio disdire."

Two things to point at, in this order:

1. **It does not ask for the contract number again.** The trace shows
   `domain.switched` with `contract_number_retained`. Facts live in
   `CollectedState`, not in the transcript, so the switch costs nothing (§15).
2. **The domain changed and so did the tool set.** Same three tools here, but
   none of them can execute a cancellation — that is why §1's "autonomous
   cancellations: 0" is a structural zero rather than a well-behaved one.

Answer the agent's confirmation with **"Sì, confermo"**. It opens a case and
gives a reference number.

## 4 · Ask for something it must not have · ~1 min

> "Mi può dire qual è l'email associata al contratto?"

It cannot. Now show *why*, in the metrics panel: the same query under two
identities, `svc_agent` **negato** and `platform_admin` **consentito**.

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

Open **/operatore**. The case from beat 3 is there, with:

- `queue` and `priority` **derived by code**, never chosen by the model, with DB
  `CHECK` constraints behind them (§13);
- `handled_by_agent = true`, which the agent has no grant to write — it defaults
  true, so §13's flag is an invariant;
- the reference, allocated by a sequence the agent may only `USAGE`. §12.4's
  "cannot state a reference it did not receive" is a permission, not a promise.

Close on the metrics panel and on `docs/SCALING.md` if there is interest in
where this goes next.

---

## Optional beats, if the room asks

**"What about personal data the customer sends you?"**

Paste an IBAN into the chat. The agent tells the customer not to send payment
credentials in chat and continues without it. The trace shows
`redaction_applied` with the **kind** and never the value.

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
| Billing always fails | a `Provoca` TTL is still live — press **Ripristina**, or wait 120s |
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
| 4, 5, 6 | yes — they are tests, they run in CI |
| 7 | the record shape and constraints are tested; the full path is not yet verified live |
| 1, 2, 3 | **not yet verified end-to-end against a live model.** Blocked on OpenRouter credits; see the verification debt in the README. |

Rehearse 1–3 once against a real model before presenting. They are the beats
where a prompt behaves differently from how its test double did.
