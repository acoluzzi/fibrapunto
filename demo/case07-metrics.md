# 7 · What is measured · 1 min

**Shows:** four of six metrics computed by code from reported facts; the judge grades only the writing; disagreements are the audit of the judge.

[Console, metrics panel. Then the analyst tab for the run-over-run chart.]

  > Say: "Four of the six are code, from the trace and from SQL. Cancellation discipline is zero because no domain has a tool that can execute one. No invention is zero because every numeral the customer did not write is counted, including with billing down."
  > Say: "The model grades only register, tone and whether the writing is any good. Where the two disagree, a person reads the transcript. On the last judged run there were five disagreements: two the judge's mistakes, two imprecise wording, one real, a verified lock-in date stated as 'today'. Code cannot catch that. The judge did."

Volunteer the gap before anyone finds it:

  > "The spec asks for cases written by a non-technical person. Mine are written by me in a non-technical register, which is not the same thing. The harness prints that warning on every run."

Close: "Every zero on that table is achieved by making the failure impossible, not by making it rare. The model decides what is said. Code decides what happens."

**Do not** run `make eval` or `make eval-quick` on stage: both reset the database. `make eval-audit` is safe and shows the disagreements. Point at the latest directory under `evals/runs/` if asked for the raw run.
