# The 15-minute demo

Seven cards, in order. Each card is what you type, what to point at, and the
one line to say. Typed lines are bullets; stage directions are in brackets.
The `extra-*` cards are for questions or spare minutes, never for the plan.

The through-line: **every zero in the metrics is achieved by making the
failure impossible, not by making it rare.** Say it once at the start and once
at the end.

## Running order

| #   | Card                                                  | Feature shown                                                                                                              | Min | At   |
| --- | ----------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------- | --- | ---- |
| 1   | [case01-rejected-dd](case01-rejected-dd.md)           | contract first, verification, typed outcomes in the trace, read-back, no promise                                           | 2.5 | 0:00 |
| 2   | [case02-cancelling-offer](case02-cancelling-offer.md) | domain switch keeps the facts, no cancel tool exists, one platform offer, operator decides                                 | 3   | 2:30 |
| 3   | [case03-billing-down](case03-billing-down.md)         | billing down then back, no invented figure; IBAN redacted and the payment change opened by code; email refused; a person on request | 4   | 5:30 |
|     | metrics, from the console                             | four of six computed by code, the judge audited                                                                            | 1   | 9:30 |
|     | slack, or the terminal proof                          | `make perimeter-proof`, then the raw-SQL test if asked                                                                     | 4.5 | 10:30 |

Ten and a half minutes of content. Cards 1, 2 and the first half of 3 are
the evidence: **never cut them**. If you are behind at 8:00, drop the last two
lines of card 3 (the email, the person) and the terminal.

Card 3, line by line, what to point at:

- **Billing down.** Trace: `capability.result` with outcome **serviceUnavailable**, a typed result with its instruction attached. No amount, no date.
- **Let's retry.** Billing back up, the lookup succeeds, the figures come from it.
- **IBAN.** Trace: `privacy.redaction_applied` with the kind, never the digits. The case to change the payment method opens at once, by code: §12.1 makes it categorical, so the agent does not ask.
- **Email.** It cannot. Metrics panel, perimeter row: `svc_agent` denied, `platform_admin` allowed. The column is not granted to the role.
- **A person.** Agreed at once, no why, no retention attempt. The case it names is the one already open on this conversation: one per type, reused, never a second.

## Before you start

```bash
make demo          # up + reset, prints the URLs
```

- Chat on the left half of the screen: <http://localhost:5173>
- Console on the right half: <http://localhost:5173/console>, signed in
- Operator in a third tab: <http://localhost:5173/operator>, signed in
- Analyst in a fourth tab: <http://localhost:5173/analyst>, signed in
- A second terminal at the repo root, for the perimeter proof

The console follows the chat tab: it switches when you press **New
conversation** or pick another conversation there, shows "typing" while a
turn is running, and reloads the transcript and the metrics when the reply
lands. Both tabs must be in the same browser profile. The console shows the
transcript as the backend stores it, so the security answer reads as a
placeholder there and as typed in the chat: that contrast is worth a sentence
in card 1.

Check, in the console: every switch healthy (none red), the trace shows
`session.started`, the perimeter row reads `denied` for `svc_agent`. If any is
missing, `make reset` and reload.

**Colleagues switch.** On "opening hours" it follows the real clock, Rome,
Monday to Friday 8:00 to 20:00. Outside that window every handover reply
appends the closed notice. If you present outside those hours and do not want
that wording in cards 1 and 2, set Colleagues to **available** before you
start. Set it to **none available** only if you want the last line of card 3 to show the closed-hours template.

**Do not run** `make eval`, `make eval-quick` or `make reset` during the demo:
each one resets the database and empties the operator queue.

## Contracts used

| Contract       | Customer      | Security answer | Why this one                                                         |
| -------------- | ------------- | --------------- | -------------------------------------------------------------------- |
| FP-2023-048812 | Marco Bianchi | Torino          | cards 1 and 2: the refused September debit, retry 19 Oct, no lock-in |
| FP-2022-031455 | Anna Esposito | Micio           | card 2's fresh-conversation fallback                                 |
| FP-2019-002217 | Giulia Rossi  | Birba           | held twice in billing (extra: multiple matches)                      |
| FP-2020-004199 | Anna Esposito | Micio           | in the CRM, absent from billing (extra)                              |
| FP-2022-027601 | Paolo Greco   | Bari            | card 3: billing down, IBAN, email, person; paid, nothing owed, own customer |
| FP-2024-092003 | Marco Bianchi | Torino          | live lock-in to 10 May 2027, 2 instalments (extra)                   |
| FP-2021-017350 | Giulia Rossi  | Birba           | pressure case (extra)                                                |

The security answer is checked and forgotten: it never reaches the model or
the transcript. Say it the first time you type one.

**One open case per type per contract, from any conversation.** A second
request of the same type on the same contract gets the existing reference back
(`handover.reused_open_case`), never a second case. That is why cards 1 and 2
share a contract (dispute, then cancellation: two types) and card 3 runs on
its own customer, Greco, so nothing it opens can reuse a case of Bianchi's. If you rehearsed, `make reset`
before going on stage so no case from the rehearsal is reused live; `make
demo` does that for you.

## If something goes wrong

| Symptom                                            | Do                                                                 |
| -------------------------------------------------- | ------------------------------------------------------------------ |
| Safe fallback reply, repeatedly                    | a guard fired; the trace names which one. Say so, it is a feature. |
| Billing always fails                               | a switch is still off; red ones are the ones to put back           |
| The agent asks the contract number again in card 2 | start a fresh conversation with the fallback lines on the card     |
| Empty operator queue                               | run card 2's fallback lines in a new conversation                  |
| Model errors mentioning credits                    | OpenRouter balance; nothing to do on stage, go to the terminal proof |

Do not debug live. Every card except 2 works from a fresh conversation.
