# Extra · The CRM knows the contract, billing does not · 1 min

**Shows:** the most realistic failure in the design. The agent states what it can see, states what it cannot, and hands over. It does not resolve the disagreement by picking the convenient answer.

[New conversation.]

- How much is my next bill? FP-2020-004199
- Micio

  > Verification passes: the CRM has the contract. Trace: `capability.result` with outcome **notFound** from billing.
  > It says plainly it cannot find the contract in billing and offers a colleague. No amount, no date.

- Yes please, open the case.

  > Case opened by code, reference from the sequence. A test asserts that exactly one contract is CRM-only, so nobody can quietly "fix" the fixture.
