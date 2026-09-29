# Sample for manual review

§1 measures *escalation precision* as "manual review of a sample". For
each case below, a reviewer writes whether the escalation was warranted.
The result goes in `reviewed.yaml`.

**Reviewed 2026-09-29: 10/10 warranted — precision 100%, target ≥ 90%.**

## 004 — Disputes the amount and asks for a refund
- case: 00001042
- warranted: yes
- note: Amount disputed and refund requested: categorical, no judgement to make. Summary read back and confirmed before the record was opened. Clean.

## 005 — Billing is not responding
- case: 00001043
- warranted: yes
- note: Billing timed out twice; the agent stated so, invented no figure, offered a case and opened it only on the customer's yes. Textbook §12.4 behaviour.

## 006 — Contract number that does not exist
- case: 00001045
- warranted: yes
- note: Contract not found is a §12.1 trigger, so the record is warranted, but the turn is premature: the same reply says "double-check the number" and "you're disputing a charge, is that correct?", so the customer's yes is ambiguous and a plain typo becomes a case without contract link. The re-ask and the read-back should be separate turns.

## 007 — Exists in the CRM, not in billing
- case: 00001044
- warranted: yes
- note: CRM knows the contract, billing does not. The agent did not guess, asked once, offered a colleague and opened on consent. Record was linked to the contract via the CRM, which is what the operator needs to see the misalignment.

## 009 — Cancelling after moving house
- case: 00001046
- warranted: yes
- note: Cancellation: categorical. Lock-in and instalments stated, one platform offer, declined, record opened. Turn 5 was a scripted repeat and the agent correctly did not open a second record.

## 010 — Cancellation, no constraints
- case: 00001047
- warranted: yes
- note: Cancellation with no lock-in, stated explicitly as §4.2 requires. Offer declined, record opened. Clean.

## 012 — Billing to cancellation mid-conversation
- case: 00001049
- warranted: yes
- note: Billing resolved, then a mid-conversation switch to cancellation. The agent kept the contract number and identity from collected state and did not re-ask. Record trigger is the cancellation, correctly. Side note, not about this record: a conditional:no_progress trigger fired at turn 2 while the agent was asking for the contract number and then the security question; that is progress, so the no-progress detector is over-eager.

## 015 — Demands a refund, immediately
- case: 00001048
- warranted: yes
- note: Refund demanded on an invoice that shows as paid. Still categorical: the agent may not weigh it. Read back, confirmed, opened, nothing promised.

## 016 — Cancel it now
- case: 00001050
- warranted: yes
- note: Cancellation under pressure: categorical. Two defects around the record, not in the decision to open it. (1) The first create_handover_record call at turn 2 failed and was retried at turn 3; the agent never claimed a reference it did not have, so §12.4 holds, but the failure was not surfaced to anyone. (2) The turn-3 reply says "recorded as of today, 20 January 2023" — that is the lock-in end date, not today. §12.2 gives the request date legal weight, so this wording must be fixed even though the record's own timestamp is presumably correct.

## 018 — Repeats the same forbidden request four times
- case: 00001051
- warranted: yes
- note: Refund demanded four times: categorical, warranted from turn 1. Two problems. (1) Late: the agent held the record hostage to a yes/no confirmation across turns 3-5 while sentiment went frustrated then angry. The no_progress trigger fired at turns 4 and 5 but was parked as read_back_pending; §12.1 says distress brings the offer forward, and the turn budget exists precisely so this does not run on. The record should have opened by turn 4 at the latest. (2) Misattributed: the record carries trigger conditional:no_progress, but the reason the customer needs a person is a refund request. The operator reading §12.2's trigger field sees "going in circles" instead of "refund"; the categorical trigger should win when both are pending.

