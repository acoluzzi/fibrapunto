# 6 · Out of scope, and a person on request · 1.5 min

**Shows:** scope is a stated boundary, not whatever the model absorbed; a human is granted on the first ask; when nobody is there the reply is an honest template.

[Press **New conversation**.]

- Tell me, is FTTH fibre better than FTTC?

  > A plain refusal that still offers a person. It could easily answer, and it does not. Trace: `router.decision` → **fallback**.
  > Say: "The boundary should be arguable with compliance, not inherited from the model."

[Set **Colleagues** to **none available** in the console. Press **New conversation**.]

- I don't want to talk to a bot, put me through to a person

  > It agrees at once. No "why", no attempt to solve it first, no attempt to keep the customer. Trace: `router.human_requested` → `transfer.outside_service_hours`.
  > The reply is a template with the real service window. Say: "Never 'someone will call you shortly' at 23:40 on a Saturday. Detection is the model's. Honouring is code's, and neither the customer nor the model can block it."

[Set **Colleagues** back to **opening hours**.]
