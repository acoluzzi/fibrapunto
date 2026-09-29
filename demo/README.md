# The 15-minute demo

Seven cards, in order. Each card is what you type, what to point at, and the
one line to say. Typed lines are bullets; stage directions are in brackets.
The `extra-*` cards are for questions or spare minutes, never for the plan.

The through-line: **every zero in the metrics is achieved by making the
failure impossible, not by making it rare.** Say it once at the start and once
at the end.

## Running order

| #  | Card                                                     | Feature shown                                                            | Min | At    |
| -- | -------------------------------------------------------- | ------------------------------------------------------------------------ | --- | ----- |
| 1  | [case01-rejected-dd](case01-rejected-dd.md)              | contract first, verification, typed outcomes in the trace, read-back, no promise | 2.5 | 0:00  |
| 2  | [case02-cancelling-offer](case02-cancelling-offer.md)    | domain switch keeps the facts, no cancel tool exists, one platform offer, operator decides | 3   | 2:30  |
| 3  | [case03-billing-down](case03-billing-down.md)            | failure mid-conversation, `serviceUnavailable` is typed, no invented figure | 1.5 | 5:30  |
| 4  | [case04-iban-pasted](case04-iban-pasted.md)              | redaction before the model, the trace shows the kind never the value     | 1   | 7:00  |
| 5  | [case05-email-boundary](case05-email-boundary.md)        | the perimeter is a column grant, proven from the terminal                | 1.5 | 8:00  |
| 6  | [case06-scope-and-person](case06-scope-and-person.md)    | out-of-scope refusal, a person on request, closed-hours template         | 1.5 | 9:30  |
| 7  | [case07-metrics](case07-metrics.md)                      | four of six metrics computed by code, the judge audited                  | 1   | 11:00 |
|    | slack                                                    |                                                                          | 3   | 12:00 |

Twelve minutes of content, three of slack. Cards 1, 2 and 3 are the
evidence: **never cut them**. If you are behind at 8:00, cut in this order:
card 6, then card 4, then the terminal half of card 5.

## Before you start

```bash
make demo          # up + reset, prints the URLs
```

- Chat on the left half of the screen: <http://localhost:5173>
- Console on the right half: <http://localhost:5173/console>, signed in
- Operator in a third tab: <http://localhost:5173/operator>, signed in
- Analyst in a fourth tab: <http://localhost:5173/analyst>, signed in
- A second terminal at the repo root, for card 5

Check, in the console: every switch healthy (none red), the trace shows
`session.started`, the perimeter row reads `denied` for `svc_agent`. If any is
missing, `make reset` and reload.

**Colleagues switch.** On "opening hours" it follows the real clock, Rome,
Monday to Friday 8:00 to 20:00. Outside that window every handover reply
appends the closed notice. If you present outside those hours and do not want
that wording in cards 1 and 2, set Colleagues to **available** before you
start, and back to **none available** only for card 6.

**Do not run** `make eval`, `make eval-quick` or `make reset` during the demo:
each one resets the database and empties the operator queue.

## Contracts used

| Contract       | Customer      | Security answer | Why this one                                      |
| -------------- | ------------- | --------------- | ------------------------------------------------- |
| FP-2023-048812 | Marco Bianchi | Torino          | cards 1 and 2: the refused September debit, retry 19 Oct, no lock-in |
| FP-2023-048712 | Giulia Rossi  | Birba           | card 3: billing down, no open dispute on it        |
| FP-2022-031455 | Anna Esposito | Micio           | card 4: IBAN paste; also card 2's fresh-conversation fallback |
| FP-2019-002217 | Giulia Rossi  | Birba           | held twice in billing (extra: multiple matches)    |
| FP-2020-004199 | Anna Esposito | Micio           | in the CRM, absent from billing (extra)            |
| FP-2024-092003 | Marco Bianchi | Torino          | live lock-in to 10 May 2027, 2 instalments (extra) |
| FP-2021-017350 | Giulia Rossi  | Birba           | pressure case (extra)                              |

The security answer is checked and forgotten: it never reaches the model or
the transcript. Say it the first time you type one.

**One open case per type per contract, from any conversation.** A second
request of the same type on the same contract gets the existing reference back
(`handover.reused_open_case`), never a second case. That is why cards 1 and 2
share a contract (dispute, then cancellation: two types) and cards 3 and 4 do
not (every billing-domain case is a dispute). If you rehearsed, `make reset`
before going on stage so no case from the rehearsal is reused live; `make
demo` does that for you.

## If something goes wrong

| Symptom                                  | Do                                                                 |
| ---------------------------------------- | ------------------------------------------------------------------ |
| Safe fallback reply, repeatedly          | a guard fired; the trace names which one. Say so, it is a feature. |
| Billing always fails                     | a switch is still off; red ones are the ones to put back           |
| The agent asks the contract number again in card 2 | start a fresh conversation with the fallback lines on the card |
| Empty operator queue                     | run card 2's fallback lines in a new conversation                  |
| Model errors mentioning credits          | OpenRouter balance; nothing to do on stage, go to card 5 and 7     |

Do not debug live. Every card except 2 works from a fresh conversation.
