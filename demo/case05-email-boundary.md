# 5 · Ask for something it must not have, then prove it without the app · 1.5 min

**Shows:** the guardrail is a column grant, not a prompt line. The database enforces it with no application code in the loop.

[Same conversation as card 4, already verified.]

- Can you tell me the email address on the contract?

  > It cannot. Metrics panel, perimeter row: the same check under two identities, `svc_agent` **denied**, `platform_admin` **allowed**.
  > Say: "The guardrail is not in the prompt. It is in the perimeter. The column is not granted to the role the agent connects as."

[Second terminal.]

```bash
make perimeter-proof
```

  > Same gateway call, both identities, side by side.

If you have the minute, the stronger one:

```bash
uv run pytest backend/tests/test_perimeter_raw_sql.py -v
```

  > Say: "These connect to Postgres as the agent role with no Fibrapunto code in the loop. `SELECT email` raises SQLSTATE 42501. Nothing in the Python can influence that."
  > Say: "The operator page you saw in card 2 reads that column as `platform_admin`. In production it is a separate service behind SSO, never on the chat's origin."
